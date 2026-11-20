#!/usr/bin/env bash
set -euo pipefail
runtime_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tar -C "$runtime_root" --exclude='__pycache__' --exclude='*.pyc' --exclude='.pytest_cache' \
  -cf - sable_runtime skills tools schemas utils nodes config server/routers/agent_campaigns.py server/services/campaign_runner.py server/services/campaign_credentials.py server/services/boltz_access_service.py server/services/credential_service.py server/models server/database.py server/schemas tests/runtime tests/unit \
  | docker build -f tests/runtime/Dockerfile.science -t sable-science-tests -
