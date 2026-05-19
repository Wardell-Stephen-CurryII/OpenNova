import pytest
from buggy import Tracker


def test_tracker_new_instances_independent():
    t1 = Tracker()
    t2 = Tracker()
    t1.record('a')
    assert len(t2.events) == 0
