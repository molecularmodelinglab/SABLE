"""Local BayBE adapter owned by this skill."""
import importlib.metadata


def run(payload):
    from baybe.utils.random import temporary_seed
    from tools.baybe_optimizer_tool import BayBEOptimizerTool
    options = {
        'optimizer_strategy': payload['strategy'], 'normalize_targets': payload['strategy'] == 'desirability'}
    with temporary_seed(payload['seed']):
        result = BayBEOptimizerTool()._run(
            targets=payload['targets'], batch_size=payload['batch_size'], encoding='ECFP',
            search_space=payload['search_space'], measurement_data=payload['measurements'],
            **options)
    if isinstance(result, str):
        raise RuntimeError(result)
    return {'recommended_ids': result, 'version': importlib.metadata.version('baybe')}

