def find_item(items, target):
    for item in items:
        if item == target:
            return item


def validate_and_get(data, key):
    if key in data:
        value = data[key]
        if value is not None:
            return value
