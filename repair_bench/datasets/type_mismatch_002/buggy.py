def parse_and_sum(text):
    parts = text.split(',')
    return sum(parts)


def average_values(values):
    return sum(values) / len(values)


def join_numbers(nums):
    return ','.join(nums)
