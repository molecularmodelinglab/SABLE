#!/usr/bin/env bash
set -euo pipefail

# If PROMPT is provided via env and no args, run with that prompt
if [ -n "${PROMPT:-}" ] && [ $# -eq 0 ]; then
  exec python /app/run_workflow.py "$PROMPT"
fi

# If first arg looks like a flag or no args, run example
if [ "${1:-}" = "" ] || [[ "$1" == "-"* ]]; then
  exec python /app/run_workflow.py --example "$@"
fi

# If explicit subcommand is provided
case "$1" in
  run)
    shift
    exec python /app/run_workflow.py "$@"
    ;;
  resume)
    shift
    exec python /app/run_workflow.py --checkpoint "$@"
    ;;
  export)
    shift
    exec python /app/run_workflow.py --output results.json "$@"
    ;;
  *)
    # Pass through to python entrypoint (treat the rest as a prompt string)
    exec python /app/run_workflow.py "$@"
    ;;
 esac
