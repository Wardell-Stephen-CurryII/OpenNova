import pytest
from buggy import consecutive_diffs


def test_consecutive_diffs_normal():
    assert consecutive_diffs([1, 3, 6]) == [2, 3]


def test_consecutive_diffs_single_handled():
    try:
        result = consecutive_diffs([5])
    except IndexError:
        pytest.fail("consecutive_diffs([5]) should not raise IndexError")
