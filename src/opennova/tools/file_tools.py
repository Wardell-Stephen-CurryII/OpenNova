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

from opennova.checkpoints import CheckpointManager
from opennova.diff.engine import DiffEngine
from opennova.security.sandbox import Sandbox, SandboxConfig
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


def _truncate_output(output: str, max_size: int = MAX_OUTPUT_SIZE) -> str:
    """Truncate output if too large."""
    if len(output) > max_size:
        return (
            output[: max_size // 2]
            + f"\n\n... [truncated {len(output) - max_size} bytes] ...\n\n"
            + output[-max_size // 2 :]
        )
    return output


def _build_sandbox(tool_config: dict[str, Any] | None = None) -> Sandbox:
    """Create a sandbox from tool config."""
    config = tool_config or {}
    sandbox_config = SandboxConfig(
        working_dir=str(config.get("working_dir", os.getcwd())),
        allowed_paths=config.get("allowed_paths", []),
        denied_paths=config.get("denied_paths"),
        max_file_size=int(config.get("max_file_size", MAX_FILE_SIZE)),
        allow_network=bool(config.get("allow_network", False)),
        read_only=bool(config.get("read_only", False)),
        temp_dir=config.get("temp_dir"),
    )
    return Sandbox(sandbox_config)


class ReadFileTool(BaseTool):
    """Read file contents with optional line range support."""

    name = "read_file"
    description = (
        "Read the contents of a file. "
        "Returns file content with line numbers. "
        "Optionally specify start_line and end_line to read a range."
    )

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.sandbox = _build_sandbox(config)

    def is_read_only(self, **kwargs: Any) -> bool:
        return True

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
        is_allowed, reason = self.sandbox.is_path_allowed(file_path)
        if not is_allowed:
            return ToolResult(success=False, output="", error=reason)
        resolved_path = str(Path(file_path).resolve())

        if _is_binary_file(resolved_path):
            return ToolResult(
                success=False,
                output="",
                error=f"Cannot read binary file: {file_path}",
            )

        try:
            ok, content_or_error = self.sandbox.safe_read(resolved_path)
            if not ok:
                return ToolResult(success=False, output="", error=str(content_or_error))

            raw_content = content_or_error if isinstance(content_or_error, bytes) else b""
            file_size = len(raw_content)
            text_content = raw_content.decode("utf-8", errors="replace")
            lines = text_content.splitlines(keepends=True)

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

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.sandbox = _build_sandbox(config)

    def is_destructive(self, **kwargs: Any) -> bool:
        return True

    def execute(self, file_path: str, content: str) -> ToolResult:
        """
        Write content to a file.

        Args:
            file_path: Path to the file to write
            content: Content to write to the file

        Returns:
            ToolResult indicating success or failure
        """
        is_allowed, reason = self.sandbox.is_path_allowed(file_path)
        if not is_allowed:
            return ToolResult(success=False, output="", error=reason)
        path = Path(file_path).resolve()

        try:
            old_content = ""
            file_existed = path.exists()
            checkpoint_id: str | None = None
            if file_existed:
                read_ok, read_result = self.sandbox.safe_read(path)
                if not read_ok:
                    return ToolResult(success=False, output="", error=str(read_result))
                old_content = (
                    read_result.decode("utf-8", errors="replace")
                    if isinstance(read_result, bytes)
                    else ""
                )
                if bool(self.config.get("checkpoint_writes", True)):
                    checkpoint_id = CheckpointManager(
                        self.config.get("working_dir", os.getcwd())
                    ).create("before write_file", [path])

            write_ok, write_result = self.sandbox.safe_write(path, content.encode("utf-8"))
            if not write_ok:
                return ToolResult(success=False, output="", error=str(write_result))

            diff_engine = DiffEngine()
            diff_text = diff_engine.generate_diff(old_content, content, str(path))

            metadata: dict[str, Any] = {
                "file_path": str(path),
                "bytes_written": len(content),
                "change_type": "modify" if file_existed else "create",
            }
            if diff_text.strip():
                metadata["diff"] = diff_text
            if checkpoint_id:
                metadata["checkpoint_id"] = checkpoint_id

            return ToolResult(
                success=True,
                output=f"Successfully wrote {len(content)} bytes to {file_path}",
                metadata=metadata,
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

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.sandbox = _build_sandbox(config)

    def is_destructive(self, **kwargs: Any) -> bool:
        return True

    def execute(self, file_path: str, content: str = "") -> ToolResult:
        """
        Create a new file.

        Args:
            file_path: Path to the file to create
            content: Optional initial content

        Returns:
            ToolResult indicating success or failure
        """
        is_allowed, reason = self.sandbox.is_path_allowed(file_path)
        if not is_allowed:
            return ToolResult(success=False, output="", error=reason)
        path = Path(file_path).resolve()

        if path.exists():
            return ToolResult(
                success=False,
                output="",
                error=f"File already exists: {file_path}",
            )

        try:
            write_ok, write_result = self.sandbox.safe_write(path, content.encode("utf-8"))
            if not write_ok:
                return ToolResult(success=False, output="", error=str(write_result))

            metadata: dict[str, Any] = {
                "file_path": str(path),
                "bytes_written": len(content),
                "change_type": "create",
            }
            if content.strip():
                diff_engine = DiffEngine()
                diff_text = diff_engine.generate_diff("", content, str(path))
                if diff_text.strip():
                    metadata["diff"] = diff_text

            return ToolResult(
                success=True,
                output=f"Created file: {file_path}",
                metadata=metadata,
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


class EditFileTool(BaseTool):
    """Replace one exact text occurrence in a file."""

    name = "edit_file"
    search_hint = "Make a precise text replacement in an existing file"
    description = (
        "Edit a file by replacing an exact old_text string with new_text. "
        "Fails if old_text is missing or appears multiple times unless replace_all is true."
    )

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.sandbox = _build_sandbox(config)

    def execute(
        self,
        file_path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
    ) -> ToolResult:
        is_allowed, reason = self.sandbox.is_path_allowed(file_path)
        if not is_allowed:
            return ToolResult(success=False, output="", error=reason)
        path = Path(file_path).resolve()

        if not old_text:
            return ToolResult(success=False, output="", error="old_text must not be empty")
        if _is_binary_file(str(path)):
            return ToolResult(success=False, output="", error=f"Cannot edit binary file: {file_path}")

        read_ok, read_result = self.sandbox.safe_read(path)
        if not read_ok:
            return ToolResult(success=False, output="", error=str(read_result))

        old_content = read_result.decode("utf-8", errors="replace") if isinstance(read_result, bytes) else ""
        occurrences = old_content.count(old_text)
        if occurrences == 0:
            return ToolResult(success=False, output="", error="old_text not found in file")
        if occurrences > 1 and not replace_all:
            return ToolResult(
                success=False,
                output="",
                error=f"old_text appears {occurrences} times; set replace_all=True or provide more context",
            )

        new_content = old_content.replace(old_text, new_text) if replace_all else old_content.replace(old_text, new_text, 1)
        write_ok, write_result = self.sandbox.safe_write(path, new_content.encode("utf-8"))
        if not write_ok:
            return ToolResult(success=False, output="", error=str(write_result))

        diff_text = DiffEngine().generate_diff(old_content, new_content, str(path))
        return ToolResult(
            success=True,
            output=f"Edited file: {file_path}",
            metadata={
                "file_path": str(path),
                "change_type": "edit",
                "occurrences_replaced": occurrences if replace_all else 1,
                "diff": diff_text,
            },
        )

    def is_destructive(self, **kwargs: Any) -> bool:
        return True


class MultiEditFileTool(BaseTool):
    """Apply multiple exact text replacements to a file."""

    name = "multi_edit_file"
    search_hint = "Apply multiple precise replacements to one file"
    description = (
        "Apply multiple exact text replacements to one file. "
        "Each edit requires old_text and new_text, and all edits must match before writing."
    )

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.sandbox = _build_sandbox(config)

    def execute(self, file_path: str, edits: list[dict[str, Any]]) -> ToolResult:
        is_allowed, reason = self.sandbox.is_path_allowed(file_path)
        if not is_allowed:
            return ToolResult(success=False, output="", error=reason)
        path = Path(file_path).resolve()

        if not edits:
            return ToolResult(success=False, output="", error="edits must contain at least one edit")
        if _is_binary_file(str(path)):
            return ToolResult(success=False, output="", error=f"Cannot edit binary file: {file_path}")

        read_ok, read_result = self.sandbox.safe_read(path)
        if not read_ok:
            return ToolResult(success=False, output="", error=str(read_result))

        old_content = read_result.decode("utf-8", errors="replace") if isinstance(read_result, bytes) else ""
        new_content = old_content
        replaced = 0

        for index, edit in enumerate(edits, 1):
            old_text = str(edit.get("old_text", ""))
            new_text = str(edit.get("new_text", ""))
            replace_all = bool(edit.get("replace_all", False))
            if not old_text:
                return ToolResult(success=False, output="", error=f"edit {index}: old_text must not be empty")
            occurrences = new_content.count(old_text)
            if occurrences == 0:
                return ToolResult(success=False, output="", error=f"edit {index}: old_text not found")
            if occurrences > 1 and not replace_all:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"edit {index}: old_text appears {occurrences} times; set replace_all=True",
                )
            new_content = new_content.replace(old_text, new_text) if replace_all else new_content.replace(old_text, new_text, 1)
            replaced += occurrences if replace_all else 1

        write_ok, write_result = self.sandbox.safe_write(path, new_content.encode("utf-8"))
        if not write_ok:
            return ToolResult(success=False, output="", error=str(write_result))

        diff_text = DiffEngine().generate_diff(old_content, new_content, str(path))
        return ToolResult(
            success=True,
            output=f"Edited file: {file_path} ({replaced} replacements)",
            metadata={
                "file_path": str(path),
                "change_type": "multi_edit",
                "occurrences_replaced": replaced,
                "diff": diff_text,
            },
        )

    def is_destructive(self, **kwargs: Any) -> bool:
        return True


class DeleteFileTool(BaseTool):
    """Delete a file (requires confirmation)."""

    name = "delete_file"
    description = (
        "Delete a file. "
        "This is a destructive operation and requires confirmation. "
        "Use with extreme caution."
    )

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.sandbox = _build_sandbox(config)

    def is_destructive(self, **kwargs: Any) -> bool:
        return True

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

        is_allowed, reason = self.sandbox.is_path_allowed(file_path)
        if not is_allowed:
            return ToolResult(success=False, output="", error=reason)
        resolved_path = str(Path(file_path).resolve())

        try:
            path = Path(resolved_path)
            if not path.is_file():
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Not a file: {file_path}",
                )

            ok, delete_result = self.sandbox.safe_delete(path)
            if not ok:
                return ToolResult(success=False, output="", error=str(delete_result))

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

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.sandbox = _build_sandbox(config)

    def is_read_only(self, **kwargs: Any) -> bool:
        return True

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
        is_allowed, reason = self.sandbox.is_path_allowed(path)
        if not is_allowed:
            return ToolResult(success=False, output="", error=reason)

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
                    entry_allowed, _ = self.sandbox.is_path_allowed(entry)
                    if not entry_allowed:
                        continue
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
                    entry_allowed, _ = self.sandbox.is_path_allowed(entry)
                    if not entry_allowed:
                        continue
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
