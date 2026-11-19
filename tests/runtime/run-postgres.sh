#!/usr/bin/env bash
# Run against a disposable PostgreSQL database supplied on a private Docker network.
set -euo pipefail
runtime_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mounts=()
for source_path in sable_runtime skills server migrations tests/runtime; do
  mounts+=(--mount "type=bind,src=$runtime_root/$source_path,dst=/app/$source_path,readonly")
done
exec docker run --rm --network "${SABLE_POSTGRES_TEST_NETWORK:-sable-campaign-migration-check}" --tmpfs /tmp \
  -e DATABASE_URL=postgresql://postgres:test-only-password@campaign-postgres:5432/campaign_tests \
  -e SABLE_TEST_POSTGRES=1 -e PYTHONDONTWRITEBYTECODE=1 -e PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  -e LLM_PROVIDER=disabled -e PYTHONPATH=/app \
  "${mounts[@]}" --workdir /app --entrypoint /opt/conda/envs/sable/bin/python sable-api:latest "$@"
