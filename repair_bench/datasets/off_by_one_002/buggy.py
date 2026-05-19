def get_chunk(data, idx, size):
    start = idx * size
    end = start + size
    return data[start:end]


def last_n(items, n):
    return items[len(items)-n:len(items)]
