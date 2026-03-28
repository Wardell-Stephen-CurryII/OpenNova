"""
File operation tools.

Implements tools for file system operations:
- ReadFileTool: Read file contents with optional line range
- WriteFileTool: Write content to a file (overwrites)
- CreateFileTool: Create a new file
- DeleteFileTool: Delete a file (requires confirmation)
- ListDirectoryTool: List directory contents
"""

import os
from pathlib import Path
from typing import Any

from opennova.tools.base import BaseTool, ToolResult


MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_OUTPUT_SIZE = 100 * 1024

BINARY_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".so",
    ".dll",
    ".dylib",
    ".exe",
    ".bin",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".7z",
    ".mp3",
    ".mp4",
    ".avi",
    ".mov",
    ".wav",
    ".sqlite",
    ".db",
}


def _is_binary_file(file_path: str) -> bool:
    """Check if file is likely binary based on extension or content."""
    ext = Path(file_path).suffix.lower()
    if ext in BINARY_EXTENSIONS:
        return True

    if not os.path.exists(file_path):
        return False

    try:
        with open(file_path, "rb") as f:
            chunk = f.read(1024)
            return b"\x00" in chunk
    except Exception:
        return False


def _validate_path(file_path: str, working_dir: str | None = None) -> tuple[bool, str, str | None]:
    """
    Validate file path for safety.

    Args:
        file_path: Path to validate
        working_dir: Optional working directory to restrict to

    Returns:
        Tuple of (is_valid, resolved_path, error_message)
    """
    try:
        path = Path(file_path).resolve()
    except Exception as e:
        return False, "", f"Invalid path: {e}"

    if not path.exists() and "delete" not in file_path:
        return False, str(path), f"Path does not exist: {file_path}"

    if working_dir:
        work_path = Path(working_dir).resolve()
        try:
            path.relative_to(work_path)
        except ValueError:
            return False, str(path), f"Path outside allowed directory: {file_path}"

    return True, str(path), None


