"""Scientific data contracts shared by discovery skills; no model-written measurements."""
import math
from hashlib import sha256
from rdkit import Chem, rdBase
from rdkit.Chem import Descriptors, QED

DESCRIPTORS = {
    'molecular_weight': (Descriptors.MolWt, 'g/mol'), 'logp': (Descriptors.MolLogP, 'dimensionless'),
    'tpsa': (Descriptors.TPSA, 'angstrom^2'), 'h_bond_donors': (Descriptors.NumHDonors, 'count'),
    'h_bond_acceptors': (Descriptors.NumHAcceptors, 'count'),
    'rotatable_bonds': (Descriptors.NumRotatableBonds, 'count'), 'qed': (QED.qed, 'dimensionless'),
}


def canonical(smiles):
    with rdBase.BlockLogs():
        mol = Chem.MolFromSmiles(smiles)
    if mol is None or not mol.GetNumAtoms() or len(Chem.GetMolFrags(mol)) != 1:
        raise ValueError('Invalid or disconnected structure')
    return Chem.MolToSmiles(mol, isomericSmiles=True)


def filter_library(products, seed, constraints):
    seed = canonical(seed)
    scaffold = None
    if constraints.get('scaffold_smarts'):
        scaffold = Chem.MolFromSmarts(constraints['scaffold_smarts'])
        if scaffold is None or not Chem.MolFromSmiles(seed).HasSubstructMatch(scaffold, useChirality=True):
            raise ValueError('Required scaffold must be valid and present in the starting molecule')
    filters = constraints.get('property_filters', [])
    if len({f['property'] for f in filters}) != len(filters):
        raise ValueError('Duplicate property constraints')
    for f in filters:
        if f['minimum'] > f['maximum']:
            raise ValueError('Invalid property bounds')
    candidates, exclusions, seen = [], [], set()
    for index, value in enumerate(products):
        try:
            smi = canonical(value)
        except ValueError:
            exclusions.append({'source_index': index, 'reason': 'invalid_structure'})
            continue
        if smi == seed or smi in seen:
            exclusions.append({'source_index': index, 'reason': 'seed_or_duplicate'})
            continue
        seen.add(smi)
        mol = Chem.MolFromSmiles(smi)
        if scaffold is not None and not mol.HasSubstructMatch(scaffold, useChirality=True):
            exclusions.append({'source_index': index, 'reason': 'scaffold_constraint'})
            continue
        values = {f['property']: float(DESCRIPTORS[f['property']][0](mol)) for f in filters}
        if any(not math.isfinite(values[f['property']]) or not f['minimum'] <= values[f['property']] <= f['maximum'] for f in filters):
            exclusions.append({'source_index': index, 'reason': 'property_constraint'})
            continue
        candidates.append({'id': 'mol-' + sha256(smi.encode()).hexdigest()[:20], 'smiles': smi})
    return candidates, exclusions


def resolve_library(results, action_id):
    library = results.get(action_id)
    if not library or library.get('kind') != 'candidate_library':
        raise ValueError('Expected a successful candidate-library action from this campaign')
    if len({c['id'] for c in library['candidates']}) != len(library['candidates']):
        raise ValueError('Duplicate candidate IDs')
    return library


def measurements(results, library_id, action_ids, targets):
    library = resolve_library(results, library_id)
    candidates = {c['id']: c['smiles'] for c in library['candidates']}
    from .scoring import SCORES, prediction_observations
    rows, provenance = {}, {}
    names = {t['name'] for t in targets}
    units = {name: definition[1] for name, definition in DESCRIPTORS.items()}
    units.update({name: definition['unit'] for name, definition in SCORES.items()})
    if len(set(action_ids)) != len(action_ids):
        raise ValueError('Duplicate measurement references')
    for action_id in action_ids:
        recorded = results.get(action_id)
        if not recorded:
            raise ValueError('Missing recorded measurements')
        for batch in prediction_observations(recorded):
            if batch.get('library_action_id') != library_id:
                raise ValueError('Measurements must reference the same recorded library')
            for observation in batch['observations']:
                candidate_id = observation.get('candidate_id')
                if candidate_id not in candidates or observation['canonical_smiles'] != canonical(candidates[candidate_id]):
                    raise ValueError('Measurement identity mismatch')
                row = rows.setdefault(candidate_id, {'Molecule_ID': candidate_id})
                for item in observation['values']:
                    prop, value = item['property'], item['value']
                    if prop not in names:
                        continue
                    score = SCORES.get(prop)
                    expected_method = score['method'] if score else 'RDKit'
                    expected_source = 'predicted' if score else 'computed'
                    scope = observation.get('protein_scope_id')
                    if observation['method'] != expected_method or observation['source'] != expected_source:
                        raise ValueError('Unsupported measurement method')
                    if score and (not isinstance(scope, str) or not scope.startswith('boltz-target:')):
                        raise ValueError('A protein target scope is required')
                    if item['unit'] != units[prop] or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                        raise ValueError('Invalid measurement unit or value')
                    signature = (observation['method'], observation['method_version'], observation['source'], item['unit'], scope)
                    if prop in provenance and provenance[prop] != signature:
                        raise ValueError('Cannot combine versions, methods, units or protein targets within an optimization campaign')
                    provenance[prop] = signature
                    if prop in row and row[prop] != value:
                        raise ValueError('Conflicting repeated measurements')
                    row[prop] = value
    scopes = {signature[-1] for name, signature in provenance.items() if name in SCORES}
    if len(scopes) > 1:
        raise ValueError('Cannot combine protein targets across objectives')
    complete = [r for r in rows.values() if names <= r.keys()]
    incomplete = [key for key, r in rows.items() if not names <= r.keys()]
    return library, complete, incomplete


def validate_targets(targets, strategy):
    if len({t['name'] for t in targets}) != len(targets):
        raise ValueError('Duplicate targets')
    if strategy == 'single' and len(targets) != 1:
        raise ValueError('Single-objective strategy requires one target')
    for target in targets:
        from .scoring import SCORES
        if target['name'] not in DESCRIPTORS and target['name'] not in SCORES:
            raise ValueError('Unsupported objective')
        if target['name'] in SCORES and target['mode'] != SCORES[target['name']]['mode']:
            raise ValueError('Use the provider-defined optimization direction for predicted scores')
        bounds = target.get('bounds')
        if bounds is not None and (len(bounds) != 2 or bounds[0] >= bounds[1]):
            raise ValueError('Target bounds must increase')
        if (target['mode'] == 'MATCH' or strategy == 'desirability') and bounds is None:
            raise ValueError('MATCH and desirability require fixed normalization bounds')
