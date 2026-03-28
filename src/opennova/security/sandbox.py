"""
Sandbox - Path and execution sandboxing.

Provides:
- Path confinement to working directory
- Safe file operation wrappers
- Execution environment isolation
"""

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class SandboxConfig:
    """Configuration for sandbox environment."""

    working_dir: str
    allowed_paths: list[str] | None = None
    denied_paths: list[str] | None = None
    max_file_size: int = 100 * 1024 * 1024  # 100MB
    allow_network: bool = False
    read_only: bool = False
    temp_dir: str | None = None


class Sandbox:
    """
    Execution sandbox for safe file operations.

    Features:
    - Confine operations to allowed directories
    - Block access to protected paths
    - Track file modifications
    - Support for rollback
    """

    DEFAULT_DENIED_PATHS = [
        "/etc",
        "/usr",
        "/bin",
        "/sbin",
        "/boot",
        "/dev",
        "/proc",
        "/sys",
        "/root",
    ]

    def __init__(self, config: SandboxConfig):
        """
        Initialize sandbox.

        Args:
            config: Sandbox configuration
        """
        self.config = config
        self.working_dir = Path(config.working_dir).resolve()
        self.allowed_paths = [
            Path(p).resolve() for p in (config.allowed_paths or [])
        ]
        self.denied_paths = [
            Path(p).resolve() for p in (config.denied_paths or self.DEFAULT_DENIED_PATHS)
        ]

        self.modifications: list[dict[str, Any]] = []
        self._original_files: dict[str, str] = {}

    def is_path_allowed(self, path: str | Path) -> tuple[bool, str]:
        """
        Check if a path is allowed within sandbox.

        Args:
            path: Path to check

        Returns:
            Tuple of (is_allowed, reason)
        """
        try:
            target_path = Path(path).resolve()
        except Exception as e:
            return False, f"Invalid path: {e}"

        for denied in self.denied_paths:
            try:
                target_path.relative_to(denied)
                return False, f"Access to protected path denied: {denied}"
            except ValueError:
                pass

        try:
            target_path.relative_to(self.working_dir)
            return True, "Within working directory"
        except ValueError:
            pass

        for allowed in self.allowed_paths:
            try:
                target_path.relative_to(allowed)
                return True, f"Within allowed path: {allowed}"
            except ValueError:
                pass

        return False, f"Path outside allowed directories: {path}"

    def safe_read(self, file_path: str | Path) -> tuple[bool, str | bytes]:
        """
        Safely read a file within sandbox.

        Args:
            file_path: Path to read

        Returns:
            Tuple of (success, content_or_error)
        """
        is_allowed, reason = self.is_path_allowed(file_path)
        if not is_allowed:
            return False, reason

        path = Path(file_path)

        if not path.exists():
            return False, f"File not found: {file_path}"

        if not path.is_file():
            return False, f"Not a file: {file_path}"

        if path.stat().st_size > self.config.max_file_size:
            return False, f"File too large: {path.stat().st_size} bytes"

        try:
            content = path.read_bytes()
            return True, content
        except Exception as e:
            return False, f"Failed to read file: {e}"

    def safe_write(
        self,
        file_path: str | Path,
        content: bytes | str,
        backup: bool = True,
    ) -> tuple[bool, str]:
        """
        Safely write a file within sandbox.

        Args:
            file_path: Path to write
            content: Content to write
            backup: Whether to backup existing file

        Returns:
            Tuple of (success, message)
        """
        if self.config.read_only:
            return False, "Sandbox is in read-only mode"

        is_allowed, reason = self.is_path_allowed(file_path)
        if not is_allowed:
            return False, reason

        path = Path(file_path)

        if isinstance(content, str):
            content = content.encode("utf-8")

        if len(content) > self.config.max_file_size:
            return False, f"Content too large: {len(content)} bytes"

        if backup and path.exists():
            try:
                self._original_files[str(path)] = path.read_text(encoding="utf-8")
            except Exception:
                pass

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

            self.modifications.append(
                {
                    "type": "write",
                    "path": str(path),
                    "size": len(content),
                }
            )

            return True, f"Successfully wrote to {file_path}"

        except Exception as e:
            return False, f"Failed to write file: {e}"

    def safe_delete(
        self,
        file_path: str | Path,
        backup: bool = True,
    ) -> tuple[bool, str]:
        """
        Safely delete a file within sandbox.

        Args:
            file_path: Path to delete
            backup: Whether to backup before deletion

        Returns:
            Tuple of (success, message)
        """
        if self.config.read_only:
            return False, "Sandbox is in read-only mode"

        is_allowed, reason = self.is_path_allowed(file_path)
        if not is_allowed:
            return False, reason

        path = Path(file_path)

        if not path.exists():
            return False, f"File not found: {file_path}"

        if backup and path.is_file():
            try:
                self._original_files[str(path)] = path.read_text(encoding="utf-8")
            except Exception:
                pass

        try:
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)

            self.modifications.append(
                {
                    "type": "delete",
                    "path": str(path),
                }
            )

            return True, f"Successfully deleted {file_path}"

        except Exception as e:
            return False, f"Failed to delete: {e}"

    def create_temp_file(
        self,
        content: bytes | str = "",
        suffix: str = "",
        prefix: str = "sandbox_",
    ) -> tuple[bool, str | Path]:
        """
        Create a temporary file within sandbox temp directory.

        Args:
            content: Initial content
            suffix: File suffix
            prefix: File prefix

        Returns:
            Tuple of (success, path_or_error)
        """
        temp_dir = Path(self.config.temp_dir or tempfile.gettempdir())

        try:
            temp_dir.mkdir(parents=True, exist_ok=True)

            fd, temp_path = tempfile.mkstemp(
                suffix=suffix,
                prefix=prefix,
                dir=str(temp_dir),
            )

            if content:
                if isinstance(content, str):
                    content = content.encode("utf-8")
                os.write(fd, content)

            os.close(fd)

            return True, Path(temp_path)

        except Exception as e:
            return False, f"Failed to create temp file: {e}"

    def rollback(self) -> list[str]:
        """
        Rollback all modifications.

        Returns:
            List of rollback messages
        """
        results = []

        for path, content in self._original_files.items():
            try:
                Path(path).write_text(content, encoding="utf-8")
                results.append(f"Restored: {path}")
            except Exception as e:
                results.append(f"Failed to restore {path}: {e}")

        for mod in reversed(self.modifications):
            if mod["type"] == "write":
                path = Path(mod["path"])
                if not str(path) in self._original_files and path.exists():
                    try:
                        path.unlink()
                        results.append(f"Removed new file: {path}")
                    except Exception:
                        pass

        self.modifications.clear()
        self._original_files.clear()

        return results

    def get_modifications(self) -> list[dict[str, Any]]:
        """Get list of all modifications made."""
        return self.modifications.copy()

    def __repr__(self) -> str:
        return (
            f"Sandbox(working_dir={self.working_dir}, "
            f"modifications={len(self.modifications)}, "
            f"read_only={self.config.read_only})"
        )
