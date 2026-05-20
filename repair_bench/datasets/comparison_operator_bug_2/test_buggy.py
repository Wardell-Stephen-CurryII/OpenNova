
import pytest
from buggy import is_first_iteration


def test_first_iteration():
    """With correct '== 0': returns True. Bug: 'not in 0' raises TypeError."""
    try:
        result = is_first_iteration(0)
        assert result is True, f"Expected True for i=0, got {result}"
    except TypeError:
        pytest.fail("is_first_iteration(0) raised TypeError (bug: 'not in 0' is invalid)")


def test_later_iteration():
    result = is_first_iteration(1)
    assert result is False, f"Expected False for i=1, got {result}"


def test_large_index():
    result = is_first_iteration(100)
    assert result is False, f"Expected False for i=100, got {result}"
