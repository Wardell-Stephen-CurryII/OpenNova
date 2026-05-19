import pytest
from buggy import transfer, calculate_grade, is_prime


def test_transfer_sufficient():
    fb, tb = transfer(100, 0, 50)
    assert fb == 50
    assert tb == 50


def test_calculate_grade():
    assert calculate_grade(85) == 'B'
    assert calculate_grade(45) == 'F'
    assert calculate_grade(90) == 'A'


def test_is_prime_true():
    assert is_prime(7) is True
    assert is_prime(2) is True


def test_is_prime_false():
    assert is_prime(4) is False
    assert is_prime(1) is False
