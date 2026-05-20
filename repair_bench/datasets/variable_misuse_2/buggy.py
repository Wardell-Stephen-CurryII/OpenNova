class LikelihoodCalculator:
    def __init__(self):
        self.d_radial_fid = 100.0

    def compute_scaling(self, d_radial):
        """Compute scaling factor. Bug: uses d_radial.d_radial_fid instead of self.d_radial_fid."""
        factor = (d_radial.d_radial_fid / d_radial) ** (1.0 / 3.0)
        return factor
