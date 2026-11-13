"""Measurement helpers.

Deliberately small and dependency-light. A benchmark harness that is itself
complicated produces results nobody trusts, and the first question anyone asks
of a surprising number is whether the harness is wrong.
"""

from __future__ import annotations

import math
import platform
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Samples:
    """A series of measurements, summarised the way latency should be.

    The mean is reported but never used for a gate. A hint that is usually fast
    and occasionally four seconds late is a bad hint generator, and a mean hides
    exactly that.
    """

    name: str
    unit: str
    values: list[float] = field(default_factory=list)

    def add(self, value: float) -> None:
        self.values.append(value)

    def summary(self) -> dict[str, Any]:
        if not self.values:
            return {"name": self.name, "unit": self.unit, "n": 0}
        ordered = sorted(self.values)
        return {
            "name": self.name,
            "unit": self.unit,
            "n": len(ordered),
            "min": round(ordered[0], 4),
            "median": round(statistics.median(ordered), 4),
            "p90": round(percentile(ordered, 0.90), 4),
            "max": round(ordered[-1], 4),
            "mean": round(statistics.fmean(ordered), 4),
        }


def percentile(ordered: list[float], fraction: float) -> float:
    """Nearest-rank percentile over an already sorted list.

    Nearest-rank rather than interpolated: these runs have few samples, and an
    interpolated p90 over eight numbers invents precision that is not there.
    """
    if not ordered:
        raise ValueError("no samples")
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


class Stopwatch:
    """Monotonic timing. Wall clock can step; a benchmark must not."""

    def __init__(self) -> None:
        self.started = time.perf_counter()

    def elapsed(self) -> float:
        return time.perf_counter() - self.started

    def reset(self) -> float:
        elapsed = self.elapsed()
        self.started = time.perf_counter()
        return elapsed


def machine() -> dict[str, Any]:
    """Fingerprint the machine, so a result can never be read as another's.

    Every number in this project's documentation is only meaningful next to the
    hardware that produced it. Recording that in the result file is what stops
    a measurement from a laptop being quoted as a measurement from the target.
    """
    info: dict[str, Any] = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "python": sys.version.split()[0],
        "cpu_count": _cpu_count(),
        "total_memory_gb": _total_memory_gb(),
        "accelerator": detect_accelerator(),
    }
    return info


def _cpu_count() -> int:
    import os

    return os.cpu_count() or 0


def _total_memory_gb() -> float | None:
    try:
        import os

        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return round(pages * page_size / 1024**3, 1)
    except (ValueError, OSError, AttributeError):
        return None


def detect_accelerator() -> str:
    """Best-effort, and honest about being a guess.

    Used only to warn when a run is happening somewhere the numbers will not
    transfer from. It never changes what is measured.
    """
    if sys.platform == "darwin" and platform.machine() == "arm64":
        return "apple-silicon"
    import shutil

    if shutil.which("nvidia-smi"):
        return "nvidia"
    return "none"


def peak_rss_gb(pid: int | None = None) -> float | None:
    """Peak resident memory of a process, in GB, or None if unavailable.

    Reads /proc on Linux and falls back to resource.getrusage for this process.
    The model usually runs in a *different* process -- llama.cpp's server, for
    instance -- so this is only meaningful with the right pid.
    """
    if pid is not None:
        try:
            with open(f"/proc/{pid}/status", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("VmHWM:"):
                        return round(int(line.split()[1]) / 1024**2, 2)
        except (FileNotFoundError, PermissionError, ValueError, IndexError):
            return None
        return None
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports kilobytes, macOS bytes.
        divisor = 1024**2 if sys.platform != "darwin" else 1024**3
        return round(peak / divisor, 2)
    except (ImportError, AttributeError):
        return None
