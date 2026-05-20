
import pytest
from buggy import is_distance_function


def test_exact_match():
    """With '==': 'distance' matches exactly."""
    result = is_distance_function("distance")
    assert result is True, f"Expected True for 'distance', got {result}"


def test_no_substring_match():
    """Bug: 'in' operator matches substrings. 'dist' in 'distance' is True, but 'dist' == 'distance' is False."""
    result = is_distance_function("dist")
    assert result is False, f"Expected False for 'dist' (not exact match), got {result} (bug: 'in' matches substring 'dist' in 'distance')"


def test_completely_different():
    result = is_distance_function("length")
    assert result is False, f"Expected False for 'length', got {result}"
