import pytest
from buggy import get_last, get_middle


def test_get_last_nonempty():
    assert get_last([1, 2, 3]) == 3


def test_get_last_single():
    assert get_last([42]) == 42


def test_get_middle():
    assert get_middle([1, 2, 3, 4, 5]) == 3


def test_get_middle_empty_handled():
    try:
        result = get_middle([])
    except IndexError:
        pytest.fail("get_middle([]) should not raise IndexError")
