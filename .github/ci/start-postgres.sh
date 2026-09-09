#!/usr/bin/env bash
# Build and start the CI Postgres image. On GitHub-hosted VMs a fixed host
# port is fine; on the shared Hetzner runner host it is not. Namespace the
# container and publish a job-unique loopback port, then export the DSN.
set -euo pipefail

# shellcheck source=ci-slot.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ci-slot.sh"

image_name="rememberstack-postgres:ci"

if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
  slot="$(ci_slot_id)"
  container_name="rememberstack-postgres-ci-${slot}"
  host_port="$(ci_host_port "${slot}" 20000 5000)"
else
  container_name="rememberstack-postgres-ci"
  host_port="5432"
fi

dsn="postgresql+psycopg://rememberstack:rememberstack_test@127.0.0.1:${host_port}/rememberstack_test"

docker build --file Dockerfile.postgres --tag "${image_name}" .
docker rm --force "${container_name}" >/dev/null 2>&1 || true
docker run --detach --name "${container_name}" \
  --label umc-gha=1 \
  --label "gha-runner=$(ci_runner_label)" \
  --env POSTGRES_USER=rememberstack \
  --env POSTGRES_PASSWORD=rememberstack_test \
  --env POSTGRES_DB=rememberstack_test \
  --publish "127.0.0.1:${host_port}:5432" \
  "${image_name}" \
  postgres \
  -c shared_preload_libraries=pg_textsearch,pg_partman_bgw \
  -c pg_partman_bgw.dbname=rememberstack_test \
  -c pg_partman_bgw.role=rememberstack

for attempt in $(seq 1 60); do
  if docker exec "${container_name}" \
    psql --username rememberstack --dbname rememberstack_test \
      --tuples-only --command 'SELECT 1' >/dev/null 2>&1; then
    if [[ -n "${GITHUB_ENV:-}" ]]; then
      {
        echo "REMEMBERSTACK_PG_PORT=${host_port}"
        echo "REMEMBERSTACK_PG_CONTAINER=${container_name}"
        echo "REMEMBERSTACK_DATABASE_URL=${dsn}"
      } >> "${GITHUB_ENV}"
    fi
    echo "Postgres ready on 127.0.0.1:${host_port} (${container_name})"
    exit 0
  fi
  sleep 1
done

docker logs "${container_name}"
exit 1
