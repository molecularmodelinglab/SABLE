"""
Custom exceptions for workflow nodes and tools to enable robust error reporting.
"""

from typing import Any, Dict, Optional


class WorkflowError(Exception):
    """Base class for workflow-related errors with structured context."""

    def __init__(
        self,
        message: str,
        *,
        node: Optional[str] = None,
        tool: Optional[str] = None,
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.node = node
        self.tool = tool
        self.code = code
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message": str(self),
            "node": self.node,
            "tool": self.tool,
            "code": self.code,
            "details": self.details,
        }


class NodeError(WorkflowError):
    """Raised when a graph node detects an invalid state or cannot proceed."""


class ToolError(WorkflowError):
    """Raised when a tool fails or returns unusable results."""

