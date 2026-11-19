"""Load deployment-owned endpoints and credential references without exposing secrets."""
import json
import os
import re
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit
from pydantic import SecretStr
from .executor import ServiceBinding


def load_services(path, *, credential_store=None):
    if not path:
        return {}, None
    source = Path(path)
    if not source.is_absolute():
        raise ValueError('Service bindings require an absolute configuration path')
    configured = json.loads(source.read_text())
    bindings, references, providers = {}, {}, {}
    for name, config in configured.items():
        if set(config) - {'endpoint', 'endpoint_env', 'credential_env', 'credential_provider', 'transport'}:
            raise ValueError('Unknown service configuration field')
        transport = config.get('transport', 'http')
        if transport not in {'http', 'sdk'}:
            raise ValueError('Unknown service transport')
        if config.get('credential_env') and config.get('credential_provider'):
            raise ValueError('Configure one credential source')
        if transport == 'sdk':
            if 'endpoint' in config or 'endpoint_env' in config:
                raise ValueError('SDK services manage their own endpoint')
            endpoint = None
        else:
            if ('endpoint' in config) == ('endpoint_env' in config):
                raise ValueError('Configure exactly one of endpoint or endpoint_env')
            if 'endpoint_env' in config:
                variable = config['endpoint_env']
                if not isinstance(variable, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', variable):
                    raise ValueError('endpoint_env must name an environment variable')
                endpoint = os.environ.get(variable, '')
                if not endpoint:
                    raise ValueError(f'Service endpoint environment variable {variable} is missing or empty')
            else:
                endpoint = config['endpoint']
            if not isinstance(endpoint, str) or not endpoint or any(char.isspace() for char in endpoint):
                raise ValueError('Service endpoint must be a nonempty URL without whitespace')
            endpoint = endpoint.rstrip('/')
            parsed = urlsplit(endpoint)
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('Service endpoint must be an HTTP URL without embedded credentials or query parameters')
        reference = name if config.get('credential_env') or config.get('credential_provider') else None
        bindings[name] = ServiceBinding(endpoint, reference)
        if config.get('credential_provider'):
            provider = config['credential_provider']
            if not isinstance(provider, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', provider):
                raise ValueError('Invalid credential provider')
            if credential_store is None:
                raise ValueError('Saved credentials require a credential store')
            providers[name] = provider
        elif reference:
            references[reference] = config['credential_env']

    return bindings, ServiceCredentials(references, providers, credential_store)


class ServiceCredentials:
    """Resolve environment secrets or pin and recheck saved credential identities."""
    def __init__(self, references, providers, store):
        self.references, self.providers, self.store = references, providers, store

    def requires_pin(self, reference):
        return reference in self.providers

    def pin(self, owner, reference):
        if reference not in self.providers:
            return reference
        provider = self.providers[reference]
        credential_id = self.store.select(owner, provider)
        secret = self.store.resolve(owner, provider, credential_id)
        fingerprint = sha256(secret.get_secret_value().encode()).hexdigest()
        return 'saved:' + provider + ':' + credential_id + ':' + fingerprint

    def __call__(self, owner, reference):
        if reference in self.providers:
            reference = self.pin(owner, reference)
        if reference.startswith('saved:'):
            _, provider, credential_id, fingerprint = reference.split(':', 3)
            if provider not in self.providers.values() or self.store is None:
                raise PermissionError('Saved credential provider is unavailable')
            secret = self.store.resolve(owner, provider, credential_id)
            if sha256(secret.get_secret_value().encode()).hexdigest() != fingerprint:
                raise PermissionError('The pinned credential has changed')
            return secret
        value = os.environ.get(self.references[reference], '')
        if not value:
            raise PermissionError('Configured service credential is unavailable')
        return SecretStr(value)
