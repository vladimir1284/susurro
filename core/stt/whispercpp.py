"""Speech recognition against a whisper.cpp server.

Same windowing as ``WhisperRecognizer`` -- overlapping re-decodes, a partial per
interval, a final when the utterance closes -- with the model behind HTTP instead
of in-process. The only thing that changes is ``_transcribe``.

Why it exists: ``faster-whisper`` downloads CTranslate2 weights from Hugging Face
on first use. A machine that already has ``whisper.cpp`` and a ``ggml`` model, and
a network that will not fetch another, can serve that model with
``whisper-server`` and have the core talk to it. It also lets the core run on a
different machine from the model.

``feed`` is called off the event loop, so a blocking client is the right one.
"""

from __future__ import annotations

import io
import logging
import re
import wave
from dataclasses import dataclass, field

import httpx

from core.protocol import SAMPLE_RATE, Lane
from core.stt.whisper import WhisperRecognizer

log = logging.getLogger("susurro.stt")

#: whisper.cpp reports silence and noise as text -- ``[BLANK_AUDIO]``,
#: ``(music)``, ``(speaking in foreign language)`` -- where faster-whisper
#: returns nothing. Left in, they would reach the language model as if someone
#: had said them.
_ANNOTATION = re.compile(r"^\s*(\[[^\]]*\]|\([^)]*\)|\*[^*]*\*)\s*$")


@dataclass
class WhisperCppRecognizer(WhisperRecognizer):
    """whisper.cpp's ``whisper-server`` behind the ``Recognizer`` interface."""

    base_url: str = "http://127.0.0.1:8081"
    #: Seconds before a decode request is abandoned. A partial that takes longer
    #: than the window it covers means the model is slower than real time, and
    #: that should surface as an error rather than as an ever-growing backlog.
    timeout_s: float = 30.0

    _client: httpx.Client | None = field(default=None, init=False)

    @property
    def model_name(self) -> str:
        return f"whisper.cpp @ {self.base_url}"

    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=self.base_url, timeout=self.timeout_s)
        return self._client

    def warmup(self) -> None:
        """Fail at session start if the server is not there.

        ``whisper-server`` answers any GET with its demo page, so reaching it is
        the check; there is no model to load on this side.
        """
        self.client().get("/").raise_for_status()
        log.info("whisper.cpp server reachable at %s", self.base_url)

    def _wav(self, lane: Lane) -> bytes:
        out = io.BytesIO()
        with wave.open(out, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(bytes(self._lanes[lane].pcm))
        return out.getvalue()

    def _transcribe(self, lane: Lane) -> tuple[str, bool]:
        response = self.client().post(
            "/inference",
            files={"file": ("window.wav", self._wav(lane), "audio/wav")},
            data={
                "response_format": "verbose_json",
                "language": self.language,
                "temperature": "0.0",
            },
        )
        response.raise_for_status()
        segments = [
            segment
            for segment in response.json().get("segments", [])
            if not _ANNOTATION.match(segment["text"])
        ]
        text = " ".join(segment["text"].strip() for segment in segments).strip()
        window_s = len(self._lanes[lane].pcm) / 2 / SAMPLE_RATE
        ended = bool(segments) and (window_s - segments[-1]["end"]) >= self.silence_s
        return text, ended
