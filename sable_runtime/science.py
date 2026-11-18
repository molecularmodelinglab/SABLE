"""Scientific data contracts shared by discovery skills; no model-written measurements."""
import json
import math
import os
import subprocess
import sys
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
    rows, versions = {}, set()
    names = {t['name'] for t in targets}
    for action_id in action_ids:
        batch = results.get(action_id)
        if not batch or batch.get('library_action_id') != library_id:
            raise ValueError('Measurements must reference the same recorded library')
        for observation in batch['observations']:
            candidate_id = observation.get('candidate_id')
            if candidate_id not in candidates or observation['canonical_smiles'] != candidates[candidate_id]:
                raise ValueError('Measurement identity mismatch')
            if observation['method'] != 'RDKit' or observation['source'] != 'computed':
                raise ValueError('Unsupported measurement method')
            versions.add(observation['method_version'])
            row = rows.setdefault(candidate_id, {'Molecule_ID': candidate_id})
            for item in observation['values']:
                prop, value = item['property'], item['value']
                if prop not in names:
                    continue
                if item['unit'] != DESCRIPTORS[prop][1] or not math.isfinite(value):
                    raise ValueError('Invalid measurement unit or value')
                if prop in row and row[prop] != value:
                    raise ValueError('Conflicting repeated measurements')
                row[prop] = value
    if len(versions) > 1:
        raise ValueError('Cannot combine RDKit versions within an optimization campaign')
    complete = [r for r in rows.values() if names <= r.keys()]
    incomplete = [key for key, r in rows.items() if not names <= r.keys()]
    return library, complete, incomplete


def validate_targets(targets, strategy):
    if len({t['name'] for t in targets}) != len(targets):
        raise ValueError('Duplicate targets')
    if strategy == 'single' and len(targets) != 1:
        raise ValueError('Single-objective strategy requires one target')
    for target in targets:
        if target['name'] not in DESCRIPTORS:
            raise ValueError('Unsupported objective')
        bounds = target.get('bounds')
        if bounds is not None and (len(bounds) != 2 or bounds[0] >= bounds[1]):
            raise ValueError('Target bounds must increase')
        if (target['mode'] == 'MATCH' or strategy == 'desirability') and bounds is None:
            raise ValueError('MATCH and desirability require fixed normalization bounds')


def worker(kind, payload):
    env = {k: v for k, v in os.environ.items() if k in {
        'PATH', 'PYTHONPATH', 'PYTHONDONTWRITEBYTECODE', 'HEALER_DATA_DIR', 'LANG'}}
    env.update(OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
               HOME='/tmp', XDG_CACHE_HOME='/tmp', PYTHONUNBUFFERED='1')
    try:
        result = subprocess.run([sys.executable, '-m', 'sable_runtime.scientific_worker', kind],
                                input=json.dumps(payload, allow_nan=False), capture_output=True, text=True,
                                timeout=120, env=env)
    except subprocess.TimeoutExpired:
        raise RuntimeError('Local scientific operation exceeded its time budget') from None
    if result.returncode:
        raise RuntimeError('Scientific worker failed')
    return json.loads(result.stdout)
