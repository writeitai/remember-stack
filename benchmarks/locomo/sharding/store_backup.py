"""Fail-closed complete-plane backup and restore for sharded LoCoMo stores."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime
from datetime import UTC
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from typing import Annotated
from typing import Literal

from google.api_core.exceptions import GoogleAPIError
from google.api_core.exceptions import PreconditionFailed
from google.auth.exceptions import GoogleAuthError
from google.cloud import storage
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

EXPECTED_VOLUMES = ("postgres-data", "minio-data", "app-state", "forget-manifests")
RECEIPT_DIRECTORY = Path(".locomo-backups/receipts")
LIVE_STORE_MARKER = Path(".locomo-live-store.json")

NonEmpty = Annotated[str, Field(min_length=1)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class StoreBackupError(RuntimeError):
    """Report a safety failure that must preserve the current store."""


class FrozenModel(BaseModel):
    """Strict immutable base for durable backup metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ArchiveRecord(FrozenModel):
    """Identify and validate one archive in a recovery unit."""

    kind: Literal["volume", "run", "mounts"]
    logical_name: NonEmpty
    docker_volume: str | None = None
    relative_path: NonEmpty
    byte_size: int = Field(ge=0)
    sha256: Sha256


class RunIdentity(FrozenModel):
    """Bind a store to the benchmark protocol and input bytes that produced it."""

    protocol_name: NonEmpty
    protocol_fingerprint: NonEmpty
    repository_revision: NonEmpty
    prepared_at: NonEmpty
    dataset_sha256: Sha256
    item_ids_sha256: Sha256


class BackupManifest(FrozenModel):
    """Describe one complete, restorable LoCoMo recovery unit."""

    schema_version: Literal[1] = 1
    kind: Literal["rememberstack-locomo-store-backup"] = (
        "rememberstack-locomo-store-backup"
    )
    created_at: NonEmpty
    sample_id: NonEmpty
    deployment_id: NonEmpty
    compose_project: NonEmpty
    run: RunIdentity
    archives: Annotated[tuple[ArchiveRecord, ...], Field(min_length=6, max_length=6)]


class BackupReceipt(FrozenModel):
    """Prove that a manifest and all of its archives passed remote verification."""

    schema_version: Literal[1] = 1
    kind: Literal["rememberstack-locomo-backup-receipt"] = (
        "rememberstack-locomo-backup-receipt"
    )
    status: Literal["verified"] = "verified"
    sample_id: NonEmpty
    remote_prefix: NonEmpty
    manifest_sha256: Sha256
    verified_at: NonEmpty


class LiveStoreMarker(FrozenModel):
    """Name the sample whose isolated store currently occupies Compose volumes."""

    schema_version: Literal[1] = 1
    sample_id: NonEmpty
    recorded_at: NonEmpty


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""

    return datetime.now(tz=UTC)


def _timestamp(value: datetime) -> str:
    """Render a UTC timestamp with a stable filesystem-safe form."""

    return value.strftime("%Y%m%dT%H%M%SZ")


def _log(message: str) -> None:
    """Emit one UTC-stamped operator log line."""

    print(f"{_utc_now().strftime('%Y-%m-%dT%H:%M:%SZ')} {message}", flush=True)


