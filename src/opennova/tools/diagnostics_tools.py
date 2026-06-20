"""Diagnostics tools for source code checks."""

from __future__ import annotations

import ast
import py_compile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opennova.security.sandbox import Sandbox, SandboxConfig
from opennova.tools.base import BaseTool, ToolResult

IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}


@dataclass
class PythonSymbol:
    name: str
    kind: str
    file: str
    line: int
    end_line: int
    context: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "file": self.file,
            "line": self.line,
            "end_line": self.end_line,
            "context": self.context,
        }


class PythonASTIndexer:
    """Small AST indexer for Python symbols and references."""

    def __init__(self, sandbox: Sandbox):
        self.sandbox = sandbox

    def python_files(self, path: str) -> tuple[bool, str | list[Path]]:
        allowed, reason = self.sandbox.is_path_allowed(path)
        if not allowed:
            return False, reason
        target = Path(path).resolve()
        if not target.exists():
            return False, f"Path does not exist: {path}"
        files = [target] if target.is_file() else sorted(target.rglob("*.py"))
        filtered = []
        for file_path in files:
            if file_path.suffix != ".py" or any(part in IGNORED_DIRS for part in file_path.parts):
                continue
            allowed, _ = self.sandbox.is_path_allowed(file_path)
            if allowed:
                filtered.append(file_path)
        return True, filtered

    def collect_symbols(self, path: str) -> tuple[bool, str | list[PythonSymbol]]:
        ok, files_or_error = self.python_files(path)
        if not ok:
            return False, str(files_or_error)

        symbols: list[PythonSymbol] = []
        for file_path in files_or_error:
            source = file_path.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            lines = source.splitlines()
            for node in ast.walk(tree):
                symbol = self._symbol_from_node(node, file_path, lines)
                if symbol:
                    symbols.append(symbol)
        return True, symbols

    def collect_references(self, symbol: str, path: str, max_results: int) -> tuple[bool, str | list[dict[str, Any]]]:
        ok, files_or_error = self.python_files(path)
        if not ok:
            return False, str(files_or_error)

        refs: list[dict[str, Any]] = []
        for file_path in files_or_error:
            source = file_path.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            lines = source.splitlines()
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == symbol:
                    refs.append(
                        {
                            "file": str(file_path),
                            "line": node.lineno,
                            "column": node.col_offset,
                            "context": lines[node.lineno - 1].strip() if node.lineno <= len(lines) else "",
                        }
                    )
                    if len(refs) >= max_results:
                        return True, refs
        return True, refs

    def _symbol_from_node(self, node: ast.AST, file_path: Path, lines: list[str]) -> PythonSymbol | None:
        kind = ""
        name = ""
        if isinstance(node, ast.ClassDef):
            kind = "class"
            name = node.name
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "function"
            name = node.name
        elif isinstance(node, ast.Import):
            kind = "import"
            name = node.names[0].asname or node.names[0].name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.names:
            kind = "import"
            name = node.names[0].asname or node.names[0].name
        elif isinstance(node, ast.Assign):
            target = node.targets[0] if node.targets else None
            if isinstance(target, ast.Name):
                kind = "assignment"
                name = target.id

        if not kind or not name or not hasattr(node, "lineno"):
            return None

        line = int(getattr(node, "lineno", 1))
        end_line = int(getattr(node, "end_lineno", line))
        context = lines[line - 1].strip() if 0 < line <= len(lines) else ""
        return PythonSymbol(
            name=name,
            kind=kind,
            file=str(file_path),
            line=line,
            end_line=end_line,
            context=context,
        )


class _PythonCodeTool(BaseTool):
    """Shared sandbox/indexer setup for Python code understanding tools."""

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.sandbox = Sandbox(
            SandboxConfig(
                working_dir=str(self.config.get("working_dir", Path.cwd())),
                allowed_paths=self.config.get("allowed_paths", []),
                read_only=True,
            )
        )
        self.indexer = PythonASTIndexer(self.sandbox)

    def is_read_only(self, **kwargs: Any) -> bool:
        return True


