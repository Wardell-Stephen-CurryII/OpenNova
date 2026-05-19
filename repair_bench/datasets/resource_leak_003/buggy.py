import sqlite3


def query_users(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users')
    rows = cursor.fetchall()
    return rows


def count_records(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM records')
    result = cur.fetchone()
    return result[0]
