"""OpenAI-compatible hint backend.

No model runs here. What is under test is the request shape — which is where
the prompt cache is won or lost — and the tolerance of the streaming parser,
which has to survive the small differences between llama.cpp, Ollama and vLLM.
"""

from __future__ import annotations

import json

import httpx
import pytest

from core.llm.base import HintContext
from core.llm.openai_compat import (
    MAX_TOKENS,
    OpenAICompatGenerator,
    parse_sse_chunk,
    system_prompt,
)

PREAMBLE = "## BACKGROUND\nFifteen years of backend work."


def sse(*chunks: str, done: bool = True) -> bytes:
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": chunk}}]})
        for chunk in chunks
    ]
    if done:
        lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode()


def generator_with(handler) -> OpenAICompatGenerator:
    generator = OpenAICompatGenerator(base_url="http://core.test")
    # Injecting the transport rather than standing a server up: the behaviour
    # worth testing is what gets sent, not whether sockets work.
    generator._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://core.test"
    )
    return generator


class TestSseParsing:
    def test_extracts_content(self) -> None:
        line = 'data: {"choices":[{"delta":{"content":"hello"}}]}'
        assert parse_sse_chunk(line) == "hello"

    @pytest.mark.parametrize(
        "line",
        [
            "",
            "   ",
            ": keep-alive",
            "data: [DONE]",
            "event: ping",
            "data: not json",
            'data: {"choices":[]}',
            'data: {"choices":[{"delta":{}}]}',
            'data: {"choices":[{"delta":{"content":""}}]}',
        ],
        ids=[
            "blank", "whitespace", "comment", "done", "event", "garbage",
            "no-choices", "empty-delta", "empty-content",
        ],
    )
    def test_ignores_everything_that_is_not_content(self, line: str) -> None:
        # Servers differ on keep-alives, blank lines and whether [DONE] arrives
        # at all. None of that is worth failing a live hint over.
        assert parse_sse_chunk(line) is None

    def test_tolerates_a_missing_space_after_the_colon(self) -> None:
        assert parse_sse_chunk('data:{"choices":[{"delta":{"content":"x"}}]}') == "x"


