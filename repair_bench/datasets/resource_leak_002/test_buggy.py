import pytest
import os
import tempfile
from buggy import write_log, copy_file


@pytest.fixture
def temp_files():
    fd, src = tempfile.mkstemp(suffix='.txt')
    os.write(fd, b'test data\n')
    os.close(fd)
    dst_dir = tempfile.mkdtemp()
    dst = os.path.join(dst_dir, 'out.txt')
    yield src, dst
    if os.path.exists(src):
        os.unlink(src)
    if os.path.exists(dst):
        os.unlink(dst)
    os.rmdir(dst_dir)


def test_write_log(temp_files):
    src, _ = temp_files
    log_file = src + '.log'
    write_log(log_file, ['a', 'b'])
    # Data should be flushed and readable
    with open(log_file) as f:
        content = f.read()
    assert 'a' in content
    assert 'b' in content
    os.unlink(log_file)


def test_copy_file(temp_files):
    src, dst = temp_files
    copy_file(src, dst)
    assert os.path.exists(dst)
    with open(dst) as f:
        assert f.read() == 'test data\n'
