import os

from rdkit import Chem
from sable_runtime.science import canonical, filter_library
from sable_runtime.enumeration import enumeration_settings


def enumerate(inputs, context):
    settings = enumeration_settings(inputs)
    starting = canonical(inputs['starting_smiles'])
    constraints = {'scaffold_smarts': inputs.get('scaffold_smarts'),
                   'property_filters': inputs.get('property_filters', [])}
    filter_library([], starting, constraints)
    mode = inputs.get('mode', 'MoleculeHEALER')
    if mode == 'FragmentHEALER':
        raise ValueError('FragmentHEALER is not supported by the HEALER API')
    if mode not in {'MoleculeHEALER', 'SiteHEALER'}:
        raise ValueError('Unsupported HEALER API mode')
    sites = inputs.get('reactive_sites')
    if mode == 'SiteHEALER' and not sites:
        raise ValueError('Site enumeration requires explicit reactive atom indices')
    if sites:
        atom_count = Chem.MolFromSmiles(inputs['starting_smiles']).GetNumAtoms()
        if mode != 'SiteHEALER' or any(type(i) is not int or not 0 <= i < atom_count for i in sites):
            raise ValueError('Reactive atom indices must refer to the input structure in SiteHEALER mode')
    # Preserve input atom ordering for SiteHEALER; canonicalize only the result.
    payload = {**inputs, 'mode': mode, 'constraints': constraints,
               'stock': os.getenv('HEALER_STOCK', 'US_stock'),
               'reaction_tags': inputs.get('reaction_tags', ['all']),
               **settings, 'max_candidates': settings['max_total_products']}
    return context.submit_job('enumeration', payload)
