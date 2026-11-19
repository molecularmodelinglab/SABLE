#!/usr/bin/env bash
# Create and remove the isolated database used by PostgreSQL integration tests.
set -euo pipefail
runtime_scripts="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export SABLE_POSTGRES_TEST_NETWORK="sable-campaign-check-$$"
campaign_test_container="$SABLE_POSTGRES_TEST_NETWORK-db"
cleanup() {
  docker rm -f "$campaign_test_container" >/dev/null 2>&1 || true
  docker network rm "$SABLE_POSTGRES_TEST_NETWORK" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker network create --internal "$SABLE_POSTGRES_TEST_NETWORK" >/dev/null
docker run -d --name "$campaign_test_container" --network "$SABLE_POSTGRES_TEST_NETWORK" \
  --network-alias campaign-postgres --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_PASSWORD=test-only-password -e POSTGRES_DB=campaign_tests postgres:16-alpine >/dev/null
for attempt in {1..30}; do
  if docker exec "$campaign_test_container" pg_isready -U postgres -d campaign_tests >/dev/null; then
    break
  fi
  if [ "$attempt" -eq 30 ]; then exit 1; fi
  sleep 1
done
bash "$runtime_scripts/run-postgres.sh" -m alembic upgrade head
bash "$runtime_scripts/run-postgres.sh" -m pytest -c tests/runtime/pytest.ini --confcutdir=tests/runtime tests/runtime/test_postgres_campaigns.py -q
bash "$runtime_scripts/run-postgres.sh" -m alembic downgrade 9c5d2e71b804
bash "$runtime_scripts/run-postgres.sh" -m alembic upgrade head
