"""Tests for OPENNOVA.md project guide initialization and routing."""

import os
import tempfile
from pathlib import Path

from opennova.memory.project import ProjectMemory
from opennova.memory.project_guide import GUIDE_FILENAME, ProjectGuideManager
from opennova.runtime.agent import AgentRuntime
from opennova.runtime.loop import ReActLoop
from opennova.runtime.state import AgentState
from opennova.tools.base import ToolRegistry
from opennova.tools.project_guide_tool import InitProjectGuideTool


def test_project_guide_create_skip_and_force_overwrite():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "pyproject.toml").write_text(
            '[project]\nname = "demo-app"\ndescription = "Demo app"\n'
            'dependencies = ["openai>=1.0.0", "textual>=0.52.0"]\n',
            encoding="utf-8",
        )
        (root / "README.md").write_text("Use OPENAI_API_KEY\n", encoding="utf-8")

        manager = ProjectGuideManager(project_path=root)

        created = manager.create_or_skip()
        assert created.status == "created"
        assert (root / GUIDE_FILENAME).exists()

        guide = (root / GUIDE_FILENAME).read_text(encoding="utf-8")
        assert "## 项目概述" in guide
        assert "## 技术栈" in guide
        assert "demo-app" in guide
        assert "OPENAI_API_KEY" in guide

        skipped = manager.create_or_skip()
        assert skipped.status == "skipped"

        overwritten = manager.create_or_skip(force=True)
        assert overwritten.status == "overwritten"
        assert overwritten.overwritten is True


def test_project_guide_load_for_context_truncates():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        path = root / GUIDE_FILENAME
        path.write_text("# Title\n\n" + ("A" * 120), encoding="utf-8")

        manager = ProjectGuideManager(project_path=root)
        loaded = manager.load_for_context(max_chars=40)

        assert loaded is not None
        assert "[... OPENNOVA.md content truncated" in loaded


def test_project_guide_high_confidence_detection():
    manager = ProjectGuideManager(project_path=".")

    assert manager.is_high_confidence_init_request("初始化这个项目")
    assert manager.is_high_confidence_init_request("initialize project guide")
    assert manager.is_high_confidence_init_request("create OPENNOVA.md for this repo")
    assert not manager.is_high_confidence_init_request("初始化这个项目并修复测试失败")
    assert not manager.is_high_confidence_init_request("please fix this bug in parser")


def test_react_loop_routes_init_only_when_missing_file():
    class DummyProvider:
        model = "dummy"

        async def chat(self, messages, tools=None, **kwargs):
            raise NotImplementedError

        async def stream_chat(self, messages, tools=None, **kwargs):
            if False:
                yield None

    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = Path.cwd()
        try:
            os.chdir(tmpdir)
            registry = ToolRegistry()
            registry.clear()
            registry.register(InitProjectGuideTool(config={"working_dir": tmpdir}))
            loop = ReActLoop(
                llm=DummyProvider(),
                tool_registry=registry,
                state=AgentState(),
                stream=False,
            )

            action = loop._route_task_to_project_init("初始化这个项目")
            assert action is not None
            assert action.tool_name == "init_project_guide"

            Path(GUIDE_FILENAME).write_text("# existing", encoding="utf-8")
            loop2 = ReActLoop(
                llm=DummyProvider(),
                tool_registry=registry,
                state=AgentState(),
                stream=False,
            )
            assert loop2._route_task_to_project_init("初始化这个项目") is None
            assert loop2._route_task_to_project_init("fix failing tests") is None
        finally:
            os.chdir(old_cwd)


def test_runtime_memory_includes_project_guide_context():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / GUIDE_FILENAME).write_text(
            "# OPENNOVA\n\n## 编码规范\n- Prefer small commits\n",
            encoding="utf-8",
        )

        runtime = AgentRuntime.__new__(AgentRuntime)
        runtime.project_memory = ProjectMemory(project_path=tmpdir)

        messages = AgentRuntime._build_memory_messages(runtime, "Refactor loop")
        assert messages
        assert "Project guide (OPENNOVA.md)" in messages[0].content
        assert "Prefer small commits" in messages[0].content
