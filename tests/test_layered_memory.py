"""Tests for layered project memory files."""

from __future__ import annotations

import tempfile
from pathlib import Path

from opennova.memory.layered import LayeredMemoryManager
from opennova.memory.project import ProjectMemory
from opennova.runtime.agent import AgentRuntime


def test_layered_memory_loads_markdown_files_with_labels_and_dedupes(tmp_path: Path):
    memory_dir = tmp_path / ".opennova" / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "workflow.md").write_text("Use TDD for bug fixes.\n", encoding="utf-8")
    (memory_dir / "duplicate.md").write_text("Use TDD for bug fixes.\n", encoding="utf-8")
    (memory_dir / "architecture.md").write_text("Runtime owns tool registry.\n", encoding="utf-8")

    content = LayeredMemoryManager(tmp_path).load_for_context()

    assert "Memory file: .opennova/memory/architecture.md" in content
    assert "Runtime owns tool registry." in content
    assert content.count("Use TDD for bug fixes.") == 1


def test_layered_memory_truncates_to_context_budget(tmp_path: Path):
    memory_dir = tmp_path / ".opennova" / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "large.md").write_text("x" * 200, encoding="utf-8")

    content = LayeredMemoryManager(tmp_path).load_for_context(max_chars=80)

    assert len(content) > 80
    assert "[... .opennova/memory content truncated for context budget ...]" in content


def test_runtime_memory_includes_layered_memory_context():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        memory_dir = root / ".opennova" / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "workflow.md").write_text("Always run targeted tests first.\n", encoding="utf-8")

        runtime = AgentRuntime(
            {
                "default_provider": "deepseek",
                "providers": {"deepseek": {"api_key": "test-key", "default_model": "deepseek-v4-pro"}},
                "mcp": {"enabled": False, "servers": []},
                "skills": {"enabled": False, "dirs": []},
            },
            enable_mcp=False,
            enable_skills=False,
        )
        runtime.project_memory = ProjectMemory(project_path=tmpdir)

        messages = AgentRuntime._build_memory_messages(runtime, "Run tests")

        assert "Layered project memory (.opennova/memory)" in messages[0].content
        assert "Always run targeted tests first." in messages[0].content
