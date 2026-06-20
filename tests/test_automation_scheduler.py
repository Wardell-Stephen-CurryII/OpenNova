"""Tests for local automation scheduler foundation."""

from __future__ import annotations

from pathlib import Path


def test_local_automation_scheduler_runs_due_task_and_persists(tmp_path: Path):
    from opennova.automation import LocalAutomationScheduler

    now = [100.0]
    calls: list[str] = []
    scheduler = LocalAutomationScheduler(tmp_path / "automations.json", clock=lambda: now[0])
    task_id = scheduler.schedule_interval("check docs", prompt="Review docs", interval_seconds=10)

    due = scheduler.run_due(lambda task: calls.append(task.prompt))

    assert due == [task_id]
    assert calls == ["Review docs"]
    assert scheduler.get(task_id).next_run_at == 110.0

    reloaded = LocalAutomationScheduler(tmp_path / "automations.json", clock=lambda: now[0])
    assert reloaded.get(task_id).name == "check docs"


def test_local_automation_scheduler_skips_future_task(tmp_path: Path):
    from opennova.automation import LocalAutomationScheduler

    scheduler = LocalAutomationScheduler(tmp_path / "automations.json", clock=lambda: 100.0)
    scheduler.schedule_once("later", prompt="Not yet", run_at=200.0)

    assert scheduler.run_due(lambda task: None) == []
