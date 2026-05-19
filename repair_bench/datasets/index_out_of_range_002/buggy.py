def consecutive_diffs(data):
    result = []
    for i in range(len(data)):
        diff = data[i + 1] - data[i]
        result.append(diff)
    return result
