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



def predict(inputs, context):
    return context.submit_job('prediction', _payload(inputs, [inputs['molecule']]))


def screen(inputs, context):
    """Queue an independently tracked prediction for every selected molecule."""
    from sable_runtime.scoring import selected_candidates, validate_scoring_scope
    library_id, molecules = selected_candidates(inputs, context.results)
    payloads = [{**_payload(inputs, [molecule]), 'library_action_id': library_id} for molecule in molecules]
    validate_scoring_scope(context.results, library_id, payloads[0]['protein_scope_id'])
    return context.submit_jobs('prediction', payloads)
