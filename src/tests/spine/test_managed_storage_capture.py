"""Independent D152 resource preimage checks, not native cut qualification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from rememberstack.core.storage_capture import member_payload_digest
from rememberstack.model.storage_capture import StorageReferenceMember
from rememberstack.profiles.managed_storage_capture import _resource_set
from rememberstack.profiles.managed_storage_capture import _Spool
from rememberstack.profiles.managed_storage_capture import (
    ManagedStorageCaptureUnavailable,
)


def test_resource_set_matches_independent_document_and_keeps_zero(
    tmp_path: Path,
) -> None:
    """Match the binding literal JSON/preimage and actual once-counted amounts."""
    spool = _Spool(path=tmp_path / "resource.sqlite")
    try:
        for identity, amount in (("b" * 64, "17"), ("a" * 64, "0")):
            spool.resource(
                component="canonical_object",
                store="raw",
                identity=identity,
                version="c" * 64,
                evidence="d" * 64,
                measured_bytes=amount,
            )
        # Repeated logical references never make an additional stored origin.
        spool.resource(
            component="canonical_object",
            store="raw",
            identity="b" * 64,
            version="c" * 64,
            evidence="d" * 64,
            measured_bytes="17",
        )
        excluded = StorageReferenceMember(
            opaque_id="f" * 64,
            payload_digest="0" * 64,
            component="canonical_object",
            purpose="privacy_control",
            store_binding_id="forget",
            resource_identity="a" * 64,
            version_identity="c" * 64,
            logical_bytes="9999",
            alias_group=None,
        )
        excluded = excluded.model_copy(
            update={"payload_digest": member_payload_digest(member=excluded)}
        )
        spool.member(member=excluded)
        spool.resource(
            component=None,
            store="forget",
            identity=excluded.resource_identity,
            version=excluded.version_identity,
            evidence="d" * 64,
            measured_bytes=excluded.logical_bytes,
        )
        assert tuple(spool.members()) == (excluded,)
        actual, measured = _resource_set(
            spool=spool, capture_id="e" * 64, component="canonical_object"
        )
        document = {
            "contract": "rememberstack.storage_resource_set.v1",
            "capture_id": "e" * 64,
            "component": "canonical_object",
            "resources": [
                {
                    "store_binding_id": "raw",
                    "resource_identity": identity,
                    "version_identity": "c" * 64,
                    "resource_evidence_digest": "d" * 64,
                    "measured_bytes": amount,
                }
                for identity, amount in (("a" * 64, "0"), ("b" * 64, "17"))
            ],
        }
        canonical = json.dumps(
            document, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode()
        assert (
            actual
            == hashlib.sha256(
                b"rememberstack.storage_resource_set.v1\0" + canonical
            ).hexdigest()
        )
        assert measured == 17
        with pytest.raises(
            ManagedStorageCaptureUnavailable,
            match="capture_resource_ownership_conflict",
        ):
            spool.resource(
                component="canonical_object",
                store="raw",
                identity="b" * 64,
                version="c" * 64,
                evidence="d" * 64,
                measured_bytes="18",
            )
    finally:
        spool.close()
