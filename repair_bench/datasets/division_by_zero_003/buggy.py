def compute_ratio(part, total):
    if total > 0:
        return part / total
    return part / total


def compute_diff_ratio(a, b):
    diff = a - b
    return abs(diff) / (a + b)
