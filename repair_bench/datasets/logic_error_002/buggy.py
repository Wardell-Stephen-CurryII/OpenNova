def discount_price(price, is_vip):
    if is_vip or price > 100:
        return price * 0.8
    return price


def can_retry(attempts, is_admin):
    if attempts > 3 and is_admin:
        return False
    return True
