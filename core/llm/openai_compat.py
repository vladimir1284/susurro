"""Hint generation against an OpenAI-compatible server.

This is the backend that will actually be used: ``llama.cpp``'s server, Ollama
and vLLM all speak this API, so one client covers every runtime worth
benchmarking in M0 and the choice between them stays a configuration change.

The important part is not the HTTP. It is that the prompt is built
**append-only**, so the server's prefix cache can be reused:

* The preamble goes in a system message, built once at ``prefill`` and never
  rebuilt.
* Each hint appends the transcript and a short instruction after it.
* Nothing before the append point is ever rewritten.

``prefill`` sends the preamble with ``max_tokens=0``. That processes the static
context and populates the server's cache without generating anything, so the
first real hint of a session pays only for its own delta. A server that does not
support a zero-token completion simply returns nothing useful, which is
harmless: the cost moves to the first hint instead of being paid up front.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx

from core.llm.base import HintContext
from core.protocol import Mode

log = logging.getLogger("susurro.llm")

#: Short, because the machine cannot write long answers fast enough for them to
#: be read in a live conversation. See docs/architecture/inference.md.
MAX_TOKENS: dict[str, int] = {
    "conversational": 60,
    "technical": 220,
    "business": 80,
}

SYSTEM_PROMPTS: dict[tuple[str, str], str] = {
    ("en", "conversational"): (
        "You help someone during a live conversation. Reply with at most four "
        "fragments, each on its own line starting with '- '. Keywords, not "
        "sentences. No preamble, no explanation. They are reading you while "
        "making eye contact with someone else."
    ),
    ("en", "technical"): (
        "You help someone during a live technical interview. Give the approach "
        "in one line, then a short code sketch. No preamble. Name the edge "
        "cases in a final line starting with 'Edge:'."
    ),
    ("en", "business"): (
        "You help someone during a live business meeting. At most four "
        "fragments, each on its own line starting with '- '. Name concrete "
        "next steps and risks, not pleasantries."
    ),
    ("es", "conversational"): (
        "Ayudas a alguien durante una conversación en vivo. Responde con cuatro "
        "fragmentos como máximo, cada uno en su línea empezando por '- '. "
        "Palabras clave, no frases. Sin preámbulo. Te leen mientras miran a los "
        "ojos a otra persona."
    ),
    ("es", "technical"): (
        "Ayudas a alguien durante una entrevista técnica en vivo. Da el enfoque "
        "en una línea y luego un esbozo de código. Sin preámbulo. Nombra los "
        "casos límite en una línea final que empiece por 'Límites:'."
    ),
    ("es", "business"): (
        "Ayudas a alguien durante una reunión de negocio en vivo. Cuatro "
        "fragmentos como máximo, cada uno en su línea empezando por '- '. "
        "Nombra próximos pasos y riesgos concretos, no cortesías."
    ),
}


def system_prompt(language: str, mode: str) -> str:
    return SYSTEM_PROMPTS.get(
        (language, mode), SYSTEM_PROMPTS[("en", "conversational")]
    )


@dataclass
class OpenAICompatGenerator:
    """Streaming chat-completions client.

    Works against any server exposing ``POST /v1/chat/completions`` with
    ``stream: true`` — llama.cpp, Ollama, vLLM, LM Studio.
    """

    base_url: str = "http://127.0.0.1:8080"
    model: str = "local"
    api_key: str = ""
    timeout_s: float = 60.0
    temperature: float = 0.3

    preamble: str = field(default="", init=False)
    prefill_count: int = field(default=0, init=False)
    _client: httpx.AsyncClient | None = field(default=None, init=False)

    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url.rstrip("/"),
                headers=headers,
                timeout=self.timeout_s,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def messages(self, context: HintContext | None, preamble: str) -> list[dict]:
        """Build the request. Order is the contract, not a style choice.

        System message first and unchanged for the whole session, then the
        transcript, then the instruction. Anything that reordered these would
        move the cache boundary and cost a full prefill.
        """
        language = context.language if context else "en"
        mode: Mode = context.mode if context else "conversational"
        system = f"{system_prompt(language, mode)}\n\n{preamble}" if preamble else (
            system_prompt(language, mode)
        )
        messages = [{"role": "system", "content": system}]
        if context is None:
            return messages
        body = "\n".join(context.transcript) if context.transcript else "(no speech yet)"
        messages.append({"role": "user", "content": body})
        return messages

    async def prefill(self, preamble: str) -> None:
        self.preamble = preamble
        self.prefill_count += 1
        payload = {
            "model": self.model,
            "messages": self.messages(None, preamble),
            "max_tokens": 0,
            "stream": False,
        }
        try:
            await self.client().post("/v1/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            # Not fatal. A failed warm-up only means the first hint of the
            # session pays the prefill cost that this call was meant to absorb.
            log.warning("prefill warm-up failed, first hint will be slower: %s", exc)

    async def stream(self, context: HintContext) -> AsyncIterator[str]:
        payload = {
            "model": self.model,
            "messages": self.messages(context, self.preamble),
            "max_tokens": MAX_TOKENS.get(context.mode, 80),
            "temperature": self.temperature,
            "stream": True,
        }
        async with self.client().stream(
            "POST", "/v1/chat/completions", json=payload
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                chunk = parse_sse_chunk(line)
                if chunk:
                    yield chunk


def parse_sse_chunk(line: str) -> str | None:
    """Pull the text out of one server-sent-events line.

    Tolerant on purpose: servers differ on keep-alive comments, blank lines and
    whether the final ``[DONE]`` arrives at all, and none of that is worth
    failing a live hint over.
    """
    line = line.strip()
    if not line or line.startswith(":") or not line.startswith("data:"):
        return None
    data = line[len("data:") :].strip()
    if data == "[DONE]":
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        log.debug("unparseable SSE line: %r", line[:120])
        return None
    choices = payload.get("choices") or []
    if not choices:
        return None
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    return content or None
