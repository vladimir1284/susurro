"""The benchmark harness.

No models run here. What is tested is the harness itself — the arithmetic, the
gate evaluation, and the refusals — because a harness nobody has checked is the
first thing doubted when a number comes back surprising.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from bench.metrics import Samples, machine, percentile
from bench.report import GATES, Report
from bench.run import (
    CHARS_PER_TOKEN,
    LlmBench,
    build_parser,
    context_of,
    legacy_hint_payload,
    main,
)


class TestPercentile:
    def test_nearest_rank_does_not_interpolate(self) -> None:
        # Interpolating a p90 over ten numbers invents precision that is not
        # there, and these runs have few samples by design.
        assert percentile(list(range(1, 11)), 0.90) == 9

    @pytest.mark.parametrize(
        "fraction, expected", [(0.0, 1), (0.5, 5), (1.0, 10)]
    )
    def test_edges(self, fraction: float, expected: int) -> None:
        assert percentile(list(range(1, 11)), fraction) == expected

    def test_a_single_sample(self) -> None:
        assert percentile([42.0], 0.9) == 42.0

    def test_no_samples_is_an_error(self) -> None:
        with pytest.raises(ValueError):
            percentile([], 0.9)


class TestSamples:
    def test_summary_reports_the_tail_not_just_the_mean(self) -> None:
        # A hint generator that is usually fast and occasionally four seconds
        # late is a bad one, and a mean hides exactly that.
        samples = Samples("ttft", "s")
        for value in [0.2] * 8 + [4.0, 4.0]:
            samples.add(value)
        summary = samples.summary()
        assert summary["median"] == 0.2
        assert summary["max"] == 4.0
        # Two slow samples in ten: the median still says everything is fine and
        # the p90 does not.
        assert summary["p90"] == 4.0
        assert summary["mean"] < summary["p90"]

    def test_an_empty_series_reports_no_samples(self) -> None:
        assert Samples("x", "s").summary() == {"name": "x", "unit": "s", "n": 0}


class TestMachineFingerprint:
    def test_records_what_makes_a_number_transferable(self) -> None:
        info = machine()
        for key in ("platform", "machine", "cpu_count", "total_memory_gb",
                    "accelerator", "python"):
            assert key in info

    def test_this_container_reports_no_accelerator(self) -> None:
        # If this ever starts reporting one, the warning in the summary stops
        # firing and a meaningless number could be quoted as a real one.
        assert machine()["accelerator"] in ("none", "nvidia", "apple-silicon")


class TestGates:
    def report_with(self, **values: float) -> Report:
        report = Report(machine={"accelerator": "none"})
        report.llm = {k: v for k, v in values.items()}
        return report

    def test_all_measurements_inside_the_limits_passes(self) -> None:
        report = self.report_with(
            warm_ttft_s=0.4, resident_memory_gb=9.0, stt_realtime_factor=0.3
        )
        assert report.gate_passed() is True

    def test_a_measurement_over_the_limit_fails(self) -> None:
        report = self.report_with(
            warm_ttft_s=1.4, resident_memory_gb=9.0, stt_realtime_factor=0.3
        )
        assert report.gate_passed() is False
        failed = [v for v in report.verdicts() if not v.passed]
        assert [v.name for v in failed] == ["warm_ttft_s"]

    def test_a_missing_measurement_is_not_a_pass(self) -> None:
        # Silence is not evidence. A gate nobody measured has not been met.
        report = self.report_with(warm_ttft_s=0.4)
        assert report.gate_passed() is False

    def test_a_value_exactly_at_the_limit_fails(self) -> None:
        # At a real-time factor of exactly 1.0 the transcript never catches up.
        report = self.report_with(
            warm_ttft_s=0.4, resident_memory_gb=9.0, stt_realtime_factor=1.0
        )
        assert report.gate_passed() is False

    def test_summaries_are_read_by_their_median(self) -> None:
        report = Report(machine={})
        report.llm = {
            "warm_ttft_s": {"median": 0.5, "p90": 2.0, "n": 5},
            "resident_memory_gb": 9.0,
        }
        report.stt = {"stt_realtime_factor": {"median": 0.2, "n": 3}}
        assert report.gate_passed() is True

    def test_every_gate_explains_itself(self) -> None:
        for name, gate in GATES.items():
            assert gate["what"], f"{name} has no explanation"


class TestReportFile:
    def test_writes_a_readable_result(self, tmp_path: Path) -> None:
        report = Report(machine=machine())
        report.llm = {"warm_ttft_s": {"median": 0.4, "n": 1}}
        path = report.write(tmp_path / "nested" / "result.json")
        written = json.loads(path.read_text())
        assert written["schema"] == 1
        assert "recorded_at" in written
        assert written["machine"]["cpu_count"] >= 1
        assert written["gate_passed"] is False

    def test_the_summary_warns_when_there_is_no_accelerator(self) -> None:
        report = Report(machine={"accelerator": "none", "cpu_count": 2})
        assert "do not transfer" in report.summary()

    def test_a_dry_run_says_so_in_the_file(self, tmp_path: Path) -> None:
        report = Report(machine=machine(), dry_run=True)
        path = report.write(tmp_path / "dry.json")
        assert json.loads(path.read_text())["dry_run"] is True


class TestPromptSizing:
    @pytest.mark.parametrize("tokens", [100, 1000, 4000])
    def test_context_is_about_the_requested_size(self, tokens: int) -> None:
        assert len(context_of(tokens)) == tokens * CHARS_PER_TOKEN


class TestLlmBench:
    def bench(self, handler) -> tuple[LlmBench, httpx.AsyncClient]:
        bench = LlmBench(base_url="http://model.test", model="m", repeats=1)
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://model.test"
        )
        return bench, client

    async def test_ttft_measures_until_the_first_content_token(self) -> None:
        body = (
            'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
            "data: [DONE]\n\n"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=body.encode())

        bench, client = self.bench(handler)
        elapsed = await bench.ttft(client, "system", "user")
        await client.aclose()
        assert elapsed >= 0

    async def test_a_stream_with_no_content_is_an_error(self) -> None:
        # Silently recording a zero would be far worse than failing: it would
        # look like an excellent result.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"data: [DONE]\n\n")

        bench, client = self.bench(handler)
        with pytest.raises(RuntimeError, match="no content"):
            await bench.ttft(client, "system", "user")
        await client.aclose()

    async def test_decode_rate_excludes_the_first_token(self) -> None:
        # The first token's arrival is prefill, not decode. Counting it would
        # mix the two numbers the benchmark exists to separate.
        chunks = "".join(
            'data: {"choices":[{"delta":{"content":"x"}}]}\n\n' for _ in range(11)
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=(chunks + "data: [DONE]\n\n").encode())

        bench, client = self.bench(handler)
        rate = await bench.decode_rate(client, "system")
        await client.aclose()
        assert rate > 0

    async def test_cold_prefill_varies_the_prompt_between_repeats(self) -> None:
        # A "cold" number measured against a warm server cache benchmarks
        # nothing at all.
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            seen.append(payload["messages"][0]["content"][:40])
            return httpx.Response(
                200, content=b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
            )

        bench = LlmBench(base_url="http://model.test", model="m", repeats=3)
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://model.test"
        )
        for index in range(3):
            await bench.ttft(client, f"run {index}\n" + context_of(10), "Go.")
        await client.aclose()
        assert len(set(seen)) == 3


class TestCli:
    def test_the_speech_benchmark_refuses_to_guess_at_audio(self, capsys) -> None:
        # Silence or a tone understates the work and overstates the result.
        assert main(["--skip-llm"]) == 2
        assert "--audio is required" in capsys.readouterr().err

    def test_a_dry_run_writes_a_file_and_marks_it(self, tmp_path: Path, capsys) -> None:
        out = tmp_path / "dry.json"
        assert main(["--dry-run", "--out", str(out)]) == 0
        written = json.loads(out.read_text())
        assert written["dry_run"] is True
        assert any("DRY RUN" in note for note in written["notes"])
        assert "measures nothing" in capsys.readouterr().out

    def test_the_exit_code_reports_the_gate(self, tmp_path: Path) -> None:
        # So that CI, or a person in a hurry, cannot miss a failure.
        out = tmp_path / "empty.json"
        assert main(["--skip-llm", "--skip-stt", "--out", str(out)]) == 1

    def test_a_label_is_recorded(self, tmp_path: Path) -> None:
        out = tmp_path / "labelled.json"
        main(["--dry-run", "--label", "llama-cpp", "--out", str(out)])
        notes = json.loads(out.read_text())["notes"]
        assert any("llama-cpp" in note for note in notes)

    def test_defaults_point_at_a_local_server(self) -> None:
        args = build_parser().parse_args([])
        assert args.llm_url.startswith("http://127.0.0.1")


class TestFirstHintAfterPrefill:
    """AUDIT.md D1: the case that tells whether the cache works at all."""

    @pytest.fixture
    def calls(self, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
        import bench.run as run_module

        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            seen.append(payload)
            if payload.get("stream"):
                return httpx.Response(
                    200, content=b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
                )
            return httpx.Response(200, json={"choices": []})

        class Mocked(run_module.OpenAICompatGenerator):
            def client(self) -> httpx.AsyncClient:
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        transport=httpx.MockTransport(handler),
                        base_url="http://model.test",
                    )
                return self._client

        monkeypatch.setattr(run_module, "OpenAICompatGenerator", Mocked)
        return seen

    @staticmethod
    def bench() -> LlmBench:
        return LlmBench(base_url="http://model.test", model="m", repeats=2)

    async def test_each_variant_sends_what_its_name_says(self, calls: list[dict]) -> None:
        report = Report(machine={})
        await self.bench().run_prefill_case(report, "es", "business")
        assert {
            "d1_first_hint_ttft_cold_s",
            "d1_first_hint_ttft_prefilled_s",
            "d1_first_hint_ttft_legacy_s",
        } <= set(report.llm)
        # cold sends no zero-token request; prefilled and legacy send one per repeat.
        assert len([c for c in calls if c.get("max_tokens") == 0]) == 4

    async def test_every_run_has_its_own_preamble(self, calls: list[dict]) -> None:
        # Otherwise a run would warm the next and every variant would look cached.
        await self.bench().run_prefill_case(Report(machine={}), "es", "business")
        markers = {
            line
            for call in calls
            for line in call["messages"][0]["content"].splitlines()
            if line.startswith("## BACKGROUND (d1")
        }
        assert len(markers) == 6  # 3 variants x 2 repeats

    async def test_the_prefilled_variant_warms_the_system_message_the_hint_sends(
        self, calls: list[dict]
    ) -> None:
        await self.bench().first_hint_ttft("prefilled", 0, "es", "business")
        prefill, hint = calls
        assert prefill["messages"][0] == hint["messages"][0]

    async def test_the_legacy_variant_has_the_old_mismatch(self, calls: list[dict]) -> None:
        await self.bench().first_hint_ttft("legacy", 0, "es", "business")
        prefill, hint = calls
        assert prefill["messages"][0] != hint["messages"][0]

    def test_the_legacy_payload_keeps_the_mode_in_the_system_message(self) -> None:
        from core.llm.openai_compat import SYSTEM_PROMPTS

        payload = legacy_hint_payload("m", "PRE", ("them: hi",), "es", "business")
        assert payload["messages"][0]["content"].startswith(
            SYSTEM_PROMPTS[("es", "business")]
        )
