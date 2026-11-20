#!/usr/bin/env bash
# Type-check, build, and exercise campaign controls in a disposable container.
set -euo pipefail
runtime_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec docker run --rm --network none --tmpfs /tmp \
  --mount "type=bind,src=$runtime_root/ui/src,dst=/workspace/ui/src,readonly" \
  --mount "type=bind,src=$runtime_root/tests/runtime/check-campaign-availability.cjs,dst=/tmp/check-campaign-availability.cjs,readonly" \
  --mount "type=bind,src=$runtime_root/tests/runtime/check-campaign-workspace.cjs,dst=/tmp/check-campaign-workspace.cjs,readonly" \
  --mount "type=bind,src=$runtime_root/tests/runtime/check-binding-presentation.cjs,dst=/tmp/check-binding-presentation.cjs,readonly" \
  sable-agent-ui-tests sh -c './node_modules/.bin/tsc --noEmit && npm run build && node /tmp/check-campaign-availability.cjs && node /tmp/check-campaign-workspace.cjs && node /tmp/check-binding-presentation.cjs'
