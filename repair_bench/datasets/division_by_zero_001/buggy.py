def divide(a, b):
    return a / b


def average(numbers):
    total = 0
    for n in numbers:
        total += n
    return total / len(numbers)


def calculate_rate(value, reference):
    return 100 * value / (reference - 100)
