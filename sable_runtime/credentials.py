"""Bridge to SABLE's existing encrypted, user-owned credential service.

Dependencies are supplied by the authenticated application composition layer so
importing the foundation never initializes a database or loads an environment.
"""
from pydantic import SecretStr


class ProviderCredentialResolver:
    def __init__(self, session_factory, credential_service, bindings):
        self.session_factory = session_factory
        self.credential_service = credential_service
        # Host-controlled mapping: (owner, logical reference) -> (record ID, provider).
        self.bindings = dict(bindings)

    def __call__(self, owner, reference):
        binding = self.bindings.get((owner, reference))
        if binding is None:
            raise PermissionError('Credential is not available')
        credential_id, provider = binding
        with self.session_factory() as db:
            record = self.credential_service.get_owned(db, credential_id, owner)
            if record is None or record.status != 'active' or record.provider != provider:
                raise PermissionError('Credential is not available')
            return SecretStr(self.credential_service.decrypt(record.encrypted_secret))
