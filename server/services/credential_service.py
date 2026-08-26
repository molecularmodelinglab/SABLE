import base64
import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Optional, Tuple

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from server.models.provider_credential import ProviderCredential


logger = logging.getLogger(__name__)
BOLTZ_AUTH_URL = "https://api.boltz.bio/compute/v1/auth/me"


class CredentialConfigurationError(RuntimeError):
    pass


class CredentialValidationUnavailable(RuntimeError):
    pass


class CredentialService:
    def _cipher(self) -> Fernet:
        master_key = os.getenv("PROVIDER_CREDENTIAL_MASTER_KEY", "").strip()
        environment = os.getenv("ENVIRONMENT", "development").strip().lower()
        if not master_key and environment == "development":
            application_secret = os.getenv("SECRET_KEY", "").strip()
            if application_secret:
                derived_key = hashlib.sha256(
                    b"sable-provider-credentials\0" + application_secret.encode("utf-8")
                ).digest()
                master_key = base64.urlsafe_b64encode(derived_key).decode("ascii")
        if not master_key:
            raise CredentialConfigurationError(
                "PROVIDER_CREDENTIAL_MASTER_KEY is required for provider credentials"
            )
        try:
            return Fernet(master_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise CredentialConfigurationError(
                "PROVIDER_CREDENTIAL_MASTER_KEY must be a valid Fernet key"
            ) from exc

    def encrypt(self, secret: str) -> bytes:
        return self._cipher().encrypt(secret.encode("utf-8"))

    def decrypt(self, encrypted_secret: bytes) -> str:
        try:
            return self._cipher().decrypt(encrypted_secret).decode("utf-8")
        except InvalidToken as exc:
            raise CredentialConfigurationError("Provider credential cannot be decrypted") from exc

    def get_owned(
        self,
        db: Session,
        credential_id: object,
        user_id: object,
    ) -> Optional[ProviderCredential]:
        return db.query(ProviderCredential).filter(
            ProviderCredential.id == credential_id,
            ProviderCredential.user_id == user_id,
        ).first()

    def validate_secret(self, secret: str) -> Tuple[bool, Optional[str]]:
        try:
            response = httpx.get(
                BOLTZ_AUTH_URL,
                headers={"x-api-key": secret},
                timeout=10.0,
            )
            if response.status_code == 200:
                return True, None
            if response.status_code in (401, 403):
                return False, "Boltz Platform rejected the credential."
            raise CredentialValidationUnavailable(
                "Boltz Platform validation is temporarily unavailable. Try again."
            )
        except CredentialValidationUnavailable:
            raise
        except Exception as exc:
            logger.warning("Boltz credential validation failed: %s", type(exc).__name__)
            raise CredentialValidationUnavailable(
                "Boltz Platform validation is temporarily unavailable. Try again."
            ) from exc

    def apply_validation(self, credential: ProviderCredential, secret: str) -> Optional[str]:
        valid, error = self.validate_secret(secret)
        credential.status = "active" if valid else "invalid"
        credential.last_validated_at = datetime.now(timezone.utc)
        return error


credential_service = CredentialService()