"""Speech recognition backends."""

from core.stt.base import Recognizer, Segment
from core.stt.fake import ScriptedRecognizer

__all__ = ["Recognizer", "ScriptedRecognizer", "Segment"]
