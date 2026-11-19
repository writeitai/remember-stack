#!/usr/bin/env bash
# Real MinIO, independent of the SeaweedFS Compose profile. Upstream registry
# images are no longer public; build a tiny image from the verified release asset.
set -euo pipefail

release=RELEASE.2025-09-07T16-13-09Z
case "$(docker info --format '{{.Architecture}}')" in
  aarch64|arm64) arch=arm64 ;;
  x86_64|amd64) arch=amd64 ;;
  *) echo 'Unsupported MinIO test architecture' >&2; exit 1 ;;
esac
build_dir=$(mktemp -d)
trap 'rm -rf "$build_dir"' EXIT
asset="minio.linux-${arch}.${release}"
base="https://github.com/minio/minio/releases/download/${release}"
curl --fail --location --retry 3 "${base}/${asset}" --output "$build_dir/minio"
curl --fail --location --retry 3 "${base}/${asset}.sha256sum" --output "$build_dir/minio.sha256sum"
python3 - "$build_dir" <<'PY'
from hashlib import file_digest
from pathlib import Path
import sys
root = Path(sys.argv[1])
expected = (root / "minio.sha256sum").read_text().split()[0]
with (root / "minio").open("rb") as handle:
    actual = file_digest(handle, "sha256").hexdigest()
if actual != expected:
    sys.exit("MinIO release checksum mismatch")
PY
chmod +x "$build_dir/minio"
cat > "$build_dir/Dockerfile" <<'EOF'
FROM scratch
COPY minio /minio
ENTRYPOINT ["/minio"]
EOF
docker build --tag rememberstack-minio-test "$build_dir"
docker run --detach --name rememberstack-minio-test \
  --publish 127.0.0.1:59000:9000 \
  --env MINIO_ROOT_USER=rememberstack \
  --env MINIO_ROOT_PASSWORD=rememberstack_test \
  --env MINIO_BROWSER=off rememberstack-minio-test server /data
curl --fail --silent --show-error --retry 30 --retry-all-errors --retry-delay 1 \
  --max-time 3 http://127.0.0.1:59000/minio/health/live
