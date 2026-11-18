#!/usr/bin/env bash
# Run contributor commands in Docker, writing only to an explicitly selected work directory.
set -euo pipefail
runtime_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
work_dir="${1:?Provide an existing absolute contributor work directory}"
shift
if [[ "$work_dir" != /* || ! -d "$work_dir" ]]; then
  echo 'The contributor work directory must be an existing absolute path' >&2
  exit 2
fi
exec docker run --rm --network none --read-only --tmpfs /tmp \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$runtime_root/sable_runtime,dst=/workspace/sable_runtime,readonly" \
  --mount "type=bind,src=$work_dir,dst=/work" \
  --workdir /work sable-science-tests -m sable_runtime.skills "$@"
