
import pytest
from buggy import LikelihoodCalculator


def test_scaling_with_float():
    """self.d_radial_fid / d_radial. Bug: d_radial.d_radial_fid fails - float has no such attr."""
    calc = LikelihoodCalculator()
    try:
        result = calc.compute_scaling(50.0)
        expected = (100.0 / 50.0) ** (1.0 / 3.0)
        assert abs(result - expected) < 0.001, f"Expected {expected}, got {result}"
    except AttributeError:
        pytest.fail("compute_scaling raised AttributeError (bug: accessing .d_radial_fid on float)")


def test_scaling_with_other_value():
    calc = LikelihoodCalculator()
    calc.d_radial_fid = 27.0
    result = calc.compute_scaling(8.0)
    expected = (27.0 / 8.0) ** (1.0 / 3.0)  # 1.5
    assert abs(result - 1.5) < 0.001, f"Expected ~1.5, got {result}"
