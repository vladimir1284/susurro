"""The PulseAudio backend, against a stand-in ``parec``.

No audio server is needed: ``parec`` is replaced by a script on PATH that emits
chosen bytes, goes quiet, or fails. What is under test is the framing, the
silence padding and the error path, not PulseAudio.
"""

from __future__ import annotations

import stat
import time
from pathlib import Path

import pytest

from capture.backends import pulse
from capture.backends.base import SourceError
from capture.backends.pulse import PulseSource
from capture.codec import FRAME_BYTES
from capture.main import resolve_backend


def fake_parec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    script = tmp_path / "parec"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")


def test_whole_frames_come_out_of_whatever_chunking_parec_uses(tmp_path, monkeypatch) -> None:
    # 2.5 frames, written in awkward pieces: two whole frames, the half is held.
    payload = bytes(range(256)) * ((FRAME_BYTES * 5 // 2) // 256 + 1)
    payload = payload[: FRAME_BYTES * 5 // 2]
    (tmp_path / "data").write_bytes(payload)
    fake_parec(tmp_path, monkeypatch, f"cat {tmp_path / 'data'}\n")

    frames = list(PulseSource("any").frames())

    assert frames == [payload[:FRAME_BYTES], payload[FRAME_BYTES : 2 * FRAME_BYTES]]


def test_a_quiet_stream_is_padded_with_silence_at_clock_pace(tmp_path, monkeypatch) -> None:
    # The monitor of an idle sink says nothing at all. The lane must still
    # produce frames, or the detector never sees the pause that ends a turn.
    fake_parec(tmp_path, monkeypatch, "sleep 5\n")
    source = PulseSource("any")
    started = time.monotonic()
    frames = []
    for frame in source.frames():
        frames.append(frame)
        if time.monotonic() - started >= 0.5:
            break
    source.close()

    assert all(frame == bytes(FRAME_BYTES) for frame in frames)
    # 0.5 s is 25 frames; allow for scheduling, but it must not trickle.
    assert 20 <= len(frames) <= 27


def test_a_failing_parec_raises_with_its_own_message(tmp_path, monkeypatch) -> None:
    fake_parec(tmp_path, monkeypatch, "echo 'Stream error: No such entity' >&2\nexit 1\n")

    with pytest.raises(SourceError, match="No such entity"):
        list(PulseSource("nope").frames())


def test_a_missing_parec_says_what_to_install(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(SourceError, match="pulseaudio-utils"):
        PulseSource("any")


def test_auto_prefers_pulse_when_a_server_answers(monkeypatch) -> None:
    monkeypatch.setattr(pulse, "available", lambda: True)
    assert resolve_backend("auto") == "pulse"


def test_auto_falls_back_to_pipewire_when_nothing_answers(monkeypatch) -> None:
    monkeypatch.setattr(pulse, "available", lambda: False)
    assert resolve_backend("auto") == "pipewire"


def test_an_explicit_choice_is_never_second_guessed(monkeypatch) -> None:
    monkeypatch.setattr(pulse, "available", lambda: True)
    assert resolve_backend("pipewire") == "pipewire"
