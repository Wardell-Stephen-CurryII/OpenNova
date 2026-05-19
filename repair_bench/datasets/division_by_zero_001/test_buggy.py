import pytest
from buggy import divide, average, calculate_rate


def test_divide_normal():
    assert divide(10, 2) == 5.0


def test_divide_by_zero_handled():
    try:
        result = divide(10, 0)
    except ZeroDivisionError:
        pytest.fail("divide(10, 0) should not raise ZeroDivisionError")


def test_average_normal():
    assert average([1, 2, 3]) == 2.0


def test_average_empty_handled():
    try:
        result = average([])
    except ZeroDivisionError:
        pytest.fail("average([]) should not raise ZeroDivisionError")


def test_calculate_rate_normal():
    assert calculate_rate(50, 200) == 50.0


def test_calculate_rate_boundary_handled():
    try:
        result = calculate_rate(50, 100)
    except ZeroDivisionError:
        pytest.fail("calculate_rate(50, 100) should not raise ZeroDivisionError")
