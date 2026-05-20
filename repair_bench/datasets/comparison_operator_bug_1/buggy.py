def check_value(value):
    """Check if value is None. Bug: uses 'not in' instead of 'is'."""
    if value not in None:
        return "valid"
    return "none"
