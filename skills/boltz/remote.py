"""Single-phase Boltz HTTP handlers with separate provider metric semantics."""
import json
import math
import re
import time

import httpx




def _request(context, method, path, **kwargs):
    headers = {'Accept': 'application/json'}
    if context.secret:
        headers['Authorization'] = 'Bearer ' + context.secret.get_secret_value()
    deadline = time.monotonic() + 30
    try:
        with httpx.Client(timeout=httpx.Timeout(20, connect=5), follow_redirects=False,
                          transport=httpx.HTTPTransport(retries=0), trust_env=False) as client:
            with client.stream(method, context.service.endpoint.rstrip('/') + path,
                               headers=headers, **kwargs) as response:
                if not 200 <= response.status_code < 300:
                    raise ValueError()
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > 4_000_000 or time.monotonic() > deadline:
                        raise ValueError()
                return json.loads(content)
    except Exception:
        raise RuntimeError('Remote request failed; inspect job before resubmitting') from None


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', value):
        raise ValueError('Invalid remote job identifier')
    return value


def _number(value, *, confidence=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Boltz returned a non-finite metric')
    if confidence and not 0 <= value <= 1:
        raise ValueError('Boltz confidence metric is outside its documented range')
    return value



def submit_prediction(payload, context):
    molecule, = payload['molecules']
    used = {p['chain_id'] for p in payload['proteins']}
    chain = 'L'
    while chain in used:
        chain += 'L'
    document = {'version': 1, 'sequences': [
        {'protein': {'id': p['chain_id'], 'sequence': p['sequence']}}
        for p in payload['proteins']] + [{'ligand': {'id': chain, 'smiles': molecule['smiles']}}],
        'properties': [{'affinity': {'binder': chain}}]}
    # JSON is valid YAML; this preserves BoltzTool._affinity_yaml without LangChain imports.
    response = _request(context, 'POST', '/submit_yaml_text', json={'yaml_text': json.dumps(document)})
    return {'remote_id': _id(response.get('job_id'))}


def poll_prediction(remote_id, payload, context):
    response = _request(context, 'GET', '/jobs/' + _id(remote_id))
    status = response.get('status')
    if status in {'pending', 'queued', 'submitted', 'running'}:
        return {'status': 'running'}
    if status in {'failed', 'cancelled'}:
        return {'status': status}
    if status != 'completed':
        raise ValueError('Unknown self-hosted Boltz job state')
    affinity = response.get('affinity')
    if isinstance(affinity, dict):
        affinity = affinity.get('affinity_pred_value')
    metrics = {'affinity': _number(affinity)}
    confidence = response.get('confidence')
    if isinstance(confidence, dict):
        confidence = confidence.get('confidence_score')
    if confidence is not None:
        metrics['binding_confidence'] = _number(confidence, confidence=True)
    return {'status': 'succeeded', 'result': {
        'kind': 'boltz_prediction', 'provider': 'self_hosted',
        'protein_scope_id': payload['protein_scope_id'],
        'measurement': {**payload['molecules'][0], 'metrics': metrics}}}
