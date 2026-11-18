"""Load deployment-owned endpoints and credential references without exposing secrets."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from pydantic import SecretStr
from .executor import ServiceBinding


def load_services(path):
    if not path:
        return {}, None
    source = Path(path)
    if not source.is_absolute():
        raise ValueError('Service bindings require an absolute configuration path')
    configured = json.loads(source.read_text())
    bindings, references = {}, {}
    for name, config in configured.items():
        if set(config) - {'endpoint', 'credential_env'}:
            raise ValueError('Unknown service configuration field')
        endpoint = config['endpoint'].rstrip('/')
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Service endpoint must be an HTTP URL without embedded credentials or query parameters')
        reference = name if config.get('credential_env') else None
        bindings[name] = ServiceBinding(endpoint, reference)
        if reference:
            references[reference] = config['credential_env']

    def credentials(owner, reference):
        # Shared deployment credentials; per-owner resolvers can be injected instead.
        value = os.environ.get(references[reference], '')
        if not value:
            raise PermissionError('Configured service credential is unavailable')
        return SecretStr(value)

    return bindings, credentials
