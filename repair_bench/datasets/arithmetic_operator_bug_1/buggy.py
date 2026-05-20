class GuiWidget:
    X_SCALING_FACTOR = 5

    def format_padding(self):
        """Calculate padding width. Bug: uses + instead of *."""
        return " " * int(4 + self.X_SCALING_FACTOR)
