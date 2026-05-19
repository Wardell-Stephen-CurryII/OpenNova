import pytest
import os
import tempfile
import sqlite3
from buggy import query_users, count_records


@pytest.fixture
def temp_db():
    fd, name = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    conn = sqlite3.connect(name)
    conn.execute('CREATE TABLE users (id INTEGER, name TEXT)')
    conn.execute("INSERT INTO users VALUES (1, 'Alice')")
    conn.execute('CREATE TABLE records (id INTEGER)')
    conn.execute('INSERT INTO records VALUES (1)')
    conn.commit()
    conn.close()
    yield name
    os.unlink(name)


def test_query_users(temp_db):
    rows = query_users(temp_db)
    assert len(rows) == 1
    assert rows[0] == (1, 'Alice')


def test_count_records(temp_db):
    assert count_records(temp_db) == 1
