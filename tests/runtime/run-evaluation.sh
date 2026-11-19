#!/usr/bin/env bash
# Run fixture evaluations offline, or opt into a bounded live-model evaluation.
set -euo pipefail
runtime_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ $# -eq 0 ]]; then
  exec bash "$runtime_root/tests/runtime/run-science.sh" tests/runtime/campaign_evaluation.py
fi
config_file="${1:?Pass an absolute model configuration path}"
if [[ "$config_file" != /* || ! -f "$config_file" ]]; then
  echo 'Supply an existing absolute model configuration path, or no arguments for offline checks.' >&2
  exit 2
fi
shift
mounts=()
for source_path in sable_runtime skills tools schemas utils nodes config tests/runtime; do
  mounts+=(--mount "type=bind,src=$runtime_root/$source_path,dst=/workspace/$source_path,readonly")
done
exec docker run --rm --read-only --tmpfs /tmp "${mounts[@]}" \
  --mount "type=bind,src=$config_file,dst=/tmp/sable.env,readonly" \
  sable-science-tests tests/runtime/campaign_evaluation.py --live-config /tmp/sable.env "$@"
