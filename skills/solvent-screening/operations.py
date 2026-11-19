"""Rank supplied solvent parameters against a Hansen interaction sphere."""
from math import sqrt


def screen(inputs, context):
    target = inputs['target']
    candidates = inputs['candidates']
    ids = [candidate['id'] for candidate in candidates]
    if len(set(ids)) != len(ids):
        raise ValueError('Candidate IDs must be unique')
    excluded = set(inputs.get('excluded_ids', []))
    if not excluded <= set(ids):
        raise ValueError('Excluded IDs must belong to the supplied candidates')
    ranking = []
    for candidate in candidates:
        if candidate['id'] in excluded:
            continue
        differences = [candidate[key] - target[key] for key in ('delta_d', 'delta_p', 'delta_h')]
        distance = sqrt(4 * differences[0] ** 2 + differences[1] ** 2 + differences[2] ** 2)
        ratio = distance / inputs['radius']
        ranking.append({
            'id': candidate['id'], 'distance': distance, 'relative_energy_difference': ratio,
            'sphere_position': 'inside' if ratio < 1 else ('boundary' if ratio == 1 else 'outside'),
            'parameters': candidate,
        })
    ranking.sort(key=lambda row: (row['distance'], row['id']))
    return {
        'kind': 'solvent_screen', 'method': 'Hansen distance', 'method_version': '1',
        'parameter_unit': 'MPa^0.5', 'temperature_k': inputs['temperature_k'],
        'target': target, 'radius': inputs['radius'], 'radius_source': inputs['radius_source'],
        'ranking': ranking, 'excluded_ids': sorted(excluded),
        'limitations': ['Supplied parameters and radius are not independently verified.',
                        'Sphere membership is a screening heuristic, not measured solubility or safety.'],
    }
