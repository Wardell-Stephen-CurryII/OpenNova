
import pytest
from buggy import GuiWidget


def test_padding_scaling():
    """4 * 5 = 20 spaces; 4 + 5 = 9 spaces (bug)."""
    widget = GuiWidget()
    result = widget.format_padding()
    # Correct: 4 * X_SCALING_FACTOR = 4 * 5 = 20 spaces
    # Bug: 4 + X_SCALING_FACTOR = 4 + 5 = 9 spaces
    assert len(result) >= 15, f"Expected >= 15 spaces, got {len(result)} (bug: + instead of *)"


def test_padding_not_too_large():
    widget = GuiWidget()
    result = widget.format_padding()
    assert len(result) == 20, f"Expected exactly 20 spaces, got {len(result)}"
