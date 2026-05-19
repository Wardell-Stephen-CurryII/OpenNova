def count_to(n):
    result = []
    i = 0
    while i <= n:
        result.append(i)
        i += 1
    return result


def search_range(arr, lo, hi):
    result = []
    for i in range(lo, hi):
        if i < len(arr):
            result.append(arr[i])
    return result
