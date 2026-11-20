"""Collect bounded protein structure files without exposing provider URLs or credentials."""
import time
from urllib.parse import urljoin, urlsplit

import httpx


def retain_structure(url, context, *, provider, name, deadline=None):
    """Return an owned artifact reference or a nonfatal availability notice."""
    if not url:
        return {'structure_status': 'No structure file returned by the provider'}
    if not getattr(context, 'write_artifact', None):
        return {'structure_status': 'Structure storage is unavailable'}
    try:
        deadline = min(deadline if deadline is not None else float('inf'), time.monotonic() + 20)
        if time.monotonic() >= deadline:
            raise ValueError('Structure collection time allowance exceeded')
        headers = {}
        if provider == 'self_hosted':
            base = context.service.endpoint.rstrip('/') + '/'
            url = urljoin(base, url)
            parsed, origin = urlsplit(url), urlsplit(base)
            if (parsed.scheme, parsed.hostname, parsed.port) != (origin.scheme, origin.hostname, origin.port):
                raise ValueError('Unexpected structure origin')
            if context.secret:
                headers['Authorization'] = 'Bearer ' + context.secret.get_secret_value()
        else:
            parsed = urlsplit(url)
            host = parsed.hostname or ''
            # Signed object-storage URLs receive no Platform credentials.
            trusted = host == 'api.boltz.bio' or host.endswith(('.boltz.bio', '.s3.amazonaws.com', '.storage.googleapis.com', '.blob.core.windows.net')) or host == 'storage.googleapis.com' or ('.s3.' in host and host.endswith('.amazonaws.com'))
            if parsed.scheme != 'https' or parsed.port not in (None, 443) or not trusted:
                raise ValueError('Unexpected structure origin')
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError('Invalid structure URL')
        with httpx.Client(timeout=httpx.Timeout(15, connect=5), follow_redirects=False, trust_env=False,
                          transport=httpx.HTTPTransport(retries=0)) as client:
            with client.stream('GET', url, headers=headers) as response:
                if response.status_code != 200:
                    raise ValueError('Structure unavailable')
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 3_000_000 or time.monotonic() > deadline:
                        raise ValueError('Structure exceeds allowance')
        content = data.decode('utf-8')
        if context.secret and context.secret.get_secret_value() in content:
            raise ValueError('Credential in structure')
        if '_atom_site.' in content and 'data_' in content:
            extension = 'cif'
        elif any(line.startswith(('ATOM  ', 'HETATM')) for line in content.splitlines()):
            extension = 'pdb'
        else:
            raise ValueError('Not a coordinate file')
        return {'structure': context.write_artifact(name + '.' + extension, content)}
    except Exception:
        return {'structure_status': 'Structure could not be retained (download, format, or storage limit); prediction metrics remain available'}
