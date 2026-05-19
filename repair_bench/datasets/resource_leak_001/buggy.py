def read_file_content(filepath):
    f = open(filepath, 'r')
    data = f.read()
    return data


def count_lines(filepath):
    f = open(filepath, 'r')
    lines = f.readlines()
    return len(lines)
