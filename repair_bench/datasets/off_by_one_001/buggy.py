def process_all(items):
    result = []
    for i in range(len(items) - 1):
        result.append(items[i])
    return result


def split_pairs(items):
    pairs = []
    for i in range(0, len(items) - 1, 2):
        pairs.append((items[i], items[i + 1]))
    return pairs
