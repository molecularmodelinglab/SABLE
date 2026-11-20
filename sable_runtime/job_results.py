"""Resolve action-level evidence only after every job in a batch is terminal."""
from collections import defaultdict

TERMINAL = {'succeeded', 'partial', 'failed', 'cancelled', 'timed_out'}


def resolved_job_results(jobs):
    groups = defaultdict(list)
    for job in jobs:
        groups[job['action']].append(job)
    results = {}
    for action, members in groups.items():
        if not all(job['status'] in TERMINAL for job in members):
            continue
        successful = [job['result'] for job in members if job['status'] in {'succeeded', 'partial'}]
        failures = [{'job_id': job['id'], 'status': job['status'], 'error': job.get('error')}
                    for job in members if job['status'] not in {'succeeded', 'partial'}]
        if len(members) == 1:
            results[action] = successful[0] if successful else {'kind': 'job_outcome', **failures[0]}
        else:
            results[action] = {'kind': 'remote_batch_result', 'results': successful, 'failures': failures}
    return results
