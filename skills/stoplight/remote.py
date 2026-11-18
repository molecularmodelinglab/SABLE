"""Bounded access to Stoplight's synchronous CSV property endpoint."""
import csv
import io
import math
import time

import httpx


PROPERTIES = (
    'ALogP', 'AmpC β-lactamase aggregation', 'CNS Activity',
    'Cysteine protease cruzain aggregation', 'FSP3',
    'Firefly Luciferase interference', 'HBA', 'HBD', 'Molecular Weight',
    'Nano Luciferase interference', 'Num Heavy Atoms',
    'Num Saturated Quaternary Carbons', 'Number of Rings',
    'Number of Rotatable Bonds', 'Polar Surface Area', 'Redox interference',
    'Solubility in Water (mg/L)', 'Thiol interference',
)


def _request(context, method, path, **kwargs):
    """Do not retry submissions, follow redirects, or expose provider error text."""
    endpoint = context.service.endpoint.rstrip('/')
    headers = {'Accept': 'application/json'}
    if context.secret:
        headers['Authorization'] = 'Bearer ' + context.secret.get_secret_value()
    deadline = time.monotonic() + 30
    try:
        with httpx.Client(timeout=httpx.Timeout(20, connect=5), follow_redirects=False,
                          transport=httpx.HTTPTransport(retries=0), trust_env=False) as client:
            with client.stream(method, endpoint + path, headers=headers, **kwargs) as response:
                if not 200 <= response.status_code < 300:
                    raise ValueError('Remote service rejected request')
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > 4_000_000 or time.monotonic() > deadline:
                        raise ValueError('Remote response exceeded bounds')
                return bytes(content)
    except Exception:
        raise RuntimeError('Remote request failed; inspect job before resubmitting') from None


def submit(payload, context):
    molecules = payload['molecules']
    options = dict.fromkeys(PROPERTIES, True)
    options['precision'] = str(payload.get('precision', 2))
    content = _request(context, 'POST', '/smiles-csv', json={
        'smiles': [row['smiles'] for row in molecules], 'options': options,
    })
    reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig')))
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError('Stoplight returned missing or duplicate CSV columns')
    if 'SMILES' not in reader.fieldnames or 'HBD' not in reader.fieldnames:
        raise ValueError('Stoplight response lacks identity or validity columns')
    rows = list(reader)
    if len(rows) != len(molecules):
        raise ValueError('Stoplight row count does not match the submitted batch')
    measurements, exclusions = [], []
    for molecule, row in zip(molecules, rows):
        # The service has no molecule-ID field. Never silently assign reordered rows.
        if row['SMILES'] != molecule['smiles'] or None in row:
            raise ValueError('Stoplight CSV identity does not match submission order')
        values = {}
        for name in PROPERTIES:
            try:
                value = float(row.get(name, ''))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                values[name] = value
        if 'HBD' not in values:
            exclusions.append({'id': molecule['id'], 'reason': 'Missing finite HBD validity value'})
        else:
            measurements.append({**molecule, 'properties': values})
    if not measurements:
        return {'status': 'failed'}
    result = {'kind': 'molecular_properties', 'provider': 'stoplight',
              'measurements': measurements, 'exclusions': exclusions,
              'precision': payload.get('precision', 2)}
    return {'status': 'partial' if exclusions else 'succeeded', 'result': result}


def poll(remote_id, payload, context):
    raise NotImplementedError('Stoplight has no remote polling API; submit returns terminal results')
