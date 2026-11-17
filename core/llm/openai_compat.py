"""Hint generation against an OpenAI-compatible server.

This is the backend that will actually be used: ``llama.cpp``'s server, Ollama
and vLLM all speak this API, so one client covers every runtime worth
benchmarking in M0 and the choice between them stays a configuration change.

The important part is not the HTTP. It is that the prompt is built
**append-only**, so the server's prefix cache can be reused:

* The system message depends on the session's language and its preamble, and on
  nothing else: not the mode, which a manual hint may change per request. It is
  built once at ``prefill`` and never rebuilt, so what ``prefill`` sends is
  exactly the start of what every hint sends.
* Each hint appends the transcript, then the mode's instruction, after it.
* Nothing before the append point is ever rewritten.

``prefill`` sends the preamble with ``max_tokens=0``. That processes the static
context and populates the server's cache without generating anything, so the
first real hint of a session pays only for its own delta. A server that does not
support a zero-token completion simply returns nothing useful, which is
harmless: the cost moves to the first hint instead of being paid up front.

A prefill that *fails* is a different matter, and it is raised rather than
logged. It is the first time anything talks to the model server, so it is the
earliest moment a dead one can be noticed -- which is at the start of a session
rather than in the middle of a call.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx

from core.llm.base import HintContext
from core.protocol import Language

log = logging.getLogger("susurro.llm")

#: Short, because the machine cannot write long answers fast enough for them to
#: be read in a live conversation. See docs/architecture/inference.md.
MAX_TOKENS: dict[str, int] = {
    "conversational": 60,
    "technical": 220,
    "business": 80,
}

#: The system message's own text: language only. Everything that differs by mode
#: lives in ``SYSTEM_PROMPTS`` and goes after the transcript, so that a hint in
#: a different mode does not change the cached prefix.
BASE_PROMPTS: dict[str, str] = {
    "en": (
        "You help someone during a live conversation. The transcript labels "
        "each line 'them' (the other party) or 'me' (the person reading you). "
        "They read you while making eye contact with someone else, so be brief. "
        "The instruction after the transcript says what kind of answer is wanted."
    ),
    "es": (
        "Ayudas a alguien durante una conversación en vivo. La transcripción "
        "marca cada línea como 'them' (la otra parte) o 'me' (quien te lee). "
        "Te leen mientras miran a los ojos a otra persona, así que sé breve. "
        "La instrucción tras la transcripción dice qué tipo de respuesta se "
        "quiere."
    ),
}

#: Per mode: appended after the transcript on every hint.
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
        "You help someone during a live business meeting. 'them' is the other "
        "party and 'me' is the person reading you. Answer FIRST what 'them' said "
        "last: what to say now, in keywords. At most four lines starting with "
        "'- ', each under 10 words. Do not repeat what 'me' already said. No "
        "preamble, no pleasantries."
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
        "Ayudas a alguien durante una reunión de negocio en vivo. 'them' es la "
        "otra parte y 'me' es quien te lee. Responde PRIMERO a lo último que "
        "dijo 'them': qué decir ahora, en palabras clave. Como máximo cuatro "
        "líneas que empiecen por '- ', de menos de 10 palabras cada una. No "
        "repitas lo que 'me' ya dijo. Sin preámbulo ni cortesías."
    ),
}

#: Appended after the transcript, every time. The system prompt and the profile
#: come first and are long; a small model given a transcript with nothing after
#: it answers the profile -- its notes, its goal -- rather than the last thing
#: said. On the six questions of a recorded call, the hint talked about the
#: scheduled site visit in 11 of 15 answers where it had nothing to do with the
#: question. With this line it did in 5; with this line and the revised business
#: prompt, in 1, and no hint was cut off mid-sentence (it had been 18 of 18).
#: It sits at the very end, so the transcript before it stays an unchanged prefix.
#:
#: Only the business mode has been measured. In the technical mode the same line
#: made the model answer in the first person ("I would implement...") instead of
#: giving the approach in one line, so it is not applied there until someone
#: has measured what it does.
LAST_THING_MODES = frozenset({"business"})
LAST_THING: dict[str, str] = {
    "en": "[Respond to the last thing 'them' said.]",
    "es": "[Responde a lo último que dijo 'them'.]",
}


def system_prompt(language: str, mode: str) -> str:
    """The mode's instruction, which goes after the transcript."""
    return SYSTEM_PROMPTS.get(
        (language, mode), SYSTEM_PROMPTS[("en", "conversational")]
    )


def system_message(language: str, preamble: str) -> dict:
    """The first message of every request, prefill and hint alike."""
    base = BASE_PROMPTS.get(language, BASE_PROMPTS["en"])
    content = f"{base}\n\n{preamble}" if preamble else base
    return {"role": "system", "content": content}


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

    def messages(self, context: HintContext, preamble: str) -> list[dict]:
        """Build a hint request. Order is the contract, not a style choice.

        System message first and unchanged for the whole session, then the
        transcript, then the mode's instruction. Anything that reordered these
        would move the cache boundary and cost a full prefill. The next hint's
        transcript replaces the suffix after the old one's end, which is the
        only part that was ever going to change.
        """
        language, mode = context.language, context.mode
        body = "\n".join(context.transcript) if context.transcript else "(no speech yet)"
        body += "\n\n" + system_prompt(language, mode)
        if context.transcript and mode in LAST_THING_MODES:
            body += "\n" + LAST_THING.get(language, LAST_THING["en"])
        return [system_message(language, preamble), {"role": "user", "content": body}]

    async def prefill(self, preamble: str, language: Language) -> None:
        self.preamble = preamble
        self.prefill_count += 1
        payload = {
            "model": self.model,
            "messages": [system_message(language, preamble)],
            "max_tokens": 0,
            "stream": False,
        }
        # Failures are raised rather than swallowed. Whether a dead model
        # server should stop a session is the server's decision, not this
        # backend's -- and swallowing it here meant a session started happily
        # with nothing behind it and the user found out mid-call.
        response = await self.client().post("/v1/chat/completions", json=payload)
        response.raise_for_status()

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
