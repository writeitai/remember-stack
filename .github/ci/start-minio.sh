#!/usr/bin/env bash
# Start MinIO for jobs that talk to it on the host network. Publish unique
# loopback ports so parallel jobs on umc-gha-01 do not fight 9000/9001.
set -euo pipefail

# shellcheck source=ci-slot.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ci-slot.sh"

image="quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z"

if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
  slot="$(ci_slot_id)"
  container_name="remember-minio-${slot}"
  api_port="$(ci_host_port "${slot}" 25000 5000)"
  console_port="$(ci_host_port "${slot}" 30000 5000)"
else
  container_name="remember-minio"
  api_port="9000"
  console_port="9001"
fi

docker rm --force "${container_name}" >/dev/null 2>&1 || true
docker run --detach --name "${container_name}" \
  --label umc-gha=1 \
  --label "gha-runner=$(ci_runner_label)" \
  --publish "127.0.0.1:${api_port}:9000" \
  --publish "127.0.0.1:${console_port}:9001" \
  --env MINIO_ROOT_USER=rememberstack \
  --env MINIO_ROOT_PASSWORD=rememberstack_test \
  "${image}" \
  server /data --console-address :9001

for attempt in $(seq 1 30); do
  if curl -s -f "http://127.0.0.1:${api_port}/minio/health/live" >/dev/null 2>&1; then
    if [[ -n "${GITHUB_ENV:-}" ]]; then
      {
        echo "REMEMBERSTACK_MINIO_PORT=${api_port}"
        echo "REMEMBERSTACK_MINIO_CONTAINER=${container_name}"
        echo "REMEMBERSTACK_MINIO_ENDPOINT_URL=http://127.0.0.1:${api_port}"
      } >> "${GITHUB_ENV}"
    fi
    echo "MinIO ready on 127.0.0.1:${api_port} (${container_name})"
    exit 0
  fi
  sleep 1
done

docker logs "${container_name}"
exit 1
