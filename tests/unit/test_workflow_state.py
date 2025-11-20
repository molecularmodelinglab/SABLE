import pytest

from schemas.state import WorkflowState


def test_model_copy_preserves_event_callback():
    captured = []

    def handler(event):
        captured.append(event)

    state = WorkflowState(user_prompt="stream me")
    setattr(state, "_event_callback", handler)

    cloned = state.model_copy()

    cloned.log("test_action", {"foo": "bar"})

    assert captured, "Expected event callback to fire for cloned state"
    assert captured[0]["action"] == "test_action"
    assert captured[0]["data"] == {"foo": "bar"}


def test_registry_fallback_invokes_event_callback():
    captured = []

    def handler(event):
        captured.append(event)

    state = WorkflowState(user_prompt="registry fallback test")
    WorkflowState.register_event_callback(state.workflow_id, handler)
    try:
        state.log("test_action", {"value": 42})
    finally:
        WorkflowState.unregister_event_callback(state.workflow_id)

    assert captured, "Expected registry fallback to deliver event callback"
    assert captured[0]["action"] == "test_action"
    assert captured[0]["data"] == {"value": 42}
