import pytest
from buggy import Person, get_city
from buggy import Address


def test_get_city_valid():
    p = Person(Address('Beijing'))
    assert get_city(p) == 'Beijing'


def test_get_city_no_address_handled():
    p = Person()
    try:
        result = get_city(p)
    except AttributeError:
        pytest.fail("get_city(person without address) should not raise AttributeError")
