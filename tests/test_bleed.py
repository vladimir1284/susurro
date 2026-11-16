"""The loudspeaker rule: the far voice must not become the user's words.

Frames are 20 ms of a steady tone, so levels are exact: a tone of amplitude A
has an RMS of A / sqrt(2).
"""

from __future__ import annotations

import numpy as np

from core.bleed import BleedGate, rms
from core.llm.fake import CannedHintGenerator
from core.main import build_bleed, build_detectors
from core.server import Core
from core.session.profiles import ProfileStore
from tests.test_vad import FIXTURES, TOKEN, RecordingRecognizer, quiet, tone


def frame(amplitude: int) -> bytes:
    return tone(20, amplitude=amplitude)


def feed_far(gate: BleedGate, amplitude: int, frames: int = 40) -> None:
    for _ in range(frames):
        gate.far(frame(amplitude))


class TestBleedGate:
    def test_a_mic_frame_quieter_than_the_far_voice_is_silenced(self) -> None:
        gate = BleedGate(ratio=2.0)
        feed_far(gate, 6000)
        out = gate.mic(frame(3000))
        assert out == bytes(len(out))
        assert gate.suppressed == 1

    def test_a_mic_frame_clearly_louder_than_it_goes_through(self) -> None:
        gate = BleedGate(ratio=2.0)
        feed_far(gate, 3000)
        loud = frame(12000)
        assert gate.mic(loud) == loud

    def test_with_the_far_lane_silent_nothing_is_touched(self) -> None:
        gate = BleedGate(ratio=2.0)
        feed_far(gate, 0)
        whisper = frame(200)
        assert gate.mic(whisper) == whisper

    def test_a_far_lane_that_has_just_stopped_still_counts_for_a_moment(self) -> None:
        # The voice reaches the microphone late, so its tail is still bleed.
        gate = BleedGate(ratio=2.0)
        feed_far(gate, 6000, frames=10)
        for _ in range(5):
            gate.far(quiet(20))
        assert gate.mic(frame(3000)) == bytes(640)

    def test_the_hold_expires(self) -> None:
        gate = BleedGate(ratio=2.0)
        feed_far(gate, 6000, frames=10)
        feed_far(gate, 0, frames=60)
        quiet_voice = frame(300)
        assert gate.mic(quiet_voice) == quiet_voice

    def test_a_dip_between_syllables_does_not_open_a_hole(self) -> None:
        gate = BleedGate(ratio=2.0)
        feed_far(gate, 2000)
        for _ in range(4):
            gate.mic(frame(12000))
        # One quiet frame in the middle of the user's own speech.
        dip = frame(500)
        assert gate.mic(dip) == dip

    def test_ratio_zero_switches_it_off(self) -> None:
        gate = BleedGate(ratio=0)
        feed_far(gate, 6000)
        quiet_frame = frame(1000)
        assert gate.mic(quiet_frame) == quiet_frame

    def test_reset_forgets_the_far_voice(self) -> None:
        gate = BleedGate(ratio=2.0)
        feed_far(gate, 6000)
        gate.reset()
        quiet_frame = frame(1000)
        assert gate.mic(quiet_frame) == quiet_frame
        assert gate.suppressed == 0

    def test_rms_of_a_tone(self) -> None:
        assert abs(rms(frame(10000)) - 10000 / np.sqrt(2)) < 50
        assert rms(b"") == 0.0


class TestBuildBleed:
    def test_on_by_default(self) -> None:
        assert build_bleed({}) is not None

    def test_zero_turns_it_off(self) -> None:
        assert build_bleed({"bleed_ratio": 0}) is None

    def test_the_ratio_is_configurable(self) -> None:
        assert build_bleed({"bleed_ratio": 3.5}).ratio == 3.5  # type: ignore[union-attr]


class TestInTheCore:
    """What the recognizer hears, end to end through ``route_audio``."""

    def core(self, bleed: BleedGate | None) -> tuple[Core, RecordingRecognizer]:
        recognizer = RecordingRecognizer()
        core = Core(
            profiles=ProfileStore(FIXTURES),
            recognizer=recognizer,
            generator=CannedHintGenerator(),
            token=TOKEN,
            detectors=build_detectors({"backend": "energy"}),
            bleed=bleed,
        )
        return core, recognizer

    async def play(self, core: Core, mic_amplitude: int) -> None:
        for _ in range(60):
            await core.route_audio("far", frame(8000))
            await core.route_audio("mic", frame(mic_amplitude))
        for _ in range(60):
            await core.route_audio("far", quiet(20))
            await core.route_audio("mic", quiet(20))

    async def test_without_the_rule_the_loudspeaker_is_transcribed_as_the_user(self) -> None:
        core, recognizer = self.core(bleed=None)
        await self.play(core, mic_amplitude=2000)
        assert any(lane == "mic" for lane, _ in recognizer.fed)

    async def test_with_the_rule_the_mic_lane_hears_nothing(self) -> None:
        core, recognizer = self.core(bleed=BleedGate(ratio=2.0))
        await self.play(core, mic_amplitude=2000)
        assert not any(lane == "mic" for lane, _ in recognizer.fed)
        assert any(lane == "far" for lane, _ in recognizer.fed)

    async def test_the_user_talking_over_the_far_voice_is_still_heard(self) -> None:
        core, recognizer = self.core(bleed=BleedGate(ratio=2.0))
        await self.play(core, mic_amplitude=30000)
        assert any(lane == "mic" for lane, _ in recognizer.fed)

    async def test_a_new_session_starts_without_the_last_ones_far_level(self) -> None:
        gate = BleedGate(ratio=2.0)
        feed_far(gate, 8000)
        core, _ = self.core(bleed=gate)
        core.state.configure("es-business-industrial", "es", "business")
        await core.start_session()
        quiet_voice = frame(500)
        assert gate.mic(quiet_voice) == quiet_voice
