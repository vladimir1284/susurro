"""Opus encoding for one lane.

The capture client is deliberately the dumbest process in the system: it reads
audio, encodes it, and sends it. No voice activity detection, no buffering
policy, no model. That is what keeps a new client platform a few hundred lines
of work instead of a port of the whole pipeline.

Format is fixed rather than negotiated, matching ``core.protocol``: 16 kHz mono,
20 ms frames. 16 kHz is what the speech models consume; sending more costs
bandwidth and gets thrown away. 20 ms is what the voice activity detector steps
on.
"""

from __future__ import annotations

from dataclasses import dataclass

import opuslib

from core.protocol import FRAME_MS, SAMPLE_RATE

CHANNELS = 1
BYTES_PER_SAMPLE = 2  # signed 16-bit PCM

#: Samples in one 20 ms frame at 16 kHz.
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
#: Bytes of PCM in one frame.
FRAME_BYTES = FRAME_SAMPLES * BYTES_PER_SAMPLE * CHANNELS

#: Speech at 16 kHz mono. Opus is well under this; the cap in the protocol is
#: what catches a stream that has gone wrong.
DEFAULT_BITRATE = 24_000


class CodecError(Exception):
    pass


@dataclass
class OpusEncoder:
    """One encoder per lane. Opus is stateful, so lanes must not share one."""

    bitrate: int = DEFAULT_BITRATE

    def __post_init__(self) -> None:
        self._encoder = opuslib.Encoder(
            SAMPLE_RATE, CHANNELS, opuslib.APPLICATION_VOIP
        )
        self._encoder.bitrate = self.bitrate

    def encode(self, pcm: bytes) -> bytes:
        if len(pcm) != FRAME_BYTES:
            raise CodecError(
                f"expected {FRAME_BYTES} bytes of PCM for a {FRAME_MS} ms frame, "
                f"got {len(pcm)}"
            )
        return self._encoder.encode(pcm, FRAME_SAMPLES)


@dataclass
class OpusDecoder:
    """Only the tests and the core need this; the capture client never decodes."""

    def __post_init__(self) -> None:
        self._decoder = opuslib.Decoder(SAMPLE_RATE, CHANNELS)

    def decode(self, packet: bytes) -> bytes:
        return self._decoder.decode(packet, FRAME_SAMPLES)
