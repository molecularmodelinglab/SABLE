"""Bounded JSON subprocess transport for host-installed skill snapshots.

This isolates process state; installed Python code is trusted, not sandboxed.
"""
import json
import os
import subprocess
import sys


def run_snapshot(entry, code, payload):
    base_names = {'PATH', 'PYTHONPATH', 'PYTHONDONTWRITEBYTECODE', 'LANG'}
    allowed = set(os.getenv('SABLE_WORKER_ENV_ALLOWLIST', '').split(','))
    requested = set(entry.environment)
    if requested - allowed:
        raise ValueError('Worker environment is not authorized by the deployment')
    env = {k: v for k, v in os.environ.items() if k in base_names | requested}
    env.update(OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
               HOME='/tmp', XDG_CACHE_HOME='/tmp', PYTHONUNBUFFERED='1')
    envelope = {'code': code.decode(), 'file': entry.file, 'handler': entry.handler, 'payload': payload}
    try:
        result = subprocess.run([sys.executable, '-m', 'sable_runtime.worker_process'],
                                input=json.dumps(envelope, allow_nan=False), capture_output=True,
                                text=True, timeout=entry.timeout_seconds, env=env)
    except subprocess.TimeoutExpired:
        raise RuntimeError('Local worker exceeded its time budget') from None
    if result.returncode:
        raise RuntimeError('Local worker failed')
    return json.loads(result.stdout)


def run_worker(skill, name, payload):
    entry = skill.manifest.workers.get(name)
    if entry is None:
        raise ValueError('Worker is not declared by this skill')
    return run_snapshot(entry, skill.files[entry.file], payload)
