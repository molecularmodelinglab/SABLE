"""Invoke the optimizer adapter with its default strategy settings."""
import importlib.metadata


def run(payload):
    from baybe.utils.random import temporary_seed
    from tools.baybe_optimizer_tool import BayBEOptimizerTool
    with temporary_seed(payload['seed']):
        result = BayBEOptimizerTool()._run(
            targets=payload['targets'], batch_size=payload['batch_size'], encoding='ECFP',
            search_space=payload['search_space'], measurement_data=payload['measurements'])
    if isinstance(result, str):
        raise RuntimeError(result)
    return {'recommended_ids': result, 'version': importlib.metadata.version('baybe')}

