from datetime import datetime, timezone
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from pydantic import ValidationError

from server.models.provider_credential import ProviderCredential
from server.models.user import User
from server.schemas.provider_credential import ProviderCredentialResponse
from server.schemas.run import RunCreateRequest
from server.services.credential_service import (
    CredentialConfigurationError,
    CredentialService,
)
from server.services.run_launcher import _validate_execution_access
from server.routers import provider_credentials as credential_router


def test_credential_encryption_round_trip(monkeypatch):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_MASTER_KEY", Fernet.generate_key().decode("ascii"))
    service = CredentialService()

    encrypted = service.encrypt("boltz-secret-key")

    assert encrypted != b"boltz-secret-key"
    assert service.decrypt(encrypted) == "boltz-secret-key"


def test_credential_encryption_fails_closed_without_master_key(monkeypatch):
    monkeypatch.delenv("PROVIDER_CREDENTIAL_MASTER_KEY", raising=False)

    with pytest.raises(CredentialConfigurationError):
        CredentialService().encrypt("boltz-secret-key")


def test_credential_response_never_contains_secret_fields():
    credential = ProviderCredential(
        id=uuid4(),
        user_id=uuid4(),
        provider="boltz_platform",
        name="Research key",
        encrypted_secret=b"ciphertext",
        key_hint="1234",
        status="active",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    response = ProviderCredentialResponse.model_validate(credential)

    payload = response.model_dump(mode="json")
    assert "api_key" not in payload
    assert "encrypted_secret" not in payload
    assert payload["key_hint"] == "1234"


def test_platform_run_requires_credential_and_rejects_affinity():
    with pytest.raises(ValidationError):
        RunCreateRequest(
            prompt="screen molecules",
            characterization={"boltz": {"provider": "platform"}},
        )

    with pytest.raises(ValidationError):
        RunCreateRequest(
            prompt="screen molecules",
            characterization={
                "boltz": {
                    "provider": "platform",
                    "credential_id": str(uuid4()),
                    "metrics": ["binding_affinity"],
                },
            },
        )


def test_run_omits_characterization_for_account_resolution():
    request = RunCreateRequest(prompt="optimize QED")

    assert request.characterization is None


def test_worker_rechecks_self_hosted_access_before_execution():
    configuration = {"boltz": {"provider": "self_hosted"}}
    denied_user = User(extra_metadata={"roles": []})

    with pytest.raises(HTTPException) as error:
        _validate_execution_access(object(), denied_user, configuration)

    assert error.value.status_code == 403
    assert error.value.detail == "Self-hosted Boltz access has not been approved"

    approved_user = User(extra_metadata={
        "roles": [],
        "boltz_self_hosted_access": {"status": "approved"},
    })
    _validate_execution_access(object(), approved_user, configuration)


def test_delete_rejects_credential_used_by_active_job(monkeypatch):
    credential = ProviderCredential(id=uuid4(), user_id=uuid4())
    user = type("UserStub", (), {"id": credential.user_id})()
    monkeypatch.setattr(credential_router, "_get_owned_or_404", lambda *args: credential)
    monkeypatch.setattr(
        credential_router.provider_job_service,
        "has_active_jobs",
        lambda *args: True,
    )

    with pytest.raises(HTTPException) as error:
        credential_router.delete_provider_credential(credential.id, user, object())

    assert error.value.status_code == 409