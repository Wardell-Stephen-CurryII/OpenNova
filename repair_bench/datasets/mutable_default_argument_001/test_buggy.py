import pytest
from buggy import append_to, add_entry


def test_append_to_first_call():
    assert append_to(1) == [1]


def test_append_to_does_not_accumulate():
    result = append_to(2)
    # Second call without argument should get a fresh list
    assert len(result) == 1
    assert result == [2]


def test_add_entry_idempotent():
    r1 = add_entry('a', 1)
    r2 = add_entry('a', 2)
    assert r2['a'] == 1
