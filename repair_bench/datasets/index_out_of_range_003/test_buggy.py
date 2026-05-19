import pytest
from buggy import get_page, first_n


def test_get_page_normal():
    assert get_page([1, 2, 3, 4, 5], 0, 2) == [1, 2]


def test_first_n_more_than_length_handled():
    try:
        result = first_n([1, 2, 3], 5)
    except IndexError:
        pytest.fail("first_n([1,2,3], 5) should not raise IndexError")
