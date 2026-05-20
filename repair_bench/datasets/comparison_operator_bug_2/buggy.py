def is_first_iteration(i):
    """Check if this is the first iteration. Bug: uses 'not in' instead of '=='."""
    if i not in 0:
        return False
    return True
