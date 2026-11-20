"""Authenticated CAS Common Chemistry lookup with original scientific records."""
import json
import re
import time
from datetime import datetime, timezone
import requests
from rdkit import Chem, rdBase

ORIGIN = 'https://commonchemistry.cas.org/api-overview'


def _cas(value):
    if not isinstance(value, str): raise ValueError('CAS Registry Number must be text')
    value = value.strip()
    if not re.fullmatch(r'[1-9][0-9]{1,6}-[0-9]{2}-[0-9]', value):
        raise ValueError('Use a hyphenated CAS Registry Number')
    digits = value.replace('-', '')
    if sum(int(digit)*weight for weight, digit in enumerate(reversed(digits[:-1]), 1)) % 10 != int(digits[-1]):
        raise ValueError('Invalid CAS Registry Number checksum')
    return value


def _request(context, path, params, *, accept='application/json'):
    if context.secret is None:
        raise ValueError('Configure CAS_API_KEY in the service binding')
    started = time.monotonic()
    try:
        with requests.get(context.service.endpoint.rstrip('/') + path, params=params,
                          headers={'Accept': accept, 'x-origin': ORIGIN,
                                   'X-API-KEY': context.secret.get_secret_value()},
                          timeout=(5, 20), allow_redirects=False, stream=True) as response:
            if response.status_code in (401, 403): return 'authentication_required', None
            if response.status_code == 404: return 'not_found', None
            if response.status_code == 429: return 'rate_limited', None
            if response.status_code != 200: return 'unavailable', None
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=16384):
                size += len(chunk)
                if size > 2_000_000 or time.monotonic() - started > 30:
                    return 'invalid_response', None
                chunks.append(chunk)
        return 'resolved', b''.join(chunks).decode('utf-8')
    except requests.RequestException:
        return 'unavailable', None
    except UnicodeError:
        return 'invalid_response', None


def _provenance():
    return {'source': 'CAS Common Chemistry', 'retrieved_at': datetime.now(timezone.utc).isoformat()}


def detail(inputs, context):
    number = _cas(inputs['cas_rn'])
    result = {'kind': 'cas_record', **_provenance(), 'cas_rn': number,
              'source_url': 'https://commonchemistry.cas.org/detail?cas_rn=' + number,
              'status': 'invalid_response', 'record': None, 'smiles': None}
    status, body = _request(context, '/detail', {'cas_rn': number})
    if status != 'resolved': return {**result, 'status': status}
    try:
        data = json.loads(body)
        if not isinstance(data, dict) or data.get('error') or data.get('rn') != number:
            raise ValueError('CAS response does not match the requested registry number')
        if not isinstance(data.get('name'), str):
            raise ValueError('CAS record has no substance identity')
        properties = data.get('experimentalProperties', [])
        if not isinstance(properties, list) or any(not isinstance(p, dict) or not isinstance(p.get('name'), str) or not isinstance(p.get('property'), str) for p in properties):
            raise ValueError('Invalid property records')
        smiles = data.get('isomericSmile') or data.get('smile') or data.get('canonicalSmile')
        if smiles:
            if not isinstance(smiles, str) or len(smiles) > 10000:
                raise ValueError('Invalid structure')
            with rdBase.BlockLogs(): molecule = Chem.MolFromSmiles(smiles)
            if molecule is None or not molecule.GetNumAtoms(): raise ValueError('Invalid structure')
            smiles = Chem.MolToSmiles(molecule, isomericSmiles=True)
        # Drop provider SVG markup; retain scientific values, conditions and citations verbatim.
        data.pop('image', None)
        blocks = [{'type': 'table', 'title': 'Reported experimental properties',
                   'columns': [{'key':'name','label':'Property'}, {'key':'value','label':'Reported value'}, {'key':'citation','label':'Source number'}],
                   'rows': [{'name': p['name'], 'value': p['property'], 'citation': str(p.get('sourceNumber', ''))} for p in properties]}]
        if smiles: blocks.append({'type':'molecules','title':'Reported structure','items':[{'id':number,'smiles':smiles}]})
        return {**result, 'status': 'resolved', 'record': data, 'smiles': smiles or None,
                'presentation': {'version':1, 'title':'CAS lookup · '+number,
                    'summary': data['name'][:800] + '. Database-reported values; retain units and conditions. Full citations are in source details.', 'blocks':blocks}}
    except (ValueError, TypeError):
        return result


def search(inputs, context):
    query, offset, size = inputs['query'].strip(), inputs.get('offset', 0), inputs.get('size', 20)
    if not query: raise ValueError('A search query is required')
    result = {'kind': 'cas_search', **_provenance(), 'query': query, 'offset': offset, 'size': size,
              'status': 'invalid_response', 'count': None, 'matches': [], 'has_more': False}
    status, body = _request(context, '/search', {'q': query, 'offset': offset, 'size': size})
    if status != 'resolved': return {**result, 'status': status}
    try:
        data = json.loads(body)
        count, rows = data['count'], data['results']
        if isinstance(count, str) and re.fullmatch(r'[0-9]+', count): count = int(count)
        if type(count) is not int or count < 0 or not isinstance(rows, list) or len(rows) > size:
            raise ValueError('Invalid search page')
        matches = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('name'), str): raise ValueError('Invalid search identity')
            matches.append({'cas_rn': _cas(row['rn']), 'name': row['name']})
        if len({r['cas_rn'] for r in matches}) != len(matches) or len(matches) > max(0, count-offset):
            raise ValueError('Inconsistent search page')
        return {**result, 'status': 'resolved', 'count': count, 'matches': matches,
                'has_more': offset+len(matches) < count,
                'presentation': {'version': 1, 'title': 'CAS search results',
                    'summary': f'{count} matching substances; showing {len(matches)} at offset {offset}. Select an identity before retrieving details.',
                    'blocks': [{'type': 'table', 'title': 'Substances', 'columns': [{'key':'cas_rn','label':'CAS RN'}, {'key':'name','label':'Name'}], 'rows':matches}]}}
    except (ValueError, TypeError, KeyError):
        return result


def export_molfile(inputs, context):
    detail_result = context.results.get(inputs['detail_action_id'])
    if not detail_result or detail_result.get('kind') != 'cas_record' or detail_result.get('status') != 'resolved':
        raise ValueError('Reference a resolved CAS detail action from this campaign')
    record = detail_result['record']
    uri = record.get('uri')
    if not isinstance(uri, str) or not re.fullmatch(r'substance/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+', uri):
        raise ValueError('The CAS record has no supported export URI')
    result = {'kind': 'cas_structure', **_provenance(), 'cas_rn': detail_result['cas_rn'],
              'source_url': detail_result['source_url'], 'detail_action_id': inputs['detail_action_id'],
              'status': 'invalid_response', 'artifact': None}
    status, body = _request(context, '/export', {'uri': uri, 'returnAsAttachment': 'false'}, accept='text/plain')
    if status != 'resolved': return {**result, 'status': status}
    if context.secret and context.secret.get_secret_value() in body: return result
    with rdBase.BlockLogs(): molecule = Chem.MolFromMolBlock(body, sanitize=True, removeHs=True)
    if molecule is None or not molecule.GetNumAtoms(): return result
    if detail_result.get('smiles') and Chem.MolToSmiles(molecule, isomericSmiles=True) != detail_result['smiles']:
        return result
    if context.write_artifact is None: raise ValueError('Campaign artifact storage is required')
    artifact = context.write_artifact(detail_result['cas_rn'] + '.mol', body)
    return {**result, 'status': 'resolved', 'artifact': artifact}
