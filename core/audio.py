"""Opus decoding on the core side.

Decoding happens here, once per lane, rather than inside each recognizer.
Every recognizer wants 16-bit mono PCM and none wants an Opus packet, so
putting the decoder in the server means a new speech backend never has to think
about the wire format.

One decoder per lane, never shared: Opus carries predictor state between
packets, and feeding two independent streams through one decoder corrupts both.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import opuslib

from core.protocol import FRAME_MS, SAMPLE_RATE, Lane

CHANNELS = 1
BYTES_PER_SAMPLE = 2
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * BYTES_PER_SAMPLE


class DecodeError(Exception):
    code = "decode_failed"


@dataclass
class LaneDecoder:
    """Holds one Opus decoder per lane for the life of a session."""

    _decoders: dict[str, opuslib.Decoder] = field(default_factory=dict, init=False)

    def decode(self, lane: Lane, packet: bytes) -> bytes:
        decoder = self._decoders.get(lane)
        if decoder is None:
            decoder = opuslib.Decoder(SAMPLE_RATE, CHANNELS)
            self._decoders[lane] = decoder
        try:
            return decoder.decode(packet, FRAME_SAMPLES)
        except Exception as exc:  # opuslib raises its own error types
            raise DecodeError(f"lane {lane!r}: {exc}") from exc

    def reset(self) -> None:
        """Drop decoder state, e.g. between sessions."""
        self._decoders.clear()
