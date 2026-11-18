"""Single-phase Boltz HTTP handlers with separate provider metric semantics."""
import json
import math
import re
import time

import httpx


SCREEN = '/compute/v1/small-molecule/library-screen'
METRICS = ('binding_confidence', 'optimization_score', 'structure_confidence',
           'ptm', 'iptm', 'complex_plddt', 'complex_iplddt')


def _request(context, method, path, **kwargs):
    headers = {'Accept': 'application/json'}
    if context.secret:
        headers['Authorization'] = 'Bearer ' + context.secret.get_secret_value()
    deadline = time.monotonic() + 30
    try:
        with httpx.Client(timeout=httpx.Timeout(20, connect=5), follow_redirects=False,
                          transport=httpx.HTTPTransport(retries=0), trust_env=False) as client:
            with client.stream(method, context.service.endpoint.rstrip('/') + path,
                               headers=headers, **kwargs) as response:
                if not 200 <= response.status_code < 300:
                    raise ValueError()
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > 4_000_000 or time.monotonic() > deadline:
                        raise ValueError()
                return json.loads(content)
    except Exception:
        raise RuntimeError('Remote request failed; inspect job before resubmitting') from None


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', value):
        raise ValueError('Invalid remote job identifier')
    return value


def _number(value, *, confidence=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Boltz returned a non-finite metric')
    if confidence and not 0 <= value <= 1:
        raise ValueError('Boltz confidence metric is outside its documented range')
    return value


def submit(payload, context):
    if not context.secret:
        raise ValueError('Boltz Platform requires a credential service binding')
    body = {'molecules': payload['molecules'],
            'target': {'type': 'no_template', 'entities': [
                {'type': 'protein', 'chain_ids': [p['chain_id']], 'value': p['sequence']}
                for p in payload['proteins']]},
            'molecule_filters': {'boltz_smarts_catalog_filter_level': 'disabled'}}
    # Match BoltzPlatformAdapter's request shape, using the verified SDK REST path.
    response = _request(context, 'POST', SCREEN, json=body)
    return {'remote_id': _id(response.get('id'))}


def _status(value):
    mapping = {'pending': 'running', 'running': 'running', 'succeeded': 'succeeded',
               'failed': 'failed', 'stopped': 'cancelled'}
    if value not in mapping:
        raise ValueError('Unknown Boltz job state')
    return mapping[value]


def poll(remote_id, payload, context):
    path = SCREEN + '/' + _id(remote_id)
    status = _status(_request(context, 'GET', path).get('status'))
    if status != 'succeeded':
        return {'status': status}
    by_id = {row['id']: row for row in payload['molecules']}
    by_smiles = {}
    for molecule in payload['molecules']:
        by_smiles.setdefault(molecule['smiles'], []).append(molecule['id'])
    rows, cursor, cursors = [], None, set()
    # Cap pagination and result size; never follow provider-supplied URLs.
    for _ in range(2):
        params = {'limit': 100}
        if cursor:
            params['after_id'] = cursor
        page = _request(context, 'GET', path + '/results', params=params)
        if not isinstance(page.get('data'), list):
            raise ValueError('Invalid Boltz result page')
        rows.extend(page['data'])
        if len(rows) > len(by_id):
            raise ValueError('Boltz returned excess result rows')
        if page.get('has_more') is False or not page.get('last_id'):
            if page.get('has_more') is True:
                raise ValueError('Missing Boltz pagination cursor')
            break
        cursor = _id(page['last_id'])
        if cursor in cursors:
            raise ValueError('Repeated Boltz pagination cursor')
        cursors.add(cursor)
    else:
        raise ValueError('Boltz result pagination exceeded bounds')
    measurements, seen = [], set()
    for row in rows:
        molecule_id = row.get('external_id')
        if not molecule_id:
            matches = by_smiles.get(row.get('smiles'), [])
            if len(matches) != 1:
                raise ValueError('Ambiguous Boltz molecule identity')
            molecule_id = matches[0]
        if molecule_id not in by_id or molecule_id in seen:
            raise ValueError('Unknown or duplicate Boltz molecule identity')
        if row.get('smiles') != by_id[molecule_id]['smiles']:
            raise ValueError('Boltz molecule identity conflicts with submitted SMILES')
        seen.add(molecule_id)
        metrics = {key: _number(value, confidence=key != 'optimization_score')
                   for key in METRICS if (value := row.get('metrics', {}).get(key)) is not None}
        if not metrics:
            continue
        warnings = [warning['message'] for warning in (row.get('warnings') or [])]
        if any(not isinstance(w, str) for w in warnings):
            raise ValueError('Invalid Boltz warnings')
        measurements.append({**by_id[molecule_id], 'metrics': metrics, 'warnings': warnings,
                             'provider_result_id': _id(row.get('id'))})
    accepted = {row['id'] for row in measurements}
    exclusions = [{'id': row['id'], 'reason': 'No usable Platform result'}
                  for row in payload['molecules'] if row['id'] not in accepted]
    if not measurements:
        return {'status': 'failed'}
    return {'status': 'partial' if exclusions else 'succeeded', 'result': {
        'kind': 'boltz_screen', 'provider': 'platform',
        'protein_scope_id': payload['protein_scope_id'],
        'library_action_id': payload['library_action_id'],
        'measurements': measurements, 'exclusions': exclusions}}


def cancel(remote_id, payload, context):
    response = _request(context, 'POST', SCREEN + '/' + _id(remote_id) + '/stop')
    status = _status(response.get('status'))
    # A cancellation can race completion; collect through the normal validated path.
    return poll(remote_id, payload, context) if status == 'succeeded' else {'status': status}