def _run(
    *, args: Sequence[str], capture_output: bool = False, text: bool = True
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    """Run one checked command and translate failures into preservation errors."""

    try:
        return subprocess.run(
            list(args), check=True, capture_output=capture_output, text=text
        )
    except FileNotFoundError as exc:
        raise StoreBackupError(f"required command is not installed: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = ""
        if exc.stderr:
            detail = f": {str(exc.stderr).strip()}"
        raise StoreBackupError(f"command failed: {' '.join(args)}{detail}") from exc


def _sha256(path: Path) -> str:
    """Calculate the SHA-256 digest of one local file."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _bytes_sha256(value: bytes) -> str:
    """Calculate the SHA-256 digest of in-memory bytes."""

    return hashlib.sha256(value).hexdigest()


def _write_model(*, path: Path, model: BaseModel) -> bytes:
    """Atomically write one validated model as canonical human-readable JSON."""

    payload = (model.model_dump_json(indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_bytes(payload)
    temporary.chmod(0o600)
    os.replace(temporary, path)
    return payload


def _load_object(path: Path) -> dict[str, object]:
    """Load one JSON object and reject non-object roots."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StoreBackupError(f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise StoreBackupError(f"JSON root must be an object: {path}")
    return value


def _required_string(*, value: object, field: str) -> str:
    """Return one required non-empty string from untrusted run metadata."""

    if not isinstance(value, str) or not value:
        raise StoreBackupError(f"run.json has no valid {field}")
    return value


def _run_identity(run_dir: Path) -> RunIdentity:
    """Read the stable protocol identity from a prepared benchmark run."""

    value = _load_object(run_dir / "run.json")
    return RunIdentity(
        protocol_name=_required_string(
            value=value.get("protocol_name"), field="protocol_name"
        ),
        protocol_fingerprint=_required_string(
            value=value.get("protocol_fingerprint"), field="protocol_fingerprint"
        ),
        repository_revision=_required_string(
            value=value.get("repository_revision"), field="repository_revision"
        ),
        prepared_at=_required_string(
            value=value.get("prepared_at"), field="prepared_at"
        ),
        dataset_sha256=_required_string(
            value=value.get("dataset_sha256"), field="dataset_sha256"
        ),
        item_ids_sha256=_required_string(
            value=value.get("item_ids_sha256"), field="item_ids_sha256"
        ),
    )


def _volume_names(*, compose_project: str) -> dict[str, str]:
    """Resolve exactly one Docker volume for each logical Compose volume."""

    resolved: dict[str, str] = {}
    for logical_name in EXPECTED_VOLUMES:
        result = _run(
            args=(
                "docker",
                "volume",
                "ls",
                "--quiet",
                "--filter",
                f"label=com.docker.compose.project={compose_project}",
                "--filter",
                f"label=com.docker.compose.volume={logical_name}",
            ),
            capture_output=True,
        )
        names = [line for line in str(result.stdout).splitlines() if line]
        if len(names) != 1:
            raise StoreBackupError(
                f"expected one {logical_name!r} volume for project "
                f"{compose_project!r}, found {len(names)}"
            )
        resolved[logical_name] = names[0]
    return resolved


def _volume_count(*, compose_project: str) -> int:
    """Count all Docker volumes labelled for one Compose project."""

    result = _run(
        args=(
            "docker",
            "volume",
            "ls",
            "--quiet",
            "--filter",
            f"label=com.docker.compose.project={compose_project}",
        ),
        capture_output=True,
    )
    return len([line for line in str(result.stdout).splitlines() if line])


def _volume_mountpoint(volume_name: str) -> Path:
    """Return the host mountpoint for one named Docker volume."""

    result = _run(
        args=(
            "docker",
            "volume",
            "inspect",
            "--format",
            "{{ .Mountpoint }}",
            volume_name,
        ),
        capture_output=True,
    )
    mountpoint = Path(str(result.stdout).strip())
    if not mountpoint.is_dir():
        raise StoreBackupError(
            f"Docker volume mountpoint is not a directory: {mountpoint}"
        )
    return mountpoint


def _archive_directory(*, source: Path, destination: Path) -> None:
    """Create one ownership-preserving zstd-compressed tar archive."""

    if not source.is_dir():
        raise StoreBackupError(f"archive source is not a directory: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _run(
        args=(
            "tar",
            "--create",
            "--zstd",
            "--file",
            str(destination),
            "--directory",
            str(source),
            "--acls",
            "--xattrs",
            "--numeric-owner",
            ".",
        )
    )


def _extract_directory(*, archive: Path, destination: Path) -> None:
    """Extract one ownership-preserving zstd-compressed tar archive."""

    _run(
        args=(
            "tar",
            "--extract",
            "--zstd",
            "--file",
            str(archive),
            "--directory",
            str(destination),
            "--acls",
            "--xattrs",
            "--numeric-owner",
        )
    )


def _record_for_archive(
    *,
    kind: Literal["volume", "run", "mounts"],
    logical_name: str,
    docker_volume: str | None,
    archive: Path,
    root: Path,
) -> ArchiveRecord:
    """Build checksum metadata for one newly created archive."""

    return ArchiveRecord(
        kind=kind,
        logical_name=logical_name,
        docker_volume=docker_volume,
        relative_path=archive.relative_to(root).as_posix(),
        byte_size=archive.stat().st_size,
        sha256=_sha256(archive),
    )


def _remote_join(*, prefix: str, name: str) -> str:
    """Join a GCS prefix and a relative object name."""

    return f"{prefix.rstrip('/')}/{name.lstrip('/')}"


def _parse_gcs_uri(uri: str) -> tuple[str, str]:
    """Split one gs:// URI into a bucket name and object path."""

    if not uri.startswith("gs://"):
        raise StoreBackupError("backup destination must be a gs:// URI")
    bucket, separator, object_name = uri[5:].partition("/")
    if not bucket:
        raise StoreBackupError("backup destination has no bucket name")
    if not separator:
        object_name = ""
    return bucket, object_name.strip("/")


@lru_cache(maxsize=1)
def _storage_client() -> storage.Client:
    """Create a GCS client from the workload's Application Default Credentials."""

    try:
        return storage.Client()
    except (GoogleAPIError, GoogleAuthError, OSError, ValueError) as exc:
        raise StoreBackupError("cannot initialize GCS workload credentials") from exc


def _remote_bytes(remote_path: str) -> bytes:
    """Read and CRC32C-validate one complete GCS object."""

    bucket_name, object_name = _parse_gcs_uri(remote_path)
    if not object_name:
        raise StoreBackupError("remote object path is empty")
    try:
        return (
            _storage_client()
            .bucket(bucket_name)
            .blob(object_name)
            .download_as_bytes(checksum="crc32c")
        )
    except (GoogleAPIError, GoogleAuthError, OSError, ValueError) as exc:
        raise StoreBackupError(
            f"cannot read or validate GCS object: {remote_path}"
        ) from exc


def _upload_file(*, source: Path, remote_path: str) -> None:
    """Create one immutable GCS object with transport checksum validation."""

    bucket_name, object_name = _parse_gcs_uri(remote_path)
    if not object_name:
        raise StoreBackupError("remote object path is empty")
    try:
        blob = _storage_client().bucket(bucket_name).blob(object_name)
        blob.upload_from_filename(str(source), if_generation_match=0, checksum="crc32c")
        blob.reload()
    except PreconditionFailed as exc:
        raise StoreBackupError(
            f"refusing to replace an existing GCS object: {remote_path}"
        ) from exc
    except (GoogleAPIError, GoogleAuthError, OSError, ValueError) as exc:
        raise StoreBackupError(f"cannot upload GCS object: {remote_path}") from exc
    if blob.size != source.stat().st_size:
        raise StoreBackupError(
            f"remote object size differs after upload: {remote_path}"
        )


def _upload_directory(*, source: Path, remote_prefix: str) -> None:
    """Upload every file under one staging directory to a unique GCS prefix."""

    files = sorted(path for path in source.rglob("*") if path.is_file())
    if not files:
        raise StoreBackupError("backup staging directory contains no files")
    for path in files:
        _upload_file(
            source=path,
            remote_path=_remote_join(
                prefix=remote_prefix, name=path.relative_to(source).as_posix()
            ),
        )


def _download_recovery_unit(*, receipt: BackupReceipt, staging: Path) -> BackupManifest:
    """Download every manifest-declared object with GCS checksum validation."""

    manifest_bytes = _remote_bytes(
        _remote_join(prefix=receipt.remote_prefix, name="manifest.json")
    )
    if _bytes_sha256(manifest_bytes) != receipt.manifest_sha256:
        raise StoreBackupError("remote manifest no longer matches the receipt")
    manifest = BackupManifest.model_validate_json(manifest_bytes)
    manifest_path = staging / "manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    for archive in manifest.archives:
        relative = Path(archive.relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise StoreBackupError("manifest contains an unsafe archive path")
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        bucket_name, object_name = _parse_gcs_uri(
            _remote_join(prefix=receipt.remote_prefix, name=archive.relative_path)
        )
        try:
            (
                _storage_client()
                .bucket(bucket_name)
                .blob(object_name)
                .download_to_filename(str(destination), checksum="crc32c")
            )
        except (GoogleAPIError, GoogleAuthError, OSError, ValueError) as exc:
            raise StoreBackupError(
                f"cannot download or validate GCS object: {archive.relative_path}"
            ) from exc
    return manifest


def preflight_destination(remote_destination: str) -> None:
    """Prove that the federated workload can list the configured private bucket."""

    bucket_name, _prefix = _parse_gcs_uri(remote_destination)
    try:
        next(iter(_storage_client().list_blobs(bucket_name, max_results=1)), None)
    except (GoogleAPIError, GoogleAuthError, OSError, ValueError) as exc:
        raise StoreBackupError(
            f"configured GCS destination is not readable: {remote_destination}"
        ) from exc


def _safe_component(value: str) -> str:
    """Convert one manifest identity component into a safe object-path segment."""

    safe = "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in value
    )
    safe = safe.strip(".-")
    if not safe:
        raise StoreBackupError("backup identity produced an empty path component")
    return safe


def _receipt_path(*, run_dir: Path, sample_id: str) -> Path:
    """Return the stable local verified-receipt path for one sample."""

    return run_dir / RECEIPT_DIRECTORY / f"{_safe_component(sample_id)}.json"


def _marker_path(run_dir: Path) -> Path:
    """Return the live-store marker path for one benchmark run."""

    return run_dir / LIVE_STORE_MARKER


def record_live_store(*, run_dir: Path, sample_id: str, compose_project: str) -> None:
    """Record which sample owns a newly created complete set of volumes."""

    _volume_names(compose_project=compose_project)
    marker = LiveStoreMarker(sample_id=sample_id, recorded_at=_utc_now().isoformat())
    _write_model(path=_marker_path(run_dir), model=marker)
    _log(f"sample={sample_id} live-store-marker=recorded")


def verify_receipt(receipt_path: Path) -> BackupReceipt:
    """Re-read remote manifest and receipt bytes for one local verified receipt."""

    try:
        local_bytes = receipt_path.read_bytes()
    except OSError as exc:
        raise StoreBackupError(
            f"verified receipt is unavailable: {receipt_path}"
        ) from exc
    receipt = BackupReceipt.model_validate_json(local_bytes)
    manifest_bytes = _remote_bytes(
        _remote_join(prefix=receipt.remote_prefix, name="manifest.json")
    )
    if _bytes_sha256(manifest_bytes) != receipt.manifest_sha256:
        raise StoreBackupError("remote manifest no longer matches the verified receipt")
    remote_receipt = _remote_bytes(
        _remote_join(prefix=receipt.remote_prefix, name="receipt.json")
    )
    if remote_receipt != local_bytes:
        raise StoreBackupError("remote receipt no longer matches the local receipt")
    return receipt


def authorize_wipe(*, run_dir: Path, compose_project: str) -> None:
    """Refuse volume deletion unless the current live store has a valid receipt."""

    count = _volume_count(compose_project=compose_project)
    marker_path = _marker_path(run_dir)
    if count == 0 and not marker_path.exists():
        _log("wipe-authorized reason=no-existing-store")
        return
    if count not in (0, len(EXPECTED_VOLUMES)):
        raise StoreBackupError(
            f"Compose project has {count} volumes; expected zero or {len(EXPECTED_VOLUMES)}"
        )
    if not marker_path.is_file():
        raise StoreBackupError(
            "existing Compose store has no live-sample marker; identify and back it up before wiping"
        )
    marker = LiveStoreMarker.model_validate_json(marker_path.read_bytes())
    if count:
        _volume_names(compose_project=compose_project)
    receipt = verify_receipt(_receipt_path(run_dir=run_dir, sample_id=marker.sample_id))
    if receipt.sample_id != marker.sample_id:
        raise StoreBackupError(
            "live-store marker and verified receipt name different samples"
        )
    _log(f"sample={marker.sample_id} wipe-authorized receipt=verified")


def clear_live_store_marker(*, run_dir: Path) -> None:
    """Remove the local marker after Docker confirms volume deletion."""

    marker = _marker_path(run_dir)
    if marker.exists():
        marker.unlink()


def backup_store(
    *,
    sample_id: str,
    run_dir: Path,
    mount_root: Path,
    deployment_id: str,
    compose_project: str,
    remote_destination: str,
    staging_root: Path,
) -> BackupReceipt:
    """Stop, archive, upload, and verify one complete LoCoMo sample store."""

    if not run_dir.is_dir():
        raise StoreBackupError(f"run directory does not exist: {run_dir}")
    if not mount_root.is_dir():
        raise StoreBackupError(f"mount root does not exist: {mount_root}")
    marker_path = _marker_path(run_dir)
    if not marker_path.is_file():
        raise StoreBackupError("refusing to back up an unmarked live store")
    marker = LiveStoreMarker.model_validate_json(marker_path.read_bytes())
    if marker.sample_id != sample_id:
        raise StoreBackupError("requested sample does not own the marked live store")

    identity = _run_identity(run_dir)
    created_at = _utc_now()
    backup_id = f"{_timestamp(created_at)}-{secrets.token_hex(4)}"
    staging = staging_root / backup_id
    staging.mkdir(parents=True, mode=0o700)

    _log(f"sample={sample_id} backup-stage=stop status=starting")
    _run(args=("docker", "compose", "stop", "--timeout", "120"))
    volumes = _volume_names(compose_project=compose_project)

    archives: list[ArchiveRecord] = []
    for logical_name in EXPECTED_VOLUMES:
        docker_volume = volumes[logical_name]
        archive = staging / "volumes" / f"{logical_name}.tar.zst"
        _log(f"sample={sample_id} backup-stage=archive volume={logical_name}")
        _archive_directory(
            source=_volume_mountpoint(docker_volume), destination=archive
        )
        archives.append(
            _record_for_archive(
                kind="volume",
                logical_name=logical_name,
                docker_volume=docker_volume,
                archive=archive,
                root=staging,
            )
        )

    run_archive = staging / "run.tar.zst"
    _archive_directory(source=run_dir, destination=run_archive)
    archives.append(
        _record_for_archive(
            kind="run",
            logical_name="run-directory",
            docker_volume=None,
            archive=run_archive,
            root=staging,
        )
    )
    mounts_archive = staging / "mounts.tar.zst"
    _archive_directory(source=mount_root, destination=mounts_archive)
    archives.append(
        _record_for_archive(
            kind="mounts",
            logical_name="published-mount-root",
            docker_volume=None,
            archive=mounts_archive,
            root=staging,
        )
    )

    manifest = BackupManifest(
        created_at=created_at.isoformat(),
        sample_id=sample_id,
        deployment_id=deployment_id,
        compose_project=compose_project,
        run=identity,
        archives=tuple(archives),
    )
    manifest_path = staging / "manifest.json"
    manifest_bytes = _write_model(path=manifest_path, model=manifest)
    prepared = _safe_component(identity.prepared_at)
    remote_prefix = "/".join(
        (
            remote_destination.rstrip("/"),
            _safe_component(identity.protocol_fingerprint),
            _safe_component(identity.repository_revision),
            prepared,
            _safe_component(sample_id),
            backup_id,
        )
    )

    _log(f"sample={sample_id} backup-stage=upload status=starting")
    _upload_directory(source=staging, remote_prefix=remote_prefix)
    remote_manifest = _remote_bytes(
        _remote_join(prefix=remote_prefix, name="manifest.json")
    )
    if remote_manifest != manifest_bytes:
        raise StoreBackupError("remote manifest bytes differ from the local manifest")

    receipt = BackupReceipt(
        sample_id=sample_id,
        remote_prefix=remote_prefix,
        manifest_sha256=_bytes_sha256(manifest_bytes),
        verified_at=_utc_now().isoformat(),
    )
    receipt_staging_path = staging / "receipt.json"
    receipt_bytes = _write_model(path=receipt_staging_path, model=receipt)
    _upload_file(
        source=receipt_staging_path,
        remote_path=_remote_join(prefix=remote_prefix, name="receipt.json"),
    )
    if (
        _remote_bytes(_remote_join(prefix=remote_prefix, name="receipt.json"))
        != receipt_bytes
    ):
        raise StoreBackupError("remote receipt bytes differ from the local receipt")
    _write_model(
        path=_receipt_path(run_dir=run_dir, sample_id=sample_id), model=receipt
    )
    _log(f"sample={sample_id} backup-stage=verified prefix={remote_prefix}")
    return receipt


def _validate_download(*, staging: Path, manifest: BackupManifest) -> None:
    """Validate every downloaded archive before any restore target is changed."""

    expected = {
        "postgres-data",
        "minio-data",
        "app-state",
        "forget-manifests",
        "run-directory",
        "published-mount-root",
    }
    actual = {archive.logical_name for archive in manifest.archives}
    if actual != expected:
        raise StoreBackupError("backup archive inventory is incomplete or unexpected")
    for archive in manifest.archives:
        relative = Path(archive.relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise StoreBackupError("manifest contains an unsafe archive path")
        path = staging / relative
        if not path.is_file():
            raise StoreBackupError(f"downloaded archive is missing: {relative}")
        if path.stat().st_size != archive.byte_size:
            raise StoreBackupError(f"downloaded archive size differs: {relative}")
        if _sha256(path) != archive.sha256:
            raise StoreBackupError(f"downloaded archive checksum differs: {relative}")


def _ensure_empty_directory(path: Path) -> None:
    """Create one restore directory or refuse if it already contains entries."""

    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise StoreBackupError(f"restore target is not an empty directory: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)


def _volume_is_empty(volume_name: str) -> bool:
    """Return whether one Docker volume mountpoint contains no entries."""

    return not any(_volume_mountpoint(volume_name).iterdir())


def restore_store(
    *,
    receipt_path: Path,
    run_dir: Path,
    mount_root: Path,
    compose_project: str,
    staging_root: Path,
    start_services: bool,
) -> BackupManifest:
    """Restore one verified GCS recovery unit into empty local targets."""

    receipt = verify_receipt(receipt_path)
    staging = staging_root / f"restore-{_timestamp(_utc_now())}-{secrets.token_hex(4)}"
    staging.mkdir(parents=True, mode=0o700)
    _log(f"sample={receipt.sample_id} restore-stage=download status=starting")
    manifest = _download_recovery_unit(receipt=receipt, staging=staging)
    manifest_path = staging / "manifest.json"
    if _sha256(manifest_path) != receipt.manifest_sha256:
        raise StoreBackupError("downloaded manifest does not match the receipt")
    if manifest.sample_id != receipt.sample_id:
        raise StoreBackupError("manifest and receipt name different samples")
    _validate_download(staging=staging, manifest=manifest)
    _ensure_empty_directory(run_dir)
    _ensure_empty_directory(mount_root)

    count = _volume_count(compose_project=compose_project)
    if count:
        volumes = _volume_names(compose_project=compose_project)
        non_empty = [
            name for name, volume in volumes.items() if not _volume_is_empty(volume)
        ]
        if non_empty:
            raise StoreBackupError(
                f"refusing to restore over non-empty volumes: {', '.join(non_empty)}"
            )
    _run(args=("docker", "compose", "down", "--remove-orphans"))
    if count == 0:
        _run(args=("docker", "compose", "create"))
        _run(args=("docker", "compose", "down", "--remove-orphans"))
    volumes = _volume_names(compose_project=compose_project)
    if any(not _volume_is_empty(volume) for volume in volumes.values()):
        raise StoreBackupError("Compose created a non-empty restore volume")

    by_name = {archive.logical_name: archive for archive in manifest.archives}
    for logical_name in EXPECTED_VOLUMES:
        archive = by_name[logical_name]
        _extract_directory(
            archive=staging / archive.relative_path,
            destination=_volume_mountpoint(volumes[logical_name]),
        )
    _extract_directory(
        archive=staging / by_name["run-directory"].relative_path, destination=run_dir
    )
    _extract_directory(
        archive=staging / by_name["published-mount-root"].relative_path,
        destination=mount_root,
    )
    receipt_destination = _receipt_path(run_dir=run_dir, sample_id=receipt.sample_id)
    _write_model(path=receipt_destination, model=receipt)
    _write_model(
        path=_marker_path(run_dir),
        model=LiveStoreMarker(
            sample_id=receipt.sample_id, recorded_at=_utc_now().isoformat()
        ),
    )
    if start_services:
        _run(
            args=(
                "docker",
                "compose",
                "up",
                "--detach",
                "--wait",
                "postgres",
                "minio",
                "setup",
                "api",
            )
        )
    _log(f"sample={receipt.sample_id} restore-stage=complete")
    return manifest


def _path(value: str) -> Path:
    """Resolve a command-line path without requiring it to exist yet."""

    return Path(value).expanduser().resolve()


def _parser() -> argparse.ArgumentParser:
    """Build the complete backup/restore command-line parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record-live", help="mark a newly isolated store")
    record.add_argument("--run-dir", type=_path, required=True)
    record.add_argument("--sample", required=True)
    record.add_argument("--compose-project", default="rememberstack")

    authorize = subparsers.add_parser(
        "authorize-wipe", help="verify the current store may be deleted"
    )
    authorize.add_argument("--run-dir", type=_path, required=True)
    authorize.add_argument("--compose-project", default="rememberstack")

    clear = subparsers.add_parser(
        "clear-live", help="clear the marker after confirmed volume deletion"
    )
    clear.add_argument("--run-dir", type=_path, required=True)

    preflight = subparsers.add_parser(
        "preflight", help="verify the keyless GCS destination"
    )
    preflight.add_argument("--destination", required=True)

    backup = subparsers.add_parser("backup", help="create and verify one backup")
    backup.add_argument("--sample", required=True)
    backup.add_argument("--run-dir", type=_path, required=True)
    backup.add_argument("--mount-root", type=_path, required=True)
    backup.add_argument("--deployment-id", required=True)
    backup.add_argument("--compose-project", default="rememberstack")
    backup.add_argument("--destination", required=True)
    backup.add_argument(
        "--staging-root",
        type=_path,
        default=Path("/var/lib/rememberstack-locomo-backups"),
    )

    verify = subparsers.add_parser("verify", help="re-verify one remote receipt")
    verify.add_argument("--receipt", type=_path, required=True)

    restore = subparsers.add_parser("restore", help="restore one verified backup")
    restore.add_argument("--receipt", type=_path, required=True)
    restore.add_argument("--run-dir", type=_path, required=True)
    restore.add_argument("--mount-root", type=_path, required=True)
    restore.add_argument("--compose-project", default="rememberstack")
    restore.add_argument(
        "--staging-root",
        type=_path,
        default=Path("/var/lib/rememberstack-locomo-backups"),
    )
    restore.add_argument("--start", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one fail-closed backup, verification, wipe-guard, or restore action."""

    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "record-live":
            record_live_store(
                run_dir=arguments.run_dir,
                sample_id=arguments.sample,
                compose_project=arguments.compose_project,
            )
        elif arguments.command == "authorize-wipe":
            authorize_wipe(
                run_dir=arguments.run_dir, compose_project=arguments.compose_project
            )
        elif arguments.command == "clear-live":
            clear_live_store_marker(run_dir=arguments.run_dir)
        elif arguments.command == "preflight":
            preflight_destination(arguments.destination)
        elif arguments.command == "backup":
            backup_store(
                sample_id=arguments.sample,
                run_dir=arguments.run_dir,
                mount_root=arguments.mount_root,
                deployment_id=arguments.deployment_id,
                compose_project=arguments.compose_project,
                remote_destination=arguments.destination,
                staging_root=arguments.staging_root,
            )
        elif arguments.command == "verify":
            verify_receipt(arguments.receipt)
        elif arguments.command == "restore":
            restore_store(
                receipt_path=arguments.receipt,
                run_dir=arguments.run_dir,
                mount_root=arguments.mount_root,
                compose_project=arguments.compose_project,
                staging_root=arguments.staging_root,
                start_services=arguments.start,
            )
        else:  # pragma: no cover - argparse enforces the command choices.
            raise StoreBackupError(f"unknown command: {arguments.command}")
    except (OSError, StoreBackupError, ValueError) as exc:
        _log(f"ERROR: {exc}; current store and staging files are preserved")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
