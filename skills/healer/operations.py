import os
from rdkit import Chem, rdBase
from sable_runtime.science import canonical, filter_library
from sable_runtime.enumeration import enumeration_settings, yield_summary


def enumerate_analogs(inputs, context):
    settings = enumeration_settings(inputs)
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
               **settings, 'seed': inputs.get('seed', 42),
               'reactive_sites': inputs.get('reactive_sites')}
    generated = context.run_worker('compute', payload)
    candidates, exclusions = filter_library(generated['products'], seed, constraints)
    return {'kind': 'candidate_library', 'starting_smiles': seed, 'candidates': candidates,
            'constraints': constraints, 'exclusions': exclusions,
            'enumeration': yield_summary(settings['max_total_products'], generated.get('source_count', len(generated['products'])), len(generated['products']), candidates, exclusions, generated.get('backend_filtered_count', 0)),
            'provenance': {'tool': 'HEALER', 'version': generated['version'], 'rdkit_version': rdBase.rdkitVersion,
                           'stock': stock, 'mode': mode, 'seed': payload['seed'],
                           **settings, 'max_candidates': settings['max_total_products'],
                           'source_count': len(generated['products']), 'reaction_rows': generated['rows']}}
