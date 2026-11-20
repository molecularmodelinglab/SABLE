"""Local HEALER adapter owned by this skill."""
import importlib.metadata
import json


def run(payload):
    from tools.healer_enumerator_tool import HealerEnumeratorTool
    tool = HealerEnumeratorTool(healer_mode=payload['mode'], execution_mode='internal',
                               bb_source=payload['stock'], reaction_tags=payload['reaction_tags'],
                               max_bbs_per_frag=payload['max_bbs_per_frag'], max_total_products=payload['max_total_products'])
    products = tool._run(molecule=payload['starting_smiles'], n_compositions=payload['n_compositions'],
                         max_evals_per_comp=10000, random_seed=payload['seed'],
                         reactive_sites=payload.get('reactive_sites'))
    frame = tool._enumerator.get_results(calc_similarity=True, calc_properties=False)
    if isinstance(products, str):
        return {'products': [], 'rows': [], 'source_count': len(frame), 'backend_filtered_count': len(frame), 'version': importlib.metadata.version('mol-healer')}
    rows = json.loads(frame.head(payload['max_total_products']).to_json(orient='records'))
    return {'products': list(products.values())[:payload['max_total_products']], 'rows': rows,
            'source_count': len(frame), 'backend_filtered_count': max(0, len(frame) - len(products)),
            'version': importlib.metadata.version('mol-healer')}

