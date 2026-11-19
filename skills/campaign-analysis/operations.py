"""Prepare sourced observation series for reproducible plots and summaries."""
import math


def analyze(inputs, context):
    property_name = inputs['property']
    rows, methods, units, seen = [], set(), set(), set()
    for action_id in inputs['measurement_action_ids']:
        result = context.results.get(action_id)
        if not isinstance(result, dict) or not isinstance(result.get('observations'), list):
            raise ValueError('Select recorded descriptor observations')
        library_id = result.get('library_action_id')
        for observation in result['observations']:
            matching = [value for value in observation['values'] if value['property'] == property_name]
            if len(matching) != 1 or not math.isfinite(matching[0]['value']):
                raise ValueError('Every selected observation must contain the requested property exactly once')
            candidate = observation.get('candidate_id')
            rounds = {r['round'] for r in context.results.values() if r.get('kind') == 'recommendation'
                      and library_id and r.get('library_action_id') == library_id
                      and candidate and any(c['id'] == candidate for c in r['candidates'])}
            if inputs['chart'] == 'progress' and len(rounds) != 1:
                raise ValueError('Progress plots require observations linked to one recorded optimization round')
            identity = (library_id, candidate or observation['canonical_smiles'])
            if identity in seen:
                raise ValueError('Repeated molecules would bias the summary; select nonoverlapping observation actions')
            seen.add(identity)
            methods.add((observation['method'], observation['method_version'], observation['source']))
            units.add(matching[0]['unit'])
            rows.append({'id': candidate or str(len(rows)+1), 'smiles': observation['canonical_smiles'],
                         'iteration': next(iter(rounds)) if rounds else 0, 'value': matching[0]['value']})
    if not rows or len(rows) > 1000 or len(methods) != 1 or len(units) != 1:
        raise ValueError('Select one to 1000 observations with a shared method, version, source, and unit')
    output = context.run_worker('plot', {**inputs, 'rows': rows, 'unit': next(iter(units))})
    return {**output, 'kind': 'campaign_analysis', 'source_action_ids': inputs['measurement_action_ids'],
            'method': 'Recorded observations; deterministic plot', 'observation_method': list(next(iter(methods)))}
