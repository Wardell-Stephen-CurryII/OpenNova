import pytest
from buggy import compute_ratio, compute_diff_ratio


def test_compute_ratio_normal():
    assert compute_ratio(3, 10) == 0.3


def test_compute_ratio_zero_total_handled():
    try:
        result = compute_ratio(5, 0)
    except ZeroDivisionError:
        pytest.fail("compute_ratio(5, 0) should not raise ZeroDivisionError")


def test_compute_diff_ratio_opposite_handled():
    try:
        result = compute_diff_ratio(5, -5)
    except ZeroDivisionError:
        pytest.fail("compute_diff_ratio(5, -5) should not raise ZeroDivisionError")
