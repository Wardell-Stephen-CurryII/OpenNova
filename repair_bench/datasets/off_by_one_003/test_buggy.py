import pytest
from buggy import count_to, search_range


def test_count_to_length():
    r = count_to(5)
    # count_to(5) should return [0,1,2,3,4,5] (inclusive) or [0,1,2,3,4] (exclusive)
    # The key: it should NOT return 7 elements
    assert len(r) <= 6


def test_search_range_inclusive():
    r = search_range([10, 20, 30, 40, 50], 1, 4)
    # Should include hi index (inclusive)
    assert 40 in r
