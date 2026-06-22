"""Diagnostics tools for source code checks."""

from __future__ import annotations

import ast
import py_compile
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opennova.security.sandbox import Sandbox, SandboxConfig
from opennova.tools.base import BaseTool, ToolResult

IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}


def detect_python_analysis_backend() -> dict[str, Any]:
    """Detect optional Python analysis backends while keeping AST fallback."""
    status = get_python_backend_status()
    return {
        "name": status.backend,
        "available": True,
        "fallback": status.fallback if status.backend != "ast" else None,
    }


@dataclass
class PythonBackendStatus:
    """Availability of optional Python analysis backends."""

    backend: str
    pyright_available: bool
    ruff_available: bool
    fallback: str = "ast"

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "pyright_available": self.pyright_available,
            "ruff_available": self.ruff_available,
            "fallback": self.fallback,
        }


@dataclass
class PythonAnalysisCommandPlan:
    """Planned external Python analyzer command."""

    backend: str
    argv: list[str]
    fallback_reason: str = ""


@dataclass
class PythonAnalysisEvent:
    """Unified event shape for Python analysis results."""

    kind: str
    backend: str
    path: str
    success: bool
    payload: dict[str, Any]


def get_python_backend_status() -> PythonBackendStatus:
    """Return detailed Python analysis backend availability."""
    pyright_available = shutil.which("pyright") is not None
    ruff_available = shutil.which("ruff") is not None
    if pyright_available:
        backend = "pyright"
    elif ruff_available:
        backend = "ruff"
    else:
        backend = "ast"
    return PythonBackendStatus(
        backend=backend,
        pyright_available=pyright_available,
        ruff_available=ruff_available,
    )


class PythonExternalAnalyzer:
    """Plan external analyzer invocations while preserving AST fallback."""

    def __init__(self, status: PythonBackendStatus | None = None):
        self.status = status or get_python_backend_status()

    def plan_diagnostics(self, path: str | Path) -> PythonAnalysisCommandPlan:
        target = str(path)
        if self.status.backend == "pyright" and self.status.pyright_available:
            return PythonAnalysisCommandPlan(
                backend="pyright",
                argv=["pyright", target, "--outputjson"],
            )
        if self.status.backend == "ruff" and self.status.ruff_available:
            return PythonAnalysisCommandPlan(
                backend="ruff",
                argv=["ruff", "check", target, "--output-format=json"],
            )
        return PythonAnalysisCommandPlan(
            backend="ast",
            argv=[],
            fallback_reason="No external Python analyzer available; using AST fallback",
        )

    def run_diagnostics(
        self,
        path: str | Path,
        runner: Any | None = None,
    ) -> dict[str, Any]:
        """Run one external diagnostics command or return AST fallback metadata."""
        plan = self.plan_diagnostics(path)
        if not plan.argv:
            return {
                "backend": plan.backend,
                "success": True,
                "argv": [],
                "output": plan.fallback_reason,
                "error": "",
                "fallback": True,
            }

        if runner is None:
            completed = subprocess.run(
                plan.argv,
                capture_output=True,
                text=True,
                check=False,
            )
        else:
            completed = runner(plan.argv)

        returncode = int(getattr(completed, "returncode", 0) if not isinstance(completed, dict) else completed.get("returncode", 0))
        stdout = str(getattr(completed, "stdout", "") if not isinstance(completed, dict) else completed.get("stdout", ""))
        stderr = str(getattr(completed, "stderr", "") if not isinstance(completed, dict) else completed.get("stderr", ""))
        return {
            "backend": plan.backend,
            "success": returncode == 0,
            "argv": plan.argv,
            "output": stdout,
            "error": stderr,
            "returncode": returncode,
            "fallback": False,
        }

    def event_for_diagnostics(self, path: str | Path, runner: Any | None = None) -> PythonAnalysisEvent:
        """Return a unified diagnostics event."""
        payload = self.run_diagnostics(path, runner=runner)
        return PythonAnalysisEvent(
            kind="diagnostics",
            backend=str(payload.get("backend", self.status.backend)),
            path=str(path),
            success=bool(payload.get("success", False)),
            payload=payload,
        )

    def _symbol_event(self, kind: str, path: str | Path, symbol: str) -> PythonAnalysisEvent:
        payload = {
            "symbol": symbol,
            "backend": self.status.backend,
            "fallback": self.status.backend == "ast",
        }
        return PythonAnalysisEvent(
            kind=kind,
            backend=self.status.backend,
            path=str(path),
            success=True,
            payload=payload,
        )

    def event_for_hover(self, path: str | Path, symbol: str) -> PythonAnalysisEvent:
        """Return a unified hover event placeholder."""
        return self._symbol_event("hover", path, symbol)

    def event_for_definition(self, path: str | Path, symbol: str) -> PythonAnalysisEvent:
        """Return a unified definition event placeholder."""
        return self._symbol_event("definition", path, symbol)

    def event_for_references(self, path: str | Path, symbol: str) -> PythonAnalysisEvent:
        """Return a unified references event placeholder."""
        return self._symbol_event("references", path, symbol)


