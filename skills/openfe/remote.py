"""OpenFE phases with explicit collection and read-only stage polling."""
import json
import math
import re
import time

import httpx


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


def _path(payload):
    name = payload['campaign_name']
    if not re.fullmatch(r'sable_[a-f0-9]{32}', name):
        raise ValueError('Expected an owned OpenFE campaign name')
    return '/campaigns/' + name


def _record(payload, kind='openfe_campaign'):
    return {'kind': kind, **{key: payload[key] for key in (
        'campaign_name', 'origin_action_id', 'protocol', 'ligands', 'reference', 'repeats', 'stage')}}


def submit_create(payload, context):
    campaign = {'name': payload['campaign_name'], 'protocol': payload['protocol'],
                'execution': {'repeats': payload['repeats']}}
    files = [('files', ('protein.pdb', payload['protein_pdb'].encode(), 'chemical/x-pdb'))]
    ligands = []
    for index, ligand in enumerate(payload['ligands']):
        filename = f'ligand_{index}.sdf'
        files.append(('files', (filename, ligand['sdf'].encode(), 'chemical/x-mdl-sdfile')))
        ligands.append({'name': ligand['id'], 'smiles': ligand['smiles'], 'path': filename})
    if sum(len(item[1][1]) for item in files) > 3_000_000:
        raise ValueError('OpenFE coordinate upload exceeds package limit')
    if payload['protocol'] == 'abfe':
        campaign['complexes'] = [{'name': ligand['name'], 'protein': {'path': 'protein.pdb'},
                                 'ligand': ligand} for ligand in ligands]
        campaign['neutralize_ligands'] = False
    else:
        campaign.update(protein={'path': 'protein.pdb'}, ligands=ligands,
                        reference=payload['reference'], alignment={'pose': 'keep'},
                        network={'method': 'radial', 'central_ligand': payload['reference']})
    response = _request(context, 'POST', '/campaigns/upload',
                        data={'campaign': json.dumps(campaign)}, files=files)
    if response.get('name') != payload['campaign_name'] or response.get('protocol') != payload['protocol']:
        raise ValueError('OpenFE created an unexpected campaign')
    record = {**payload, 'ligands': [row['id'] for row in payload['ligands']],
              'reference': payload.get('reference'), 'stage': 'created'}
    return {'status': 'succeeded', 'result': _record(record)}


def poll_create(remote_id, payload, context):
    if remote_id != payload['campaign_name']:
        raise ValueError('OpenFE remote identifier differs from the owned campaign')
    _summary(payload, context)
    record = {**payload, 'ligands': [row['id'] for row in payload['ligands']],
              'reference': payload.get('reference'), 'stage': 'created'}
    return {'status': 'succeeded', 'result': _record(record)}


def _summary(payload, context):
    summary = _request(context, 'GET', _path(payload))
    if summary.get('name') != payload['campaign_name'] or summary.get('protocol') != payload['protocol']:
        raise ValueError('OpenFE campaign identity or protocol mismatch')
    return summary


def submit_stage(payload, context):
    summary = _summary(payload, context)
    operation = summary.get('last_operation') or {}
    expected = {'prep': None, 'plan': 'prep', 'submit': 'plan'}[payload['stage']]
    if expected is None:
        valid = not operation
    else:
        valid = operation.get('operation') == expected and operation.get('status') == 'done'
    if not valid:
        raise ValueError('OpenFE phase precondition changed; reconcile before resubmitting')
    response = _request(context, 'POST', _path(payload) + '/' + payload['stage'])
    if response.get('operation') != payload['stage'] or response.get('status') not in {'running', 'done'}:
        raise ValueError('Unexpected OpenFE phase response')
    return {'remote_id': payload['campaign_name']}


def poll_stage(remote_id, payload, context):
    if remote_id != payload['campaign_name']:
        raise ValueError('OpenFE remote identifier differs from the owned campaign')
    operation = _summary(payload, context).get('last_operation') or {}
    if not operation:
        return {'status': 'running'}
    if operation.get('operation') != payload['stage']:
        # The API records just the last phase. Do not mistake old completion for new completion.
        previous = {'prep': None, 'plan': 'prep', 'submit': 'plan'}[payload['stage']]
        if operation.get('operation') == previous and operation.get('status') == 'done':
            return {'status': 'running'}
        return {'status': 'failed'}
    if operation.get('status') == 'failed':
        return {'status': 'failed'}
    if operation.get('status') == 'running':
        return {'status': 'running'}
    if operation.get('status') != 'done':
        raise ValueError('Unknown OpenFE phase state')
    return {'status': 'succeeded', 'result': _record(payload)}


