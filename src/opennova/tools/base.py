"""
Base Tool System - Abstract base class and registry for all tools.

This module provides the foundation for OpenNova's tool system:
- BaseTool: Abstract base class all tools must inherit from
- ToolRegistry: Singleton registry for tool management
- ToolResult: Standard return structure for tool execution
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

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
            json_type = self._python_type_to_json(param_type)

            prop: dict[str, Any] = {"type": json_type}

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
        type_mapping = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            list: "array",
            dict: "object",
        }

        if hasattr(python_type, "__origin__"):
            origin = python_type.__origin__
            if origin is list:
                return "array"
            if origin is dict:
                return "object"
            if origin is str | type(None):
                return "string"

        return type_mapping.get(python_type, "string")

    def __repr__(self) -> str:
        return f"Tool({self.name})"


class ToolRegistry:
    """
    Singleton registry for managing all tools.

    Provides centralized tool registration and retrieval.
    Tools are registered once and accessed globally throughout the application.
    """

    _instance: "ToolRegistry | None" = None
    _tools: dict[str, BaseTool] = {}

    def __new__(cls) -> "ToolRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tools = {}
        return cls._instance

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
        """Reset the singleton instance (mainly for testing)."""
        cls._instance = None

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

    from opennova.tools.file_tools import (
        ReadFileTool,
        WriteFileTool,
        CreateFileTool,
        DeleteFileTool,
        ListDirectoryTool,
    )
    from opennova.tools.shell_tools import ExecuteCommandTool

    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(CreateFileTool())
    registry.register(DeleteFileTool())
    registry.register(ListDirectoryTool())
    registry.register(ExecuteCommandTool())

    return registry
