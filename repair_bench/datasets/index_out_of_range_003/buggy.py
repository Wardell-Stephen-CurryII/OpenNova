def get_page(items, page, page_size):
    start = page * page_size
    end = start + page_size
    return items[start:end]


def first_n(items, n):
    return [items[i] for i in range(n)]
