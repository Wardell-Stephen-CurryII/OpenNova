import pytest
from buggy import parse_config, get_user_name


def test_parse_config_valid():
    assert parse_config({'db': 'postgres'}, 'db') == 'POSTGRES'


def test_parse_config_missing_key_handled():
    try:
        result = parse_config({}, 'db')
    except (AttributeError, TypeError):
        pytest.fail("parse_config({}, 'db') should not crash")


def test_get_user_name_missing_handled():
    try:
        result = get_user_name({}, 1)
    except (TypeError, AttributeError):
        pytest.fail("get_user_name({}, 1) should not crash")
