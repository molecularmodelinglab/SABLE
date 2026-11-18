from datetime import datetime, timezone
from types import SimpleNamespace
import importlib.metadata
from sable_runtime.science import measurements, validate_targets, worker
from utils.objective_ranking import rank_experimental_results


def prepare(inputs, context):
    validate_targets(inputs['targets'], inputs['strategy'])
    return measurements(context.results, inputs['library_action_id'], inputs['measurement_action_ids'], inputs['targets'])


def recommend(inputs, context):
    library, rows, incomplete = prepare(inputs, context)
    library_id = inputs['library_action_id']
    specification = {'targets': inputs['targets'], 'strategy': inputs['strategy'], 'max_rounds': inputs['max_rounds']}
    previous = [r for r in context.results.values() if r.get('kind') == 'recommendation' and r.get('library_action_id') == library_id]
    if any(r['specification'] != specification for r in previous):
        raise ValueError('Objectives and round budget cannot change within a library optimization')
    completed_rounds = len([r for r in previous if r['candidates']])
    measured = {r['Molecule_ID'] for r in rows}
    previously_selected = {c['id'] for r in previous for c in r['candidates']}
    if previously_selected - measured:
        raise ValueError('Evaluate previous recommendations before requesting another batch')
    remaining = [c for c in library['candidates'] if c['id'] not in measured]
    base = {'kind': 'recommendation', 'library_action_id': library_id, 'specification': specification,
            'round': completed_rounds + 1, 'measurement_action_ids': inputs['measurement_action_ids'],
            'measurement_count': len(rows), 'incomplete_ids': incomplete,
            'method': 'BayBE', 'method_version': importlib.metadata.version('baybe'), 'candidates': []}
    if completed_rounds >= inputs['max_rounds'] or not remaining:
        return {**base, 'stop_reason': 'round_budget' if completed_rounds >= inputs['max_rounds'] else 'search_space_exhausted'}
    size = min(inputs['batch_size'], len(remaining))
    output = worker('baybe', {'targets': inputs['targets'], 'strategy': inputs['strategy'],
                             'batch_size': size, 'search_space': {c['id']: c['smiles'] for c in library['candidates']},
                             'measurements': rows, 'seed': inputs.get('seed', 42)})
    ids = output['recommended_ids']
    remaining_by_id = {c['id']: c for c in remaining}
    if len(ids) != size or len(set(ids)) != size or not set(ids) <= remaining_by_id.keys():
        raise ValueError('Optimizer returned invalid or repeated recommendations')
    return {**base, 'candidates': [remaining_by_id[i] for i in ids], 'stop_reason': None}


def rank(inputs, context):
    library, rows, incomplete = prepare(inputs, context)
    targets = [SimpleNamespace(name=t['name'], mode=t['mode'], weight=t.get('weight', 1), bounds=t.get('bounds')) for t in inputs['targets']]
    by_id = {c['id']: c['smiles'] for c in library['candidates']}
    results = [SimpleNamespace(molecule_id=row['Molecule_ID'], smiles=by_id[row['Molecule_ID']],
               iteration=0, metadata={}, timestamp=datetime.now(timezone.utc),
               properties={k: v for k, v in row.items() if k != 'Molecule_ID'}) for row in rows]
    report = rank_experimental_results(targets, results, inputs['strategy'])
    return {'kind': 'optimization_report', 'library_action_id': inputs['library_action_id'],
            'measurement_action_ids': inputs['measurement_action_ids'], 'objectives': inputs['targets'],
            'constraints': library['constraints'], 'evaluated_candidates': len(rows),
            'library_size': len(library['candidates']), 'incomplete_ids': incomplete, **report}
