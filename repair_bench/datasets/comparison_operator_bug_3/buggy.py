def is_distance_function(func_name):
    """Check if a function is a distance function. Bug: uses 'in' instead of '=='."""
    distance = func_name in 'distance'
    return distance
