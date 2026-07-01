"""Tests for OS-level process sandbox planning."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from opennova.security.audit import SecurityAuditLogger
from opennova.tools.shell_tools import ExecuteCommandTool


class Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_process_sandbox_auto_selects_seatbelt_on_darwin(tmp_path: Path):
    from opennova.security.process_sandbox import ProcessSandbox, ProcessSandboxConfig

    workdir = tmp_path / "work"
    workdir.mkdir()
    allowed = tmp_path / "allowed"
    allowed.mkdir()

    sandbox = ProcessSandbox(
        ProcessSandboxConfig(
            enabled=True,
            backend="auto",
            enforce=True,
            working_dir=str(workdir),
            allowed_paths=[str(allowed)],
            allow_network=False,
            tmp_dir=str(tmp_path / "tmp"),
        ),
        platform_name="Darwin",
        executable_resolver=lambda name: "/usr/bin/sandbox-exec" if name == "sandbox-exec" else None,
    )

    plan = sandbox.wrap(
        command="echo hi",
        argv=["echo", "hi"],
        run_with_shell=False,
        working_dir=str(workdir),
        env={"PATH": "/usr/bin"},
    )

    assert plan.argv[:2] == ["/usr/bin/sandbox-exec", "-f"]
    assert plan.argv[-2:] == ["echo", "hi"]
    profile_text = Path(plan.argv[2]).read_text(encoding="utf-8")
    assert "(deny network*)" in profile_text
    assert "signal*" not in profile_text
    assert f"(subpath \"{workdir}\")" in profile_text
    assert f"(subpath \"{allowed}\")" in profile_text
    assert plan.metadata["backend"] == "seatbelt"
    assert plan.metadata["applied"] is True
    assert plan.metadata["network_allowed"] is False


def test_process_sandbox_auto_selects_bubblewrap_on_linux(tmp_path: Path):
    from opennova.security.process_sandbox import ProcessSandbox, ProcessSandboxConfig

    workdir = tmp_path / "work"
    workdir.mkdir()
    allowed = tmp_path / "allowed"
    allowed.mkdir()

    sandbox = ProcessSandbox(
        ProcessSandboxConfig(
            enabled=True,
            backend="auto",
            enforce=True,
            working_dir=str(workdir),
            allowed_paths=[str(allowed)],
            allow_network=False,
            tmp_dir=str(tmp_path / "tmp"),
        ),
        platform_name="Linux",
        executable_resolver=lambda name: "/usr/bin/bwrap" if name in {"bwrap", "bubblewrap"} else None,
    )

    plan = sandbox.wrap(
        command="python -V",
        argv=["python", "-V"],
        run_with_shell=False,
        working_dir=str(workdir),
        env={"PATH": "/usr/bin"},
    )

    assert plan.argv[0] == "/usr/bin/bwrap"
    assert "--unshare-net" in plan.argv
    assert _contains_sequence(plan.argv, ["--bind", str(workdir), str(workdir)])
    assert _contains_sequence(plan.argv, ["--bind", str(allowed), str(allowed)])
    assert _contains_sequence(plan.argv, ["--chdir", str(workdir)])
    assert plan.argv[-2:] == ["python", "-V"]
    assert plan.metadata["backend"] == "bubblewrap"
    assert plan.metadata["applied"] is True


def test_process_sandbox_backend_unavailable_blocks_when_enforced(tmp_path: Path):
    from opennova.security.process_sandbox import (
        ProcessSandbox,
        ProcessSandboxConfig,
        ProcessSandboxError,
    )

    workdir = tmp_path / "work"
    workdir.mkdir()
    sandbox = ProcessSandbox(
        ProcessSandboxConfig(
            enabled=True,
            backend="bubblewrap",
            enforce=True,
            working_dir=str(workdir),
        ),
        platform_name="Linux",
        executable_resolver=lambda name: None,
    )

    with pytest.raises(ProcessSandboxError, match="not available"):
        sandbox.wrap(
            command="echo hi",
            argv=["echo", "hi"],
            run_with_shell=False,
            working_dir=str(workdir),
            env={},
        )


def test_process_sandbox_backend_unavailable_falls_back_when_not_enforced(tmp_path: Path):
    from opennova.security.process_sandbox import ProcessSandbox, ProcessSandboxConfig

    workdir = tmp_path / "work"
    workdir.mkdir()
    sandbox = ProcessSandbox(
        ProcessSandboxConfig(
            enabled=True,
            backend="bubblewrap",
            enforce=False,
            working_dir=str(workdir),
        ),
        platform_name="Linux",
        executable_resolver=lambda name: None,
    )

    plan = sandbox.wrap(
        command="echo hi",
        argv=["echo", "hi"],
        run_with_shell=False,
        working_dir=str(workdir),
        env={},
    )

    assert plan.argv == ["echo", "hi"]
    assert plan.metadata["applied"] is False
    assert "not available" in plan.metadata["fallback_reason"]


def test_seatbelt_shell_fallback_runs_shell_inside_sandbox(tmp_path: Path):
    from opennova.security.process_sandbox import ProcessSandbox, ProcessSandboxConfig

    workdir = tmp_path / "work"
    workdir.mkdir()
    sandbox = ProcessSandbox(
        ProcessSandboxConfig(enabled=True, backend="seatbelt", enforce=True, working_dir=str(workdir)),
        platform_name="Darwin",
        executable_resolver=lambda name: "/usr/bin/sandbox-exec" if name == "sandbox-exec" else None,
    )

    plan = sandbox.wrap(
        command="echo hi | cat",
        argv=None,
        run_with_shell=True,
        working_dir=str(workdir),
        env={},
    )

    assert plan.argv[-3:] == ["/bin/sh", "-lc", "echo hi | cat"]


def test_execute_command_sync_uses_process_sandbox_argv(tmp_path: Path):
    tool = ExecuteCommandTool(
        config={
            "working_dir": str(tmp_path),
            "process_sandbox": {"enabled": True, "backend": "none"},
        }
    )

    with patch("opennova.tools.shell_tools.subprocess.run") as mock_run:
        mock_run.return_value = Completed(returncode=0, stdout="ok\n")
        result = tool.execute("echo hi")

    assert result.success is True
    assert mock_run.call_args.args[0] == ["echo", "hi"]
    assert mock_run.call_args.kwargs["shell"] is False
    assert result.metadata["process_sandbox"]["backend"] == "none"
    assert result.metadata["process_sandbox"]["applied"] is False


@pytest.mark.asyncio
async def test_execute_command_async_uses_process_sandbox_argv(tmp_path: Path):
    tool = ExecuteCommandTool(
        config={
            "working_dir": str(tmp_path),
            "process_sandbox": {"enabled": True, "backend": "none"},
        }
    )

    class DummyProcess:
        returncode = 0

        async def communicate(self):
            return b"ok\n", b""

    with patch("opennova.tools.shell_tools.asyncio.create_subprocess_exec") as mock_exec:
        mock_exec.return_value = DummyProcess()
        result = await tool.execute_async("echo hi")

    assert result.success is True
    assert mock_exec.call_args.args[:2] == ("echo", "hi")
    assert result.metadata["process_sandbox"]["backend"] == "none"


def test_security_audit_records_process_sandbox_metadata(tmp_path: Path):
    audit_path = tmp_path / "security.jsonl"
    logger = SecurityAuditLogger(path=audit_path)

    logger.log_tool_event(
        tool_name="execute_command",
        arguments={"command": "echo hi"},
        result=type(
            "Result",
            (),
            {
                "success": True,
                "error": None,
                "metadata": {
                    "process_sandbox": {
                        "enabled": True,
                        "backend": "seatbelt",
                        "applied": True,
                    }
                },
            },
        )(),
    )

    event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert event["result"]["process_sandbox"]["backend"] == "seatbelt"
    assert event["result"]["process_sandbox"]["applied"] is True


def _contains_sequence(items: list[str], sequence: list[str]) -> bool:
    return any(items[index : index + len(sequence)] == sequence for index in range(len(items)))
