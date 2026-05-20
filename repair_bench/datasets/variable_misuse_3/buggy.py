import os


def get_file_stat(root_dir_alias, fpath):
    """Get lstat of a file. Bug: uses root_dir_alias instead of fpath."""
    lstat = os.lstat(root_dir_alias)
    return lstat
