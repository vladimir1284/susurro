"""Voice activity detection and the speculative hints it drives.

The energy detector is used for the behavioural tests because it is
deterministic: a tone is loud, silence is not, and there is no model to make
the outcome depend on hardware or weights. Silero is exercised separately, and
only in the direction that can honestly be checked here.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import numpy as np
import pytest
import websockets

from capture.codec import FRAME_BYTES, OpusEncoder
from core import protocol as p
from core.llm.fake import CannedHintGenerator
from core.main import build_detectors
from core.server import Core, serve_core
from core.session.profiles import ProfileStore
from core.stt.fake import ScriptedRecognizer, ScriptedUtterance
from core.vad import (
    WINDOW_MS,
    WINDOW_SAMPLES,
    EnergyDetector,
    SileroDetector,
    VadEvent,
    build_detector,
)

FIXTURES = Path(__file__).parent / "fixtures" / "profiles"
TOKEN = "test-token"


def silero_available() -> bool:
    try:
        SileroDetector()
    except (FileNotFoundError, ImportError):
        return False
    return True


#: Silero cannot be a normal dependency: its package declares torch and only
#: the ONNX file it ships is used. A contributor who skipped the documented
#: `pip install --no-deps silero-vad` should be told that, not handed six
#: failures.
needs_silero = pytest.mark.skipif(
    not silero_available(),
    reason="run `pip install --no-deps silero-vad onnxruntime` to exercise Silero",
)


def tone(ms: int, amplitude: int = 8000, hz: int = 300) -> bytes:
    n = 16 * ms
    wave = np.sin(2 * np.pi * hz * np.arange(n) / 16000) * amplitude
    return wave.astype(np.int16).tobytes()


def quiet(ms: int) -> bytes:
    return bytes(2 * 16 * ms)


class TestEnergyDetector:
    def test_silence_produces_no_events(self) -> None:
        assert EnergyDetector().push(quiet(2000)) == []

    def test_a_loud_signal_opens_an_utterance(self) -> None:
        detector = EnergyDetector()
        assert detector.push(tone(500)) == [VadEvent.SPEECH_START]
        assert detector.speaking is True

    def test_silence_after_speech_closes_it(self) -> None:
        detector = EnergyDetector()
        detector.push(tone(500))
        assert detector.push(quiet(1000)) == [VadEvent.SPEECH_END]
        assert detector.speaking is False

    def test_a_short_gap_does_not_end_the_turn(self) -> None:
        # The pauses inside a sentence are longer than people expect. Cutting
        # on the first quiet window would fire end-of-turn mid-sentence, which
        # is the most annoying thing this component can do.
        detector = EnergyDetector(end_windows=16)
        detector.push(tone(500))
        assert detector.push(quiet(200)) == []
        assert detector.push(tone(200)) == []
        assert detector.speaking is True

    def test_a_single_loud_window_does_not_open_one(self) -> None:
        # A door closing is not an utterance.
        detector = EnergyDetector(start_windows=2)
        assert detector.push(tone(WINDOW_MS)) == []

    def test_a_full_turn_is_one_start_and_one_end(self) -> None:
        detector = EnergyDetector()
        events = detector.push(tone(800) + quiet(1000))
        assert events == [VadEvent.SPEECH_START, VadEvent.SPEECH_END]

    def test_two_turns_produce_two_pairs(self) -> None:
        detector = EnergyDetector()
        events = detector.push(tone(500) + quiet(1000) + tone(500) + quiet(1000))
        assert events == [
            VadEvent.SPEECH_START,
            VadEvent.SPEECH_END,
            VadEvent.SPEECH_START,
            VadEvent.SPEECH_END,
        ]

    def test_partial_windows_are_buffered_across_calls(self) -> None:
        # Audio arrives in 20 ms frames and the detector works on 32 ms
        # windows, so every push leaves a remainder.
        detector = EnergyDetector()
        events = []
        for _ in range(40):
            events.extend(detector.push(tone(20)))
        assert events == [VadEvent.SPEECH_START]

    def test_reset_forgets_the_utterance(self) -> None:
        detector = EnergyDetector()
        detector.push(tone(500))
        detector.reset()
        assert detector.speaking is False
        assert detector.push(quiet(1000)) == []

    def test_quiet_speech_below_the_threshold_is_missed(self) -> None:
        # Documenting a real limitation rather than asserting it away: this is
        # a loudness threshold, not a speech detector.
        assert EnergyDetector(threshold=0.1).push(tone(500, amplitude=500)) == []


@needs_silero
class TestSileroDetector:
    def test_it_loads_without_torch(self) -> None:
        # The silero-vad package declares torch; the wheel also ships the ONNX
        # file, which is all this uses. Installing torch for a 2 MB model would
        # be absurd on a machine already short of memory.
        import sys

        SileroDetector()
        assert "torch" not in sys.modules

    def test_silence_is_not_speech(self) -> None:
        detector = SileroDetector()
        assert detector.push(quiet(2000)) == []
        assert detector.speaking is False

    def test_broadband_noise_is_not_speech(self) -> None:
        # The thing an energy threshold gets wrong, and the reason Silero is
        # the default.
        rng = np.random.default_rng(0)
        noise = (rng.standard_normal(16000) * 8000).astype(np.int16).tobytes()
        assert SileroDetector().push(noise) == []

    def test_a_tone_is_not_speech(self) -> None:
        assert SileroDetector().push(tone(1000)) == []

    def test_positive_detection_is_not_tested_here(self) -> None:
        # There is no speech audio in this repository, and synthesising
        # something a speech model accepts is not a test of anything. The
        # positive direction is verified by using it; the negative direction is
        # what is checked above.
        detector = SileroDetector()
        assert detector.push(quiet(100)) == []

    def test_reset_clears_the_model_state(self) -> None:
        detector = SileroDetector()
        detector.push(quiet(100))
        detector.reset()
        assert detector.speaking is False

    def test_each_window_reaches_the_model_with_the_previous_windows_tail(self) -> None:
        # The model wants 64 samples of context in front of every 512. Without
        # them it runs, returns a probability, and scores real speech near 0.1:
        # nothing ever opens an utterance, and a test of silence cannot tell.
        seen: list[np.ndarray] = []

        class Recorder:
            def run(self, _names, feed):
                seen.append(feed["input"][0].copy())
                return [np.array([[0.0]], dtype=np.float32)], feed["state"]

        detector = SileroDetector()
        detector._session = Recorder()
        first = (np.arange(512) % 100).astype(np.int16)
        second = (np.arange(512) % 100 + 1000).astype(np.int16)
        detector.push(first.tobytes() + second.tobytes())

        assert [len(window) for window in seen] == [576, 576]
        assert not seen[0][:64].any()  # nothing came before the first window
        scaled = first.astype(np.float32) / 32768.0
        assert np.array_equal(seen[1][:64], scaled[-64:])

    def test_reset_forgets_the_context_as_well_as_the_state(self) -> None:
        seen: list[np.ndarray] = []

        class Recorder:
            def run(self, _names, feed):
                seen.append(feed["input"][0].copy())
                return [np.array([[0.0]], dtype=np.float32)], feed["state"]

        detector = SileroDetector()
        detector._session = Recorder()
        detector.push(tone(32))
        detector.reset()
        detector.push(tone(32))
        assert not seen[-1][:64].any()


class TestBuildDetector:
    def test_off_means_no_detector(self) -> None:
        assert build_detector({"backend": "off"}) is None

    def test_energy_is_selected_by_name(self) -> None:
        assert isinstance(build_detector({"backend": "energy"}), EnergyDetector)

    def test_silero_is_the_default_when_it_is_available(self) -> None:
        detector = build_detector({})
        if silero_available():
            assert isinstance(detector, SileroDetector)
        else:
            # Falling back rather than refusing to start: a missing optional
            # model should cost you better detection, not a working core.
            assert isinstance(detector, EnergyDetector)

    def test_an_unknown_backend_fails_loudly(self) -> None:
        with pytest.raises(SystemExit, match="unknown vad backend"):
            build_detector({"backend": "telepathy"})

    def test_tuning_reaches_the_detector(self) -> None:
        detector = build_detector({"backend": "energy", "end_windows": 3})
        detector.push(tone(500))
        assert detector.push(quiet(200)) == [VadEvent.SPEECH_END]

    def test_lanes_get_separate_detectors(self) -> None:
        # They carry state across windows; sharing one would make each lane's
        # silence close the other lane's utterance.
        detectors = build_detectors({"backend": "energy"})
        assert detectors["far"] is not detectors["mic"]

    def test_window_size_matches_what_silero_expects(self) -> None:
        assert WINDOW_SAMPLES == 512
        assert WINDOW_MS == 32


SCRIPT = [
    ScriptedUtterance(lane="far", text="So why did you leave?", frames=2),
]


@pytest.fixture
async def core() -> Core:
    return Core(
        profiles=ProfileStore(FIXTURES),
        recognizer=ScriptedRecognizer(script=list(SCRIPT), partial_every=50),
        generator=CannedHintGenerator(chunk_delay_s=0.02),
        token=TOKEN,
        detectors=build_detectors({"backend": "energy"}),
    )


@pytest.fixture
async def url(core: Core) -> AsyncIterator[str]:
    async with serve_core(core, "127.0.0.1", 0) as server:
        yield f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"


async def open_console(url: str) -> websockets.ClientConnection:
    ws = await websockets.connect(f"{url}/console?token={TOKEN}")
    await ws.recv()  # profiles
    await ws.recv()  # state
    await ws.send(
        json.dumps(
            {
                "type": "session.config",
                "profile_id": "en-technical-backend",
                "language": "en",
                "mode": "conversational",
            }
        )
    )
    await ws.recv()
    await ws.send(json.dumps({"type": "session.start"}))
    # More than one state event arrives: loading, then live.
    for _ in range(10):
        event = json.loads(await ws.recv())
        if event["type"] == "state" and event["session"] == "live":
            return ws
    raise AssertionError("session never went live")


async def drain(ws: websockets.ClientConnection, kind: str, limit: int = 60) -> dict:
    for _ in range(limit):
        event = json.loads(await ws.recv())
        if event["type"] == kind:
            return event
    raise AssertionError(f"no {kind!r} event in {limit} messages")


async def send_audio(capture, pcm: bytes, encoder: OpusEncoder, start: int = 0) -> int:
    """Send PCM as 20 ms Opus frames, the way a real client would."""
    sent = start
    for offset in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES):
        frame = p.AudioFrame(
            timestamp_us=sent * 20_000,
            payload=encoder.encode(pcm[offset : offset + FRAME_BYTES]),
        )
        await capture.send(frame.encode())
        sent += 1
    return sent


class TestSpeculativeHints:
    async def open_far_lane(self, url: str):
        capture = await websockets.connect(f"{url}/capture?token={TOKEN}")
        await capture.send(
            p.Hello(session_id="s", lane="far", client="test/0.1").to_json()
        )
        await capture.recv()
        return capture

    async def test_the_other_party_falling_silent_starts_a_hint(
        self, url: str, core: Core
    ) -> None:
        console = await open_console(url)
        capture = await self.open_far_lane(url)
        encoder = OpusEncoder()

        sent = await send_audio(capture, tone(600), encoder)
        await send_audio(capture, quiet(1200), encoder, sent)

        start = await drain(console, "hint.start")
        assert start["trigger"] == "speculative"
        # Wait for the first chunk: hint.start is broadcast before the task
        # that calls the generator has run.
        await drain(console, "hint.delta")
        # The transcript the hint was written against includes the sentence
        # that just ended: the recognizer is flushed before generating.
        assert core.generator.contexts[-1].transcript == ("them: So why did you leave?",)
        await capture.close()
        await console.close()

    async def test_the_user_speaking_does_not_start_one(
        self, url: str, core: Core
    ) -> None:
        # Hints are for answering them, not for narrating yourself.
        console = await open_console(url)
        capture = await websockets.connect(f"{url}/capture?token={TOKEN}")
        await capture.send(
            p.Hello(session_id="s", lane="mic", client="test/0.1").to_json()
        )
        await capture.recv()
        encoder = OpusEncoder()
        sent = await send_audio(capture, tone(600), encoder)
        await send_audio(capture, quiet(1200), encoder, sent)
        await capture.close()
        assert core.state.active_hint is None
        await console.close()

    async def test_the_other_party_resuming_cancels_it(
        self, url: str, core: Core
    ) -> None:
        console = await open_console(url)
        capture = await self.open_far_lane(url)
        encoder = OpusEncoder()

        sent = await send_audio(capture, tone(600), encoder)
        sent = await send_audio(capture, quiet(1200), encoder, sent)
        start = await drain(console, "hint.start")
        await send_audio(capture, tone(600), encoder, sent)

        end = await drain(console, "hint.end")
        assert end["hint_id"] == start["hint_id"]
        assert end["reason"] == "cancelled"
        await capture.close()
        await console.close()

    async def test_a_manual_hint_survives_the_other_party_resuming(
        self, url: str, core: Core
    ) -> None:
        # They asked for it. Throwing it away because someone started talking
        # again would make the button unreliable.
        console = await open_console(url)
        capture = await self.open_far_lane(url)
        encoder = OpusEncoder()

        await console.send(json.dumps({"type": "hint.request", "mode": "conversational"}))
        start = await drain(console, "hint.start")
        assert start["trigger"] == "manual"

        await send_audio(capture, tone(600), encoder)
        assert core.state.active_hint == start["hint_id"]
        await capture.close()
        await console.close()

    async def test_nothing_is_spoken_means_nothing_is_written(
        self, url: str, core: Core
    ) -> None:
        # An end-of-turn with an empty transcript is not a question.
        console = await open_console(url)
        capture = await self.open_far_lane(url)
        await send_audio(capture, quiet(2000), OpusEncoder())
        assert core.state.active_hint is None
        await capture.close()
        await console.close()

    async def test_detection_is_off_without_detectors(self, url: str) -> None:
        plain = Core(
            profiles=ProfileStore(FIXTURES),
            recognizer=ScriptedRecognizer(script=list(SCRIPT)),
            generator=CannedHintGenerator(),
            token=TOKEN,
            detectors=None,
        )
        await plain.route_audio("far", tone(600))
        assert plain.state.active_hint is None


class RecordingRecognizer:
    """Remembers exactly what it was given, and nothing else."""

    language = "en"

    def __init__(self) -> None:
        self.fed: list[tuple[str, int]] = []
        self.flushed: list[str] = []

    def select_language(self, language) -> None: ...

    def select_vocabulary(self, terms) -> None: ...

    def warmup(self) -> None: ...

    def reset(self) -> None: ...

    def feed(self, lane, timestamp_us, pcm):
        self.fed.append((lane, timestamp_us))
        return []

    def flush(self, lane):
        self.flushed.append(lane)
        return []


async def until(condition, seconds: float = 3.0) -> None:
    for _ in range(int(seconds / 0.02)):
        if condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition never became true")


class TestVoiceGatesRecognition:
    """Speech models decode what they are fed, silence included.

    A recognizer that hears two lanes of nothing falls behind real time, and
    the model server queues until requests time out. Silence is also what
    these models invent words from. So the detector decides what is heard.
    """

    async def lane(self, url: str, core: Core, name: str):
        recognizer = RecordingRecognizer()
        core.recognizer = recognizer
        console = await open_console(url)
        capture = await websockets.connect(f"{url}/capture?token={TOKEN}")
        await capture.send(
            p.Hello(session_id="s", lane=name, client="test/0.1").to_json()
        )
        await capture.recv()
        return recognizer, console, capture

    async def test_silence_is_never_decoded(self, url: str, core: Core) -> None:
        recognizer, console, capture = await self.lane(url, core, "far")
        await send_audio(capture, quiet(3000), OpusEncoder())
        await asyncio.sleep(0.3)  # let the server work through what it was sent
        assert recognizer.fed == []
        await capture.close()
        await console.close()

    async def test_speech_is_decoded_with_its_lead_in_and_then_closed(
        self, url: str, core: Core
    ) -> None:
        recognizer, console, capture = await self.lane(url, core, "mic")
        encoder = OpusEncoder()
        sent = await send_audio(capture, quiet(1000), encoder)  # frames 0-49
        sent = await send_audio(capture, tone(600), encoder, sent)  # 50-79
        await send_audio(capture, quiet(1200), encoder, sent)

        await until(lambda: recognizer.flushed)  # the turn was closed on its end
        stamps = [ts for _lane, ts in recognizer.fed]
        total = 50 + 30 + 60
        assert 30 <= len(stamps) < total - 20  # the long silences were skipped
        # The lead-in is there: decoding starts a little before the tone does,
        # not at the first frame of the call and not after the tone began.
        assert 30 * 20_000 <= stamps[0] <= 50 * 20_000
        # And the clock kept counting through what was withheld.
        assert stamps == sorted(stamps)
        await capture.close()
        await console.close()

    async def test_the_profiles_vocabulary_reaches_the_recognizer(
        self, url: str, core: Core
    ) -> None:
        heard: list[list[str]] = []

        class Listening(RecordingRecognizer):
            def select_vocabulary(self, terms) -> None:
                heard.append(list(terms))

        core.recognizer = Listening()
        console = await open_console(url)  # starts the Spanish industrial profile
        assert heard
        assert {"Modbus", "PLC"} <= set(heard[0])
        await console.close()

    async def test_without_detectors_the_recognizer_hears_everything(self) -> None:
        recognizer = RecordingRecognizer()
        plain = Core(
            profiles=ProfileStore(FIXTURES),
            recognizer=recognizer,
            generator=CannedHintGenerator(),
            token=TOKEN,
            detectors=None,
        )
        for _ in range(10):
            await plain.route_audio("far", quiet(20))
        assert [ts for _lane, ts in recognizer.fed] == [i * 20_000 for i in range(10)]
