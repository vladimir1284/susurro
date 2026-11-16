"""Word error rate against a script, tolerant of everything said off-script.

A recorded call is never exactly the script: people add a greeting, repeat a
line, mutter to themselves. Scoring the whole transcript against the whole
script would charge every extra word as an error and bury the thing being
measured, which is how well the recognizer heard the words that were meant to
be heard.

So each scripted sentence is aligned on its own against the lane's transcript,
with the transcript free at both ends: the sentence has to be matched in full,
and the words around it cost nothing. A sentence that was never said scores as
entirely wrong, which is also right.

This is a measurement of the recognizer on that call, not a general accuracy
claim. It is only as good as the script is faithful to what was read.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_PUNCTUATION = re.compile(r"[¿?¡!.,;:…\"'()\[\]«»—–-]")


def normalize(text: str) -> list[str]:
    """Lowercase words, no punctuation, accents folded.

    Accents are folded because a recognizer that writes "que" for "qué" has
    heard the word correctly; counting it wrong would measure orthography.
    """
    folded = unicodedata.normalize("NFD", text.lower())
    folded = "".join(ch for ch in folded if unicodedata.category(ch) != "Mn")
    return _PUNCTUATION.sub(" ", folded).split()


def edits(reference: list[str], hypothesis: list[str]) -> int:
    """Fewest word edits to turn the reference into *some span* of the hypothesis."""
    if not reference:
        return 0
    previous = [0] * (len(hypothesis) + 1)  # free start: any hypothesis word may begin
    for i, ref_word in enumerate(reference, start=1):
        current = [i] + [0] * len(hypothesis)
        for j, hyp_word in enumerate(hypothesis, start=1):
            substitution = previous[j - 1] + (ref_word != hyp_word)
            current[j] = min(substitution, previous[j] + 1, current[j - 1] + 1)
        previous = current
    return min(previous)  # free end: the span may stop anywhere


@dataclass
class SentenceScore:
    reference: str
    edits: int
    words: int

    @property
    def wer(self) -> float:
        return self.edits / self.words if self.words else 0.0


@dataclass
class Score:
    sentences: list[SentenceScore] = field(default_factory=list)

    @property
    def edits(self) -> int:
        return sum(s.edits for s in self.sentences)

    @property
    def words(self) -> int:
        return sum(s.words for s in self.sentences)

    @property
    def wer(self) -> float:
        return self.edits / self.words if self.words else 0.0


def score(references: list[str], transcript: str) -> Score:
    """Score each scripted sentence against everything the lane transcribed."""
    hypothesis = normalize(transcript)
    result = Score()
    for sentence in references:
        words = normalize(sentence)
        result.sentences.append(
            SentenceScore(reference=sentence, edits=edits(words, hypothesis), words=len(words))
        )
    return result


def term_recall(terms: list[str], transcript: str) -> dict[str, bool]:
    """Whether each key term appears intact, which is what a hint most needs.

    A few words carry the meaning of a technical conversation, and a recognizer
    can score well overall while mangling exactly those.
    """
    hypothesis = " ".join(normalize(transcript))
    return {term: " ".join(normalize(term)) in hypothesis for term in terms}
