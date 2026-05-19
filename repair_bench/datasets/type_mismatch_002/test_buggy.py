import pytest
from buggy import parse_and_sum, average_values, join_numbers


def test_parse_and_sum():
    try:
        result = parse_and_sum('1,2,3')
        assert result == 6
    except TypeError:
        pytest.fail('parse_and_sum should handle string input')


def test_join_numbers_handled():
    try:
        result = join_numbers([1, 2, 3])
        assert '1' in result
    except TypeError:
        pytest.fail('join_numbers should handle int list')
