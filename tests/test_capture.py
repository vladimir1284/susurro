"""Capture client tests.

The file replay backend is testable here. The PipeWire backend is not: this
machine has no /dev/snd and no PipeWire daemon, which is exactly why M3 in the
roadmap exists. Nothing in this file pretends otherwise.
"""

from __future__ import annotations

import json
import math
import struct
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import websockets
from websockets.asyncio.server import ServerConnection, serve

from capture.backends.base import SourceError
from capture.backends.file import WavSource, write_wav
from capture.client import CaptureError, stream_lane, stream_session
from capture.codec import FRAME_BYTES, FRAME_SAMPLES, CodecError, OpusEncoder
from core.audio import LaneDecoder
from core import protocol as p
from core.llm.fake import CannedHintGenerator
from core.server import Core
from core.session.profiles import ProfileStore
from core.stt.fake import ScriptedRecognizer, ScriptedUtterance

FIXTURES = Path(__file__).parent / "fixtures" / "profiles"
TOKEN = "test-token"


def tone(frames: int, *, hz: int = 440, channels: int = 1) -> bytes:
    """A sine wave, so the encoder is given something compressible and real."""
    samples = []
    for n in range(frames * FRAME_SAMPLES):
        value = int(12000 * math.sin(2 * math.pi * hz * n / 16000))
        samples.extend([value] * channels)
    return struct.pack(f"<{len(samples)}h", *samples)


@pytest.fixture
def wav(tmp_path: Path) -> Path:
    return write_wav(tmp_path / "speech.wav", tone(10))


class TestCodec:
    def test_round_trip_preserves_frame_length(self) -> None:
        encoder, decoder = OpusEncoder(), LaneDecoder()
        packet = encoder.encode(tone(1))
        assert 0 < len(packet) < p.MAX_PAYLOAD_BYTES
        assert len(decoder.decode("mic", packet)) == FRAME_BYTES

    def test_encoded_frames_fit_the_protocol_payload_cap(self) -> None:
        encoder = OpusEncoder()
        pcm = tone(50)
        for i in range(50):
            packet = encoder.encode(pcm[i * FRAME_BYTES : (i + 1) * FRAME_BYTES])
            # Must survive AudioFrame's validation, which is where an
            # overlong packet would be caught at runtime.
            p.AudioFrame(timestamp_us=i, payload=packet)

    @pytest.mark.parametrize("size", [0, FRAME_BYTES - 2, FRAME_BYTES + 2])
    def test_rejects_a_wrong_sized_frame(self, size: int) -> None:
        with pytest.raises(CodecError, match="expected"):
            OpusEncoder().encode(b"\x00" * size)

    def test_lanes_need_separate_encoders(self) -> None:
        # Opus is stateful. Sharing one encoder between lanes would interleave
        # two signals into one predictor and corrupt both.
        mic, far = OpusEncoder(), OpusEncoder()
        assert mic._encoder is not far._encoder


class TestWavSource:
    def test_yields_whole_frames(self, wav: Path) -> None:
        frames = list(WavSource(wav, speed=0).frames())
        assert len(frames) == 10
        assert {len(frame) for frame in frames} == {FRAME_BYTES}

    def test_drops_a_trailing_partial_frame(self, tmp_path: Path) -> None:
        # Zero-padding would put 20 ms of invented silence into a transcript.
        path = write_wav(tmp_path / "ragged.wav", tone(3) + b"\x00" * 100)
        assert len(list(WavSource(path, speed=0).frames())) == 3

    def test_downmixes_stereo(self, tmp_path: Path) -> None:
        path = write_wav(tmp_path / "stereo.wav", tone(4, channels=2), channels=2)
        frames = list(WavSource(path, speed=0).frames())
        assert len(frames) == 4
        assert {len(frame) for frame in frames} == {FRAME_BYTES}

    def test_refuses_a_wrong_sample_rate_with_a_usable_message(
        self, tmp_path: Path
    ) -> None:
        path = write_wav(tmp_path / "44k.wav", tone(2), sample_rate=44100)
        with pytest.raises(SourceError, match="ffmpeg"):
            WavSource(path)

    def test_refuses_a_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(SourceError, match="no such file"):
            WavSource(tmp_path / "absent.wav")

    def test_refuses_a_non_wav_file(self, tmp_path: Path) -> None:
        path = tmp_path / "notes.txt"
        path.write_text("this is not audio")
        with pytest.raises(SourceError, match="not a readable WAV"):
            WavSource(path)

    def test_real_time_pacing_takes_real_time(self, wav: Path) -> None:
        # Ten 20 ms frames is 200 ms of audio. Replayed at 4x it should take
        # around 50 ms: enough to prove pacing happens without making the test
        # slow or flaky on a loaded machine.
        start = time.monotonic()
        list(WavSource(wav, speed=4.0).frames())
        assert 0.02 < time.monotonic() - start < 0.5

    def test_unpaced_replay_does_not_sleep(self, wav: Path) -> None:
        start = time.monotonic()
        list(WavSource(wav, speed=0).frames())
        assert time.monotonic() - start < 0.05

    def test_looping_keeps_producing(self, wav: Path) -> None:
        source = WavSource(wav, speed=0, loop=True)
        frames = []
        for frame in source.frames():
            frames.append(frame)
            if len(frames) == 25:
                source.close()
                break
        assert len(frames) == 25


