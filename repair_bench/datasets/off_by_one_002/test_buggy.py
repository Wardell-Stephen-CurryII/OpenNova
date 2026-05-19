import pytest
from buggy import get_chunk, last_n


def test_get_chunk_partial_last():
    # get_chunk with remaining elements < size
    result = get_chunk([1, 2, 3, 4, 5], 2, 2)
    assert result == [5]


def test_last_n_zero():
    # last_n with n=0 should return empty
    assert last_n([1, 2, 3], 0) == []
