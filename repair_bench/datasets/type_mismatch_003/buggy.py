def iterate(n):
    result = []
    for i in n:
        result.append(i * 2)
    return result


def process_items(items):
    items[0] = items[0] + items[1]
    return items
