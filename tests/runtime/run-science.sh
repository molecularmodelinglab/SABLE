#!/usr/bin/env bash
# Reuse the scientific dependency image; mount only reviewed source paths read-only.
set -euo pipefail
runtime_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mounts=()
for source_path in sable_runtime skills tools schemas utils nodes config tests/runtime tests/unit server/routers/agent_campaigns.py server/services/campaign_runner.py server/services/campaign_credentials.py server/services/boltz_access_service.py server/services/credential_service.py server/models server/database.py server/schemas; do
  mounts+=(--mount "type=bind,src=$runtime_root/$source_path,dst=/workspace/$source_path,readonly")
done
exec docker run --rm --network none --read-only --tmpfs /tmp \
  -e SABLE_WORKER_ENV_ALLOWLIST=HEALER_DATA_DIR -e MPLCONFIGDIR=/tmp/matplotlib -e XDG_CACHE_HOME=/tmp/cache \
  "${mounts[@]}" sable-science-tests "$@"
