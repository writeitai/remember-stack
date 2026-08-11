"""Fail-closed LoCoMo complete-store backup and restore tests."""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
import hashlib
import json
from pathlib import Path
import subprocess

from benchmarks.locomo.sharding import store_backup
import pytest


def _run_json(path: Path) -> None:
    """Write the minimum valid benchmark run identity used by backup tests."""

    path.mkdir(parents=True)
    (path / "run.json").write_text(
        json.dumps(
            {
                "protocol_name": "RS-LoCoMo-Full-v12",
                "protocol_fingerprint": "p" * 64,
                "repository_revision": "r" * 40,
                "prepared_at": "2026-08-11T00:00:00Z",
                "dataset_sha256": "d" * 64,
                "item_ids_sha256": "e" * 64,
            }
        ),
        encoding="utf-8",
    )


def _completed_process(
    args: tuple[str, ...], *, stdout: str | bytes = ""
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    """Return one successful synthetic command result."""

    return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout)


def _write_marker(*, run_dir: Path, sample_id: str = "conv-1") -> None:
    """Write one valid live-store marker through the production serializer."""

    store_backup._write_model(
        path=run_dir / store_backup.LIVE_STORE_MARKER,
        model=store_backup.LiveStoreMarker(
            sample_id=sample_id, recorded_at=datetime.now(tz=UTC).isoformat()
        ),
    )


def test_authorize_wipe_allows_only_an_absent_unmarked_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh host needs no artificial receipt."""

    monkeypatch.setattr(store_backup, "_volume_count", lambda **_kwargs: 0)

    store_backup.authorize_wipe(run_dir=tmp_path, compose_project="rememberstack")


def test_storage_client_requires_an_explicit_project() -> None:
    """External-account credentials must not guess their GCP billing project."""

    store_backup._storage_client.cache_clear()

    with pytest.raises(store_backup.StoreBackupError, match="must be explicit"):
        store_backup._storage_client("")


def test_authorize_wipe_rejects_an_existing_unmarked_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown pre-existing bytes must never be silently classified as disposable."""

    monkeypatch.setattr(store_backup, "_volume_count", lambda **_kwargs: 4)

    with pytest.raises(store_backup.StoreBackupError, match="no live-sample marker"):
        store_backup.authorize_wipe(run_dir=tmp_path, compose_project="rememberstack")


def test_authorize_wipe_rejects_a_missing_remote_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A marker is identity, not proof of an off-host backup."""

    _write_marker(run_dir=tmp_path)
    monkeypatch.setattr(store_backup, "_volume_count", lambda **_kwargs: 4)
    monkeypatch.setattr(
        store_backup,
        "_volume_names",
        lambda **_kwargs: {
            name: f"rememberstack_{name}" for name in store_backup.EXPECTED_VOLUMES
        },
    )

    with pytest.raises(store_backup.StoreBackupError, match="receipt is unavailable"):
        store_backup.authorize_wipe(run_dir=tmp_path, compose_project="rememberstack")


def test_authorize_wipe_rejects_a_partial_volume_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partially present Compose store is never classified as disposable."""

    monkeypatch.setattr(store_backup, "_volume_count", lambda **_kwargs: 3)

    with pytest.raises(store_backup.StoreBackupError, match="expected zero or 4"):
        store_backup.authorize_wipe(run_dir=tmp_path, compose_project="rememberstack")


