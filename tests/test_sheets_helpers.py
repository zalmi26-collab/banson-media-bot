"""Tests for the column-letter and forward-fill helpers in sheets_client.

The Sheets API integration itself (reading the Gantt sheet, writing cells) is
verified manually against the live spreadsheet — the value of unit-testing it
would be small relative to mocking complexity.
"""
from sheets_client import _col_letter, _forward_fill


def test_col_letter_single():
    assert _col_letter(0) == "A"
    assert _col_letter(25) == "Z"


def test_col_letter_double():
    assert _col_letter(26) == "AA"
    assert _col_letter(27) == "AB"
    assert _col_letter(51) == "AZ"
    assert _col_letter(52) == "BA"


def test_col_letter_realistic():
    # Idx 39 (where row-1 has 1097) should be column AN
    assert _col_letter(39) == "AN"
    # Idx 75 (where row-1 has 1101) should be column BX
    assert _col_letter(75) == "BX"


def test_forward_fill_basic():
    assert _forward_fill(["a", "", "", "b", ""], 5) == ["a", "a", "a", "b", "b"]


def test_forward_fill_empty_prefix():
    assert _forward_fill(["", "", "x"], 4) == ["", "", "x", "x"]


def test_forward_fill_extends_short_row():
    # Even if input row is shorter than length, fill extends with the last value.
    assert _forward_fill(["x", "y"], 5) == ["x", "y", "y", "y", "y"]
