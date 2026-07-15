"""OS-level process sandbox command wrapping."""

from __future__ import annotations

import os
import platform
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

ProcessSandboxBackend = Literal["auto", "seatbelt", "bubblewrap", "none"]


class ProcessSandboxError(RuntimeError):
    """Raised when a required process sandbox cannot be applied."""


@dataclass
class ProcessSandboxConfig:
    """Configuration for OS-level command sandboxing."""

    enabled: bool = True
    backend: ProcessSandboxBackend = "auto"
    enforce: bool = False
    working_dir: str = "."
    allowed_paths: list[str] = field(default_factory=list)
    allow_network: bool = True
    tmp_dir: str | None = None
    extra_read_roots: list[str] = field(default_factory=list)
    extra_writable_roots: list[str] = field(default_factory=list)

    @classmethod
    def from_config(
        cls,
        sandbox_config: dict[str, Any] | None,
        *,
        working_dir: str,
        allowed_paths: list[str] | None = None,
        allow_network: bool = True,
        tmp_dir: str | None = None,
    ) -> ProcessSandboxConfig:
        data = sandbox_config or {}
        backend_value = str(data.get("backend", "auto"))
        if backend_value not in {"auto", "seatbelt", "bubblewrap", "none"}:
            raise ValueError(f"Unsupported process sandbox backend: {backend_value}")
        return cls(
            enabled=bool(data.get("enabled", True)),
            backend=cast(ProcessSandboxBackend, backend_value),
            enforce=bool(data.get("enforce", False)),
            working_dir=working_dir,
            allowed_paths=list(allowed_paths or []),
            allow_network=allow_network,
            tmp_dir=data.get("tmp_dir", tmp_dir),
            extra_read_roots=[str(path) for path in data.get("extra_read_roots", [])],
            extra_writable_roots=[str(path) for path in data.get("extra_writable_roots", [])],
        )


@dataclass
class ProcessSandboxPlan:
    """Wrapped command details for subprocess execution."""

    argv: list[str]
    cwd: str
    env: dict[str, str]
    metadata: dict[str, Any]
    cleanup_paths: list[str] = field(default_factory=list)

    def cleanup(self) -> None:
        """Remove temporary sandbox artifacts after process termination."""
        for value in self.cleanup_paths:
            try:
                Path(value).unlink(missing_ok=True)
            except OSError:
                continue


