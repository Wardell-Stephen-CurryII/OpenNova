import pytest
import os
import tempfile
from buggy import read_file_content, count_lines


@pytest.fixture
def temp_file():
    fd, name = tempfile.mkstemp(suffix='.txt')
    os.write(fd, b'line1\nline2\nline3\n')
    os.close(fd)
    yield name
    os.unlink(name)


def test_read_file_content(temp_file):
    # Should read content successfully (and not leak file handles)
    result = read_file_content(temp_file)
    assert result == 'line1\nline2\nline3\n'


def test_count_lines(temp_file):
    # Should count lines successfully
    result = count_lines(temp_file)
    assert result == 3
