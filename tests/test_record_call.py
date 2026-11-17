"""The take checker and the presets of bench.record_call (no audio devices needed)."""

import json
import re
from pathlib import Path

import numpy as np
import pytest

from bench import record_call as rc

DOCS = Path(__file__).parent.parent / "docs" / "development"


def tone(seconds: float, amp: float = 0.2, *, start: float = 0.0, total: float | None = None):
    total = total or seconds + start
    x = np.zeros(int(rc.RATE * total))
    t = np.arange(int(rc.RATE * seconds)) / rc.RATE
    i = int(rc.RATE * start)
    x[i : i + len(t)] = amp * np.sin(2 * np.pi * 220 * t) * (1 + np.sin(2 * np.pi * 3 * t)) / 2
    return x


def alternating(total: float, setup: str):
    """Far talks 0-10, mic 10-20 ...; mic hears far only in the loudspeaker case."""
    far, mic = np.zeros(int(rc.RATE * total)), np.zeros(int(rc.RATE * total))
    for k in range(int(total // 10)):
        seg = tone(9, start=0, total=9)
        s = slice(int(rc.RATE * k * 10), int(rc.RATE * (k * 10 + 9)))
        if k % 2 == 0:
            far[s] = seg
            if setup == "loudspeakers":
                mic[s] = seg * 0.3
        else:
            mic[s] = seg
    return mic, far


def levels(findings, level):
    return [f.text for f in findings if f.level == level]


def test_good_headphone_take_is_kept():
    mic, far = alternating(300, "headphones")
    f = rc.analyze(mic, far, setup="headphones", minutes=5, scripted=True)
    assert rc.verdict(f) == "KEEP", f


def test_silent_far_lane_is_rejected():
    mic, far = alternating(300, "headphones")
    f = rc.analyze(mic, np.zeros_like(far), setup="headphones", minutes=5, scripted=True)
    assert any("far is silent" in t for t in levels(f, "error"))


def test_lanes_of_different_length_are_rejected():
    mic, far = alternating(300, "headphones")
    f = rc.analyze(mic, far[: len(far) - 3 * rc.RATE], setup="headphones", minutes=5, scripted=True)
    assert any("differ" in t for t in levels(f, "error"))


def test_too_short_is_rejected():
    mic, far = alternating(100, "headphones")
    f = rc.analyze(mic, far, setup="headphones", minutes=5, scripted=True)
    assert any("min recorded" in t for t in levels(f, "error"))


def test_thirty_minute_call_needs_25():
    mic, far = alternating(20 * 60, "headphones")
    f = rc.analyze(mic, far, setup="headphones", minutes=30, scripted=False)
    assert any("min recorded" in t for t in levels(f, "error"))


def test_clipping_is_rejected():
    mic, far = alternating(300, "headphones")
    mic = np.clip(mic * 20, -1, 1)
    f = rc.analyze(mic, far, setup="headphones", minutes=5, scripted=True)
    assert any("mic clips" in t for t in levels(f, "error"))


def test_headphones_leak_is_rejected_loudspeakers_are_not():
    mic, far = alternating(300, "loudspeakers")
    leak = rc.analyze(mic, far, setup="headphones", minutes=5, scripted=True)
    assert any("leaks" in t for t in levels(leak, "error"))
    ok = rc.analyze(mic, far, setup="loudspeakers", minutes=5, scripted=False)
    assert not levels(ok, "error"), ok


def test_loudspeakers_without_bleed_warn():
    mic, far = alternating(300, "headphones")
    f = rc.analyze(mic, far, setup="loudspeakers", minutes=5, scripted=False)
    assert any("hardly hears" in t for t in levels(f, "warn"))


def test_interrupt_call_wants_overlap():
    mic, far = alternating(180, "headphones")
    f = rc.analyze(mic, far, setup="headphones", minutes=3, scripted=True, interrupts=True)
    assert any("overlap" in t for t in levels(f, "warn"))


def test_wav_roundtrip(tmp_path):
    import wave

    x = tone(2)
    p = tmp_path / "a.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rc.RATE)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    assert abs(len(rc.read_wav(p)) - len(x)) <= 1


@pytest.mark.parametrize("n,preset", rc.PRESETS.items())
def test_preset_matches_docs_and_script_exists(n, preset):
    """The meta.json the tool writes must be the one the reading page shows."""
    text = (DOCS / "recording-scripts.md").read_text(encoding="utf-8")
    metas = [json.loads(m) for m in re.findall(r"^(\{\"language\".*\})$", text, re.M)]
    shown = {tuple(sorted(m.items())) for m in metas}
    meta = {k: v for k, v in preset.items() if k != "script"}
    assert tuple(sorted(meta.items())) in shown
    if "script" in preset:
        assert (rc.FIXTURES / preset["script"]).exists()
    assert preset["scripted"] == ("script" in preset)
