import pytest
from buggy import is_valid_password, all_even


def test_is_valid_password_short():
    # Short password should be INVALID
    assert is_valid_password('123') is False


def test_is_valid_password_good():
    # 8+ char password should be VALID
    assert is_valid_password('secure123') is True


def test_all_even_true():
    assert all_even([2, 4, 6]) is True


def test_all_even_false():
    assert all_even([2, 3, 6]) is False
