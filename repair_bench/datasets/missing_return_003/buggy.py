def safe_parse(value):
    try:
        return int(value)
    except ValueError:
        print(f'Invalid: {value}')


def get_first_positive(numbers):
    for n in numbers:
        if n > 0:
            return n
        elif n < 0:
            continue
