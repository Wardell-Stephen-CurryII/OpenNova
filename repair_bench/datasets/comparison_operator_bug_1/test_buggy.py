
import pytest
from buggy import check_value


def test_none_value():
    """With correct 'is None': returns 'none'. Bug: 'not in None' raises TypeError."""
    try:
        result = check_value(None)
        assert result == "none", f"Expected 'none', got '{result}'"
    except TypeError:
        pytest.fail("check_value(None) raised TypeError (bug: 'not in None' is invalid)")


def test_valid_value():
    result = check_value("hello")
    assert result == "valid", f"Expected 'valid', got '{result}'"


def test_zero_value():
    result = check_value(0)
    assert result == "valid", f"Expected 'valid', got '{result}'"
