#!/usr/bin/env bash
# Send only runtime sources, skills and dedicated tests to the Docker daemon.
set -euo pipefail
runtime_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tar -C "$runtime_root" --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.pytest_cache' -cf - sable_runtime skills tests/runtime \
  | docker build -f tests/runtime/Dockerfile -t sable-runtime-tests -