class TestPromptShape:
    def test_system_message_carries_the_preamble(self) -> None:
        generator = OpenAICompatGenerator()
        messages = generator.messages(
            HintContext(mode="conversational", language="en"), PREAMBLE
        )
        assert messages[0]["role"] == "system"
        assert PREAMBLE in messages[0]["content"]

    def test_the_system_message_is_first_and_the_transcript_follows(self) -> None:
        # Order is the contract: anything that reordered these would move the
        # cache boundary and cost a full prefill.
        generator = OpenAICompatGenerator()
        context = HintContext(
            mode="conversational", language="en", transcript=("them: hello",)
        )
        messages = generator.messages(context, PREAMBLE)
        assert [m["role"] for m in messages] == ["system", "user"]
        assert messages[1]["content"].startswith("them: hello\n\n")

    def test_the_last_thing_said_is_pointed_at_after_the_transcript(self) -> None:
        # With nothing after the transcript a small model answers the profile's
        # notes instead of the question. The instruction goes last, so the
        # transcript before it stays an unchanged prefix for the cache.
        generator = OpenAICompatGenerator()
        for language, expected in (("en", "last thing 'them' said"), ("es", "lo último que dijo")):
            context = HintContext(
                mode="business", language=language, transcript=("them: a", "me: b")
            )
            body = generator.messages(context, PREAMBLE)[1]["content"]
            assert body.startswith("them: a\nme: b\n\n")
            assert body.rstrip().endswith("]")
            assert expected in body

    def test_the_last_thing_line_is_still_business_only(self) -> None:
        # Not measured elsewhere: in the technical mode the line made the model
        # answer in the first person instead of giving the approach.
        generator = OpenAICompatGenerator()
        for mode in ("conversational", "technical"):
            context = HintContext(mode=mode, language="en", transcript=("them: a",))
            body = generator.messages(context, PREAMBLE)[1]["content"]
            assert "last thing 'them' said" not in body
            assert system_prompt("en", mode) in body

    def test_a_longer_transcript_extends_the_earlier_one_as_a_prefix(self) -> None:
        generator = OpenAICompatGenerator()
        short = generator.messages(
            HintContext(mode="business", language="es", transcript=("them: a",)), PREAMBLE
        )[1]["content"]
        longer = generator.messages(
            HintContext(mode="business", language="es", transcript=("them: a", "me: b")),
            PREAMBLE,
        )[1]["content"]
        line_a = short.split("\n\n")[0]
        assert longer.startswith(line_a + "\n")
        assert line_a == "them: a"

    def test_the_system_message_does_not_change_between_hints(self) -> None:
        generator = OpenAICompatGenerator()
        first = generator.messages(
            HintContext(mode="conversational", language="en", transcript=("a",)),
            PREAMBLE,
        )
        second = generator.messages(
            HintContext(mode="conversational", language="en", transcript=("a", "b")),
            PREAMBLE,
        )
        assert first[0] == second[0]

    def test_an_empty_transcript_still_sends_something(self) -> None:
        generator = OpenAICompatGenerator()
        context = HintContext(mode="conversational", language="en")
        body = generator.messages(context, PREAMBLE)[1]["content"]
        assert body.startswith("(no speech yet)")

    @pytest.mark.parametrize("language", ["en", "es"])
    @pytest.mark.parametrize("mode", ["conversational", "technical", "business"])
    def test_every_language_and_mode_has_its_own_instruction(
        self, language: str, mode: str
    ) -> None:
        assert system_prompt(language, mode) != ""

    def test_spanish_instructions_are_in_spanish(self) -> None:
        # A Spanish session whose system prompt is English produces English
        # hints, which is the kind of bug nobody notices until a live call.
        assert "Ayudas" in system_prompt("es", "conversational")

    def test_an_unknown_combination_falls_back_rather_than_failing(self) -> None:
        assert system_prompt("fr", "poetic") == system_prompt("en", "conversational")


LANGUAGES = ["en", "es"]
MODES = ["conversational", "technical", "business"]


def serialized(messages: list[dict]) -> str:
    return json.dumps(messages, ensure_ascii=False)


class TestPrefillIsAPrefixOfEveryHint:
    """D1 of AUDIT.md. The latency argument is that the preamble is processed
    once per session. A prefix cache matches from the first token, so that only
    holds if what prefill sends is exactly the start of what every hint sends.
    The old prefill sent the English conversational system prompt whatever the
    session was, sharing zero leading characters with an es/business hint."""

    async def prefill_messages(self, language: str) -> list[dict]:
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": []})

        generator = generator_with(handler)
        await generator.prefill(PREAMBLE, language)
        await generator.aclose()
        return seen[0]["messages"]

    @pytest.mark.parametrize("language", LANGUAGES)
    @pytest.mark.parametrize("mode", MODES)
    async def test_the_prefill_is_the_start_of_the_hint(
        self, language: str, mode: str
    ) -> None:
        sent = await self.prefill_messages(language)
        hint = OpenAICompatGenerator().messages(
            HintContext(mode=mode, language=language, transcript=("them: hi",)),
            PREAMBLE,
        )
        head = serialized(sent)[:-1]  # the list without its closing bracket
        assert serialized(hint).startswith(head)

    @pytest.mark.parametrize("language", LANGUAGES)
    async def test_a_hint_in_another_mode_keeps_the_cache(self, language: str) -> None:
        # hint.request carries its own mode; it must not move the cache boundary.
        generator = OpenAICompatGenerator()
        systems = {
            mode: generator.messages(
                HintContext(mode=mode, language=language, transcript=("a",)), PREAMBLE
            )[0]
            for mode in MODES
        }
        assert len({serialized([m]) for m in systems.values()}) == 1

    @pytest.mark.parametrize("language", LANGUAGES)
    @pytest.mark.parametrize("mode", MODES)
    def test_a_grown_transcript_shares_everything_up_to_the_old_end(
        self, language: str, mode: str
    ) -> None:
        generator = OpenAICompatGenerator()
        old = ("them: first question", "me: an answer")
        before = serialized(
            generator.messages(
                HintContext(mode=mode, language=language, transcript=old), PREAMBLE
            )
        )
        after = serialized(
            generator.messages(
                HintContext(
                    mode=mode, language=language, transcript=(*old, "them: next?")
                ),
                PREAMBLE,
            )
        )
        shared = before[: before.index(old[-1]) + len(old[-1])]
        assert after.startswith(shared)

    def test_spanish_session_system_message_is_in_spanish(self) -> None:
        system = OpenAICompatGenerator().messages(
            HintContext(mode="technical", language="es"), PREAMBLE
        )[0]["content"]
        assert "Ayudas" in system
        assert PREAMBLE in system