def _number(value, uncertainty=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('OpenFE returned an invalid numeric result')
    if uncertainty and value < 0:
        raise ValueError('OpenFE returned negative uncertainty')
    return value


def _index(rows, key):
    indexed = {}
    for row in rows:
        name = row.get(key)
        if not isinstance(name, str) or not name or name in indexed:
            raise ValueError('OpenFE returned missing or duplicate identities')
        indexed[name] = row
    return indexed


def _quality(row):
    return row.get('quality') == 'fail' or any(check.get('status') == 'fail' for check in row.get('checks', []))


def submit_collect(payload, context):
    operation = _summary(payload, context).get('last_operation') or {}
    if operation.get('operation') != 'submit' or operation.get('status') != 'done':
        raise ValueError('OpenFE submit phase is not complete')
    return {'remote_id': payload['campaign_name']}


def poll_collect(remote_id, payload, context):
    if remote_id != payload['campaign_name']:
        raise ValueError('OpenFE remote identifier differs from the owned campaign')
    summary = _summary(payload, context)
    operation = summary.get('last_operation') or {}
    if operation.get('operation') != 'submit' or operation.get('status') != 'done':
        raise ValueError('OpenFE submit phase is not complete')
    suffix = '/results' if payload['protocol'] == 'abfe' else '/network'
    # The reference API deterministically writes result tables and marks completed
    # runs done. This repeat-safe materialization never starts computation.
    data = _request(context, 'GET', _path(payload) + suffix)
    summary = _summary(payload, context)
    if summary.get('last_operation') != operation:
        raise ValueError('OpenFE campaign changed during collection')
    runs = _index(summary.get('runs', []), 'name')
    if not runs:
        return {'status': 'failed'}
    if any(row.get('state') not in {'done', 'failed'} for row in runs.values()):
        return {'status': 'running'}
    done = {name for name, row in runs.items() if row['state'] == 'done'}
    failed = sorted(set(runs) - done)
    if not done:
        return {'status': 'failed'}
    for name in done:
        repeats = runs[name].get('repeats')
        if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < payload['repeats']:
            raise ValueError('OpenFE has fewer repeats than requested')
    base = {'kind': 'free_energy', 'campaign_name': payload['campaign_name'],
            'protocol': payload['protocol'], 'units': 'kcal/mol',
            'reference': payload['reference'], 'failed_runs': failed}
    if payload['protocol'] == 'abfe':
        indexed = _index(data, 'ligand')
        if set(indexed) - set(payload['ligands']):
            raise ValueError('OpenFE returned an unexpected ligand')
        measurements, warnings = [], []
        for name in payload['ligands']:
            row = indexed.get(name)
            if name not in done or not row:
                continue
            repeats = row.get('repeats')
            if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < payload['repeats']:
                raise ValueError('OpenFE ABFE result has incomplete repeats')
            if _quality(row):
                warnings.append('Failed quality checks for ligand ' + name)
            measurements.append({'ligand': name, 'value': _number(row.get('dg')),
                                 'uncertainty': _number(row.get('uncertainty'), True),
                                 'uncertainty_scope': 'absolute_binding_free_energy',
                                 'uncertainty_kind': row.get('uncertainty_kind'), 'repeats': repeats})
        base.update(quantity='absolute_binding_free_energy', measurements=measurements,
                    edges=[], quality_warnings=warnings)
    else:
        base.update(_network(data, payload, done, failed))
    if base['quality_warnings'] and not payload['allow_failed_quality']:
        return {'status': 'failed', 'diagnostics': {'reason': 'quality_checks_failed',
                                                   'quality_warnings': base['quality_warnings']}}
    accepted = {row['ligand'] for row in base['measurements']}
    base['exclusions'] = [{'id': name, 'reason': 'Missing, failed, or disconnected result'}
                          for name in payload['ligands'] if name not in accepted]
    if not accepted or (payload['protocol'] != 'abfe' and accepted <= {payload['reference']}):
        return {'status': 'failed'}
    return {'status': 'partial' if base['exclusions'] or failed else 'succeeded', 'result': base}


def _network(data, payload, done, failed):
    if any(not isinstance(data.get(key), list) for key in ('edges', 'ligands', 'cycles', 'unreachable', 'checks')):
        raise ValueError('Invalid OpenFE network response')
    indexed = _index(data['edges'], 'edge')
    if not done <= set(indexed) or set(indexed) - done - set(failed):
        raise ValueError('OpenFE network does not cover planned edges')
    if any(row.get('ddg') is not None for name, row in indexed.items() if name in failed):
        raise ValueError('Failed OpenFE edges contributed partial estimates to the fit')
    fitted = _index(data['ligands'], 'ligand')
    if set(fitted) - set(payload['ligands']):
        raise ValueError('Unexpected fitted OpenFE ligand')
    for row in fitted.values():
        _number(row.get('dg_relative'))
        _number(row.get('uncertainty'), True)
    edges, warnings = [], []
    for name, row in indexed.items():
        if name not in done:
            continue
        a, b = row.get('ligand_a'), row.get('ligand_b')
        if a == b or a not in payload['ligands'] or b not in payload['ligands']:
            raise ValueError('Invalid OpenFE network endpoints')
        edges.append({'edge': name, 'ligand_a': a, 'ligand_b': b,
                      'ddg': _number(row.get('ddg')), 'uncertainty': _number(row.get('uncertainty'), True),
                      'uncertainty_kind': row.get('uncertainty_kind')})
        if _quality(row):
            warnings.append('Failed quality checks for edge ' + name)
    if any(check.get('status') == 'fail' for check in data['checks']):
        warnings.append('Failed network quality checks')
    reference = payload['reference']
    reachable = {reference}
    while True:
        expanded = reachable | {end for edge in edges
                                if edge['ligand_a'] in reachable or edge['ligand_b'] in reachable
                                for end in (edge['ligand_a'], edge['ligand_b'])}
        if expanded == reachable:
            break
        reachable = expanded
    accepted = set(fitted) & reachable - set(data['unreachable'])
    if reference not in accepted:
        raise ValueError('OpenFE reference is missing or disconnected')
    measurements = [{'ligand': name,
                     'value': _number(fitted[name]['dg_relative'] - fitted[reference]['dg_relative']),
                     'fit_value': fitted[name]['dg_relative'], 'uncertainty': fitted[name]['uncertainty'],
                     'uncertainty_scope': 'network_mean_fit_not_reference_difference',
                     'uncertainty_kind': None}
                    for name in payload['ligands'] if name in accepted]
    return {'quantity': 'reference_relative_binding_free_energy', 'measurements': measurements,
            'edges': edges, 'quality_warnings': warnings}