def test_failed_stop_preserves_store_without_a_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stack that cannot quiesce must not be archived or authorized for deletion."""

    run_dir = tmp_path / "run"
    mount_root = run_dir / ".mounts"
    _run_json(run_dir)
    mount_root.mkdir()
    _write_marker(run_dir=run_dir)
    volume_root = tmp_path / "volume"
    volume_root.mkdir()

    def failed_stop(**_kwargs: object) -> subprocess.CompletedProcess[str]:
        """Represent a Compose stop that failed before any archive began."""

        raise store_backup.StoreBackupError("command failed: docker compose stop")

    monkeypatch.setattr(store_backup, "_run", failed_stop)

    with pytest.raises(store_backup.StoreBackupError, match="compose stop"):
        store_backup.backup_store(
            sample_id="conv-1",
            run_dir=run_dir,
            mount_root=mount_root,
            deployment_id="57000000-0000-0000-0000-000000000001",
            compose_project="rememberstack",
            gcp_project="remember-stack",
            remote_destination="gs://bucket/backups",
            staging_root=tmp_path / "staging",
        )

    assert volume_root.is_dir()
    assert not (run_dir / store_backup.RECEIPT_DIRECTORY / "conv-1.json").exists()


def test_backup_writes_receipt_only_after_remote_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fully compared upload produces a durable local and remote receipt."""

    run_dir = tmp_path / "run"
    mount_root = run_dir / ".mounts"
    staging_root = tmp_path / "staging"
    _run_json(run_dir)
    mount_root.mkdir()
    _write_marker(run_dir=run_dir)
    volume_roots: dict[str, Path] = {}
    for name in store_backup.EXPECTED_VOLUMES:
        volume_roots[name] = tmp_path / name
        volume_roots[name].mkdir()
        (volume_roots[name] / "payload").write_text(name, encoding="utf-8")

    monkeypatch.setattr(
        store_backup,
        "_volume_names",
        lambda **_kwargs: {name: name for name in store_backup.EXPECTED_VOLUMES},
    )
    monkeypatch.setattr(
        store_backup, "_volume_mountpoint", lambda name: volume_roots[name]
    )

    def archive(*, source: Path, destination: Path) -> None:
        """Represent one archive with deterministic bytes."""

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(f"archive:{source.name}".encode())

    monkeypatch.setattr(store_backup, "_archive_directory", archive)
    monkeypatch.setattr(store_backup, "_upload_directory", lambda **_kwargs: None)
    monkeypatch.setattr(store_backup, "_upload_file", lambda **_kwargs: None)
    monkeypatch.setattr(
        store_backup,
        "_run",
        lambda *, args, capture_output=False, text=True: _completed_process(args),
    )

    def remote_bytes(*, remote_path: str, project_id: str) -> bytes:
        """Mirror the current local staging object as the synthetic remote."""

        assert project_id == "remember-stack"
        name = Path(remote_path).name
        backup_directories = [path for path in staging_root.iterdir() if path.is_dir()]
        assert len(backup_directories) == 1
        return (backup_directories[0] / name).read_bytes()

    monkeypatch.setattr(store_backup, "_remote_bytes", remote_bytes)

    receipt = store_backup.backup_store(
        sample_id="conv-1",
        run_dir=run_dir,
        mount_root=mount_root,
        deployment_id="57000000-0000-0000-0000-000000000001",
        compose_project="rememberstack",
        gcp_project="remember-stack",
        remote_destination="gs://bucket/backups",
        staging_root=staging_root,
    )

    receipt_path = run_dir / store_backup.RECEIPT_DIRECTORY / "conv-1.json"
    assert receipt_path.is_file()
    assert (
        store_backup.BackupReceipt.model_validate_json(receipt_path.read_bytes())
        == receipt
    )


