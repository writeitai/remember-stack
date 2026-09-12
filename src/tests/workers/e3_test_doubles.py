"""Shared recording doubles for D96 E3 and resolver tests."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import RecordingProfileRefresher
from rememberstack.model import ClaimForNormalization
from rememberstack.model import EntityRef
from rememberstack.model import ProviderCallUsage
from rememberstack.model import ResolvedEntity
from rememberstack.model.relations import NormalizationResponse
from rememberstack.workers.e3 import E3Settings
from rememberstack.workers.e3 import NormalizeRelationsHandler


class RecordingCostMeter:
    """Capture call keys for billing assertions."""

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
        """Start with an empty call log."""
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
        """Bind an optional predicate map."""
        self.predicates = predicates or {"related_to": None}
        self.other_ensured: list[str] = []
        self.upserts: list[dict[str, Any]] = []
        self.applications = RecordingApplications()

    def active_predicates(self, *, deployment_id: object) -> dict[str, str | None]:
        """Expose the configured governed vocabulary."""
        del deployment_id
        return self.predicates

    def predicate_prompt_lines(self, *, deployment_id: object) -> str:
        """Render the governed predicate names for the fixture prompt."""
        del deployment_id
        return "\n".join(self.predicates)

    def ensure_other_predicate(self, *, deployment_id: object, predicate: str) -> None:
        """Record other-predicate registration."""
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
        """Record one upsert and return a created stub."""
        del deployment_id, subject_entity_id, object_entity_id, claim_id, doc_id
        self.upserts.append(
            {"predicate": predicate, "normalizer_version": normalizer_version}
        )

        class _Upserted:
            created = True
            relation_id = uuid4()

        return _Upserted()


class RecordingApplications:
    """Retain the first normalization answer and record staged output coordinates."""

    def __init__(self) -> None:
        """Start with no published answer or staged assertions."""
        self.published: tuple[NormalizationResponse, tuple[Any, ...]] | None = None
        self.staged: list[dict[str, Any]] = []

    def normalization(
        self, **kwargs: Any
    ) -> tuple[NormalizationResponse, tuple[Any, ...]] | None:
        """Return the already frozen answer on retry."""
        del kwargs
        return self.published

    def publish_normalization(
        self, *, output: NormalizationResponse, accepted: tuple[Any, ...], **kwargs: Any
    ) -> tuple[NormalizationResponse, tuple[Any, ...]]:
        """Retain the original answer and accepted ordinals."""
        del kwargs
        if self.published is None:
            self.published = (output, accepted)
        return self.published

    def stage(self, **kwargs: Any) -> None:
        """Record a staging request without creating a fact."""
        self.staged.append(kwargs)


def _claim() -> ClaimForNormalization:
    """Return one claim stub for unit normalization."""
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
    """Widen nested canned dictionaries for fake-provider typing."""
    return data


def _handler(
    *,
    provider: FakeModelProvider,
    resolver: object | None = None,
    facts: object | None = None,
) -> NormalizeRelationsHandler:
    """Build a handler with the generate path wired to optional test doubles."""
    return NormalizeRelationsHandler(
        claim_catalog=None,  # type: ignore[arg-type]
        chunk_catalog=None,  # type: ignore[arg-type]
        registry=None,  # type: ignore[arg-type]
        resolver=resolver,  # type: ignore[arg-type]
        facts=facts,  # type: ignore[arg-type]
        observation_adjudicator=None,  # type: ignore[arg-type]
        profile_refresher=RecordingProfileRefresher(),
        model_provider=provider,
        settings=E3Settings(normalize_model="test-model"),
        chunker_version="test",
    )


def same_fact_application_answer(*, prompt: str) -> dict[str, object]:
    """Canned identity decision for fixtures explicitly describing one repeated fact.

    This is test input, not an identity heuristic in the engine. Contextual split
    and correction decisions have separate PostgreSQL writer acceptance cases.
    """
    snapshot = json.loads(prompt.split("INPUT JSON:\n", 1)[1])
    if snapshot["facts"]:
        return {
            "target": {"fact_id": snapshot["facts"][0]["fact_id"]},
            "confidence": 0.9,
            "rationale": "Fixture reports the same fact.",
        }
    return {
        "target": {"new_handle": "fixture"},
        "new_facts": [
            {
                "handle": "fixture",
                "assertion_application_id": snapshot["application_id"],
            }
        ],
        "confidence": 0.9,
        "rationale": "First fixture assertion.",
    }
