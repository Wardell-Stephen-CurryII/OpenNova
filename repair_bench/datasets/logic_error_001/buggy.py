def is_valid_password(pw):
    return len(pw) < 8


def all_even(numbers):
    return all(n % 2 == 1 for n in numbers)
