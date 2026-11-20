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


def _boltz_selection(db, user):
    """Return the explicitly selected, currently usable Boltz capability."""
    from server.services.boltz_access_service import boltz_access_service
    from server.services.credential_service import credential_service
    metadata = user.extra_metadata if isinstance(user.extra_metadata, dict) else {}
    settings = metadata.get('boltz_settings', {})
    provider = settings.get('provider') if isinstance(settings, dict) else None
    skill = {'self_hosted': 'boltz', 'platform': 'boltz-platform'}.get(provider)
    reason = None
    if not user.is_active:
        skill, reason = None, 'This account is inactive'
    elif provider == 'self_hosted' and not boltz_access_service.get_settings(user).can_use_self_hosted:
        skill, reason = None, 'Self-hosted Boltz access has not been approved'
    elif provider == 'platform':
        try:
            credential_id = UUID(str(settings.get('credential_id')))
        except (ValueError, TypeError):
            credential_id = None
        credential = credential_service.get_owned(db, credential_id, user.id) if credential_id else None
        if credential is None or credential.provider != 'boltz_platform' or credential.status != 'active':
            skill, reason = None, 'Select an active owned Boltz Platform credential in Account'
    elif skill is None:
        reason = 'Select a Boltz provider in Account'
    return {'boltz_provider': provider if provider in {'self_hosted', 'platform'} else None,
            'boltz_skill_id': skill, 'boltz_unavailable_reason': reason}


def campaign_preferences(owner):
    """Expose account selection and availability without account credentials."""
    from server.database import SessionLocal
    from server.models.user import User
    with SessionLocal() as db:
        user = db.query(User).filter(User.id == UUID(str(owner))).first()
        if user is None:
            raise PermissionError('Campaign owner is not available')
        return _boltz_selection(db, user)


def authorize_campaign_skill(owner, skill_id, preferences=None):
    """Enforce account selection and access for each Boltz operation."""
    if skill_id not in {'boltz', 'boltz-platform'}:
        return
    current = campaign_preferences(owner)
    if current['boltz_skill_id'] != skill_id:
        raise PermissionError(current['boltz_unavailable_reason'] or 'The Boltz provider selection has changed; use a new campaign or restore its original Account setting')
    if preferences is not None and preferences.get('boltz_skill_id') != skill_id:
        raise PermissionError('This campaign has a different pinned Boltz selection; start a new campaign to use the current Account setting')
