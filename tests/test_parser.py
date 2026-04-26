import pytest

from parser import Destination, ParseError, parse_caption


class TestParseCaption:
    def test_valid(self):
        assert parse_caption("1096/6/1/18") == Destination(1096, 6, 1, 18)

    def test_with_whitespace_around(self):
        assert parse_caption("  1096/6/1/18  \n") == Destination(1096, 6, 1, 18)

    def test_with_spaces_around_slashes(self):
        assert parse_caption("1096 / 6 / 1 / 18") == Destination(1096, 6, 1, 18)

    def test_empty_returns_none(self):
        assert parse_caption("") is None
        assert parse_caption(None) is None
        assert parse_caption("   ") is None

    def test_unrelated_text_returns_none(self):
        assert parse_caption("שלום") is None
        assert parse_caption("👍") is None
        assert parse_caption("hello world") is None

    def test_three_parts_raises(self):
        with pytest.raises(ParseError):
            parse_caption("1096/6/1")

    def test_five_parts_raises(self):
        with pytest.raises(ParseError):
            parse_caption("1096/6/1/18/2")

    def test_non_numeric_part_raises(self):
        with pytest.raises(ParseError):
            parse_caption("1096/6/1/abc")

    def test_typo_with_slash_raises(self):
        with pytest.raises(ParseError):
            parse_caption("1096//1/18")
