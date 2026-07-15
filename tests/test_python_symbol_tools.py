"""Tests for AST-based Python symbol tools."""

from __future__ import annotations

import asyncio
from pathlib import Path


def _write_sample(root: Path) -> Path:
    target = root / "sample.py"
    target.write_text(
        """
import os

CONSTANT = 1

class Greeter:
    def hello(self):
        return helper()

def helper():
    return os.getcwd()

def caller():
    return helper()
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return target


def test_python_symbols_returns_structured_ast_symbols(tmp_path: Path):
    from opennova.tools.diagnostics_tools import PythonSymbolsTool

    _write_sample(tmp_path)
    result = PythonSymbolsTool(config={"working_dir": str(tmp_path)}).execute(str(tmp_path))

    assert result.success is True
    names = {symbol["name"] for symbol in result.metadata["symbols"]}
    assert {"Greeter", "hello", "helper", "caller", "CONSTANT", "os"}.issubset(names)


def test_python_definition_returns_location_and_context(tmp_path: Path):
    from opennova.tools.diagnostics_tools import PythonDefinitionTool

    _write_sample(tmp_path)
    result = PythonDefinitionTool(config={"working_dir": str(tmp_path)}).execute(
        "helper", str(tmp_path)
    )

    assert result.success is True
    assert result.metadata["definition"]["name"] == "helper"
    assert result.metadata["definition"]["kind"] == "function"
    assert "def helper" in result.output


def test_python_references_returns_limited_references(tmp_path: Path):
    from opennova.tools.diagnostics_tools import PythonReferencesTool

    _write_sample(tmp_path)
    result = PythonReferencesTool(config={"working_dir": str(tmp_path)}).execute(
        "helper",
        str(tmp_path),
        max_results=1,
    )

    assert result.success is True
    assert result.metadata["count"] == 1
    assert "helper" in result.output


def test_python_symbol_tools_reject_outside_sandbox(tmp_path: Path):
    from opennova.tools.diagnostics_tools import PythonSymbolsTool

    outside = tmp_path.parent / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")

    result = PythonSymbolsTool(config={"working_dir": str(tmp_path)}).execute(str(outside))

    assert result.success is False
    assert "outside allowed directories" in (result.error or "").lower()


def test_runtime_registers_python_symbol_tools(tmp_path: Path, monkeypatch):
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

    tools = set(runtime.get_tools())
    assert {"python_symbols", "python_definition", "python_references"}.issubset(tools)
    asyncio.run(runtime.aclose())