def _truncate_output(output: str, max_size: int = MAX_OUTPUT_SIZE) -> str:
    """Truncate output if too large."""
    if len(output) > max_size:
        return (
            output[: max_size // 2]
            + f"\n\n... [truncated {len(output) - max_size} bytes] ...\n\n"
            + output[-max_size // 2 :]
        )
    return output


class ReadFileTool(BaseTool):
    """Read file contents with optional line range support."""

    name = "read_file"
    description = (
        "Read the contents of a file. "
        "Returns file content with line numbers. "
        "Optionally specify start_line and end_line to read a range."
    )

    def execute(
        self,
        file_path: str,
        start_line: int = 1,
        end_line: int = -1,
    ) -> ToolResult:
        """
        Read file contents.

        Args:
            file_path: Path to the file to read
            start_line: First line to read (1-indexed, default: 1)
            end_line: Last line to read (-1 for end of file)

        Returns:
            ToolResult with file content
        """
        is_valid, resolved_path, error = _validate_path(file_path)
        if not is_valid:
            return ToolResult(success=False, output="", error=error)

        if _is_binary_file(resolved_path):
            return ToolResult(
                success=False,
                output="",
                error=f"Cannot read binary file: {file_path}",
            )

        try:
            file_size = os.path.getsize(resolved_path)
            if file_size > MAX_FILE_SIZE:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"File too large: {file_size} bytes (max: {MAX_FILE_SIZE})",
                )

            with open(resolved_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            total_lines = len(lines)

            if end_line == -1 or end_line > total_lines:
                end_line = total_lines

            start_idx = max(0, start_line - 1)
            end_idx = min(end_line, total_lines)

            selected_lines = lines[start_idx:end_idx]

            numbered_lines = [
                f"{i + start_idx + 1:6}: {line.rstrip()}"
                for i, line in enumerate(selected_lines)
            ]

            output = "\n".join(numbered_lines)

            if start_line > 1 or end_line < total_lines:
                header = f"File: {file_path} (lines {start_idx + 1}-{end_idx} of {total_lines})\n\n"
            else:
                header = f"File: {file_path} ({total_lines} lines)\n\n"

            full_output = header + _truncate_output(output)

            return ToolResult(
                success=True,
                output=full_output,
                metadata={
                    "file_path": resolved_path,
                    "total_lines": total_lines,
                    "lines_read": end_line - start_line + 1,
                    "file_size": file_size,
                },
            )

        except PermissionError:
            return ToolResult(
                success=False,
                output="",
                error=f"Permission denied: {file_path}",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Failed to read file: {e}",
            )


class WriteFileTool(BaseTool):
    """Write content to a file, overwriting if exists."""

    name = "write_file"
    description = (
        "Write content to a file. "
        "This will overwrite the existing file if it exists. "
        "Use with caution as this operation is destructive."
    )

    def execute(self, file_path: str, content: str) -> ToolResult:
        """
        Write content to a file.

        Args:
            file_path: Path to the file to write
            content: Content to write to the file

        Returns:
            ToolResult indicating success or failure
        """
        try:
            path = Path(file_path).resolve()
        except Exception as e:
            return ToolResult(success=False, output="", error=f"Invalid path: {e}")

        try:
            path.parent.mkdir(parents=True, exist_ok=True)

            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

            return ToolResult(
                success=True,
                output=f"Successfully wrote {len(content)} bytes to {file_path}",
                metadata={
                    "file_path": str(path),
                    "bytes_written": len(content),
                },
            )

        except PermissionError:
            return ToolResult(
                success=False,
                output="",
                error=f"Permission denied: {file_path}",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Failed to write file: {e}",
            )


class CreateFileTool(BaseTool):
    """Create a new empty file or file with initial content."""

    name = "create_file"
    description = (
        "Create a new file. "
        "Optionally provide initial content. "
        "Fails if file already exists."
    )

    def execute(self, file_path: str, content: str = "") -> ToolResult:
        """
        Create a new file.

        Args:
            file_path: Path to the file to create
            content: Optional initial content

        Returns:
            ToolResult indicating success or failure
        """
        try:
            path = Path(file_path).resolve()
        except Exception as e:
            return ToolResult(success=False, output="", error=f"Invalid path: {e}")

        if path.exists():
            return ToolResult(
                success=False,
                output="",
                error=f"File already exists: {file_path}",
            )

        try:
            path.parent.mkdir(parents=True, exist_ok=True)

            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

            return ToolResult(
                success=True,
                output=f"Created file: {file_path}",
                metadata={"file_path": str(path), "bytes_written": len(content)},
            )

        except PermissionError:
            return ToolResult(
                success=False,
                output="",
                error=f"Permission denied: {file_path}",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Failed to create file: {e}",
            )


class DeleteFileTool(BaseTool):
    """Delete a file (requires confirmation)."""

    name = "delete_file"
    description = (
        "Delete a file. "
        "This is a destructive operation and requires confirmation. "
        "Use with extreme caution."
    )

    def execute(self, file_path: str, confirm: bool = False) -> ToolResult:
        """
        Delete a file.

        Args:
            file_path: Path to the file to delete
            confirm: Must be True to confirm deletion

        Returns:
            ToolResult indicating success or failure
        """
        if not confirm:
            return ToolResult(
                success=False,
                output="Deletion not confirmed. Set confirm=True to proceed.",
                error="Deletion requires confirmation",
                metadata={"requires_confirmation": True},
            )

        is_valid, resolved_path, error = _validate_path(file_path)
        if not is_valid:
            return ToolResult(success=False, output="", error=error)

        try:
            path = Path(resolved_path)
            if not path.is_file():
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Not a file: {file_path}",
                )

            path.unlink()

            return ToolResult(
                success=True,
                output=f"Deleted file: {file_path}",
                metadata={"file_path": resolved_path},
            )

        except PermissionError:
            return ToolResult(
                success=False,
                output="",
                error=f"Permission denied: {file_path}",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Failed to delete file: {e}",
            )


class ListDirectoryTool(BaseTool):
    """List directory contents."""

    name = "list_directory"
    description = (
        "List the contents of a directory. "
        "Returns files and subdirectories with their types."
    )

    def execute(
        self,
        directory: str = ".",
        recursive: bool = False,
        max_depth: int = 3,
    ) -> ToolResult:
        """
        List directory contents.

        Args:
            directory: Directory path to list (default: current directory)
            recursive: Whether to list recursively
            max_depth: Maximum depth for recursive listing

        Returns:
            ToolResult with directory listing
        """
        try:
            path = Path(directory).resolve()
        except Exception as e:
            return ToolResult(success=False, output="", error=f"Invalid path: {e}")

        if not path.exists():
            return ToolResult(
                success=False,
                output="",
                error=f"Directory does not exist: {directory}",
            )

        if not path.is_dir():
            return ToolResult(
                success=False,
                output="",
                error=f"Not a directory: {directory}",
            )

        def format_entry(entry: Path, depth: int = 0) -> str:
            """Format a single directory entry."""
            prefix = "  " * depth
            if entry.is_dir():
                return f"{prefix}📁 {entry.name}/"
            elif entry.is_symlink():
                return f"{prefix}🔗 {entry.name} -> {entry.resolve()}"
            else:
                size = entry.stat().st_size
                return f"{prefix}📄 {entry.name} ({size} bytes)"

        def list_recursive(p: Path, depth: int = 0) -> list[str]:
            """Recursively list directory."""
            if depth > max_depth:
                return []

            entries = []
            try:
                for entry in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name)):
                    entries.append(format_entry(entry, depth))
                    if entry.is_dir() and recursive and not entry.name.startswith("."):
                        entries.extend(list_recursive(entry, depth + 1))
            except PermissionError:
                entries.append("  " * depth + "⚠️  Permission denied")

            return entries

        try:
            if recursive:
                lines = [f"Directory: {directory} (recursive, max depth: {max_depth})\n"]
                lines.extend(list_recursive(path))
            else:
                lines = [f"Directory: {directory}\n"]
                for entry in sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name)):
                    lines.append(format_entry(entry))

            output = "\n".join(lines)
            return ToolResult(
                success=True,
                output=_truncate_output(output),
                metadata={"directory": str(path), "recursive": recursive},
            )

        except PermissionError:
            return ToolResult(
                success=False,
                output="",
                error=f"Permission denied: {directory}",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Failed to list directory: {e}",
            )
