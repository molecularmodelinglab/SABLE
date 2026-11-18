#!/usr/bin/env bash
# Exercise existing adapter contracts without live provider calls.
set -euo pipefail
runtime_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec bash "$runtime_root/tests/runtime/run-science.sh" -c 'import os,pytest; os.environ["HEALER_STOCK"]="US_stock"; raise SystemExit(pytest.main(["-c","tests/runtime/pytest.ini","--confcutdir=tests/unit","-p","pytest_asyncio.plugin","-o","asyncio_default_fixture_loop_scope=function","-o","markers=integration: adapter integration\nasyncio: asynchronous adapter","tests/unit/test_healer_enumerator_api.py","tests/unit/test_boltz_contracts.py","tests/unit/test_boltz_platform_adapter.py","tests/unit/test_openfe_tool.py","tests/unit/test_openfe_network.py","-q"]))'
