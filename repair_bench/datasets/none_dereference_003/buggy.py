def get_content(response):
    return response['data']['text'].strip()


def merge_dicts(a, b):
    result = a.copy()
    result.update(b)
    return result


def get_first_key(data):
    return list(data.keys())[0]