@pytest.fixture
async def core() -> Core:
    return Core(
        profiles=ProfileStore(FIXTURES),
        recognizer=ScriptedRecognizer(
            script=[ScriptedUtterance(lane="far", text="Tell me about it.", frames=4)],
            partial_every=2,
        ),
        generator=CannedHintGenerator(),
        token=TOKEN,
    )


@pytest.fixture
async def url(core: Core) -> AsyncIterator[str]:
    from urllib.parse import urlparse

    async def router(connection: ServerConnection) -> None:
        route = urlparse(connection.request.path).path
        if route == "/capture":
            await core.handle_capture(connection)
        elif route == "/console":
            await core.handle_console(connection)
        else:
            await connection.close(1003, "unknown endpoint")

    async with serve(router, "127.0.0.1", 0) as server:
        yield f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"


class TestClient:
    async def test_a_lane_streams_every_frame(
        self, url: str, core: Core, wav: Path
    ) -> None:
        sent = await stream_lane(
            url, TOKEN, "s-1", "far", WavSource(wav, speed=0)
        )
        assert sent == 10

    async def test_a_bad_token_is_reported_not_swallowed(
        self, url: str, wav: Path
    ) -> None:
        with pytest.raises(websockets.exceptions.ConnectionClosed):
            await stream_lane(url, "wrong", "s-1", "far", WavSource(wav, speed=0))

    async def test_a_refused_lane_raises(self, url: str, wav: Path) -> None:
        async with websockets.connect(f"{url}/capture?token={TOKEN}") as holder:
            await holder.send(
                p.Hello(session_id="s", lane="mic", client="other/0.1").to_json()
            )
            await holder.recv()
            with pytest.raises(CaptureError, match="refused lane"):
                await stream_lane(url, TOKEN, "s-1", "mic", WavSource(wav, speed=0))

    async def test_both_lanes_run_concurrently(
        self, url: str, core: Core, wav: Path
    ) -> None:
        sent = await stream_session(
            url,
            TOKEN,
            "s-1",
            {
                "mic": WavSource(wav, speed=0),
                "far": WavSource(wav, speed=0),
            },
        )
        assert sent == {"mic": 10, "far": 10}

    async def test_audio_from_a_file_becomes_a_transcript(
        self, url: str, core: Core, wav: Path
    ) -> None:
        # The full path end to end: WAV -> Opus -> WebSocket -> core ->
        # recognizer -> console. Every piece is real except the recognizer,
        # which is scripted because this machine cannot host a model.
        console = await websockets.connect(f"{url}/console?token={TOKEN}")
        await console.recv()
        await console.send(
            json.dumps(
                {
                    "type": "session.config",
                    "profile_id": "en-technical-backend",
                    "language": "en",
                    "mode": "technical",
                }
            )
        )
        await console.recv()
        await console.send(json.dumps({"type": "session.start"}))
        await console.recv()

        await stream_lane(url, TOKEN, "s-1", "far", WavSource(wav, speed=0))

        finals = []
        for _ in range(30):
            event = json.loads(await console.recv())
            if event["type"] == "transcript.final":
                finals.append(event)
                break
        await console.close()

        assert finals and finals[0]["text"] == "Tell me about it."
        assert core.transcript == ["them: Tell me about it."]


class TestCli:
    def test_refuses_to_run_with_nothing_to_capture(self) -> None:
        from capture.main import build_parser, build_sources

        args = build_parser().parse_args([])
        with pytest.raises(SourceError, match="nothing to capture"):
            build_sources(args)

    def test_replay_flags_select_the_file_backend(self, wav: Path) -> None:
        from capture.main import build_parser, build_sources

        args = build_parser().parse_args(
            ["--replay-mic", str(wav), "--replay-far", str(wav), "--speed", "0"]
        )
        sources = build_sources(args)
        assert set(sources) == {"mic", "far"}
        assert all(isinstance(s, WavSource) for s in sources.values())
