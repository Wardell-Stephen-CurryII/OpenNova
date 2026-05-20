def compute_total_args(pos_args, named_args):
    """Count total arguments. Bug: uses * instead of +."""
    num_pos = len(pos_args)
    num_total = num_pos * len(named_args)
    return num_total


def getcallargs(func, *positional, **named):
    return compute_total_args(positional, named)
