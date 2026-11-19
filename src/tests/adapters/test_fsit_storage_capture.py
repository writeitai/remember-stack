"""Accepted Go/provider byte vectors for the one engine D63 decoder.

These fixtures prove cross-language framing, never native capture authority.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from rememberstack.adapters.fsit_storage_capture import check_provider_manifest
from rememberstack.adapters.fsit_storage_capture import provider_canonical_bytes
from rememberstack.adapters.fsit_storage_capture import provider_page_digest
from rememberstack.adapters.fsit_storage_capture import provider_path_identity
from rememberstack.adapters.fsit_storage_capture import ProviderItem
from rememberstack.adapters.fsit_storage_capture import ProviderManifest
from rememberstack.adapters.fsit_storage_capture import ProviderPage
from rememberstack.adapters.fsit_storage_capture import ProviderRequest
from rememberstack.adapters.fsit_storage_capture import ProviderStorageFlags

_FIXTURE = (
    Path(__file__).parents[1] / "fixtures/storage_capture/fsit_bc8482ab_manifest.json"
)


def test_actual_provider_manifest_wire_vector() -> None:
    """Match the exact provider-owned committed synthetic artifact, not a cut."""
    vector = json.loads(_FIXTURE.read_bytes())
    manifest = ProviderManifest.model_validate_json(json.dumps(vector["manifest"]))
    check_provider_manifest(manifest=manifest)
    assert manifest.manifest_digest == vector["manifest_digest"]
    assert (
        hashlib.sha256(provider_canonical_bytes(record=manifest)).hexdigest()
        == vector["canonical_manifest_sha256"]
    )


@pytest.mark.parametrize(
    ("ordinal", "expected"),
    (
        ("0", "43f721a01c59293f453e9c7f9c0563ea8b8a7ac7b3ba7786f9075ed2394537bd"),
        (
            str(2**64 - 1),
            "a0aefd4a74d5c4d7f04fcca6d7613301592696ea580bf791e764275ee7ad16e4",
        ),
    ),
)
def test_existing_go_page_framing_vectors(ordinal: str, expected: str) -> None:
    """Preserve FSIT page.go's actual maximum-u64 framing vector byte-for-byte."""
    filesystems = tuple(
        sorted(
            base64.b32encode(bytes([value]) * 16).decode().rstrip("=").lower()
            for value in range(4)
        )
    )
    request = ProviderRequest(
        cell_id="synthetic-cell",
        cut_at_unix_us="1700000000000000",
        filesystem_ids=filesystems,
        organisation_id="1" * 32,
        protocol="fsit-capture-v1",
    )
    capture_id = hashlib.sha256(
        b"fsit-capture-v1\0" + provider_canonical_bytes(record=request)
    ).hexdigest()
    item = ProviderItem(
        alias_group=None,
        entry_kind="regular",
        filesystem_id=filesystems[0],
        inline_bytes="0",
        logical_bytes=str(2**64 - 1),
        path_id="2" * 64,
        storage_flags=ProviderStorageFlags(
            chunk_backed=False,
            chunk_manifest=False,
            inline_content=False,
            metadata_gzip=False,
            remote_backed=False,
        ),
        version_id="3" * 64,
    )
    page = ProviderPage(
        capture_id=capture_id,
        item_count="1",
        items=(item,),
        page_digest=expected,
        page_ordinal=ordinal,
        protocol="fsit-page-v1",
    )
    assert provider_page_digest(page=page) == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    (
        ("", "a0c68fba11d807e4f6ce6ed5a978df3ab8c218079dea39bfbc0a96577da6096f"),
        (
            "raw/example.txt",
            "bb68279b7530d60174e7673c760f1e4069ff09079cce11535298baf11867fc5a",
        ),
        (
            "raw/žluťoučký\n\u2028\\key",
            "6adaaf922b818cb9695c2a9fe28063faa37ed27b1855209f20224b4560a47ca1",
        ),
    ),
)
def test_existing_go_path_identity_vectors(path: str, expected: str) -> None:
    """Use decoded real filesystem bytes and preserve exact UTF-8 key framing."""
    assert (
        provider_path_identity(
            capture_id="fe9205c7baa6969ca7f85b45c73f27aac732263ab4ed5ef02d2413912dc717d3",
            filesystem_id="aaaqeayeaudaocajbifqydiob4",
            relative_path=path,
        )
        == expected
    )
