
import os
import tempfile
from pathlib import Path
from buggy import get_file_stat


def test_lstat_uses_correct_path():
    """Bug: os.lstat(root_dir_alias) always stats the directory, not the file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a file inside the temp directory
        filepath = os.path.join(tmpdir, "test_file.txt")
        Path(filepath).write_text("test content")

        try:
            result = get_file_stat(tmpdir, filepath)
            # Correct: stats filepath (regular file)
            # Bug: stats tmpdir (directory)
            import stat
            if stat.S_ISDIR(result.st_mode):
                pytest.fail("get_file_stat returned directory stat (bug: used root_dir_alias instead of fpath)")
            assert stat.S_ISREG(result.st_mode), "Expected regular file stat"
        except FileNotFoundError:
            pytest.fail("get_file_stat raised FileNotFoundError")
