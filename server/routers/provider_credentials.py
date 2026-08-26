from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.audit import AuditEventType, AuditSeverity, audit_logger
from server.auth.dependencies import get_current_active_user
from server.database import get_db
from server.models.provider_credential import ProviderCredential
from server.models.user import User
from server.schemas.provider_credential import (
    ProviderCredentialCreate,
    ProviderCredentialResponse,
    ProviderCredentialUpdate,
)
from server.services.credential_service import (
    CredentialConfigurationError,
    CredentialValidationUnavailable,
    credential_service,
)
from server.services.provider_job_service import provider_job_service


router = APIRouter(prefix="/provider-credentials", tags=["provider-credentials"])


def _get_owned_or_404(db: Session, credential_id: UUID, user_id: UUID) -> ProviderCredential:
    credential = credential_service.get_owned(db, credential_id, user_id)
    if credential is None:
        raise HTTPException(status_code=404, detail="Provider credential not found")
    return credential


def _audit(event_type: AuditEventType, user: User, credential: ProviderCredential) -> None:
    audit_logger.log(
        event_type=event_type,
        message=f"Provider credential {event_type.value}",
        user_id=str(user.id),
        username=user.username,
        resource_type="provider_credential",
        resource_id=str(credential.id),
        details={"provider": credential.provider, "status": credential.status},
    )


@router.post("", response_model=ProviderCredentialResponse, status_code=status.HTTP_201_CREATED)
def create_provider_credential(
    payload: ProviderCredentialCreate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    secret = payload.api_key.get_secret_value().strip()
    try:
        credential = ProviderCredential(
            user_id=current_user.id,
            provider=payload.provider,
            name=payload.name.strip(),
            encrypted_secret=credential_service.encrypt(secret),
            key_hint=secret[-4:],
            status="unverified",
        )
    except CredentialConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if payload.validate_credential:
        try:
            credential_service.apply_validation(credential, secret)
        except CredentialValidationUnavailable:
            pass

    db.add(credential)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A credential with this name already exists") from exc
    db.refresh(credential)
    _audit(AuditEventType.PROVIDER_CREDENTIAL_CREATED, current_user, credential)
    return credential


@router.get("", response_model=List[ProviderCredentialResponse])
def list_provider_credentials(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    return db.query(ProviderCredential).filter(
        ProviderCredential.user_id == current_user.id,
    ).order_by(ProviderCredential.created_at.desc()).all()


@router.patch("/{credential_id}", response_model=ProviderCredentialResponse)
def update_provider_credential(
    credential_id: UUID,
    payload: ProviderCredentialUpdate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    credential = _get_owned_or_404(db, credential_id, current_user.id)
    event_type = AuditEventType.PROVIDER_CREDENTIAL_RENAMED
    if payload.name is not None:
        credential.name = payload.name.strip()
    if payload.api_key is not None:
        secret = payload.api_key.get_secret_value().strip()
        try:
            credential.encrypted_secret = credential_service.encrypt(secret)
        except CredentialConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        credential.key_hint = secret[-4:]
        credential.status = "unverified"
        credential.last_validated_at = None
        event_type = AuditEventType.PROVIDER_CREDENTIAL_REPLACED
        if payload.validate_credential:
            try:
                credential_service.apply_validation(credential, secret)
            except CredentialValidationUnavailable:
                pass

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A credential with this name already exists") from exc
    db.refresh(credential)
    _audit(event_type, current_user, credential)
    return credential


@router.post("/{credential_id}/validate", response_model=ProviderCredentialResponse)
def validate_provider_credential(
    credential_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    credential = _get_owned_or_404(db, credential_id, current_user.id)
    try:
        secret = credential_service.decrypt(credential.encrypted_secret)
    except CredentialConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        error = credential_service.apply_validation(credential, secret)
    except CredentialValidationUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    db.commit()
    db.refresh(credential)
    _audit(AuditEventType.PROVIDER_CREDENTIAL_VALIDATED, current_user, credential)
    if error:
        raise HTTPException(status_code=400, detail=error)
    return credential


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider_credential(
    credential_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    credential = _get_owned_or_404(db, credential_id, current_user.id)
    if provider_job_service.has_active_jobs(db, credential.id):
        raise HTTPException(
            status_code=409,
            detail="Credential is in use by an active provider job",
        )
    _audit(AuditEventType.PROVIDER_CREDENTIAL_REVOKED, current_user, credential)
    db.delete(credential)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)