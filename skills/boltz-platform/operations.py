"""Queue target-scoped Boltz predictions and library screens."""
import hashlib
import json


def _proteins(proteins):
    normalized, chains = [], set()
    for protein in proteins:
        sequence = ''.join(protein['sequence'].split()).upper()
        if not sequence or any(letter not in 'ACDEFGHIKLMNPQRSTVWYXBZUO' for letter in sequence):
            raise ValueError('Explicit protein amino-acid sequences are required')
        chain = protein['chain_id']
        if chain in chains:
            raise ValueError('Protein chain IDs must be unique')
        chains.add(chain)
        normalized.append({'sequence': sequence, 'chain_id': chain})
    return sorted(normalized, key=lambda row: row['chain_id'])


def _payload(inputs, molecules):
    if not molecules or len(molecules) > 100 or len({m['id'] for m in molecules}) != len(molecules):
        raise ValueError('Provide between one and 100 uniquely identified molecules')
    proteins = _proteins(inputs['proteins'])
    # Include exact sequences and chain assignment; scores compare only within this scope.
    scope = hashlib.sha256(json.dumps(proteins, sort_keys=True).encode()).hexdigest()
    return {'molecules': molecules, 'proteins': proteins, 'protein_scope_id': 'boltz-target:' + scope}


def screen(inputs, context):
    library = context.results.get(inputs['library_action_id'])
    if not library or library.get('kind') != 'candidate_library':
        raise ValueError('A successful candidate-library action is required')
    molecules = [{'id': row['id'], 'smiles': row['smiles']} for row in library['candidates']]
    payload = _payload(inputs, molecules)
    payload['library_action_id'] = inputs['library_action_id']
    return context.submit_job('screen', payload)


