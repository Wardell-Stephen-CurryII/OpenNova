import pytest
from buggy import iterate, process_items


def test_iterate_handles_int():
    try:
        result = iterate(5)
        assert len(result) == 5
    except TypeError:
        pytest.fail('iterate(5) should not raise TypeError')


def test_process_items():
    assert process_items(["a", "b"]) == ["ab", "b"]
