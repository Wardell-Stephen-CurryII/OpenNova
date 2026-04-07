"""
Task Output Utilities - File path management for task outputs.

Provides utilities for:
- Generating task output file paths
- Managing output directories
- Reading/writing task outputs
"""

import os
from pathlib import Path


def get_task_output_dir() -> Path:
    """Get the directory where task outputs are stored."""
    # Use XDG_DATA_HOME or ~/.local/share
    data_home = os.environ.get("XDG_DATA_HOME")
    if data_home:
        base = Path(data_home) / "opennova"
    else:
        base = Path.home() / ".local" / "share" / "opennova"

    output_dir = base / "task_outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def get_task_output_path(task_id: str) -> str:
    """
    Get the output file path for a task.

    Args:
        task_id: Task identifier

    Returns:
        Absolute path to task output file
    """
    output_dir = get_task_output_dir()
    return str(output_dir / f"{task_id}.txt")


def write_task_output(task_id: str, content: str, offset: int = 0) -> int:
    """
    Write content to task output file.

    Args:
        task_id: Task identifier
        content: Content to write
        offset: Write position (0 = append)

    Returns:
        New offset after writing
    """
    output_path = get_task_output_path(task_id)

    if offset == 0:
        mode = "a"
    else:
        mode = "r+"

    try:
        with open(output_path, mode, encoding="utf-8") as f:
            if offset > 0:
                f.seek(offset)
            written = f.write(content)
            new_offset = f.tell()
        return new_offset
    except Exception:
        return offset


def read_task_output(task_id: str, max_length: int = 10000, offset: int = 0) -> tuple[str, int]:
    """
    Read task output from file.

    Args:
        task_id: Task identifier
        max_length: Maximum bytes to read
        offset: Starting read position

    Returns:
        Tuple of (content, new_offset)
    """
    output_path = get_task_output_path(task_id)

    if not os.path.exists(output_path):
        return "", offset

    try:
        with open(output_path, "r", encoding="utf-8") as f:
            f.seek(offset)
            content = f.read(max_length)
            new_offset = f.tell()
        return content, new_offset
    except Exception:
        return "", offset


def delete_task_output(task_id: str) -> bool:
    """
    Delete task output file.

    Args:
        task_id: Task identifier

    Returns:
        True if file was deleted
    """
    output_path = get_task_output_path(task_id)
    try:
        if os.path.exists(output_path):
            os.remove(output_path)
            return True
    except Exception:
        pass
    return False


def get_task_output_size(task_id: str) -> int:
    """
    Get size of task output file in bytes.

    Args:
        task_id: Task identifier

    Returns:
        File size in bytes, 0 if not found
    """
    output_path = get_task_output_path(task_id)
    try:
        return os.path.getsize(output_path)
    except Exception:
        return 0
