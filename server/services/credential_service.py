import os
from datetime import datetime, timezone
from typing import Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from server.models.provider_credential import ProviderCredential


class CredentialConfigurationError(RuntimeError):
    pass


class CredentialService:
    def _cipher(self) -> Fernet:
        master_key = os.getenv("PROVIDER_CREDENTIAL_MASTER_KEY", "").strip()
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
            from boltz_api import Boltz

            with Boltz(api_key=secret) as client:
                client.auth.me()
            return True, None
        except Exception:
            return False, "Boltz Platform rejected the credential or could not be reached."

    def apply_validation(self, credential: ProviderCredential, secret: str) -> Optional[str]:
        valid, error = self.validate_secret(secret)
        credential.status = "active" if valid else "invalid"
        credential.last_validated_at = datetime.now(timezone.utc)
        return error


credential_service = CredentialService()