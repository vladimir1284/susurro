"""The scoring has to be right before any number it produces means anything."""

from __future__ import annotations

import pytest

from bench.wer import edits, normalize, score, term_recall


class TestNormalize:
    def test_case_punctuation_and_accents_do_not_count(self) -> None:
        assert normalize("¿Qué se PODRÍA instrumentar?") == ["que", "se", "podria", "instrumentar"]

    def test_a_hyphenated_word_is_two_words(self) -> None:
        assert normalize("Wi-Fi") == ["wi", "fi"]


class TestEdits:
    def test_a_perfect_match_costs_nothing(self) -> None:
        assert edits(["a", "b", "c"], ["a", "b", "c"]) == 0

    def test_words_around_the_sentence_are_free(self) -> None:
        assert edits(["b", "c"], ["x", "y", "b", "c", "z"]) == 0

    def test_a_substitution_costs_one(self) -> None:
        assert edits(["a", "b", "c"], ["a", "x", "c"]) == 1

    def test_a_missing_word_costs_one(self) -> None:
        assert edits(["a", "b", "c"], ["a", "c"]) == 1

    def test_an_extra_word_inside_the_sentence_costs_one(self) -> None:
        assert edits(["a", "c"], ["a", "b", "c"]) == 1

    def test_a_sentence_never_said_is_entirely_wrong(self) -> None:
        assert edits(["a", "b", "c"], ["x", "y"]) == 3

    def test_an_empty_transcript_misses_every_word(self) -> None:
        assert edits(["a", "b"], []) == 2

    def test_an_empty_reference_costs_nothing(self) -> None:
        assert edits([], ["a"]) == 0


class TestScore:
    def test_each_sentence_is_aligned_on_its_own(self) -> None:
        said = "hola buenas tardes tenemos una linea de envasado eh y despues otra cosa"
        result = score(["Tenemos una línea de envasado", "otra cosa"], said)
        assert result.edits == 0
        assert result.words == 7

    def test_an_error_rate_is_edits_over_reference_words(self) -> None:
        result = score(["uno dos tres cuatro"], "uno dos tres cinco")
        assert result.wer == pytest.approx(0.25)

    def test_the_same_phrase_said_off_script_does_not_inflate_the_rate(self) -> None:
        result = score(["me llamo luis"], "buenas me llamo luis y ya me voy")
        assert result.wer == 0.0


class TestTermRecall:
    def test_a_mangled_term_is_missed(self) -> None:
        found = term_recall(["Modbus TCP", "PostgreSQL"], "por Mobbus TCP sobre postgresql")
        assert found == {"Modbus TCP": False, "PostgreSQL": True}
