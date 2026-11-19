"""Resolve canonical UniProt accessions to sourced protein sequences."""
import json
import re
import time
from datetime import datetime, timezone
import requests

ACCESSION = r'(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})'


def _get(url, limit=3_000_000):
    started = time.monotonic()
    with requests.get(url, timeout=(5, 20), allow_redirects=False, stream=True) as response:
        if response.status_code == 404:
            raise LookupError('No record was found for this identifier')
        if response.status_code != 200:
            raise ValueError(f'Protein resource service returned HTTP {response.status_code}')
        chunks, size = [], 0
        for chunk in response.iter_content(chunk_size=16384):
            size += len(chunk)
            if size > limit or time.monotonic() - started > 30:
                raise ValueError('Protein resource exceeds the response size or time limit')
            chunks.append(chunk)
    return b''.join(chunks).decode('utf-8')


def _accession(value):
    value = value.strip().upper()
    if not re.fullmatch(ACCESSION, value):
        raise ValueError('Provide a canonical UniProt accession; isoforms require an explicit sequence')
    return value


def _source(source, identifier, url):
    return {'source': source, 'identifier': identifier, 'source_url': url,
            'retrieved_at': datetime.now(timezone.utc).isoformat()}


def resolve(inputs, context):
    accession = _accession(inputs['accession'])
    url = context.service.endpoint.rstrip('/') + '/uniprotkb/' + accession + '.json'
    data = json.loads(_get(url))
    if data.get('primaryAccession') != accession:
        raise ValueError('UniProt returned a different accession; resolve the target identity before proceeding')
    sequence = data['sequence']['value']
    if not isinstance(sequence, str) or not re.fullmatch('[ACDEFGHIKLMNPQRSTVWYXBZUO]{1,100000}', sequence):
        raise ValueError('UniProt returned an invalid protein sequence')
    if data['sequence'].get('length') != len(sequence):
        raise ValueError('UniProt sequence length does not match its metadata')
    description = data.get('proteinDescription', {})
    name = description.get('recommendedName') or next(iter(description.get('submissionNames', [])), {})
    return {'kind': 'protein_target', **_source('UniProt', accession, url),
            'protein_name': name.get('fullName', {}).get('value', ''),
            'organism': data.get('organism', {}).get('scientificName', ''),
            'sequence': sequence, 'length': len(sequence),
            'sequence_version': data.get('entryAudit', {}).get('sequenceVersion')}

