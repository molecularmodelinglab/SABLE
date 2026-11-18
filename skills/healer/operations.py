import os
from rdkit import Chem, rdBase
from sable_runtime.science import canonical, filter_library, worker


def enumerate_analogs(inputs, context):
    seed = canonical(inputs['starting_smiles'])
    constraints = {'scaffold_smarts': inputs.get('scaffold_smarts'),
                   'property_filters': inputs.get('property_filters', [])}
    filter_library([], seed, constraints)
    mode = inputs.get('mode', 'MoleculeHEALER')
    if mode == 'SiteHEALER' and not inputs.get('reactive_sites'):
        raise ValueError('Site enumeration requires explicit reactive atom indices')
    if inputs.get('reactive_sites'):
        if mode != 'SiteHEALER' or any(i >= Chem.MolFromSmiles(inputs['starting_smiles']).GetNumAtoms() for i in inputs['reactive_sites']):
            raise ValueError('Reactive atom indices must refer to the input structure in SiteHEALER mode')
    if os.getenv('HEALER_EXECUTION_MODE', 'internal') != 'internal':
        raise ValueError('This skill supports local HEALER only')
    stock = os.getenv('HEALER_STOCK', 'US_stock')
    payload = {'starting_smiles': inputs['starting_smiles'], 'mode': mode, 'stock': stock,
               'reaction_tags': inputs.get('reaction_tags', ['all']),
               'n_compositions': inputs.get('n_compositions', 4), 'seed': inputs.get('seed', 42),
               'max_candidates': inputs['max_candidates'], 'reactive_sites': inputs.get('reactive_sites')}
    generated = worker('healer', payload)
    candidates, exclusions = filter_library(generated['products'], seed, constraints)
    return {'kind': 'candidate_library', 'starting_smiles': seed, 'candidates': candidates,
            'constraints': constraints, 'exclusions': exclusions,
            'provenance': {'tool': 'HEALER', 'version': generated['version'], 'rdkit_version': rdBase.rdkitVersion,
                           'stock': stock, 'mode': mode, 'seed': payload['seed'],
                           'n_compositions': payload['n_compositions'], 'max_candidates': inputs['max_candidates'],
                           'source_count': len(generated['products']), 'reaction_rows': generated['rows']}}
