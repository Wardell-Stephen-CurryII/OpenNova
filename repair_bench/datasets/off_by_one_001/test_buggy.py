import pytest
from buggy import process_all, split_pairs


def test_process_all_includes_last():
    result = process_all([1, 2, 3, 4])
    assert len(result) == 4
    assert result == [1, 2, 3, 4]


def test_split_pairs_even():
    result = split_pairs([1, 2, 3, 4])
    assert result == [(1, 2), (3, 4)]


def test_split_pairs_odd_has_one_pair():
    result = split_pairs([1, 2, 3])
    assert len(result) == 1
