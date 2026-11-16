"""The words a speech model should expect to hear, taken from the profile.

A recognizer decodes toward whatever is common, so the words that matter most
in a session -- a client's name, a protocol, a product -- are the ones it is
most likely to hear as something else. On a recorded call "Modbus TCP" came out
as "Mobbus", and "TimescaleDB" as "Tyscale de B". Whisper-family models can be
told what to expect with an initial prompt; the profile already holds the text
that names those words.

Nothing here understands language. It looks for the shapes that proper nouns
and technical terms take -- an initial capital in the middle of a sentence,
capitals inside a word, digits, all-caps acronyms -- and keeps them. That is
cheap and needs no maintenance, at the price of sometimes keeping a word that
is merely capitalised. A stray term in the prompt costs little; a missing one
costs a mis-transcribed name.
"""

from __future__ import annotations

import re
from collections import Counter

#: Letters including the Spanish and Portuguese accented ones, then anything a
#: technical term can contain.
_TOKEN = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9+#]*")
_SENTENCE_END = re.compile(r"[.!?¿¡:\n]\s*$")

#: Enough to cover a profile's real vocabulary, few enough to stay well inside
#: the prompt a speech model will read (about 220 tokens).
MAX_TERMS = 40


def _shape_is_term(word: str) -> bool:
    if len(word) < 2:
        return False
    if any(ch.isdigit() for ch in word):
        return True
    if word.isupper():  # PLC, TCP, MQTT
        return True
    # A capital after the first letter: TimescaleDB, PostgreSQL, iPhone.
    return any(ch.isupper() for ch in word[1:])


def extract(context: dict[str, str], limit: int = MAX_TERMS) -> list[str]:
    """Candidate terms from a profile's text, most frequent first."""
    seen: dict[str, str] = {}
    counts: Counter[str] = Counter()
    for text in context.values():
        for match in _TOKEN.finditer(text):
            word = match.group()
            before = text[: match.start()]
            starts_sentence = not before.strip() or bool(_SENTENCE_END.search(before))
            capitalised = word[0].isupper() and not starts_sentence
            if not (_shape_is_term(word) or capitalised):
                continue
            key = word.lower()
            seen.setdefault(key, word)
            counts[key] += 1
    ranked = sorted(counts, key=lambda k: (-counts[k], list(seen).index(k)))
    return [seen[key] for key in ranked[:limit]]


def as_prompt(terms: list[str]) -> str:
    """A prompt for a speech model: just the words, in a plain list."""
    return ", ".join(terms) + "." if terms else ""
