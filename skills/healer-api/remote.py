"""Single-request HEALER API submission and polling adapters."""
import json
import time
from urllib.parse import quote

import requests
from rdkit import rdBase
from sable_runtime.science import canonical, filter_library


MAX_RESPONSE_BYTES = 4_000_000
READ_DEADLINE_SECONDS = 30


def _request(method, path, context, **kwargs):
    endpoint = context.service.endpoint.rstrip('/')
    headers = {}
    secret = getattr(context, 'secret', None)
    if secret is not None:
        headers['Authorization'] = f'Bearer {secret.get_secret_value()}'
    response = None
    deadline = time.monotonic() + READ_DEADLINE_SECONDS
    try:
        # No retries or redirects: each runtime invocation makes one bounded request.
        response = requests.request(method, endpoint + path, headers=headers,
                                    timeout=(5, 30), allow_redirects=False, stream=True, **kwargs)
        response.raise_for_status()
        if not 200 <= response.status_code < 300:
            raise ValueError('HEALER API returned an unexpected HTTP status')
        content = bytearray()
        for chunk in response.iter_content(chunk_size=8192):
            if time.monotonic() >= deadline:
                raise requests.Timeout('Response read deadline exceeded')
            if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                raise ValueError('HEALER API response exceeds byte limit')
            content.extend(chunk)
        if time.monotonic() >= deadline:
            raise requests.Timeout('Response read deadline exceeded')
        value = json.loads(content)
    except requests.RequestException:
        raise RuntimeError('HEALER API request failed') from None
    except ValueError:
        raise ValueError('HEALER API returned an invalid response') from None
    finally:
        if response is not None:
            response.close()
    if not isinstance(value, dict):
        raise ValueError('HEALER API returned a non-object response')
    return value


def submit(payload, context):
    mode = payload.get('mode', 'MoleculeHEALER')
    if mode == 'FragmentHEALER':
        raise ValueError('FragmentHEALER is not supported by the HEALER API')
    if mode not in {'MoleculeHEALER', 'SiteHEALER'}:
        raise ValueError('Unsupported HEALER API mode')
    body = {'molecule': payload['starting_smiles'], 'bb_source': payload['stock'],
            'reaction_tags': payload['reaction_tags'], 'max_bbs_per_frag': 5,
            'n_compositions': payload['n_compositions'],
            'max_total_products': payload['max_candidates']}
    if mode == 'SiteHEALER':
        body['reactive_sites'] = payload['reactive_sites']
    route = '/enumerate/site' if mode == 'SiteHEALER' else '/enumerate/molecule'
    submitted = _request('POST', route, context, json=body)
    remote_id = submitted.get('job_id')
    if not isinstance(remote_id, str) or not remote_id.strip():
        raise ValueError('HEALER API submission did not return a string job_id')
    if str(submitted.get('status', '')).strip().lower() != 'submitted':
        raise ValueError('HEALER API submission returned an unexpected status')
    return {'remote_id': remote_id}


def _library(response, remote_id, payload, context):
    result = response.get('result')
    rows = result.get('complete') if isinstance(result, dict) else None
    if not isinstance(rows, list):
        raise ValueError('HEALER API success response is missing result.complete')
    if any(not isinstance(row, dict) or 'Product' not in row for row in rows):
        raise ValueError('HEALER API result.complete is missing Product')
    retained = rows[:payload['max_candidates']]
    products = [row['Product'] if isinstance(row['Product'], str) else '' for row in retained]
    starting = canonical(payload['starting_smiles'])
    constraints = payload['constraints']
    candidates, exclusions = filter_library(products, starting, constraints)
    return {'kind': 'candidate_library', 'starting_smiles': starting, 'candidates': candidates,
            'constraints': constraints, 'exclusions': exclusions,
            'provenance': {'tool': 'HEALER', 'execution_mode': 'api',
                           'rdkit_version': rdBase.rdkitVersion,
                           'stock': payload['stock'], 'mode': payload['mode'],
                           'requested_seed': payload.get('seed'), 'seed_applied': False,
                           'n_compositions': payload['n_compositions'],
                           'max_candidates': payload['max_candidates'],
                           'reaction_tags': payload['reaction_tags'],
                           'source_count': len(rows), 'retained_source_count': len(retained),
                           'reaction_rows': retained, 'stats': result.get('stats', {}),
                           'remote_job': {'service': 'healer-api', 'remote_id': remote_id,
                                          'endpoint': context.service.endpoint.rstrip('/'),
                                          'status': response['status']}}}


def poll(remote_id, payload, context):
    response = _request('GET', '/jobs/' + quote(remote_id, safe=''), context)
    if 'job_id' in response and response['job_id'] != remote_id:
        raise ValueError('HEALER API returned a different job_id')
    status = str(response.get('status', '')).strip().lower()
    if status in {'pending', 'received', 'started', 'progress', 'retry'}:
        return {'status': 'running'}
    if status == 'failure':
        return {'status': 'failed'}
    if status == 'revoked':
        return {'status': 'cancelled'}
    if status == 'success':
        return {'status': 'succeeded', 'result': _library(response, remote_id, payload, context)}
    raise ValueError('HEALER API returned an unknown job status')
