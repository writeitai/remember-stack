"""D86 unknown-entity-type gate — vacated by D96 WP-I.2.

Shared recording doubles for E3 unit tests live here. The D86 retry-then-drop
assertions are skipped until rewritten as D96 name-only proofs.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.model import ClaimForNormalization
from rememberstack.model import EntityRef
from rememberstack.model import ProviderCallUsage
from rememberstack.model import ResolvedEntity
from rememberstack.workers.e3 import E3Settings
from rememberstack.workers.e3 import NormalizeRelationsHandler

pytestmark = pytest.mark.skip(
    reason="D86 vacated by D96 WP-I.2; rewrite as type-discard tests"
)


class RecordingCostMeter:
    """Capture call_keys for billing assertions."""

    def __init__(self) -> None:
        """Start with an empty record list."""
        self.records: list[tuple[str, str | None]] = []

    def record(
        self,
        *,
        call_key: str,
        tier: str | None,
        usage: ProviderCallUsage,
        outcome: str = "ok",
    ) -> None:
        """Append one metered call."""
        del usage, outcome
        self.records.append((call_key, tier))


class RecordingResolver:
    """Resolver that records resolve calls."""

    def __init__(self) -> None:
        """Empty call log."""
        self.calls: list[EntityRef] = []

    def resolve(
        self,
        *,
        deployment_id: object,
        reference: EntityRef,
        claim: ClaimForNormalization,
        meter: object = None,
        call_key: str = "resolve",
    ) -> ResolvedEntity:
        """Record the reference and return a synthetic entity id."""
        del deployment_id, claim, meter, call_key
        self.calls.append(reference)
        return ResolvedEntity(entity_id=uuid4(), created=True)


class RecordingFacts:
    """Minimal fact catalog for normalize unit paths that touch predicates."""

    def __init__(self, *, predicates: dict[str, str | None] | None = None) -> None:
        """Bind optional predicate map."""
        self.predicates = predicates or {"related_to": None}
        self.other_ensured: list[str] = []
        self.upserts: list[dict[str, Any]] = []

    def ensure_other_predicate(self, *, deployment_id: object, predicate: str) -> None:
        """Record other: registration."""
        del deployment_id
        self.other_ensured.append(predicate)

    def upsert_relation(
        self,
        *,
        deployment_id: object,
        subject_entity_id: object,
        predicate: str,
        object_entity_id: object,
        claim_id: object,
        doc_id: object,
        normalizer_version: str,
    ) -> Any:
        """Record upsert and return a created stub."""
        del deployment_id, subject_entity_id, object_entity_id, claim_id, doc_id
        self.upserts.append(
            {"predicate": predicate, "normalizer_version": normalizer_version}
        )

        class _Upserted:
            created = True
            relation_id = uuid4()

        return _Upserted()


def _claim() -> ClaimForNormalization:
    """One claim stub for unit normalize."""
    return ClaimForNormalization(
        claim_id=uuid4(),
        deployment_id=uuid4(),
        doc_id=uuid4(),
        chunk_id=uuid4(),
        claim_text="The caching process stores hot keys.",
        is_attributed=False,
        extractor_version="e2-test",
    )


def _payload(data: dict[str, Any]) -> dict[str, object]:
    """Widen nested canned dicts for FakeModelProvider typing."""
    return data


def _handler(
    *,
    provider: FakeModelProvider,
    resolver: object | None = None,
    facts: object | None = None,
) -> NormalizeRelationsHandler:
    """Handler with generate path wired; catalogs optional for drop tests."""
    return NormalizeRelationsHandler(
        claim_catalog=None,  # type: ignore[arg-type]
        chunk_catalog=None,  # type: ignore[arg-type]
        registry=None,  # type: ignore[arg-type]
        resolver=resolver,  # type: ignore[arg-type]
        facts=facts,  # type: ignore[arg-type]
        observation_adjudicator=None,  # type: ignore[arg-type]
        model_provider=provider,
        settings=E3Settings(normalize_model="test-model"),
        chunker_version="test",
    )


def test_d86_gate_retired() -> None:
    """Placeholder so the skipped module stays discoverable."""
    raise AssertionError("D86 tests must be rewritten, not re-enabled as-is")