class TestRequests:
    async def test_prefill_asks_for_no_tokens(self) -> None:
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": []})

        generator = generator_with(handler)
        await generator.prefill(PREAMBLE, "en")
        await generator.aclose()

        assert seen[0]["max_tokens"] == 0
        assert seen[0]["stream"] is False
        assert PREAMBLE in seen[0]["messages"][0]["content"]
        assert generator.prefill_count == 1

    async def test_a_failed_prefill_is_raised_not_swallowed(self) -> None:
        # Whether a dead model server should stop a session is the server's
        # decision, not this backend's. Swallowing it here meant a session
        # started happily with nothing behind it and the user found out in the
        # middle of a call.
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no server", request=request)

        generator = generator_with(handler)
        with pytest.raises(httpx.HTTPError):
            await generator.prefill(PREAMBLE, "en")
        await generator.aclose()
        # The preamble is still remembered: the server may come back, and the
        # next hint should use the right context when it does.
        assert generator.preamble == PREAMBLE

    async def test_a_prefill_rejected_by_the_server_is_raised(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="model loading")

        generator = generator_with(handler)
        with pytest.raises(httpx.HTTPStatusError):
            await generator.prefill(PREAMBLE, "en")
        await generator.aclose()

    async def test_stream_yields_each_chunk(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=sse("- one\n", "- two\n"))

        generator = generator_with(handler)
        context = HintContext(mode="conversational", language="en", transcript=("q",))
        chunks = [chunk async for chunk in generator.stream(context)]
        await generator.aclose()
        assert chunks == ["- one\n", "- two\n"]

    async def test_stream_sends_the_mode_token_budget(self) -> None:
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return httpx.Response(200, content=sse("x"))

        generator = generator_with(handler)
        context = HintContext(mode="technical", language="en", transcript=("q",))
        [chunk async for chunk in generator.stream(context)]
        await generator.aclose()
        assert seen[0]["max_tokens"] == MAX_TOKENS["technical"]
        assert seen[0]["stream"] is True

    async def test_conversational_is_budgeted_tighter_than_technical(self) -> None:
        # A conversational hint is read while making eye contact; a technical
        # one is read at a keyboard. The budgets are not interchangeable.
        assert MAX_TOKENS["conversational"] < MAX_TOKENS["technical"]

    async def test_a_server_error_is_raised_not_swallowed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="model loading")

        generator = generator_with(handler)
        context = HintContext(mode="conversational", language="en")
        with pytest.raises(httpx.HTTPStatusError):
            [chunk async for chunk in generator.stream(context)]
        await generator.aclose()

    async def test_a_stream_that_ends_without_done_still_yields(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=sse("partial", done=False))

        generator = generator_with(handler)
        context = HintContext(mode="conversational", language="en")
        chunks = [chunk async for chunk in generator.stream(context)]
        await generator.aclose()
        assert chunks == ["partial"]
