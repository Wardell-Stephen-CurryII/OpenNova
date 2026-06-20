"""
Base Tool System - Abstract base class and registry for all tools.

This module provides the foundation for OpenNova's tool system:
- BaseTool: Abstract base class all tools must inherit from
- ToolRegistry: Singleton registry for tool management
- ToolResult: Standard return structure for tool execution
"""

import types
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Union, get_args, get_origin

from opennova.providers.base import ToolSchema


@dataclass
class ToolResult:
    """
    Standard result structure for all tool executions.

    Attributes:
        success: Whether the tool executed successfully
        output: Human-readable output or result description
        error: Error message if success is False
        metadata: Additional structured data about the execution
    """

    success: bool
    output: str
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_string(self) -> str:
        """Convert result to string for LLM context."""
        if self.success:
            return self.output
        return f"Error: {self.error}\n{self.output}"


@dataclass
class ToolParameter:
    """
    Tool parameter definition.

    Simplified parameter definition that gets converted to JSON Schema.
    """

    type: str
    description: str = ""
    default: Any = None
    required: bool = True
    enum: list[Any] | None = None
    items: dict[str, Any] | None = None
    properties: dict[str, Any] | None = None

    def to_json_schema(self) -> dict[str, Any]:
        """Convert to JSON Schema format."""
        schema: dict[str, Any] = {"type": self.type, "description": self.description}

        if self.default is not None:
            schema["default"] = self.default

        if self.enum:
            schema["enum"] = self.enum

        if self.items:
            schema["items"] = self.items

        if self.properties:
            schema["properties"] = self.properties

        return schema


