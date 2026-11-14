#!/usr/bin/env bash
set -euo pipefail

container_name="rememberstack-postgres-ci"

# PostgreSQL 18 is the sole server baseline (D157). POSTGRES_IMAGE may select
# a released PG18 foundation instead of building this checkout's Dockerfile.
if [ -n "${POSTGRES_IMAGE:-}" ]; then
  image_name="${POSTGRES_IMAGE}"
  docker pull "${image_name}"
else
  image_name="rememberstack-postgres:ci"
  docker build --file Dockerfile.postgres --tag "${image_name}" .
fi
docker rm --force "${container_name}" >/dev/null 2>&1 || true
docker run --detach --name "${container_name}" \
  --env POSTGRES_USER=rememberstack \
  --env POSTGRES_PASSWORD=rememberstack_test \
  --env POSTGRES_DB=rememberstack_test \
  --publish 5432:5432 \
  "${image_name}" \
  postgres \
  -c shared_preload_libraries=pg_textsearch,pg_partman_bgw \
  -c pg_partman_bgw.dbname=rememberstack_test \
  -c pg_partman_bgw.role=rememberstack

for attempt in $(seq 1 60); do
  if docker exec "${container_name}" \
    psql --username rememberstack --dbname rememberstack_test \
      --tuples-only --command 'SELECT 1' >/dev/null 2>&1; then
    exit 0
  fi
  sleep 1
done

docker logs "${container_name}"
exit 1
