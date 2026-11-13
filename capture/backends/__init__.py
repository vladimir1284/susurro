"""Audio sources. One per platform, plus a file replay for tests and demos."""

from capture.backends.base import AudioSource, SourceError
from capture.backends.file import WavSource

__all__ = ["AudioSource", "SourceError", "WavSource"]
