"""
Lightweight telemetry helpers for structured logging across nodes and tools.
"""

from datetime import datetime
from typing import Any, Dict, Optional


def emit_event(
    state,
    *,
    kind: str,
    node: Optional[str] = None,
    tool: Optional[str] = None,
    severity: str = "info",
    data: Optional[Dict[str, Any]] = None,
):
    """Append a structured event to state.logs and print a compact line.

    Args:
        state: WorkflowState-like object with `.logs` and `.log()` method.
        kind: Short event type identifier (e.g., 'validation_failed').
        node: Node name (if applicable).
        tool: Tool name (if applicable).
        severity: 'info' | 'warning' | 'error'.
        data: Arbitrary payload.
    """
    payload = {
        "event": kind,
        "node": node,
        "tool": tool,
        "severity": severity,
        "data": data or {},
    }
    # Persist on state
    try:
        state.log(kind, payload)
    except Exception:
        # Fallback if state.log signature changes
        ts = datetime.now().isoformat()
        state.logs.append({"timestamp": ts, **payload})

    # Echo a concise line to console for live tracing
    tag = node or tool or "workflow"
    print(f"[{severity.upper()}][{tag}] {kind}: {data}")

