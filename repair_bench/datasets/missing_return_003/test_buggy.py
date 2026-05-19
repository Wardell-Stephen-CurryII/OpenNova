import pytest
from buggy import safe_parse, get_first_positive


def test_safe_parse_valid():
    assert safe_parse('42') == 42


def test_safe_parse_invalid_returns_none():
    assert safe_parse('abc') is None


def test_get_first_positive_all_zeros_returns_none():
    assert get_first_positive([0, 0, 0]) is None
