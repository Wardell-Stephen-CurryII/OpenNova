
import pytest
from buggy import compute_total_args


def test_total_args_sum():
    """num_pos + len(named). With *: 3 * 2 = 6. With +: 3 + 2 = 5."""
    result = compute_total_args((1, 2, 3), {"a": 1, "b": 2})
    assert result == 5, f"Expected 5 (3+2), got {result} (bug: * instead of +)"


def test_total_args_zero_named():
    result = compute_total_args((1, 2), {})
    assert result == 2, f"Expected 2 (2+0), got {result} (bug: * gives 0 instead of 2)"


def test_total_args_zero_positional():
    result = compute_total_args((), {"a": 1, "b": 2, "c": 3})
    assert result == 3, f"Expected 3 (0+3), got {result} (bug: * gives 0 instead of 3)"
