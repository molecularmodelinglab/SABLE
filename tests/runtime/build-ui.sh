#!/usr/bin/env bash
set -euo pipefail
runtime_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tar -C "$runtime_root" --exclude='node_modules' --exclude='dist' --exclude='.env*' \
  -cf - ui tests/runtime/Dockerfile.ui \
  | docker build -f tests/runtime/Dockerfile.ui -t sable-agent-ui-tests -
