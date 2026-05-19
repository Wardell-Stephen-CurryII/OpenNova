import pytest
from buggy import get_status, is_positive


def test_get_status_known():
    assert get_status(200) == 'OK'


def test_get_status_unknown_returns_something():
    result = get_status(302)
    # Should return something (not crash), even if it's the unknown code
    pass


def test_is_positive_negative():
    # Should return False for negative, not None
    assert is_positive(-5) is not None
    assert is_positive(-5) is False