class PythonAnalysisServerManager:
    """Lightweight lifecycle facade for future Python analysis servers."""

    def __init__(self, backend: str = "ast"):
        self.backend = backend
        self.running = False
        self.analyzer = PythonExternalAnalyzer(
            PythonBackendStatus(
                backend=backend,
                pyright_available=backend == "pyright",
                ruff_available=backend == "ruff",
            )
        )

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def status(self) -> dict[str, object]:
        return {"backend": self.backend, "running": self.running}

    def event_for(self, kind: str, path: str | Path, symbol: str = "") -> PythonAnalysisEvent:
        if kind == "diagnostics":
            return self.analyzer.event_for_diagnostics(path)
        if kind == "hover":
            return self.analyzer.event_for_hover(path, symbol=symbol)
        if kind == "definition":
            return self.analyzer.event_for_definition(path, symbol=symbol)
        if kind == "references":
            return self.analyzer.event_for_references(path, symbol=symbol)
        return PythonAnalysisEvent(
            kind=kind,
            backend=self.backend,
            path=str(path),
            success=False,
            payload={"error": f"Unsupported analysis event: {kind}"},
        )


@dataclass
class PythonSymbol:
    name: str
    kind: str
    file: str
    line: int
    end_line: int
    context: str
    qualified_name: str = ""
    parent: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "qualified_name": self.qualified_name or self.name,
            "parent": self.parent,
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
        self.imports: list[dict[str, Any]] = []

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
        self.imports = []
        for file_path in files_or_error:
            source = file_path.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            lines = source.splitlines()
            self.imports.extend(self._collect_imports_from_tree(tree, file_path, lines))
            symbols.extend(self._collect_symbols_from_tree(tree, file_path, lines))
        return True, symbols

    def resolve_import_definition(
        self,
        symbol: str,
        search_path: str,
        symbols: list[PythonSymbol],
    ) -> PythonSymbol | None:
        """Resolve an import alias to a symbol in another local Python file."""
        target_root = Path(search_path).resolve()
        if target_root.is_file():
            target_root = target_root.parent

        for import_entry in self.imports:
            if import_entry.get("alias") != symbol:
                continue
            module = str(import_entry.get("module") or "")
            imported_name = str(import_entry.get("name") or "")
            if not module or not imported_name:
                continue
            module_file = (target_root / Path(*module.split("."))).with_suffix(".py")
            if not module_file.exists():
                continue
            for item in symbols:
                if Path(item.file).resolve() == module_file.resolve() and imported_name in {
                    item.name,
                    item.qualified_name,
                }:
                    return item
        return None

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

    def _collect_symbols_from_tree(
        self,
        tree: ast.AST,
        file_path: Path,
        lines: list[str],
    ) -> list[PythonSymbol]:
        symbols: list[PythonSymbol] = []

        def visit_body(nodes: list[ast.stmt], parents: list[str]) -> None:
            for node in nodes:
                symbol = self._symbol_from_node(node, file_path, lines, parents)
                next_parents = parents
                if symbol:
                    symbols.append(symbol)
                    if symbol.kind in {"class", "function"}:
                        next_parents = [*parents, symbol.name]
                child_body = getattr(node, "body", None)
                if isinstance(child_body, list):
                    visit_body(child_body, next_parents)

        visit_body(getattr(tree, "body", []), [])
        return symbols

    def _collect_imports_from_tree(
        self,
        tree: ast.AST,
        file_path: Path,
        lines: list[str],
    ) -> list[dict[str, Any]]:
        imports: list[dict[str, Any]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(
                        {
                            "file": str(file_path),
                            "line": node.lineno,
                            "module": alias.name,
                            "name": alias.name.split(".")[0],
                            "alias": alias.asname or alias.name.split(".")[0],
                            "context": lines[node.lineno - 1].strip() if node.lineno <= len(lines) else "",
                        }
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    imports.append(
                        {
                            "file": str(file_path),
                            "line": node.lineno,
                            "module": node.module,
                            "name": alias.name,
                            "alias": alias.asname or alias.name,
                            "context": lines[node.lineno - 1].strip() if node.lineno <= len(lines) else "",
                        }
                    )
        return imports

    def _symbol_from_node(
        self,
        node: ast.AST,
        file_path: Path,
        lines: list[str],
        parents: list[str],
    ) -> PythonSymbol | None:
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
        parent = ".".join(parents)
        qualified_name = ".".join([*parents, name]) if parents else name
        return PythonSymbol(
            name=name,
            kind=kind,
            file=str(file_path),
            line=line,
            end_line=end_line,
            context=context,
            qualified_name=qualified_name,
            parent=parent,
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
        self.backend = detect_python_analysis_backend()

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
            metadata={"diagnostics": [], "backend": self.backend},
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
        return ToolResult(
            success=True,
            output=output,
            metadata={"symbols": symbols, "imports": self.indexer.imports},
        )


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
            names = {item.name, item.qualified_name or item.name}
            if symbol in names and item.kind in {"class", "function", "assignment"}:
                return ToolResult(
                    success=True,
                    output=f"{item.file}:{item.line}: {item.context}",
                    metadata={"definition": item.to_dict()},
                )
        resolved = self.indexer.resolve_import_definition(symbol, path, symbols_or_error)
        if resolved:
            return ToolResult(
                success=True,
                output=f"{resolved.file}:{resolved.line}: {resolved.context}",
                metadata={"definition": resolved.to_dict()},
            )
        for item in symbols_or_error:
            names = {item.name, item.qualified_name or item.name}
            if symbol in names and item.kind == "import":
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
