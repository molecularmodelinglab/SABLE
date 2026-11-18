import math
from rdkit import Chem, rdBase
from rdkit.Chem import Descriptors, QED


def calculate(inputs, context):
    source = None
    library_id = None
    selected = None
    if 'source_action_id' in inputs:
        source = context.results.get(inputs['source_action_id'])
        if not source or source.get('kind') not in {'candidate_library', 'recommendation'}:
            raise ValueError('Expected a candidate library or recommendation from this campaign')
        library_id = inputs['source_action_id'] if source['kind'] == 'candidate_library' else source['library_action_id']
        by_id = {c['id']: c for c in source['candidates']}
        ids = inputs.get('candidate_ids', list(by_id))
        if not ids or len(ids) > 32 or not set(ids) <= by_id.keys():
            raise ValueError('Select one to 32 candidates from the recorded source')
        selected = [by_id[i] for i in ids]
        smiles_inputs = [c['smiles'] for c in selected]
    else:
        smiles_inputs = inputs['smiles']
    observations, failures = [], []
    properties = {
        'molecular_weight': (Descriptors.MolWt, 'g/mol'),
        'logp': (Descriptors.MolLogP, 'dimensionless'),
        'tpsa': (Descriptors.TPSA, 'angstrom^2'),
        'h_bond_donors': (Descriptors.NumHDonors, 'count'),
        'h_bond_acceptors': (Descriptors.NumHAcceptors, 'count'),
        'rotatable_bonds': (Descriptors.NumRotatableBonds, 'count'),
        'qed': (QED.qed, 'dimensionless'),
    }
    for index, smiles in enumerate(smiles_inputs):
        # RDKit parser diagnostics can repeat input contents on stderr.
        with rdBase.BlockLogs():
            molecule = Chem.MolFromSmiles(smiles)
        if molecule is None or molecule.GetNumAtoms() == 0 or len(Chem.GetMolFrags(molecule)) != 1:
            failures.append({'input_index': index, 'input_smiles': smiles, 'error': 'invalid_or_disconnected_structure'})
            continue
        values = []
        for name in inputs['properties']:
            function, unit = properties[name]
            value = float(function(molecule))
            if not math.isfinite(value):
                raise ValueError('Non-finite descriptor')
            values.append({'property': name, 'value': value, 'unit': unit})
        observations.append({'input_index': index, 'input_smiles': smiles,
                             'canonical_smiles': Chem.MolToSmiles(molecule, isomericSmiles=True),
                             'method': 'RDKit', 'method_version': rdBase.rdkitVersion,
                             'source': 'computed', 'values': values})
        if selected is not None:
            observations[-1]['candidate_id'] = selected[index]['id']
    result = {'observations': observations, 'failures': failures}
    if library_id is not None:
        result['library_action_id'] = library_id
    return result
