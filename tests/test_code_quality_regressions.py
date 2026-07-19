"""Behavior regressions covered while keeping the repository Ruff-clean."""

import json
from enum import StrEnum
from pathlib import Path

import pytest

import opennova.diff as diff_package
import opennova.planning as planning_package
import opennova.runtime as runtime_package
import opennova.security as security_package
import opennova.tools as tools_package
from opennova.diff.engine import DiffEngine
from opennova.diff.parser import ChangeType
from opennova.memory.types.feedback_memory import FeedbackType
from opennova.memory.working import ActionStatus
from opennova.security.sandbox import Sandbox, SandboxConfig
from opennova.tasks import Task, TaskManager, TaskStatus, TaskType


@pytest.mark.parametrize(
    ("member", "value"),
    [
        (ChangeType.MODIFY, "modify"),
        (FeedbackType.APPROVAL, "approval"),
        (ActionStatus.SUCCESS, "success"),
        (TaskType.LOCAL_AGENT, "local_agent"),
        (TaskStatus.PENDING, "pending"),
    ],
)
def test_public_string_enums_keep_wire_values(member, value):
    assert isinstance(member, StrEnum)
    assert member == value
    assert member.value == value
    assert str(member) == value
    assert json.loads(json.dumps(member)) == value


def test_task_string_enums_round_trip_through_persistence(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    task = Task(
        id="task-round-trip",
        type=TaskType.LOCAL_WORKFLOW,
        description="Verify task persistence",
        status=TaskStatus.RUNNING,
    )

    restored = Task.from_dict(json.loads(json.dumps(task.to_dict())))

    assert restored.type is TaskType.LOCAL_WORKFLOW
    assert restored.status is TaskStatus.RUNNING
    assert restored.to_dict()["type"] == "local_workflow"
    assert restored.to_dict()["status"] == "running"


def test_diff_apply_keeps_context_and_added_lines():
    diff_text = "@@ -1,2 +1,3 @@\n alpha\n+middle\n omega"

    result = DiffEngine()._apply_diff("alpha\nomega\n", diff_text)

    assert result == "alpha\nmiddle\nomega\n"


def test_sandbox_backup_failures_do_not_block_write_or_delete(monkeypatch, tmp_path):
    write_target = tmp_path / "write.txt"
    delete_target = tmp_path / "delete.txt"
    write_target.write_text("before", encoding="utf-8")
    delete_target.write_text("remove me", encoding="utf-8")
    sandbox = Sandbox(SandboxConfig(working_dir=str(tmp_path)))

    def fail_backup_read(self, *args, **kwargs):
        raise OSError(f"Cannot back up {self}")

    monkeypatch.setattr(Path, "read_text", fail_backup_read)

    write_success, _ = sandbox.safe_write(write_target, "after", backup=True)
    delete_success, _ = sandbox.safe_delete(delete_target, backup=True)

    assert write_success is True
    assert write_target.read_bytes() == b"after"
    assert delete_success is True
    assert not delete_target.exists()
    assert sandbox._original_files == {}


@pytest.mark.asyncio
async def test_task_cleanup_failure_is_suppressed_and_callback_removed(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    manager = TaskManager()
    task = manager.create_task(TaskType.LOCAL_AGENT, "Cleanup failure")
    manager.update_task_status(task.id, TaskStatus.RUNNING)
    cleanup_calls = []

    def failing_cleanup():
        cleanup_calls.append(task.id)
        raise RuntimeError("cleanup failed")

    manager.set_cleanup_callback(task.id, failing_cleanup)

    stopped = await manager.stop_task(task.id)

    assert stopped is True
    assert task.status is TaskStatus.KILLED
    assert cleanup_calls == [task.id]
    assert task.id not in manager._cleanup_callbacks


def test_public_package_exports_still_import():
    assert diff_package.DiffEngine.__name__ == "DiffEngine"
    assert planning_package.Planner.__name__ == "Planner"
    assert runtime_package.AgentRuntime.__name__ == "AgentRuntime"
    assert security_package.Guardrails.__name__ == "Guardrails"
    assert tools_package.BaseTool.__name__ == "BaseTool"
