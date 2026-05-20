
import pytest
from buggy import StreamBuffer


def test_advance_position_stops():
    """With +: 3 + 5 = 8 >= 5, loop doesn't run. With -: 3 - 5 = -2 < 5, infinite/many iterations."""
    buf = StreamBuffer()
    result = buf.advance_position(0)
    # Correct: loop condition is 3 + 5 < 5 => 8 < 5 => False, result stays 3
    # Bug: loop condition is 3 - 5 < 5 => -2 < 5 => True, infinite loop or overrun
    assert result == 3, f"Expected 3, got {result} (bug: - instead of + caused wrong loop behavior)"