class ProcessSandbox:
    """Build platform-specific sandbox argv for command execution."""

    def __init__(
        self,
        config: ProcessSandboxConfig,
        *,
        platform_name: str | None = None,
        executable_resolver: Callable[[str], str | None] | None = None,
    ):
        self.config = config
        self.platform_name = platform_name or platform.system()
        self.executable_resolver = executable_resolver or shutil.which

    def wrap(
        self,
        *,
        command: str,
        argv: list[str] | None,
        run_with_shell: bool,
        working_dir: str,
        env: dict[str, str],
    ) -> ProcessSandboxPlan:
        """Return argv/cwd/env to execute, wrapped by the selected sandbox if available."""
        original_argv = self._command_argv(command, argv, run_with_shell)
        metadata = self._base_metadata(applied=False)

        if not self.config.enabled:
            metadata["fallback_reason"] = "process sandbox disabled"
            return ProcessSandboxPlan(original_argv, working_dir, env, metadata)

        backend, executable = self._resolve_backend()
        metadata["backend"] = backend
        if backend == "none":
            metadata["fallback_reason"] = "process sandbox backend disabled"
            return ProcessSandboxPlan(original_argv, working_dir, env, metadata)

        if not executable:
            reason = f"process sandbox backend not available: {backend}"
            if self.config.enforce:
                raise ProcessSandboxError(reason)
            metadata["fallback_reason"] = reason
            return ProcessSandboxPlan(original_argv, working_dir, env, metadata)

        if backend == "seatbelt":
            wrapped = self._wrap_seatbelt(executable, original_argv, working_dir, metadata)
        elif backend == "bubblewrap":
            wrapped = self._wrap_bubblewrap(executable, original_argv, working_dir, metadata)
        else:
            wrapped = original_argv
            metadata["fallback_reason"] = f"unsupported process sandbox backend: {backend}"

        cleanup_paths = []
        if metadata.get("profile_path"):
            cleanup_paths.append(str(metadata["profile_path"]))
        return ProcessSandboxPlan(wrapped, working_dir, env, metadata, cleanup_paths)

    def _base_metadata(self, *, applied: bool) -> dict[str, Any]:
        writable_roots = self._writable_roots()
        return {
            "enabled": self.config.enabled,
            "backend": self.config.backend,
            "enforced": self.config.enforce,
            "applied": applied,
            "network_allowed": self.config.allow_network,
            "writable_roots": writable_roots,
            "readable_roots": self._effective_read_roots(),
            "tmp_dir": self._tmp_dir(),
            "fallback_reason": None,
            "fallback_visible": True,
        }

    @staticmethod
    def _command_argv(command: str, argv: list[str] | None, run_with_shell: bool) -> list[str]:
        if run_with_shell:
            return ["/bin/sh", "-lc", command]
        return list(argv or [])

    def _resolve_backend(self) -> tuple[str, str | None]:
        backend = self.config.backend
        if backend == "auto":
            if self.platform_name == "Darwin":
                return "seatbelt", self.executable_resolver("sandbox-exec")
            if self.platform_name == "Linux":
                return "bubblewrap", (
                    self.executable_resolver("bwrap") or self.executable_resolver("bubblewrap")
                )
            return "none", None
        if backend == "seatbelt":
            return "seatbelt", self.executable_resolver("sandbox-exec")
        if backend == "bubblewrap":
            return "bubblewrap", (
                self.executable_resolver("bwrap") or self.executable_resolver("bubblewrap")
            )
        return "none", None

    def _wrap_seatbelt(
        self,
        executable: str,
        argv: list[str],
        working_dir: str,
        metadata: dict[str, Any],
    ) -> list[str]:
        profile_text = self._seatbelt_profile_text()
        tmp_dir = Path(self._tmp_dir())
        tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, profile_path = tempfile.mkstemp(prefix="opennova_", suffix=".sb", dir=str(tmp_dir))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(profile_text)
        metadata.update(
            {
                "backend": "seatbelt",
                "applied": True,
                "profile_path": profile_path,
            }
        )
        return [executable, "-f", profile_path, *argv]

    def _seatbelt_profile_text(self) -> str:
        lines = [
            "(version 1)",
            "(deny default)",
            "(allow process*)",
            "(allow sysctl-read)",
            "(deny file-write*)",
        ]
        for root in self._effective_read_roots():
            lines.append(f'(allow file-read* (subpath "{_escape_seatbelt_path(root)}"))')
        for root in self._writable_roots():
            lines.append(f'(allow file-write* (subpath "{_escape_seatbelt_path(root)}"))')
        if self.config.allow_network:
            lines.append("(allow network*)")
        else:
            lines.append("(deny network*)")
        return "\n".join(lines) + "\n"

    def _wrap_bubblewrap(
        self,
        executable: str,
        argv: list[str],
        working_dir: str,
        metadata: dict[str, Any],
    ) -> list[str]:
        wrapped = [executable, "--die-with-parent", "--proc", "/proc", "--dev", "/dev"]
        if not self.config.allow_network:
            wrapped.append("--unshare-net")

        for root in self._read_roots():
            if Path(root).exists():
                wrapped.extend(["--ro-bind", root, root])
        for root in self._writable_roots():
            Path(root).mkdir(parents=True, exist_ok=True)
            wrapped.extend(["--bind", root, root])

        tmp_dir = self._tmp_dir()
        Path(tmp_dir).mkdir(parents=True, exist_ok=True)
        wrapped.extend(["--tmpfs", "/tmp", "--setenv", "TMPDIR", "/tmp", "--chdir", working_dir])
        wrapped.extend(argv)
        metadata.update({"backend": "bubblewrap", "applied": True})
        return wrapped

    def _read_roots(self) -> list[str]:
        roots = [
            "/System",
            "/Library",
            "/usr",
            "/bin",
            "/sbin",
            "/lib",
            "/lib64",
            "/etc",
            "/dev",
            "/opt",
            "/nix",
            "/private/var/db",
            "/private/var/select",
        ]
        roots.extend(self.config.extra_read_roots)
        return _dedupe_paths(roots)

    def _effective_read_roots(self) -> list[str]:
        """Return system read roots plus all explicitly writable locations."""
        return _dedupe_paths([*self._read_roots(), *self._writable_roots()])

    def _writable_roots(self) -> list[str]:
        roots = [
            self.config.working_dir,
            *self.config.allowed_paths,
            *self.config.extra_writable_roots,
        ]
        roots.append(self._tmp_dir())
        return _dedupe_paths(str(Path(root).expanduser().resolve()) for root in roots if root)

    def _tmp_dir(self) -> str:
        return str(Path(self.config.tmp_dir or tempfile.gettempdir()).expanduser().resolve())


def _dedupe_paths(paths: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        normalized = str(Path(path).expanduser().resolve())
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _escape_seatbelt_path(path: str) -> str:
    return path.replace("\\", "\\\\").replace('"', '\\"')
