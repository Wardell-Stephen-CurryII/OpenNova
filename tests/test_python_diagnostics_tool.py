"""Tests for Python diagnostics tool."""

from __future__ import annotations

import asyncio
from pathlib import Path


def test_python_diagnostics_reports_syntax_error(tmp_path: Path):
    from opennova.tools.diagnostics_tools import PythonDiagnosticsTool

    target = tmp_path / "broken.py"
    target.write_text("def broken(:\n    pass\n", encoding="utf-8")

    result = PythonDiagnosticsTool(config={"working_dir": str(tmp_path)}).execute(str(target))

    assert result.success is False
    assert "SyntaxError" in (result.error or result.output)
    assert result.metadata["diagnostics"]


def test_runtime_registers_python_diagnostics_tool(tmp_path: Path, monkeypatch):
    from opennova.runtime.agent import AgentRuntime

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
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

    assert "python_diagnostics" in runtime.get_tools()
    asyncio.run(runtime.aclose())
