"""Vocabulary taken from a profile, and where it goes."""

from __future__ import annotations

import json
from pathlib import Path

from core.session.vocabulary import as_prompt, extract

FIXTURES = Path(__file__).parent / "fixtures" / "profiles"


class TestExtract:
    def test_acronyms_names_and_mixed_case_terms_are_kept(self) -> None:
        text = {"resume": "Trabajo con Modbus TCP y TimescaleDB sobre PostgreSQL en un PLC."}
        terms = extract(text)
        assert {"Modbus", "TCP", "TimescaleDB", "PostgreSQL", "PLC"} <= set(terms)

    def test_an_ordinary_word_that_starts_a_sentence_is_not_a_term(self) -> None:
        # "Antes" is capitalised because it is first, not because it is a name.
        assert "Antes" not in extract({"resume": "Antes trabajaba con Docker. Ahora no."})
        assert "Ahora" not in extract({"resume": "Antes trabajaba con Docker. Ahora no."})

    def test_words_with_digits_are_kept(self) -> None:
        assert {"ESP32", "STM32"} <= set(extract({"resume": "firmware en ESP32 y STM32"}))

    def test_plain_lowercase_words_are_not(self) -> None:
        assert extract({"resume": "una línea de envasado con controladores"}) == []

    def test_the_most_repeated_come_first(self) -> None:
        terms = extract({"resume": "uso Docker y Python y Docker y Docker y Python y Go"})
        assert terms[:2] == ["Docker", "Python"]

    def test_there_is_a_ceiling(self) -> None:
        text = " ".join(f"Termino{i}X" for i in range(200))
        assert len(extract({"resume": text}, limit=10)) == 10

    def test_a_profile_yields_its_own_jargon(self) -> None:
        profile = json.loads((FIXTURES / "es-business-industrial.json").read_text("utf-8"))
        terms = extract(profile["context"])
        assert {"Modbus", "TCP", "PLC", "TimescaleDB", "PostgreSQL"} <= set(terms)


class TestPrompt:
    def test_it_is_a_plain_list(self) -> None:
        assert as_prompt(["Modbus", "TCP"]) == "Modbus, TCP."

    def test_no_terms_means_no_prompt(self) -> None:
        assert as_prompt([]) == ""
