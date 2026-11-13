"""PipeWire capture for Linux.

**Untested.** It is written against the documented behaviour of ``pw-cat`` and
has never been run: the machine this was developed on has no ``/dev/snd`` and no
PipeWire daemon. Treat it as a first draft until it has run on real hardware —
that is milestone M3 in the roadmap, which exists precisely to find out what
this gets wrong.

The approach is to shell out to ``pw-cat`` rather than bind libpipewire. The
capture client's whole job is to read audio and send it, and ``pw-cat`` already
knows how to ask PipeWire for a specific node at a specific rate. Asking
PipeWire to resample is also the right call on quality grounds: its resampler is
better than anything worth writing here.

Two lanes come from two different kinds of node:

``mic``
    An ordinary input source.
``far``
    The *monitor* of the default sink — what the speakers are playing. This is
    the stream that carries everyone else's voice, and the reason speaker
    attribution needs no model.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator

from capture.backends.base import SourceError
from capture.codec import FRAME_BYTES
from core.protocol import SAMPLE_RATE


class PipeWireSource:
    def __init__(self, target: str, *, label: str = ""):
        if shutil.which("pw-cat") is None:
            raise SourceError(
                "pw-cat not found. Install pipewire-utils (Debian/Ubuntu: "
                "`apt install pipewire-bin`)."
            )
        self.target = target
        self.label = label or target
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def description(self) -> str:
        return f"pipewire:{self.label}"

    def frames(self) -> Iterator[bytes]:
        self._process = subprocess.Popen(
            [
                "pw-cat",
                "--record",
                "-",
                "--target", self.target,
                "--rate", str(SAMPLE_RATE),
                "--channels", "1",
                "--format", "s16",
                "--raw",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout = self._process.stdout
        assert stdout is not None
        while True:
            frame = stdout.read(FRAME_BYTES)
            if len(frame) < FRAME_BYTES:
                break
            yield frame
        self._raise_if_failed()

    def _raise_if_failed(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() not in (0, None):
            stderr = (process.stderr.read() if process.stderr else b"").decode(
                errors="replace"
            )
            raise SourceError(f"pw-cat exited {process.returncode}: {stderr.strip()}")

    def close(self) -> None:
        if self._process and self._process.poll() is None:
            self._process.terminate()


def list_devices() -> str:
    """Print what PipeWire offers, so `--mic` and `--far` can be filled in."""
    if shutil.which("pw-cli") is None:
        raise SourceError("pw-cli not found. Install pipewire-utils.")
    result = subprocess.run(
        ["pw-cli", "list-objects", "Node"], capture_output=True, text=True
    )
    return result.stdout
