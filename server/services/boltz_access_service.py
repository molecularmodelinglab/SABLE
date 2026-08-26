from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from server.models.user import User
from server.schemas.boltz_access import (
    AdminBoltzUserResponse,
    BoltzSettingsResponse,
    BoltzSettingsUpdate,
)
from server.schemas.run import BoltzRunConfiguration, CharacterizationRunConfiguration
from server.services.credential_service import credential_service


class BoltzAccessService:
    ACCESS_KEY = "boltz_self_hosted_access"
    SETTINGS_KEY = "boltz_settings"

    @staticmethod
    def _metadata(user: User) -> dict:
        return dict(user.extra_metadata) if isinstance(user.extra_metadata, dict) else {}

    def get_settings(self, user: User) -> BoltzSettingsResponse:
        metadata = self._metadata(user)
        access = metadata.get(self.ACCESS_KEY, {})
        access = access if isinstance(access, dict) else {}
        settings = metadata.get(self.SETTINGS_KEY, {})
        settings = settings if isinstance(settings, dict) else {}
        is_admin = user.has_role("admin")
        access_status = "approved" if is_admin else access.get("status", "not_requested")

        provider = settings.get("provider")
        if provider not in {"self_hosted", "platform"}:
            provider = "self_hosted" if is_admin else None

        return BoltzSettingsResponse(
            access_status=access_status,
            can_use_self_hosted=is_admin or access_status == "approved",
            requested_at=access.get("requested_at"),
            reviewed_at=access.get("reviewed_at"),
            provider=provider,
            credential_id=settings.get("credential_id"),
            execution_preference=settings.get("execution_preference", "auto"),
            metrics=settings.get("metrics", []),
        )

    def request_self_hosted_access(self, db: Session, user: User) -> BoltzSettingsResponse:
        current = self.get_settings(user)
        if current.can_use_self_hosted or current.access_status == "pending":
            return current

        metadata = self._metadata(user)
        metadata[self.ACCESS_KEY] = {
            "status": "pending",
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "reviewed_at": None,
            "reviewed_by": None,
        }
        user.extra_metadata = metadata
        db.commit()
        db.refresh(user)
        return self.get_settings(user)

    def update_settings(
        self,
        db: Session,
        user: User,
        payload: BoltzSettingsUpdate,
    ) -> BoltzSettingsResponse:
        current = self.get_settings(user)
        if payload.provider == "self_hosted" and not current.can_use_self_hosted:
            raise HTTPException(403, "Self-hosted Boltz access has not been approved")

        if payload.provider == "platform":
            credential = credential_service.get_owned(db, payload.credential_id, user.id)
            if credential is None or credential.status != "active":
                raise HTTPException(400, "An active owned Boltz Platform credential is required")

        metrics = payload.metrics
        if not metrics:
            metrics = ["binding_affinity"] if payload.provider == "self_hosted" else [
                "boltz_binding_confidence",
                "boltz_optimization_score",
                "boltz_structure_confidence",
            ]

        metadata = self._metadata(user)
        metadata[self.SETTINGS_KEY] = {
            "provider": payload.provider,
            "credential_id": str(payload.credential_id) if payload.credential_id else None,
            "execution_preference": "library_screen" if payload.provider == "platform" else payload.execution_preference,
            "metrics": metrics,
        }
        user.extra_metadata = metadata
        db.commit()
        db.refresh(user)
        return self.get_settings(user)

    def resolve_run_configuration(
        self,
        db: Session,
        user: User,
        requested: CharacterizationRunConfiguration | None = None,
    ) -> CharacterizationRunConfiguration:
        if requested is None:
            settings = self.get_settings(user)
            if settings.provider is None:
                raise HTTPException(400, "Configure a Boltz provider in Account before starting a run")
            boltz = BoltzRunConfiguration(
                provider=settings.provider,
                credential_id=settings.credential_id,
                execution_preference=settings.execution_preference,
                metrics=settings.metrics,
            )
            requested = CharacterizationRunConfiguration(boltz=boltz)

        boltz = requested.boltz
        if boltz.provider == "self_hosted":
            if not self.get_settings(user).can_use_self_hosted:
                raise HTTPException(403, "Self-hosted Boltz access has not been approved")
        else:
            credential = credential_service.get_owned(db, boltz.credential_id, user.id)
            if credential is None or credential.status != "active":
                raise HTTPException(400, "An active owned Boltz Platform credential is required")

        return requested

    def review_request(
        self,
        db: Session,
        user: User,
        reviewer: User,
        status: str,
    ) -> AdminBoltzUserResponse:
        metadata = self._metadata(user)
        previous = metadata.get(self.ACCESS_KEY, {})
        previous = previous if isinstance(previous, dict) else {}
        metadata[self.ACCESS_KEY] = {
            "status": status,
            "requested_at": previous.get("requested_at"),
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "reviewed_by": str(reviewer.id),
        }
        settings = metadata.get(self.SETTINGS_KEY, {})
        settings = settings if isinstance(settings, dict) else {}
        if status == "approved" and not settings.get("provider"):
            metadata[self.SETTINGS_KEY] = {
                "provider": "self_hosted",
                "credential_id": None,
                "execution_preference": "auto",
                "metrics": ["binding_affinity"],
            }
        elif status == "denied" and settings.get("provider") == "self_hosted":
            metadata.pop(self.SETTINGS_KEY, None)

        user.extra_metadata = metadata
        db.commit()
        db.refresh(user)
        return self.as_admin_response(user)

    def as_admin_response(self, user: User) -> AdminBoltzUserResponse:
        return AdminBoltzUserResponse(
            user_id=user.id,
            email=user.email,
            username=user.username,
            **self.get_settings(user).model_dump(),
        )


boltz_access_service = BoltzAccessService()