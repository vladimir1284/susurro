"""PulseAudio capture for Linux, through ``parec``.

Works on PulseAudio and on PipeWire's PulseAudio compatibility layer, so it
covers the audio servers a desktop Linux actually runs. It exists beside the
PipeWire backend because a machine can have the PipeWire daemon installed and
idle while PulseAudio owns every device -- which is the machine this was written
for.

As with the PipeWire backend, the conversion to 16 kHz mono is the audio
server's job (``parec`` asks for it), not something done in Python.

Two lanes, two kinds of source name:

``mic``
    An input source, e.g. ``alsa_input.usb-...``.
``far``
    The *monitor* of the sink the meeting plays through, e.g.
    ``alsa_output.usb-....monitor``. ``@DEFAULT_MONITOR@`` means the monitor of
    whatever the default sink is.

A monitor of an idle sink produces **no data at all**: the server suspends the
sink and the stream goes quiet until something plays. Voice activity detection
needs to see silence to end a turn, so a lane that goes quiet is padded with
silence at wall-clock pace rather than left to stall.
"""

from __future__ import annotations

import os
import select
import shutil
import subprocess
import time
from collections.abc import Iterator

from capture.backends.base import SourceError
from capture.codec import FRAME_BYTES
from core.protocol import FRAME_MS, SAMPLE_RATE

FRAME_S = FRAME_MS / 1000

#: How long a stream may say nothing before silence is substituted. Longer than
#: a frame so ordinary scheduling jitter never injects a gap into live audio.
STALL_S = 3 * FRAME_S

SILENCE = bytes(FRAME_BYTES)


class PulseSource:
    def __init__(self, source: str, *, label: str = ""):
        if shutil.which("parec") is None:
            raise SourceError(
                "parec not found. Install pulseaudio-utils "
                "(Debian/Ubuntu: `apt install pulseaudio-utils`)."
            )
        self.source = source
        self.label = label or source
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def description(self) -> str:
        return f"pulse:{self.label}"

    def frames(self) -> Iterator[bytes]:
        self._process = subprocess.Popen(
            [
                "parec",
                f"--device={self.source}",
                "--format=s16le",
                f"--rate={SAMPLE_RATE}",
                "--channels=1",
                "--raw",
                f"--latency-msec={FRAME_MS}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout = self._process.stdout
        assert stdout is not None
        fd = stdout.fileno()
        pending = bytearray()
        #: Wall-clock time the frames yielded so far account for. Real audio
        #: advances it by what it carries; a stall is filled with silence up to
        #: the present, so the lane's timeline tracks the clock either way.
        covered = time.monotonic()
        while True:
            ready, _, _ = select.select([fd], [], [], STALL_S)
            if ready:
                chunk = os.read(fd, FRAME_BYTES * 8)
                if not chunk:
                    break
                pending.extend(chunk)
                while len(pending) >= FRAME_BYTES:
                    yield bytes(pending[:FRAME_BYTES])
                    del pending[:FRAME_BYTES]
                    covered += FRAME_S
            elif self._process.poll() is not None:
                break
            else:
                now = time.monotonic()
                while covered + FRAME_S <= now:
                    yield SILENCE
                    covered += FRAME_S
        self._raise_if_failed()

    def _raise_if_failed(self) -> None:
        process = self._process
        if process is None:
            return
        process.wait(timeout=2)
        if process.returncode not in (0, None):
            stderr = (process.stderr.read() if process.stderr else b"").decode(
                errors="replace"
            )
            raise SourceError(f"parec exited {process.returncode}: {stderr.strip()}")

    def close(self) -> None:
        if self._process and self._process.poll() is None:
            self._process.terminate()


def available() -> bool:
    """Whether a PulseAudio-compatible server answers.

    Asked of the server rather than inferred from installed packages: the
    PipeWire daemon can be installed, running and empty, and ``pactl`` is what
    tells that apart from one that holds the devices.
    """
    if shutil.which("pactl") is None or shutil.which("parec") is None:
        return False
    try:
        result = subprocess.run(
            ["pactl", "info"], capture_output=True, timeout=3, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def list_devices() -> str:
    """Print what the server offers, so `--mic` and `--far` can be filled in."""
    if shutil.which("pactl") is None:
        raise SourceError(
            "pactl not found. Install pulseaudio-utils (Debian/Ubuntu: "
            "`apt install pulseaudio-utils`)."
        )
    result = subprocess.run(
        ["pactl", "list", "short", "sources"], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise SourceError(f"pactl failed: {result.stderr.strip()}")
    lines = ["Sources (pass one as --mic or --far; monitors suit the far lane):"]
    lines += [f"  {line.split(chr(9))[1]}" for line in result.stdout.splitlines() if "\t" in line]
    lines.append("  @DEFAULT_MONITOR@   (monitor of the default sink)")
    return "\n".join(lines)
