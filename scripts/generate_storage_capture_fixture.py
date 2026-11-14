"""Generate synthetic D152 regression pins; never deployed qualification.

Run with the engine environment and PYTHONPATH=src:. --check compares exact
bytes. Operator source/image/clock/cost acceptance remains separate. Historical
dated production/functional receipts are evidence and are never regenerated.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from rememberstack.profiles.storage_capture_profile import build_managed_storage_profile
from rememberstack.profiles.storage_capture_profile import current_class_rules
from rememberstack.profiles.storage_capture_profile import ManagedFilesystemBinding
from rememberstack.profiles.storage_capture_profile import ManagedStoreBindings
from rememberstack.spine import catalog_contract
from rememberstack.spine.storage_capture_catalog import NativeCatalogAcquisition
from rememberstack.spine.storage_capture_catalog import NativeCorpusPage
from rememberstack.spine.storage_capture_catalog import NativeReferencePage
from rememberstack.spine.storage_capture_catalog import NativeTerminalPage
from rememberstack.spine.storage_capture_guard import _EXTENSIONS
from rememberstack.spine.storage_capture_guard import _ROOTS
from rememberstack.spine.storage_capture_guard import NativeRelationGuardSample
from rememberstack.spine.storage_capture_view import NativeCaptureProfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "src/tests/fixtures/storage_capture/live_corpus_profile.json"


def _digest(domain: str, document: object) -> str:
    body = json.dumps(
        document, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(domain.encode() + b"\0" + body).hexdigest()


def capture() -> dict[str, object]:
    """Derive schema, source, classification, mapping and native test pins."""
    source = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / "src/rememberstack").rglob("*.py"))
    }
    schema = {
        "head": ScriptDirectory.from_config(
            Config(str(ROOT / "alembic.ini"))
        ).get_current_head(),
        "catalog": {
            name: getattr(catalog_contract, name)
            for name in sorted(vars(catalog_contract))
            if name.startswith("EXPECTED_")
        },
        "guard_roots": _ROOTS,
        "extensions": _EXTENSIONS,
        "migrations": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(
                (ROOT / "src/rememberstack/spine/migrations/versions").glob("*.py")
            )
        },
        "records": {
            model.__name__: model.model_json_schema()
            for model in (
                NativeRelationGuardSample,
                NativeCatalogAcquisition,
                NativeReferencePage,
                NativeCorpusPage,
                NativeTerminalPage,
            )
        },
    }
    schema_digest = _digest("rememberstack.test_schema.v1", schema)
    native_inputs = {
        "source_digest": _digest("rememberstack.test_engine_source.v1", source),
        "schema_digest": schema_digest,
        "schema_profile_digest": _digest(
            "rememberstack.test_schema_profile.v1", schema
        ),
        "clock_profile_digest": "5" * 64,
        "clock_error_us": "1000",
        "view_lifetime_ms": 30_000,
        "release_margin_us": 500_000,
    }
    native = NativeCaptureProfile(
        profile_digest=_digest("rememberstack.test_native_profile.v1", native_inputs),
        **native_inputs,  # type: ignore[arg-type]
    )
    bindings = ManagedStoreBindings(
        deployment_id="0" * 31 + "1",
        postgres_database_name="rememberstack",
        provider_organisation_id="1" * 32,
        provider_cell_id="synthetic-cell",
        filesystems=tuple(
            ManagedFilesystemBinding(
                role=role,
                filesystem_id=base64.b32encode(bytes([number]) * 16)
                .decode()
                .rstrip("=")
                .lower(),
            )
            for number, role in enumerate(("artifacts", "corpus", "forget", "raw"))
        ),
    )
    profile = build_managed_storage_profile(
        native=native,
        bindings=bindings,
        managed_runtime_source_digest="6" * 64,
        provider_profile_digest="a" * 64,
        provider_clock_profile_digest="b" * 64,
    )
    return {
        "kind": "synthetic_live_corpus_regression_not_deployment_qualification",
        "schema": schema,
        "profile": profile.model_dump(mode="json"),
        "request": profile.request(cut_at_unix_us="1800000000000000").model_dump(
            mode="json"
        ),
        "source_rules": current_class_rules(),
    }


def render() -> str:
    return json.dumps(capture(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    body = render()
    if args.check:
        if FIXTURE.read_text() != body:
            raise SystemExit(
                "storage capture regression fixture is stale; run its generator"
            )
    else:
        FIXTURE.write_text(body)
