"""Isolate the default optimizer adapter for controlled comparison tests."""
from pathlib import Path
from sable_runtime.contracts import LocalWorker
from sable_runtime.local_worker import run_snapshot


def baseline_worker(payload):
    entry = LocalWorker(file='baseline_baybe.py', handler='run')
    return run_snapshot(entry, Path(__file__).with_name(entry.file).read_bytes(), payload)
