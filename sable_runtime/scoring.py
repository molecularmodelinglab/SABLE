"""Normalize recorded prediction results without equating provider score semantics."""
from .science import canonical

SCORES = {
    'binding_affinity': {'method': 'Boltz self-hosted', 'unit': 'log10(uM)', 'mode': 'MIN'},
    'boltz_optimization_score': {'method': 'Boltz Platform', 'unit': 'provider_score', 'mode': 'MAX'},
}


def selected_candidates(inputs, results):
    reference = inputs.get('source_action_id') or inputs.get('library_action_id')
    source = results.get(reference)
    if not source or source.get('kind') not in {'candidate_library', 'recommendation'}:
        raise ValueError('Expected a recorded candidate library or recommendation')
    library_id = reference if source['kind'] == 'candidate_library' else source['library_action_id']
    library = results.get(library_id)
    if not library or library.get('kind') != 'candidate_library':
        raise ValueError('The source library is missing')
    by_id = {c['id']: c for c in library['candidates']}
    candidates = source['candidates']
    if not 1 <= len(candidates) <= 100 or len({c['id'] for c in candidates}) != len(candidates):
        raise ValueError('Select one to 100 unique candidates')
    if any(by_id.get(c['id']) != c for c in candidates):
        raise ValueError('Recommendation differs from its source library')
    return library_id, candidates


def prediction_observations(result):
    """Convert trusted, collected provider outputs to comparable observations."""
    if result.get('kind') == 'remote_batch_result':
        return [batch for item in result['results'] for batch in prediction_observations(item)]
    if result.get('kind') not in {'boltz_prediction', 'boltz_screen'}:
        return [result]
    platform = result['provider'] == 'platform'
    prop, metric = ('boltz_optimization_score', 'optimization_score') if platform else ('binding_affinity', 'affinity')
    spec = SCORES[prop]
    rows = result['measurements'] if platform else [result['measurement']]
    observations = []
    for row in rows:
        if metric not in row['metrics']:
            continue
        observations.append({'candidate_id': row['id'], 'canonical_smiles': canonical(row['smiles']),
            'method': spec['method'], 'method_version': 'model-version-unreported', 'source': 'predicted',
            'protein_scope_id': result['protein_scope_id'],
            'values': [{'property': prop, 'unit': spec['unit'], 'value': row['metrics'][metric]}]})
    return [{'library_action_id': result.get('library_action_id'), 'observations': observations}]


def validate_scoring_scope(results, library_id, protein_scope_id):
    """Reject a target change before spending another prediction batch."""
    for result in results.values():
        outputs = result['results'] if result.get('kind') == 'remote_batch_result' else [result]
        for output in outputs:
            if output.get('library_action_id') == library_id and output.get('protein_scope_id') not in {None, protein_scope_id}:
                raise ValueError('Keep the same protein target across optimization batches')
