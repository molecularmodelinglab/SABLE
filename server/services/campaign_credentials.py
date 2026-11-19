"""Resolve selected, user-owned credentials for durable campaign work."""
from uuid import UUID
from pydantic import SecretStr


class SavedCredentials:
    def __init__(self, session_factory=None, credential_service=None):
        self.session_factory = session_factory
        self.credential_service = credential_service

    def _dependencies(self):
        if self.session_factory is None:
            from server.database import SessionLocal
            from server.services.credential_service import credential_service
            return SessionLocal, credential_service
        return self.session_factory, self.credential_service

    def select(self, owner, provider):
        from server.models.user import User
        factory, _ = self._dependencies()
        with factory() as db:
            user = db.query(User).filter(User.id == UUID(str(owner))).first()
            if user is None or provider != 'boltz_platform':
                raise PermissionError('Configure an active owned provider credential in Account')
            metadata = user.extra_metadata if isinstance(user.extra_metadata, dict) else {}
            settings = metadata.get('boltz_settings', {})
            if not isinstance(settings, dict) or settings.get('provider') != 'platform' or not settings.get('credential_id'):
                raise PermissionError('Configure Boltz Platform and select a credential in Account')
            return str(UUID(str(settings['credential_id'])))

    def resolve(self, owner, provider, credential_id):
        factory, service = self._dependencies()
        with factory() as db:
            credential = service.get_owned(db, UUID(str(credential_id)), UUID(str(owner)))
            if credential is None or credential.provider != provider or credential.status != 'active':
                raise PermissionError('An active owned provider credential is required')
            return SecretStr(service.decrypt(credential.encrypted_secret))


def load_campaign_services(path):
    from sable_runtime.service_config import load_services
    return load_services(path, credential_store=SavedCredentials())
