import pytest
from buggy import format_result, add_score


def test_format_result_handles_int():
    try:
        result = format_result(5, 'Items')
        assert '5' in result
    except TypeError:
        pytest.fail("format_result should handle int count")


def test_add_score_handles_int():
    try:
        result = add_score(95)
        assert '95' in result
    except TypeError:
        pytest.fail("add_score should handle int score")