class PythonDiagnosticsTool(_PythonCodeTool):
    """Run lightweight Python diagnostics without starting a full LSP server."""

    name = "python_diagnostics"
    search_hint = "Check Python files for syntax diagnostics"
    description = "Check a Python file or directory for syntax diagnostics using py_compile."

    def execute(self, path: str = ".") -> ToolResult:
        allowed, reason = self.sandbox.is_path_allowed(path)
        if not allowed:
            return ToolResult(success=False, output="", error=reason)

        target = Path(path).resolve()
        if not target.exists():
            return ToolResult(success=False, output="", error=f"Path does not exist: {path}")

        files = [target] if target.is_file() else sorted(target.rglob("*.py"))
        diagnostics: list[dict[str, Any]] = []

        for file_path in files:
            allowed, _ = self.sandbox.is_path_allowed(file_path)
            if not allowed or not file_path.is_file() or file_path.suffix != ".py":
                continue
            try:
                py_compile.compile(str(file_path), doraise=True)
            except py_compile.PyCompileError as e:
                diagnostics.append(
                    {
                        "file": str(file_path),
                        "message": str(e.exc_value),
                        "type": type(e.exc_value).__name__ if e.exc_value else "PyCompileError",
                    }
                )

        if diagnostics:
            output = "\n".join(
                f"{item['file']}: {item['type']}: {item['message']}"
                for item in diagnostics
            )
            return ToolResult(
                success=False,
                output=output,
                error=f"{len(diagnostics)} Python diagnostic(s) found; first: {diagnostics[0]['type']}",
                metadata={"diagnostics": diagnostics},
            )

        return ToolResult(
            success=True,
            output=f"No Python syntax diagnostics found in {path}",
            metadata={"diagnostics": []},
        )


class PythonSymbolsTool(_PythonCodeTool):
    """List Python symbols from a file or directory."""

    name = "python_symbols"
    search_hint = "List Python classes, functions, imports, and assignments"
    description = "List Python symbols from a file or directory using static AST analysis."

    def execute(self, path: str = ".") -> ToolResult:
        ok, symbols_or_error = self.indexer.collect_symbols(path)
        if not ok:
            return ToolResult(success=False, output="", error=str(symbols_or_error))

        symbols = [symbol.to_dict() for symbol in symbols_or_error]
        output = "\n".join(
            f"{item['file']}:{item['line']}: {item['kind']} {item['name']}"
            for item in symbols
        ) or "No Python symbols found."
        return ToolResult(success=True, output=output, metadata={"symbols": symbols})


class PythonDefinitionTool(_PythonCodeTool):
    """Find the first Python definition for a symbol."""

    name = "python_definition"
    search_hint = "Find where a Python symbol is defined"
    description = "Find a Python symbol definition using static AST analysis."

    def execute(self, symbol: str, path: str = ".") -> ToolResult:
        ok, symbols_or_error = self.indexer.collect_symbols(path)
        if not ok:
            return ToolResult(success=False, output="", error=str(symbols_or_error))

        for item in symbols_or_error:
            if item.name == symbol and item.kind in {"class", "function", "assignment", "import"}:
                return ToolResult(
                    success=True,
                    output=f"{item.file}:{item.line}: {item.context}",
                    metadata={"definition": item.to_dict()},
                )
        return ToolResult(success=False, output="", error=f"Definition not found: {symbol}", metadata={"definition": None})


class PythonReferencesTool(_PythonCodeTool):
    """Find references to a Python symbol."""

    name = "python_references"
    search_hint = "Find Python references to a symbol"
    description = "Find references to a Python symbol using static AST name analysis."

    def execute(self, symbol: str, path: str = ".", max_results: int = 100) -> ToolResult:
        ok, refs_or_error = self.indexer.collect_references(symbol, path, max_results=max_results)
        if not ok:
            return ToolResult(success=False, output="", error=str(refs_or_error))

        refs = refs_or_error
        output = "\n".join(
            f"{item['file']}:{item['line']}: {item['context']}"
            for item in refs
        ) or f"No references found: {symbol}"
        return ToolResult(
            success=True,
            output=output,
            metadata={"references": refs, "count": len(refs), "symbol": symbol},
        )
