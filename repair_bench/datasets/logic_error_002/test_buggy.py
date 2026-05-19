import pytest
from buggy import discount_price, can_retry


def test_discount_non_vip_cheap():
    # Non-VIP with cheap item should NOT get discount
    assert discount_price(50, False) == 50


def test_discount_vip_expensive():
    # VIP with expensive item should get discount
    assert discount_price(200, True) == 160


def test_can_retry_regular_user():
    # Regular user with too many attempts should NOT retry
    assert can_retry(5, False) is False


def test_can_retry_admin():
    # Admin should always be able to retry
    assert can_retry(5, True) is True
