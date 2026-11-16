"""Keeping the far lane's voice out of the mic lane.

With loudspeakers, the microphone hears the person on the call a little
behind their own lane, and the recognizer then writes their words down as the
user's. Echo cancellation would remove it properly; this is the cheap rule: the
far lane is a clean copy of what the speakers are playing, so while it is
loud, a mic frame that is not clearly louder than it is the loudspeaker, not
the user, and is replaced by silence before anything listens to it.

Relative, not absolute: bleed and the far lane scale together with the
speaker volume, so a ratio survives a volume change that a fixed level would
not. It does not survive moving the microphone next to the speakers, and the
user talking over the far voice quietly will be lost. That is the trade, which
is why the ratio is configurable and ``0`` switches the rule off.

When the far lane is quiet the gate does nothing at all.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

#: 20 ms frames. The far voice reaches the mic late (air, buffers), so its level
#: is held for 0.6 s; the mic level is held for 160 ms so the dips between the
#: user's own syllables do not look like bleed.
FAR_HOLD_FRAMES = 30
MIC_HOLD_FRAMES = 8


def rms(pcm: bytes) -> float:
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    return float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0


@dataclass
class BleedGate:
    #: A mic frame is bleed when its level is below ``ratio`` times the far
    #: lane's recent level. 0 disables the gate.
    ratio: float = 2.0
    #: RMS under which the far lane counts as silent and the gate stands down.
    #: Far speech measured 400-950 on two recorded calls; its idle noise 50-125.
    far_floor: float = 250.0

    _far: deque[float] = field(default_factory=lambda: deque(maxlen=FAR_HOLD_FRAMES), init=False)
    _mic: deque[float] = field(default_factory=lambda: deque(maxlen=MIC_HOLD_FRAMES), init=False)
    suppressed: int = field(default=0, init=False)

    def reset(self) -> None:
        self._far.clear()
        self._mic.clear()
        self.suppressed = 0

    def far(self, pcm: bytes) -> None:
        self._far.append(rms(pcm))

    def mic(self, pcm: bytes) -> bytes:
        """The frame to hand on: itself, or silence if it is the loudspeaker."""
        self._mic.append(rms(pcm))
        if self.ratio <= 0 or not self._far:
            return pcm
        far_level = max(self._far)
        if far_level >= self.far_floor and max(self._mic) < self.ratio * far_level:
            self.suppressed += 1
            return bytes(len(pcm))
        return pcm