def test_failed_remote_manifest_readback_preserves_store_without_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful copy command alone never authorizes the destructive path."""

    run_dir = tmp_path / "run"
    mount_root = run_dir / ".mounts"
    staging_root = tmp_path / "staging"
    _run_json(run_dir)
    mount_root.mkdir()
    _write_marker(run_dir=run_dir)
    volume_root = tmp_path / "volume"
    volume_root.mkdir()
    monkeypatch.setattr(
        store_backup,
        "_volume_names",
        lambda **_kwargs: {name: name for name in store_backup.EXPECTED_VOLUMES},
    )
    monkeypatch.setattr(store_backup, "_volume_mountpoint", lambda _name: volume_root)

    def archive(*, source: Path, destination: Path) -> None:
        """Create a small valid local archive placeholder."""

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(str(source).encode())

    monkeypatch.setattr(store_backup, "_archive_directory", archive)
    monkeypatch.setattr(store_backup, "_upload_directory", lambda **_kwargs: None)
    monkeypatch.setattr(store_backup, "_upload_file", lambda **_kwargs: None)
    monkeypatch.setattr(
        store_backup,
        "_run",
        lambda *, args, capture_output=False, text=True: _completed_process(args),
    )
    monkeypatch.setattr(store_backup, "_remote_bytes", lambda **_kwargs: b"corrupt")

    with pytest.raises(store_backup.StoreBackupError, match="manifest bytes differ"):
        store_backup.backup_store(
            sample_id="conv-1",
            run_dir=run_dir,
            mount_root=mount_root,
            deployment_id="57000000-0000-0000-0000-000000000001",
            compose_project="rememberstack",
            gcp_project="remember-stack",
            remote_destination="gs://bucket/backups",
            staging_root=staging_root,
        )

    assert not (run_dir / store_backup.RECEIPT_DIRECTORY / "conv-1.json").exists()
    assert (volume_root).is_dir()
    assert any(staging_root.iterdir())


def test_restore_validates_every_archive_before_running_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Corrupt downloaded bytes cannot partially mutate a destination store."""

    remote_root = tmp_path / "remote"
    remote_root.mkdir()
    records: list[store_backup.ArchiveRecord] = []
    names = [*store_backup.EXPECTED_VOLUMES, "run-directory", "published-mount-root"]
    for name in names:
        relative = (
            f"volumes/{name}.tar.zst"
            if name in store_backup.EXPECTED_VOLUMES
            else f"{name}.tar.zst"
        )
        records.append(
            store_backup.ArchiveRecord(
                kind=(
                    "volume"
                    if name in store_backup.EXPECTED_VOLUMES
                    else "run"
                    if name == "run-directory"
                    else "mounts"
                ),
                logical_name=name,
                docker_volume=name if name in store_backup.EXPECTED_VOLUMES else None,
                relative_path=relative,
                byte_size=4,
                sha256=hashlib.sha256(b"good").hexdigest(),
            )
        )
        archive = remote_root / relative
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(b"evil")
    manifest = store_backup.BackupManifest(
        created_at="2026-08-11T00:00:00+00:00",
        sample_id="conv-1",
        deployment_id="57000000-0000-0000-0000-000000000001",
        compose_project="rememberstack",
        run=store_backup.RunIdentity(
            protocol_name="RS-LoCoMo-Full-v12",
            protocol_fingerprint="p" * 64,
            repository_revision="r" * 40,
            prepared_at="2026-08-11T00:00:00Z",
            dataset_sha256="d" * 64,
            item_ids_sha256="e" * 64,
        ),
        archives=tuple(records),
    )
    manifest_bytes = store_backup._write_model(
        path=remote_root / "manifest.json", model=manifest
    )
    receipt = store_backup.BackupReceipt(
        sample_id="conv-1",
        gcp_project="remember-stack",
        remote_prefix="gs://bucket/prefix",
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        verified_at="2026-08-11T00:01:00+00:00",
    )
    receipt_path = tmp_path / "receipt.json"
    receipt_bytes = store_backup._write_model(path=receipt_path, model=receipt)
    (remote_root / "receipt.json").write_bytes(receipt_bytes)
    docker_calls: list[tuple[str, ...]] = []

    def remote_bytes(*, remote_path: str, project_id: str) -> bytes:
        """Read the named object from the synthetic remote directory."""

        assert project_id == "remember-stack"
        return (remote_root / Path(remote_path).name).read_bytes()

    def download_recovery_unit(
        *, receipt: store_backup.BackupReceipt, staging: Path
    ) -> store_backup.BackupManifest:
        """Copy the synthetic recovery unit into local staging."""

        assert receipt.sample_id == "conv-1"
        for source in remote_root.rglob("*"):
            if source.is_file():
                destination = staging / source.relative_to(remote_root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read_bytes())
        return manifest

    def run_command(
        *, args: tuple[str, ...], capture_output: bool = False, text: bool = True
    ) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
        """Record any forbidden Docker mutation."""

        if args[0] == "docker":
            docker_calls.append(args)
        return _completed_process(args)

    monkeypatch.setattr(store_backup, "_remote_bytes", remote_bytes)
    monkeypatch.setattr(store_backup, "_download_recovery_unit", download_recovery_unit)
    monkeypatch.setattr(store_backup, "_run", run_command)

    with pytest.raises(store_backup.StoreBackupError, match="checksum differs"):
        store_backup.restore_store(
            receipt_path=receipt_path,
            run_dir=tmp_path / "restored-run",
            mount_root=tmp_path / "restored-mounts",
            compose_project="rememberstack",
            staging_root=tmp_path / "restore-staging",
            start_services=False,
        )

    assert docker_calls == []


def test_volume_empty_check_allows_only_empty_compose_mount_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compose-created nested mountpoints are empty, while any stored byte is not."""

    volume_root = tmp_path / "volume"
    (volume_root / "forget-manifests").mkdir(parents=True)
    monkeypatch.setattr(store_backup, "_volume_mountpoint", lambda _name: volume_root)

    assert store_backup._volume_is_empty("app-state")

    (volume_root / "forget-manifests" / "manifest.json").write_text(
        "{}", encoding="utf-8"
    )

    assert not store_backup._volume_is_empty("app-state")


def test_nested_empty_restore_target_is_retryable(tmp_path: Path) -> None:
    """A prior safe refusal may leave the default empty nested mount directory."""

    run_dir = tmp_path / "run"
    mount_root = run_dir / ".mounts"
    mount_root.mkdir(parents=True)

    store_backup._ensure_empty_restore_targets(run_dir=run_dir, mount_root=mount_root)

    (mount_root / "unexpected").write_text("data", encoding="utf-8")
    with pytest.raises(store_backup.StoreBackupError, match="not an empty"):
        store_backup._ensure_empty_restore_targets(
            run_dir=run_dir, mount_root=mount_root
        )


def test_shard_runner_guards_wipe_and_backs_up_after_judging() -> None:
    """The destructive command remains textually enclosed by the backup protocol."""

    script = Path("benchmarks/locomo/sharding/run_shard.sh").read_text(encoding="utf-8")
    loop = script[script.index('for sample_id in "${pending_samples[@]}"; do') :]

    assert loop.index("authorize-wipe") < loop.index("down --volumes --remove-orphans")
    assert loop.index("-m benchmarks.locomo judge") < loop.index(
        "stage=backup status=starting"
    )
    assert "LOCOMO_BACKUP_DESTINATION must be" in script
