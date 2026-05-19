def write_log(filepath, entries):
    f = open(filepath, 'a')
    for entry in entries:
        f.write(entry + '\n')


def copy_file(src, dst):
    fin = open(src, 'r')
    data = fin.read()
    fout = open(dst, 'w')
    fout.write(data)
    fin.close()
