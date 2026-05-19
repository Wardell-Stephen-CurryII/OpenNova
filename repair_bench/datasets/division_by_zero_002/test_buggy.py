import pytest
from buggy import safe_divide


def test_safe_divide_positive():
    assert safe_divide(10, 2) == 5.0


def test_safe_divide_negative():
    assert safe_divide(10, -2) == -5.0


def test_safe_divide_zero_handled():
    try:
        result = safe_divide(10, 0)
    except ZeroDivisionError:
        pytest.fail("safe_divide(10, 0) should not raise ZeroDivisionError")
