import pytest
from buggy import find_item, validate_and_get


def test_find_item_found():
    assert find_item([1, 2, 3], 2) == 2


def test_find_item_not_found_returns_none():
    # Should return None explicitly
    assert find_item([1, 2, 3], 99) is None


def test_validate_and_get_none_value():
    # Should return None when value is None
    assert validate_and_get({'x': None}, 'x') is None
