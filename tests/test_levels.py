import pytest

from hebelbot.levels import LevelError, fmt_eur, parse_level, parse_number, validate_levels


@pytest.mark.parametrize("text,expected", [("8,31", 8.31), ("8.31", 8.31), ("1.234,50", 1234.5), ("10 €", 10.0)])
def test_parse_number(text, expected):
    assert parse_number(text) == pytest.approx(expected)


def test_percent_levels_relative_to_entry():
    assert parse_level("-20%", 10.0, "sl") == pytest.approx(8.0)
    assert parse_level("20%", 10.0, "sl") == pytest.approx(8.0)
    assert parse_level("+30%", 10.0, "tp") == pytest.approx(13.0)
    assert parse_level("30%", 10.0, "tp") == pytest.approx(13.0)


def test_absolute_levels():
    assert parse_level("6,90", 8.31, "sl") == pytest.approx(6.9)


def test_invalid_levels():
    with pytest.raises(LevelError):
        parse_level("abc", 10, "sl")
    with pytest.raises(LevelError):
        parse_level("100%", 10, "sl")
    with pytest.raises(LevelError):
        validate_levels(10, 12, 11)


def test_fmt_eur_german():
    assert fmt_eur(1234.5) == "1.234,50 €"
    assert fmt_eur(0.456) == "0,456 €"
