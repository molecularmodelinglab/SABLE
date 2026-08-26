"""Integration tests for administrator analytics endpoints."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from server.models.audit import AuditEvent
from server.models.experiment import Experiment


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.admin
def test_admin_summary_requires_admin(client, auth_headers):
    """Regular users should be forbidden from accessing admin analytics."""
    response = client.get("/admin/analytics/summary", headers=auth_headers)
    assert response.status_code == 403


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.admin
def test_admin_summary_returns_metrics(
    client,
    admin_headers,
    admin_user,
    make_run,
    db_session,
):
    """Administrator receives a well-structured analytics payload."""
    run = make_run(admin_user, status="running")

    experiment = Experiment(
        id=f"exp-{uuid4().hex[:8]}",
        run_id=run.id,
        user_id=admin_user.id,
        session_id=run.session_id,
        workflow_name="test_workflow",
        status="success",
        prompt="Test prompt",
        parameters={},
        parsed_arguments={},
        targets=[],
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(experiment)

    audit_event = AuditEvent(
        event_type="USER_LOGIN",
        timestamp=datetime.now(timezone.utc),
        user_id=admin_user.id,
        message="Admin login",
        severity="INFO",
        success=True,
        details={},
    )
    db_session.add(audit_event)
    db_session.commit()

    response = client.get("/admin/analytics/summary", headers=admin_headers)
    assert response.status_code == 200

    data = response.json()
    assert set(data.keys()) == {"generated_at", "users", "runs", "experiments", "sessions", "audit"}

    user_metrics = data["users"]
    assert user_metrics["admins"] >= 1
    assert user_metrics["total"] >= 1

    run_metrics = data["runs"]
    assert run_metrics["total"] >= 1
    assert isinstance(run_metrics["by_status"], dict)

    experiment_metrics = data["experiments"]
    assert experiment_metrics["total"] >= 1

    audit_metrics = data["audit"]
    assert "total_last_7_days" in audit_metrics
    assert "top_events_last_30_days" in audit_metrics


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.admin
def test_boltz_access_request_and_admin_approval(
    client,
    auth_headers,
    admin_headers,
    test_user,
):
    requested = client.post("/auth/boltz-access-request", headers=auth_headers)
    assert requested.status_code == 200
    assert requested.json()["access_status"] == "pending"
    assert requested.json()["can_use_self_hosted"] is False

    forbidden = client.patch(
        f"/admin/users/{test_user.id}/boltz-access",
        headers=auth_headers,
        json={"status": "approved"},
    )
    assert forbidden.status_code == 403

    approved = client.patch(
        f"/admin/users/{test_user.id}/boltz-access",
        headers=admin_headers,
        json={"status": "approved"},
    )
    assert approved.status_code == 200
    payload = approved.json()
    assert payload["access_status"] == "approved"
    assert payload["can_use_self_hosted"] is True
    assert payload["provider"] == "self_hosted"
    assert payload["metrics"] == ["binding_affinity"]

    queue = client.get("/admin/users/boltz-access", headers=admin_headers)
    assert queue.status_code == 200
    assert any(item["user_id"] == str(test_user.id) for item in queue.json())