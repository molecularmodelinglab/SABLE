"""Queue explicitly sequenced OpenFE phases using owned result references."""
import hashlib
import io
import math

from rdkit import Chem


def _validate_ligand(text, smiles):
    supplier = Chem.ForwardSDMolSupplier(io.BytesIO(text.encode()), removeHs=False)
    molecules = list(supplier)
    if len(molecules) != 1 or molecules[0] is None:
        raise ValueError('Each ligand SDF must contain exactly one readable molecule')
    molecule = molecules[0]
    declared = Chem.MolFromSmiles(smiles)
    if declared is None or not declared.GetNumAtoms():
        raise ValueError('Invalid declared ligand SMILES')
    graph = lambda mol: Chem.MolToSmiles(Chem.RemoveHs(mol), canonical=True, isomericSmiles=True)
    if graph(molecule) != graph(declared):
        raise ValueError('Ligand SDF graph or stereochemistry does not match its declared SMILES')
    if molecule.GetNumConformers() != 1 or not molecule.GetConformer().Is3D():
        raise ValueError('Ligand SDF must contain a three-dimensional conformer')
    coordinates = molecule.GetConformer().GetPositions()
    if any(not math.isfinite(float(value)) for point in coordinates for value in point):
        raise ValueError('Ligand SDF coordinates must be finite')
    if molecule.GetNumAtoms() > 1 and len({tuple(point) for point in coordinates}) < 2:
        raise ValueError('Ligand SDF has no usable conformer coordinates')


def create(inputs, context):
    ligands = [row['id'] for row in inputs['ligands']]
    if len(set(ligands)) != len(ligands):
        raise ValueError('Ligand names must be unique')
    if inputs['protocol'] in {'rbfe', 'septop'}:
        if inputs.get('reference') not in ligands or len(ligands) < 2:
            raise ValueError('Network calculations require a reference and at least one candidate')
    elif 'reference' in inputs:
        raise ValueError('ABFE is absolute and does not accept a reference offset')
    payload = dict(inputs)
    payload.pop('protein_artifact_id')
    payload['protein_pdb'] = context.read_artifact(inputs['protein_artifact_id'])
    payload['ligands'] = [{'id': row['id'], 'smiles': row['smiles'],
                           'sdf': context.read_artifact(row['artifact_id'])} for row in inputs['ligands']]
    coordinates = [payload['protein_pdb'], *(row['sdf'] for row in payload['ligands'])]
    if any(not isinstance(text, str) or not text.strip() for text in coordinates):
        raise ValueError('OpenFE requires nonempty owned coordinate artifacts')
    if sum(len(text.encode()) for text in coordinates) > 3_000_000:
        raise ValueError('OpenFE coordinate upload exceeds package limit')
    if not any(line.startswith(('ATOM  ', 'HETATM')) for line in payload['protein_pdb'].splitlines()):
        raise ValueError('Protein PDB must contain ATOM or HETATM records')
    for ligand in payload['ligands']:
        _validate_ligand(ligand['sdf'], ligand['smiles'])
    payload['origin_action_id'] = context.action_id
    payload['campaign_name'] = _name(context.campaign_id, context.action_id)
    return context.submit_job('create', payload)


def _name(campaign_id, action_id):
    return 'sable_' + hashlib.sha256((campaign_id + ':' + action_id).encode()).hexdigest()[:32]


def _previous(inputs, context):
    previous = context.results.get(inputs['previous_action_id'])
    if not previous or previous.get('kind') != 'openfe_campaign':
        raise ValueError('An owned OpenFE campaign result is required')
    if previous['campaign_name'] != _name(context.campaign_id, previous['origin_action_id']):
        raise ValueError('OpenFE campaign does not belong to this campaign')
    return {key: previous[key] for key in (
        'campaign_name', 'origin_action_id', 'protocol', 'ligands', 'reference', 'repeats', 'stage')}


def advance(inputs, context):
    payload = _previous(inputs, context)
    expected = {'prep': 'created', 'plan': 'prep', 'submit': 'plan'}[inputs['stage']]
    if payload['stage'] != expected:
        raise ValueError('OpenFE phases must follow create, prep, plan, submit')
    payload['stage'] = inputs['stage']
    return context.submit_job('stage', payload)


def collect(inputs, context):
    payload = _previous(inputs, context)
    if payload['stage'] != 'submit':
        raise ValueError('OpenFE collection requires a completed submit phase')
    payload['allow_failed_quality'] = inputs.get('allow_failed_quality', False)
    return context.submit_job('collect', payload)
