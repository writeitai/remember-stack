"""Offline D155 attestation proofs; no benchmark execution or hosted calls."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path

from benchmarks.locomo.corpus import capture_publications
from benchmarks.locomo.retrieval import P3Mount
from benchmarks.locomo.retrieval import RetrievalInfrastructureError
import httpx
import pytest

from remember.client import MemoryClient
from tests.benchmarks.corpus_support import publications
from tests.benchmarks.corpus_support import write_corpus


@pytest.mark.parametrize(
    "failure",
    [None, "incomplete", "changed", "version", "unserved", "path", "representation"],
)
def test_publication_attestation_uses_single_version_markers(
    failure: str | None,
) -> None:
    publication = publications()[0]
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/readiness":
            assert json.loads(request.content)["version_ids"] == [
                str(publication.version_id)
            ]
            ready = failure != "incomplete"
            revision = (
                "changed"
                if failure == "changed" and len(requests) == 3
                else publication.revision
            )
            return httpx.Response(
                200,
                json={
                    "ready": ready,
                    "versions": [],
                    "capabilities": {
                        "p3": {
                            "required": True,
                            "ready": ready,
                            "reason": "ready"
                            if ready
                            else "corpus_publication_incomplete",
                            "version": revision,
                            "checked_at": "2026-11-13T00:00:00Z",
                        }
                    },
                },
            )
        assert request.url.path == "/documents/search"
        assert json.loads(request.content)["filters"]["doc_ids"] == [
            str(publication.doc_id)
        ]
        return httpx.Response(
            200,
            json={
                "as_of": "2026-11-13T00:00:00Z",
                "documents": [
                    {
                        "doc_id": str(publication.doc_id),
                        "version_id": str(
                            publication.doc_id
                            if failure == "version"
                            else publication.version_id
                        ),
                        "representation_id": None
                        if failure == "representation"
                        else str(publication.representation_id),
                        "version_no": 1,
                        "status": "ready",
                        "family": "markdown",
                        "served_version": failure != "unserved",
                        "p3_path": None if failure == "path" else publication.path,
                    }
                ],
            },
        )

    with httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(respond)
    ) as http:
        client = MemoryClient(client=http)
        if failure is None:
            assert capture_publications(
                client=client, versions={publication.doc_id: publication.version_id}
            ) == (publication,)
        else:
            with pytest.raises(ValueError):
                capture_publications(
                    client=client, versions={publication.doc_id: publication.version_id}
                )
    assert requests == ["/readiness", "/documents/search", "/readiness"]


@pytest.mark.parametrize(
    "field",
    [
        "doc_id",
        "version_id",
        "representation_id",
        "export_revision",
        "updating",
        "checksum",
    ],
)
def test_folder_attestation_rejects_wrong_identity_or_bytes(
    tmp_path: Path, field: str
) -> None:
    expected = write_corpus(tmp_path)
    folder = tmp_path / expected[0].path
    metadata = folder / "document.json"
    record = json.loads(metadata.read_text())
    if field == "updating":
        (folder / "_index.md").write_text("State: updating\n")
    elif field == "checksum":
        (folder / "original.bin").write_bytes(b"\x00\xffactual")
        record["files"] = [{"path": "original.bin", "sha256": "0" * 64}]
    else:
        record[field] = "wrong"
    metadata.write_text(json.dumps(record))
    with pytest.raises(RetrievalInfrastructureError, match="differs from readiness"):
        P3Mount(root=tmp_path, publications=expected)


def test_inventory_freezes_binary_reader_bytes_and_navigation(tmp_path: Path) -> None:
    expected = write_corpus(tmp_path)
    folder = tmp_path / expected[0].path
    binary = folder / "original.bin"
    binary.write_bytes(b"\x00\xfforiginal")
    metadata = folder / "document.json"
    record = json.loads(metadata.read_text())
    record["files"] = [
        {
            "path": "original.bin",
            "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        }
    ]
    metadata.write_text(json.dumps(record))
    index = tmp_path / "_index.md"
    index.write_text("Current membership")
    mount = P3Mount(root=tmp_path, publications=expected)
    try:
        assert expected[0].path + "/original.bin" in mount.inventory
        assert len(mount.inventory_sha256) == 64
        mount.verify_inventory()
        index.write_text("Changed membership")
        with pytest.raises(RetrievalInfrastructureError, match="inventory changed"):
            mount.verify_inventory()
    finally:
        mount.close()


def test_snapshot_marker_alone_cannot_attest_a_live_corpus(tmp_path: Path) -> None:
    (tmp_path / ".snapshot-version").write_text("p3-v1")
    with pytest.raises(RetrievalInfrastructureError):
        P3Mount(root=tmp_path, publications=publications())


def test_asset_folder_cannot_impersonate_a_second_document(tmp_path: Path) -> None:
    expected = write_corpus(tmp_path)
    nested = replace(expected[0], path=expected[0].path + "/media/" + expected[0].path)
    with pytest.raises(RetrievalInfrastructureError):
        P3Mount(root=tmp_path, publications=(nested,))


def test_replaced_root_cannot_reuse_the_pinned_inventory(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    expected = write_corpus(root)
    mount = P3Mount(root=root, publications=expected)
    root.rename(tmp_path / "former")
    write_corpus(root)
    try:
        with pytest.raises(RetrievalInfrastructureError, match="root changed"):
            mount.verify_inventory()
        with pytest.raises(RetrievalInfrastructureError, match="root changed"):
            _ = mount.root
    finally:
        mount.close()
