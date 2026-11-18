import math
from rdkit import Chem, rdBase
from rdkit.Chem import Descriptors, QED


def calculate(inputs, context):
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
    for index, smiles in enumerate(inputs['smiles']):
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
    return {'observations': observations, 'failures': failures}
