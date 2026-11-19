"""Resolve chemical names through a configured PubChem PUG REST service."""
import json
import time
from datetime import datetime, timezone

import requests
from rdkit import Chem, rdBase


def resolve(inputs, context):
    name = inputs['name'].strip()
    result = {'query': name, 'status': 'unavailable', 'matches': [], 'truncated': False,
              'source': 'PubChem', 'retrieved_at': datetime.now(timezone.utc).isoformat(),
              'method_version': rdBase.rdkitVersion}
    if not name:
        raise ValueError('A chemical name is required')
    endpoint = context.service.endpoint.rstrip('/')
    try:
        started = time.monotonic()
        with requests.post(endpoint + '/compound/name/property/IsomericSMILES,InChIKey,IUPACName/JSON',
                           data={'name': name, 'name_type': 'complete'},
                           timeout=(5, 20), allow_redirects=False, stream=True) as response:
            if response.status_code == 404:
                return {**result, 'status': 'not_found'}
            if response.status_code != 200:
                return result
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=16384):
                size += len(chunk)
                if size > 1_000_000 or time.monotonic() - started > 30:
                    return {**result, 'status': 'invalid_response'}
                chunks.append(chunk)
        rows = json.loads(b''.join(chunks))['PropertyTable']['Properties']
        if not isinstance(rows, list) or not rows:
            return {**result, 'status': 'invalid_response'}
        matches = {}
        for row in rows:
            cid = row['CID']
            smiles = row.get('SMILES') or row.get('IsomericSMILES')
            if type(cid) is not int or cid < 1 or not isinstance(smiles, str) or not 0 < len(smiles) <= 2048:
                raise ValueError('Invalid compound record')
            with rdBase.BlockLogs():
                molecule = Chem.MolFromSmiles(smiles)
            if molecule is None or molecule.GetNumAtoms() == 0:
                raise ValueError('Invalid structure')
            match = {'cid': cid, 'smiles': Chem.MolToSmiles(molecule, isomericSmiles=True),
                     'source_smiles': smiles, 'source_url': f'https://pubchem.ncbi.nlm.nih.gov/compound/{cid}',
                     'inchikey': row.get('InChIKey', ''), 'iupac_name': row.get('IUPACName', ''),
                     'components': len(Chem.GetMolFrags(molecule))}
            if any(not isinstance(match[key], str) or len(match[key]) > 4096 for key in ('inchikey', 'iupac_name')):
                raise ValueError('Invalid compound metadata')
            if cid in matches and matches[cid] != match:
                raise ValueError('Conflicting compound records')
            matches[cid] = match
        return {**result, 'status': 'resolved' if len(matches) == 1 else 'ambiguous',
                'matches': list(matches.values())[:20], 'truncated': len(matches) > 20}
    except requests.RequestException:
        return result
    except (ValueError, KeyError, TypeError):
        return {**result, 'status': 'invalid_response'}
