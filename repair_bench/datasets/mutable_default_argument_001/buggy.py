def append_to(item, lst=[]):
    lst.append(item)
    return lst


def add_entry(name, data, cache={}):
    if name not in cache:
        cache[name] = data
    return cache
