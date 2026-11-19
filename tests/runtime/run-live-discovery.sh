#!/usr/bin/env bash
# Explicit live-model smoke test; no production campaign or database is used.
set -euo pipefail
runtime_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
config_file="${1:?Pass the absolute path to an authorized model configuration file}"
mounts=()
for source_path in sable_runtime skills tools schemas utils nodes config tests/runtime server/routers/agent_campaigns.py server/services/campaign_credentials.py; do
  mounts+=(--mount "type=bind,src=$runtime_root/$source_path,dst=/workspace/$source_path,readonly")
done
exec docker run --rm --read-only --tmpfs /tmp "${mounts[@]}" \
  -e SABLE_V2_WORKER_ENV_ALLOWLIST=HEALER_DATA_DIR -e MPLCONFIGDIR=/tmp/matplotlib -e XDG_CACHE_HOME=/tmp/cache \
  --mount "type=bind,src=$config_file,dst=/tmp/sable.env,readonly" \
  sable-science-tests tests/runtime/live_discovery_smoke.py
