"""Require a current measured ranking when reporting optimized libraries."""


def check(results, evidence):
    from sable_runtime.scoring import prediction_observations
    optimized = {r['library_action_id'] for r in results.values()
                 if r.get('kind') == 'recommendation'}
    for library_id in optimized:
        measured = {key for key, r in results.items()
                    if any(batch.get('library_action_id') == library_id and batch.get('observations')
                           for batch in prediction_observations(r))}
        if measured and not any(
            r.get('kind') == 'optimization_report' and r.get('library_action_id') == library_id
            and measured <= set(r.get('measurement_action_ids', []))
            for key, r in results.items() if key in evidence
        ):
            return {'error': 'ranking_required', 'library_action_id': library_id,
                    'measurement_action_ids': sorted(measured),
                    'instruction': 'Call baybe.rank with cumulative measurements and cite its action in finish evidence.'}
    return None
