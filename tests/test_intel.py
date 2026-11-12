from __future__ import annotations

import pytest

from healdragg.intel import COUNTRIES, NAMES, detect_script, guess, lookup_names


def test_script_detection() -> None:
    assert detect_script("张伟") == "han"
    assert detect_script("さくら") == "kana"
    assert detect_script("김민준") == "hangul"
    assert detect_script("محمد") == "arabic"
    assert detect_script("Иван") == "cyrillic"
    assert detect_script("John") == "latin"


def test_cyrillic_names() -> None:
    ivan = guess("Иван")
    assert ivan.gender == "male"
    assert ivan.country == "RU"
    assert ivan.source in {"dictionary", "script"}

    anna = guess("Анна")
    assert anna.gender == "female"
    assert anna.country == "RU"
    assert 0 < anna.gender_confidence <= 1


def test_eastern_names() -> None:
    assert guess("张伟").country == "CN"
    assert guess("さくら").gender == "female"
    assert guess("김민준").country == "KR"
    assert guess("محمد").country == "AR"


def test_turkish_and_indian() -> None:
    assert guess("Mehmet").country == "TR"
    assert guess("Priya").gender == "female"
    assert guess("Budi").country == "ID"


def test_unknown_name_falls_back_to_heuristic() -> None:
    result = guess("Златослава")
    assert result.source == "heuristic"
    assert result.gender == "female"
    assert result.country is None


def test_exceptions() -> None:
    for name in ("Никита", "Илья", "Кузьма"):
        assert guess(name).gender == "male", name


def test_empty_and_lookup_list() -> None:
    empty = guess("   ")
    assert empty.source == "empty" and empty.gender == "unknown"
    rows = lookup_names(["Анна", "", "Mehmet"])
    assert len(rows) == 2
    assert all("gender" in row and "country_name" in row for row in rows)


@pytest.mark.parametrize("country", sorted(COUNTRIES))
def test_every_country_has_names(country: str) -> None:
    by_gender = NAMES[country]
    assert len(by_gender["male"]) >= 15
    assert len(by_gender["female"]) >= 15
