import pytest
from buggy import get_content, merge_dicts, get_first_key


def test_get_content_valid():
    assert get_content({'data': {'text': 'hello'}}) == 'hello'


def test_get_content_missing_text_handled():
    try:
        result = get_content({'data': {}})
    except (AttributeError, KeyError):
        pytest.fail('get_content(...) should not crash on missing text')


def test_get_first_key_empty_handled():
    try:
        result = get_first_key({})
    except IndexError:
        pytest.fail('get_first_key({}) should not raise IndexError')
