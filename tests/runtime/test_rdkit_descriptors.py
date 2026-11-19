"""Check descriptor values, scientific distinctions, and result contracts."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2] / 'skills' / 'rdkit-descriptors'
MANIFEST = json.loads((ROOT / 'manifest.json').read_text())
OPERATION = MANIFEST['operations'][0]
PROPERTIES = OPERATION['input_schema']['properties']['properties']['items']['enum']
spec = importlib.util.spec_from_file_location('rdkit_descriptors', ROOT / 'operations.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def calculate(smiles, properties=PROPERTIES):
    inputs = {'smiles': smiles, 'properties': properties}
    jsonschema.validate(inputs, OPERATION['input_schema'])
    result = module.calculate(inputs, SimpleNamespace(results={}))
    jsonschema.validate(result, OPERATION['output_schema'])
    return result


def values(observation):
    return {value['property']: value['value'] for value in observation['values']}


def test_ethanol_physical_descriptors_and_units():
    result = calculate(['CCO'])
    assert result['failures'] == []
    observation = result['observations'][0]
    data = values(observation)
    assert len(data) == 23
    assert data['molecular_weight'] == pytest.approx(46.069, abs=.001)
    assert data['exact_molecular_weight'] == pytest.approx(46.041865, abs=.000001)
    assert data['heavy_atom_molecular_weight'] == pytest.approx(40.021, abs=.001)
    assert data['logp'] == pytest.approx(-.0014, abs=.0001)
    assert data['molar_refractivity'] == pytest.approx(12.7598, abs=.0001)
    assert data['tpsa'] == pytest.approx(20.23)
    assert data['labute_asa'] == pytest.approx(19.898427, abs=.000001)
    assert data['h_bond_donors'] == data['h_bond_acceptors'] == data['no_count'] == data['nhoh_count'] == 1
    assert data['heavy_atom_count'] == 3
    assert data['heteroatoms'] == 1
    assert data['valence_electrons'] == 20
    assert data['radical_electrons'] == 0
    assert data['fraction_csp3'] == 1
    assert 0 <= data['qed'] <= 1
    units = {item['property']: item['unit'] for item in observation['values']}
    assert units['molecular_weight'] == 'g/mol'
    assert units['exact_molecular_weight'] == 'Da'
    assert units['molar_refractivity'] == 'cm^3/mol'
    assert units['labute_asa'] == units['tpsa'] == 'angstrom^2'
    assert observation['method'] == 'RDKit' and observation['method_version']
    # Below the pharmaceutical reference mass range, but still a valid observation.
    assert data['molecular_weight'] < 150


@pytest.mark.parametrize('smiles,expected', [
    ('c1ccccc1', {'ring_count': 1, 'aromatic_rings': 1, 'aliphatic_rings': 0, 'saturated_rings': 0, 'heterocycles': 0, 'fraction_csp3': 0}),
    ('C1CCNCC1', {'ring_count': 1, 'aromatic_rings': 0, 'aliphatic_rings': 1, 'saturated_rings': 1, 'heterocycles': 1, 'fraction_csp3': 1}),
    ('C1=CCCCC1', {'ring_count': 1, 'aromatic_rings': 0, 'aliphatic_rings': 1, 'saturated_rings': 0}),
    ('[CH3]', {'radical_electrons': 1, 'valence_electrons': 7, 'heavy_atom_count': 1}),
    ('CCCC', {'rotatable_bonds': 1, 'ring_count': 0}),
])
def test_topology_and_electron_counts(smiles, expected):
    data = values(calculate([smiles])['observations'][0])
    for name, expected_value in expected.items():
        assert data[name] == expected_value


def test_isotope_and_invalid_structures():
    result = calculate(['CCO', '[13CH3]CO', 'invalid', 'CCO.O'], ['exact_molecular_weight'])
    masses = [values(row)['exact_molecular_weight'] for row in result['observations']]
    assert masses[1] - masses[0] == pytest.approx(1.003355, abs=.000001)
    assert [row['input_index'] for row in result['failures']] == [2, 3]


def test_source_candidate_identity_with_new_descriptors():
    context = SimpleNamespace(results={'library': {'kind': 'candidate_library', 'candidates': [{'id': 'ethanol', 'smiles': 'CCO'}]}})
    inputs = {'source_action_id': 'library', 'candidate_ids': ['ethanol'], 'properties': ['exact_molecular_weight', 'heavy_atom_count']}
    jsonschema.validate(inputs, OPERATION['input_schema'])
    result = module.calculate(inputs, context)
    jsonschema.validate(result, OPERATION['output_schema'])
    assert result['library_action_id'] == 'library'
    assert result['observations'][0]['candidate_id'] == 'ethanol'
    assert values(result['observations'][0])['heavy_atom_count'] == 3