class BaseTool(ABC):
    """
    Abstract base class for all tools.

    All tools must inherit from this class and implement:
    - execute(): The actual tool logic
    - Optionally override get_schema() for custom parameter definitions

    Example:
        class ReadFileTool(BaseTool):
            name = "read_file"
            description = "Read file contents"

            def execute(self, file_path: str, start_line: int = 1, end_line: int = -1) -> ToolResult:
                try:
                    with open(file_path) as f:
                        ...
                    return ToolResult(success=True, output=content)
                except Exception as e:
                    return ToolResult(success=False, output="", error=str(e))
    """

    name: str = ""
    description: str = ""
    aliases: list[str] = []
    search_hint: str = ""
    max_result_chars: int = 100_000
    progress_metadata: dict[str, Any] = {}

    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize tool with optional configuration."""
        self.config = config or {}

    @abstractmethod
    def execute(self, **kwargs: Any) -> ToolResult:
        """
        Execute the tool with given parameters.

        Args:
            **kwargs: Tool-specific parameters

        Returns:
            ToolResult with success status and output/error
        """
        pass

    def get_parameters_schema(self) -> dict[str, Any]:
        """
        Get the JSON Schema for tool parameters.

        Override this method to define custom parameter schemas.
        Default implementation uses introspection of execute() signature.

        Returns:
            JSON Schema dict for parameters
        """
        import inspect
        from typing import get_type_hints

        sig = inspect.signature(self.execute)
        hints = get_type_hints(self.execute) if hasattr(self, "__class__") else {}

        properties: dict[str, Any] = {}
        required: list[str] = []

        for param_name, param in sig.parameters.items():
            if param_name == "kwargs":
                continue

            param_type = hints.get(param_name, str)
            prop = self._python_type_to_schema(param_type)

            if param.default is inspect.Parameter.empty:
                required.append(param_name)
            else:
                prop["default"] = param.default

            properties[param_name] = prop

        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    def get_schema(self) -> ToolSchema:
        """
        Get complete tool schema for LLM.

        Returns:
            ToolSchema with name, description, and parameters
        """
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=self.get_parameters_schema(),
        )

    @staticmethod
    def _python_type_to_json(python_type: type) -> str:
        """Convert Python type annotation to JSON Schema type."""
        schema = BaseTool._python_type_to_schema(python_type)
        schema_type = schema.get("type")
        return schema_type if isinstance(schema_type, str) else "string"

    @staticmethod
    def _python_type_to_schema(python_type: Any) -> dict[str, Any]:
        """Convert Python type annotation to a JSON Schema fragment."""
        type_mapping = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            list: "array",
            dict: "object",
            Any: "object",
        }

        if python_type is None or python_type is type(None):
            return {"type": "null"}

        if isinstance(python_type, type) and issubclass(python_type, Enum):
            values = [member.value for member in python_type]
            return {"type": BaseTool._json_type_for_values(values), "enum": values}

        origin = get_origin(python_type)
        if origin is not None:
            if origin in (list, tuple, set):
                args = get_args(python_type)
                item_schema = BaseTool._python_type_to_schema(args[0]) if args else {"type": "string"}
                return {"type": "array", "items": item_schema}
            if origin is dict:
                return {"type": "object"}
            if origin is Literal:
                values = list(get_args(python_type))
                return {"type": BaseTool._json_type_for_values(values), "enum": values}
            if origin in (Union, types.UnionType):
                non_none_args = [arg for arg in get_args(python_type) if arg is not type(None)]
                if len(non_none_args) == 1:
                    return BaseTool._python_type_to_schema(non_none_args[0])
                return {
                    "anyOf": [BaseTool._python_type_to_schema(arg) for arg in non_none_args],
                }

        return {"type": type_mapping.get(python_type, "string")}

    @staticmethod
    def _json_type_for_values(values: list[Any]) -> str:
        """Infer a scalar JSON type from enum or literal values."""
        if values and all(isinstance(value, bool) for value in values):
            return "boolean"
        if values and all(isinstance(value, int) and not isinstance(value, bool) for value in values):
            return "integer"
        if values and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            return "number"
        return "string"

    def is_read_only(self, **kwargs: Any) -> bool:
        """Return whether this tool call only reads state."""
        return False

    def is_destructive(self, **kwargs: Any) -> bool:
        """Return whether this tool call can destroy or overwrite data."""
        return False

    def requires_permission(self, **kwargs: Any) -> bool:
        """Return whether this tool call should go through user approval."""
        return self.is_destructive(**kwargs)

    def is_concurrency_safe(self, **kwargs: Any) -> bool:
        """Return whether this tool call can safely run in parallel."""
        return self.is_read_only(**kwargs)

    def __repr__(self) -> str:
        return f"Tool({self.name})"


class ToolRegistry:
    """
    Registry for managing tools.

    Runtime instances should own their registry so tool configuration and
    runtime references do not leak between sessions or child agents.
    """

    _global_registry: "ToolRegistry | None" = None

    def __init__(self, tools: list[BaseTool] | None = None):
        self._tools: dict[str, BaseTool] = {}
        for tool in tools or []:
            self.register(tool)

    @classmethod
    def global_registry(cls) -> "ToolRegistry":
        """Return an explicitly shared registry for legacy/global use cases."""
        if cls._global_registry is None:
            cls._global_registry = cls()
        return cls._global_registry

    def register(self, tool: BaseTool) -> None:
        """
        Register a tool instance.

        Args:
            tool: Tool instance to register
        """
        if not tool.name:
            raise ValueError("Tool must have a name attribute")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        """
        Get a tool by name.

        Args:
            name: Tool name

        Returns:
            Tool instance

        Raises:
            KeyError: If tool is not registered
        """
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not found. Available: {list(self._tools.keys())}")
        return self._tools[name]

    def list_tools(self) -> list[ToolSchema]:
        """
        Get schemas for all registered tools.

        Returns:
            List of ToolSchema for LLM consumption
        """
        return [tool.get_schema() for tool in self._tools.values()]

    def list_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def has_tool(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def unregister(self, name: str) -> bool:
        """
        Remove a tool from the registry.

        Args:
            name: Tool name to remove

        Returns:
            True if tool was removed, False if not found
        """
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def clear(self) -> None:
        """Remove all tools from registry (mainly for testing)."""
        self._tools.clear()

    @classmethod
    def reset(cls) -> None:
        """Reset the explicitly shared registry (mainly for testing)."""
        cls._global_registry = None

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __repr__(self) -> str:
        return f"ToolRegistry({len(self._tools)} tools: {list(self._tools.keys())})"


def register_builtin_tools(registry: ToolRegistry | None = None) -> ToolRegistry:
    """
    Register all built-in tools.

    Args:
        registry: Optional registry to use (creates new one if None)

    Returns:
        The registry with all tools registered
    """
    if registry is None:
        registry = ToolRegistry()

    from opennova.tools.diagnostics_tools import (
        PythonDefinitionTool,
        PythonDiagnosticsTool,
        PythonReferencesTool,
        PythonSymbolsTool,
    )
    from opennova.tools.file_tools import (
        CreateFileTool,
        DeleteFileTool,
        EditFileTool,
        ListDirectoryTool,
        MultiEditFileTool,
        ReadFileTool,
        WriteFileTool,
    )
    from opennova.tools.mcp_resource_tools import ListMCPResourcesTool, ReadMCPResourceTool
    from opennova.tools.project_guide_tool import InitProjectGuideTool
    from opennova.tools.search_tools import GlobFilesTool, GrepCodeTool
    from opennova.tools.shell_tools import ExecuteCommandTool
    from opennova.tools.worktree_tools import EnterWorktreeTool, ExitWorktreeTool

    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(CreateFileTool())
    registry.register(EditFileTool())
    registry.register(MultiEditFileTool())
    registry.register(DeleteFileTool())
    registry.register(ListDirectoryTool())
    registry.register(ExecuteCommandTool())
    registry.register(GlobFilesTool())
    registry.register(GrepCodeTool())
    registry.register(PythonDiagnosticsTool())
    registry.register(PythonSymbolsTool())
    registry.register(PythonDefinitionTool())
    registry.register(PythonReferencesTool())
    registry.register(InitProjectGuideTool())
    registry.register(ListMCPResourcesTool())
    registry.register(ReadMCPResourceTool())
    registry.register(EnterWorktreeTool())
    registry.register(ExitWorktreeTool())

    return registry
