"""Fail-closed LoCoMo complete-store backup and restore tests."""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
import hashlib
import inspect
import io
import json
from pathlib import Path
import subprocess
import tarfile
from typing import overload

from benchmarks.locomo.sharding import store_backup
import pytest


def _run_json(path: Path) -> None:
    """Write the minimum valid benchmark run identity used by backup tests."""

    path.mkdir(parents=True)
    (path / "run.json").write_text(
        json.dumps(
            {
                "protocol_name": "RS-LoCoMo-Full-v14",
                "protocol_fingerprint": "p" * 64,
                "repository_revision": "r" * 40,
                "prepared_at": "2026-08-11T00:00:00Z",
                "dataset_sha256": "d" * 64,
                "item_ids_sha256": "e" * 64,
            }
        ),
        encoding="utf-8",
    )
    (path / "state.json").write_text(
        json.dumps(
            {
                "ingests": {
                    "doc-1": {
                        "sample_id": "conv-1",
                        "deployment_id": "57000000-0000-0000-0000-000000000001",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    for name in ("manifest.json", "documents.json"):
        (path / name).write_text("{}\n", encoding="utf-8")


@overload
def _completed_process(
    args: tuple[str, ...], *, stdout: str = ""
) -> subprocess.CompletedProcess[str]: ...


@overload
def _completed_process(
    args: tuple[str, ...], *, stdout: bytes
) -> subprocess.CompletedProcess[bytes]: ...


def _completed_process(
    args: tuple[str, ...], *, stdout: str | bytes = ""
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    """Return one successful synthetic command result."""

    if isinstance(stdout, bytes):
        return subprocess.CompletedProcess[bytes](
            args=args, returncode=0, stdout=stdout
        )
    return subprocess.CompletedProcess[str](args=args, returncode=0, stdout=stdout)


def _uploaded_metadata(
    *, source: Path, **_kwargs: object
) -> store_backup.RemoteObjectMetadata:
    """Return deterministic immutable metadata for one synthetic upload."""

    return store_backup.RemoteObjectMetadata(
        byte_size=source.stat().st_size, generation=1, crc32c="crc32c-one"
    )


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
            compose_project="rememberstack",
            gcp_project="remember-stack",
            remote_destination="gs://bucket/backups",
            staging_root=tmp_path / "staging",
        )

    assert volume_root.is_dir()
    assert not store_backup._receipt_path(run_dir=run_dir, sample_id="conv-1").exists()


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
    monkeypatch.setattr(store_backup, "_upload_file", _uploaded_metadata)
    monkeypatch.setattr(store_backup.shutil, "rmtree", lambda _path: None)
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
        compose_project="rememberstack",
        gcp_project="remember-stack",
        remote_destination="gs://bucket/backups",
        staging_root=staging_root,
    )

    receipt_path = store_backup._receipt_path(run_dir=run_dir, sample_id="conv-1")
    assert receipt_path.is_file()
    assert (
        store_backup.BackupReceipt.model_validate_json(receipt_path.read_bytes())
        == receipt
    )

    checked_archives: list[str] = []

    def remote_metadata(
        *, remote_path: str, project_id: str
    ) -> store_backup.RemoteObjectMetadata:
        """Return synthetic identity while recording archive re-verification."""

        assert project_id == "remember-stack"
        name = Path(remote_path).name
        checked_archives.append(name)
        backup_directories = [path for path in staging_root.iterdir() if path.is_dir()]
        matches = list(backup_directories[0].rglob(name))
        assert len(matches) == 1
        return store_backup.RemoteObjectMetadata(
            byte_size=matches[0].stat().st_size, generation=1, crc32c="crc32c-one"
        )

    monkeypatch.setattr(store_backup, "_remote_metadata", remote_metadata)

    verified_receipt, verified_manifest = store_backup.verify_receipt(receipt_path)

    assert verified_receipt == receipt
    assert verified_manifest.sample_id == "conv-1"
    assert verified_manifest.deployment_id == "57000000-0000-0000-0000-000000000001"
    assert len(checked_archives) == 6

    def replaced_metadata(
        *, remote_path: str, project_id: str
    ) -> store_backup.RemoteObjectMetadata:
        """Represent a same-size object recreated under a new generation."""

        current = remote_metadata(remote_path=remote_path, project_id=project_id)
        return current.model_copy(update={"generation": 2})

    monkeypatch.setattr(store_backup, "_remote_metadata", replaced_metadata)
    with pytest.raises(store_backup.StoreBackupError, match="identity differs"):
        store_backup.verify_receipt(receipt_path)


def test_sample_deployment_identity_must_be_unique(tmp_path: Path) -> None:
    """Backup cannot stamp an ambiguous checkpoint deployment into its manifest."""

    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "ingests": {
                    "doc-1": {"sample_id": "conv-1", "deployment_id": "one"},
                    "doc-2": {"sample_id": "conv-1", "deployment_id": "two"},
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(store_backup.StoreBackupError, match="one deployment"):
        store_backup._sample_deployment_id(run_dir=tmp_path, sample_id="conv-1")


def test_manifest_deployment_must_match_ingest_checkpoints(tmp_path: Path) -> None:
    """A stale receipt cannot authorize wipe or startup under a wrong namespace."""

    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "ingests": {
                    "doc-1": {
                        "sample_id": "conv-1",
                        "deployment_id": "checkpointed-deployment",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    manifest = store_backup.BackupManifest.model_construct(
        sample_id="conv-1", deployment_id="stale-shell-deployment"
    )

    with pytest.raises(store_backup.StoreBackupError, match="ingest checkpoints"):
        store_backup._require_manifest_deployment(manifest=manifest, run_dir=tmp_path)


def test_manifest_checkpoint_hashes_must_match_local_run(tmp_path: Path) -> None:
    """A receipt for older checkpoint bytes cannot authorize this run's wipe."""

    run_dir = tmp_path / "run"
    _run_json(run_dir)
    manifest = store_backup.BackupManifest.model_construct(
        run_files_sha256=store_backup._run_file_hashes(run_dir)
    )
    store_backup._require_manifest_checkpoint_files(manifest=manifest, run_dir=run_dir)

    (run_dir / "state.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(store_backup.StoreBackupError, match="checkpoint differs"):
        store_backup._require_manifest_checkpoint_files(
            manifest=manifest, run_dir=run_dir
        )


def test_scoring_base_binds_ingests_but_allows_answer_progress(tmp_path: Path) -> None:
    """Answer checkpoints may advance while the protected ingestion stays fixed."""

    run_dir = tmp_path / "run"
    _run_json(run_dir)
    manifest = store_backup.BackupManifest.model_construct(
        checkpoint="scoring-base",
        sample_id="conv-1",
        sample_ingests_sha256=store_backup._sample_ingests_sha256(
            run_dir=run_dir, sample_id="conv-1"
        ),
        run_files_sha256=store_backup._run_file_hashes(run_dir),
    )
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    state["answers"] = {"conv-1/question-1": {}}
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    store_backup._require_manifest_scoring_base(manifest=manifest, run_dir=run_dir)

    state["ingests"]["doc-1"]["deployment_id"] = "changed"
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(store_backup.StoreBackupError, match="ingests differ"):
        store_backup._require_manifest_scoring_base(manifest=manifest, run_dir=run_dir)


def test_scoring_and_final_receipts_use_separate_stable_paths(tmp_path: Path) -> None:
    """A pre-scoring receipt can never masquerade as the final checkpoint."""

    scoring = store_backup._receipt_path(
        run_dir=tmp_path, sample_id="conv-1", checkpoint="scoring-base"
    )
    final = store_backup._receipt_path(run_dir=tmp_path, sample_id="conv-1")

    assert scoring.name == "conv-1.json"
    assert scoring.parent.name == "scoring-base"
    assert final.name == "conv-1.json"
    assert final.parent.name == "final"
    assert scoring != final
    colliding_sample = store_backup._receipt_path(
        run_dir=tmp_path, sample_id="conv-1.scoring-base", checkpoint="final"
    )
    assert colliding_sample != scoring


def test_restore_preserves_the_checkpoint_receipt_namespace() -> None:
    """A restored scoring base remains usable as scoring authority."""

    source = inspect.getsource(store_backup.restore_store)

    assert "checkpoint=manifest.checkpoint" in source


def test_scoring_authorization_binds_live_store_and_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remote-valid but misplaced receipt cannot authorize answer calls."""

    run_dir = tmp_path / "run"
    _run_json(run_dir)
    _write_marker(run_dir=run_dir)
    manifest = store_backup.BackupManifest.model_construct(
        checkpoint="scoring-base",
        sample_id="conv-1",
        deployment_id="57000000-0000-0000-0000-000000000001",
        compose_project="rememberstack",
        sample_ingests_sha256=store_backup._sample_ingests_sha256(
            run_dir=run_dir, sample_id="conv-1"
        ),
        run_files_sha256=store_backup._run_file_hashes(run_dir),
    )
    receipt = store_backup.BackupReceipt(
        sample_id="conv-1",
        gcp_project="remember-stack",
        remote_prefix="gs://other-bucket/runs/unit",
        manifest_sha256="a" * 64,
        verified_at="2026-08-14T00:00:00+00:00",
    )
    monkeypatch.setattr(
        store_backup,
        "_volume_names",
        lambda **_kwargs: {
            name: f"rememberstack_{name}" for name in store_backup.EXPECTED_VOLUMES
        },
    )
    monkeypatch.setattr(
        store_backup, "verify_receipt", lambda _path: (receipt, manifest)
    )

    with pytest.raises(store_backup.StoreBackupError, match="different destination"):
        store_backup.authorize_scoring(
            run_dir=run_dir,
            sample_id="conv-1",
            compose_project="rememberstack",
            remote_destination="gs://expected-bucket/runs",
        )


def test_scoring_completion_requires_every_answer_and_judge(tmp_path: Path) -> None:
    """A post-ingest receipt cannot make an unfinished store wipe-eligible."""

    (tmp_path / "manifest.json").write_text(
        json.dumps({"item_ids": ["conv-1/question-1"]}), encoding="utf-8"
    )
    (tmp_path / "state.json").write_text(
        json.dumps({"answers": {}, "judges": {}}), encoding="utf-8"
    )

    with pytest.raises(store_backup.StoreBackupError, match="answer-and-judge"):
        store_backup._require_sample_scoring_complete(
            run_dir=tmp_path, sample_id="conv-1"
        )

    (tmp_path / "state.json").write_text(
        json.dumps(
            {"answers": {"conv-1/question-1": {}}, "judges": {"conv-1/question-1": {}}}
        ),
        encoding="utf-8",
    )
    store_backup._require_sample_scoring_complete(run_dir=tmp_path, sample_id="conv-1")


def test_archive_identity_is_required() -> None:
    """A size-only legacy archive cannot satisfy the current wipe contract."""

    with pytest.raises(ValueError):
        store_backup.ArchiveRecord.model_validate(
            {
                "kind": "run",
                "logical_name": "run-directory",
                "relative_path": "run.tar.zst",
                "byte_size": 4,
                "sha256": hashlib.sha256(b"good").hexdigest(),
            }
        )


def test_receipt_verification_rejects_a_missing_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A receipt cannot authorize deletion after a required object disappears."""

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
    monkeypatch.setattr(store_backup, "_upload_file", _uploaded_metadata)
    monkeypatch.setattr(store_backup.shutil, "rmtree", lambda _path: None)
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
        return next(backup_directories[0].rglob(name)).read_bytes()

    monkeypatch.setattr(store_backup, "_remote_bytes", remote_bytes)
    receipt = store_backup.backup_store(
        sample_id="conv-1",
        run_dir=run_dir,
        mount_root=mount_root,
        compose_project="rememberstack",
        gcp_project="remember-stack",
        remote_destination="gs://bucket/backups",
        staging_root=staging_root,
    )

    def missing_remote_object(**_kwargs: object) -> store_backup.RemoteObjectMetadata:
        """Represent an archive object that is no longer readable."""

        raise store_backup.StoreBackupError("cannot inspect GCS object")

    monkeypatch.setattr(store_backup, "_remote_metadata", missing_remote_object)

    with pytest.raises(store_backup.StoreBackupError, match="cannot inspect GCS"):
        store_backup.verify_receipt(
            store_backup._receipt_path(run_dir=run_dir, sample_id=receipt.sample_id)
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
    monkeypatch.setattr(store_backup, "_upload_file", _uploaded_metadata)
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
            compose_project="rememberstack",
            gcp_project="remember-stack",
            remote_destination="gs://bucket/backups",
            staging_root=staging_root,
        )

    assert not store_backup._receipt_path(run_dir=run_dir, sample_id="conv-1").exists()
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
                gcs_generation=1,
                gcs_crc32c="crc32c-one",
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
            protocol_name="RS-LoCoMo-Full-v14",
            protocol_fingerprint="p" * 64,
            repository_revision="r" * 40,
            prepared_at="2026-08-11T00:00:00Z",
            dataset_sha256="d" * 64,
            item_ids_sha256="e" * 64,
        ),
        run_files_sha256={name: "f" * 64 for name in store_backup.RUN_CHECKPOINT_FILES},
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
    monkeypatch.setattr(
        store_backup,
        "_remote_metadata",
        lambda **_kwargs: store_backup.RemoteObjectMetadata(
            byte_size=4, generation=1, crc32c="crc32c-one"
        ),
    )
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
            compose_base_env=None,
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


def test_restore_rebases_absolute_p3_pointer_to_the_new_mount_root(
    tmp_path: Path,
) -> None:
    """A moved recovery unit never keeps reading the source host's P3 path."""

    old_deployment = tmp_path / "old" / "deployment-1"
    mount_root = tmp_path / "new"
    deployment = mount_root / "deployment-1"
    snapshot = deployment / "p3-version-1"
    old_deployment.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    link = deployment / "p3"
    link.symlink_to(old_deployment / "p3-version-1", target_is_directory=True)

    store_backup._rebase_published_mount_links(mount_root)

    assert link.readlink() == Path("p3-version-1")
    assert link.resolve() == snapshot


def test_restore_rebases_the_duplicated_nested_mount_pointer(tmp_path: Path) -> None:
    """An external mount target does not leave the run copy tied to its source."""

    run_dir = tmp_path / "restored-run"
    deployment = run_dir / ".mounts" / "deployment-1"
    snapshot = deployment / "p3-version-1"
    snapshot.mkdir(parents=True)
    old_target = tmp_path / "source" / "deployment-1" / "p3-version-1"
    link = deployment / "p3"
    link.symlink_to(old_target, target_is_directory=True)

    store_backup._rebase_published_mount_links(run_dir / ".mounts")

    assert link.readlink() == Path("p3-version-1")
    assert link.resolve() == snapshot


def test_standalone_store_lock_refuses_a_concurrent_operation(tmp_path: Path) -> None:
    """Backup and restore commands share the runner's host-level exclusion."""

    lock_file = tmp_path / "store.lock"
    with store_backup._exclusive_store_lock(
        lock_file=lock_file, inherited_lock_fd=None
    ):
        with pytest.raises(store_backup.StoreBackupError, match="owns this host"):
            with store_backup._exclusive_store_lock(
                lock_file=lock_file, inherited_lock_fd=None
            ):
                pytest.fail("a second operation acquired the store lock")


def test_archive_validation_rejects_parent_traversal(tmp_path: Path) -> None:
    """A root restore never accepts a tar member outside its destination."""

    plain = tmp_path / "unsafe.tar"
    archive = tmp_path / "unsafe.tar.zst"
    with tarfile.open(plain, "w") as handle:
        member = tarfile.TarInfo("../escape")
        payload = b"unsafe"
        member.size = len(payload)
        handle.addfile(member, io.BytesIO(payload))
    subprocess.run(
        ("zstd", "--quiet", "--force", str(plain), "-o", str(archive)), check=True
    )

    with pytest.raises(store_backup.StoreBackupError, match="unsafe member"):
        store_backup._validate_archive_members(
            archive=archive,
            destination=tmp_path / "destination",
            allow_absolute_p3_pointer=False,
        )


def test_archive_validation_accepts_nested_absolute_p3_pointer(tmp_path: Path) -> None:
    """The default run archive may duplicate the safely rebaseable mount link."""

    run_dir = tmp_path / "run"
    deployment = run_dir / ".mounts" / "deployment-1"
    snapshot = deployment / "p3-version-1"
    snapshot.mkdir(parents=True)
    (snapshot / ".snapshot-version").write_text("version-1", encoding="utf-8")
    (deployment / "p3").symlink_to(snapshot, target_is_directory=True)
    archive = tmp_path / "run.tar.zst"
    store_backup._archive_directory(source=run_dir, destination=archive)

    store_backup._validate_archive_members(
        archive=archive,
        destination=tmp_path / "destination",
        allow_absolute_p3_pointer=True,
    )


def test_runtime_environment_replays_saved_non_secret_bindings(tmp_path: Path) -> None:
    """Restore derives model/routing settings without copying deployment secrets."""

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    bindings = {
        name: store_backup.UNSET_MODEL_BINDINGS.get(name, f"value-{name}")
        for name in store_backup.MODEL_BINDING_ENVIRONMENT
    }
    (run_dir / "state.json").write_text(
        json.dumps({"readiness": {"conv-1": {"model_bindings": bindings}}}),
        encoding="utf-8",
    )

    path = store_backup._write_runtime_environment(
        run_dir=run_dir,
        sample_id="conv-1",
        deployment_id="57000000-0000-0000-0000-000000000001",
        repository_revision="r" * 40,
    )
    payload = path.read_text(encoding="utf-8")

    assert 'REMEMBERSTACK_E2_EXTRACT_MODEL="value-claim_extraction"' in payload
    assert 'REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER=""' in payload
    assert 'REMEMBERSTACK_OPENROUTER_REASONING_EFFORT=""' in payload
    assert 'REMEMBERSTACK_OPENROUTER_REASONING_EFFORT_MAP=""' in payload
    assert (
        'REMEMBERSTACK_SELFHOST_DEPLOYMENT_ID="57000000-0000-0000-0000-000000000001"'
        in payload
    )
    assert f'REMEMBERSTACK_BUILD_REVISION="{"r" * 40}"' in payload
    assert "OPENROUTER_API_KEY" not in payload
    assert "POSTGRES_PASSWORD" not in payload


def test_runtime_binding_replay_covers_every_readiness_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new readiness binding cannot silently disappear during restore."""

    from rememberstack.profiles.selfhost import _model_bindings

    monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_API_KEY", "test-key")

    assert set(store_backup.MODEL_BINDING_ENVIRONMENT) == set(_model_bindings())


def test_runtime_validation_uses_the_image_revision_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compose need not copy the build stamp out of the image environment."""

    revision = "r" * 40
    deployment_id = "57000000-0000-0000-0000-000000000001"
    manifest = store_backup.BackupManifest.model_construct(
        sample_id="conv-1",
        deployment_id=deployment_id,
        run=store_backup.RunIdentity(
            protocol_name="RS-LoCoMo-Full-v14",
            protocol_fingerprint="p" * 64,
            repository_revision=revision,
            prepared_at="2026-08-11T00:00:00Z",
            dataset_sha256="d" * 64,
            item_ids_sha256="e" * 64,
        ),
    )
    monkeypatch.setattr(
        store_backup,
        "_runtime_environment",
        lambda **_kwargs: {"REMEMBERSTACK_E2_EXTRACT_MODEL": "saved-model"},
    )

    def run_command(
        *, args: tuple[str, ...], capture_output: bool = False, text: bool = True
    ) -> subprocess.CompletedProcess[str]:
        """Return Compose config and its referenced image metadata."""

        if args[:3] == ("docker", "image", "inspect"):
            return _completed_process(
                args, stdout=json.dumps([f"REMEMBERSTACK_BUILD_REVISION={revision}"])
            )
        return _completed_process(
            args,
            stdout=json.dumps(
                {
                    "services": {
                        "api": {
                            "image": "rememberstack:test",
                            "environment": {
                                "REMEMBERSTACK_E2_EXTRACT_MODEL": "saved-model",
                                "REMEMBERSTACK_SELFHOST_DEPLOYMENT_ID": deployment_id,
                            },
                        }
                    }
                }
            ),
        )

    monkeypatch.setattr(store_backup, "_run", run_command)

    store_backup._validate_runtime_identity(
        manifest=manifest,
        compose_project="rememberstack",
        compose_base_env=tmp_path / "base.env",
        runtime_environment=tmp_path / "run/.locomo-backups/compose-runtime.env",
    )


def test_shard_runner_guards_wipe_and_backs_up_before_scoring() -> None:
    """The runner verifies ingestion off-host before any scoring can begin."""

    script = Path("benchmarks/locomo/sharding/run_shard.sh").read_text(encoding="utf-8")
    loop = script[script.index('for sample_id in "${pending_samples[@]}"; do') :]

    assert loop.index("authorize-wipe") < loop.index("down --volumes --remove-orphans")
    post_ingest_backup = loop.index("stage=post-ingest-backup status=starting")
    answer = loop.index("-m benchmarks.locomo answer", post_ingest_backup)
    judge = loop.index("-m benchmarks.locomo judge", answer)
    final_backup = loop.index("stage=final-backup status=starting", judge)
    assert post_ingest_backup < answer < judge < final_backup
    between_backup_and_answer = loop[post_ingest_backup:answer]
    assert (
        between_backup_and_answer.count('require_verified_scoring_backup "$sample_id"')
        == 2
    )
    start_existing = script[
        script.index("start_existing_store()") : script.index(
            "backup_completed_live_store()"
        )
    ]
    assert "--no-recreate" in start_existing
    assert "status=resumable-ingested-checkpoint" in script
    assert "stage=stack status=resuming-existing-store" in script
    assert "stage=answer status=resuming-from-checkpoint" in script
    resume_start = loop.index("stage=stack status=resuming-existing-store")
    resume_answer = loop.index("stage=answer status=resuming-from-checkpoint")
    assert (
        loop.rfind('require_verified_scoring_backup "$sample_id"', 0, resume_start) >= 0
    )
    assert (
        loop[resume_start:resume_answer].count(
            'require_verified_scoring_backup "$sample_id"'
        )
        == 1
    )
    assert 'backup_sample "$sample_id" scoring-base' in loop
    assert 'backup_sample "$sample_id" final' in loop
    assert 'require_verified_final_backup "$sample_id"' in loop
    assert "stop_store_after_failed_scoring_authorization" in script
    assert 'if ! "$python_bin" "$backup_tool" authorize-scoring' in script
    assert "^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$" in script
    assert '[[ "$sample_id" == "$marked_sample" ]] && marked_pending=true' in script
    assert "LOCOMO_BACKUP_DESTINATION must be" in script
    assert "LOCOMO_BACKUP_TOOL:-benchmarks/locomo/sharding/store_backup.py" in script
    assert 'compose=(docker compose --project-name "$compose_project")' in script
    assert "REMEMBERSTACK_E2_EXTRACT_MODEL=openai/gpt-5.6-luna" in script
    assert "REMEMBERSTACK_OBS_FRONTIER_MODEL=openai/gpt-5.6-luna" in script
    assert "REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER=nebius" in script
    assert "unset REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER_ORDER" in script
    assert 'published=$("${compose[@]}" port api 8000 | head -n 1)' in script
    assert 'REMEMBERSTACK_API_URL="http://127.0.0.1:$port"' in script
    assert (
        "REMEMBERSTACK_OPENROUTER_INVALID_COMPLETION_CAPTURE_DIR="
        "/var/lib/rememberstack/invalid-completions"
    ) in script
    assert "extract_claim_workers=${LOCOMO_EXTRACT_CLAIM_WORKERS:-8}" in script
    assert (
        "adjudicate_observation_workers=${LOCOMO_ADJUDICATE_OBSERVATION_WORKERS:-4}"
        in script
    )
    assert (
        "REMEMBERSTACK_BUILD_REVISION"
        in script[script.index("attest_worker_environment") :]
    )
    assert script.count("attest_worker_environment") == 4
    assert script.index("attest_worker_environment") < script.index(
        'log "sample=$sample_id stage=ingest status=starting"'
    )
    drain = script[script.index("wait_for_drain()") :]
    assert drain.index("attest_worker_environment") < drain.index("SELECT count(*)")
    assert script.count("--lock-fd 9") == 7
    assert script.index("flock --nonblock") < script.index(
        'for sample_id in "${pending_samples[@]}"; do'
    )


def test_python_compose_commands_use_the_volume_gate_project() -> None:
    """Restore and backup cannot mutate a different Compose project."""

    assert store_backup._compose_command(
        compose_project="exact-project", arguments=("down", "--volumes")
    ) == ("docker", "compose", "--project-name", "exact-project", "down", "--volumes")


def test_restore_runtime_command_unsets_parent_overrides(tmp_path: Path) -> None:
    """Saved bindings and identity win over exported operator shell values."""

    command = store_backup._compose_runtime_command(
        compose_project="exact-project",
        compose_base_env=tmp_path / "base.env",
        runtime_environment=tmp_path / "runtime.env",
        arguments=("up", "api"),
    )

    assert command[:3] == ("env", "--unset", "REMEMBERSTACK_E1_EMBEDDING_MODEL")
    assert "REMEMBERSTACK_SELFHOST_DEPLOYMENT_ID" in command
    assert "REMEMBERSTACK_BUILD_REVISION" in command
    assert command[-2:] == ("up", "api")
