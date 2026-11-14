"""D156 step 3: the ``documents`` filter on the three context operations, on PostgreSQL.

The memory holds two tagged products, as in the design's example (document
tags design §4.4): Autoboard's decision log says worker runs restart after
12 hours, a later Legalit operations note says 6 hours. Operations run through
the real ``OperationSurface`` (JSON arguments, coercion, executor) over the
real query engine and P1 adapter; lineages, claims and facts are seeded with
the step 2 helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID
from uuid import uuid4

import pytest
from sqlalchemy import text

from remember.models import DocumentSearchFilters
from remember.models import DocumentTagFilter
from remember.models import DocumentTagsUpdate
from rememberstack.adapters import p1_ranked
from rememberstack.model import AssuredOperation
from rememberstack.model import ClaimValidPrecision
from rememberstack.model import ContextBundleV2
from rememberstack.model import Envelope
from rememberstack.model import NegativeKind
from rememberstack.model import P1FactRow
from rememberstack.model import UnknownTagError
from rememberstack.spine.assured_operations import CANONICAL_OPERATIONS
from rememberstack.surfaces.operation_executor import OperationExecutor
from rememberstack.surfaces.operation_surface import InvalidArgumentError
from rememberstack.surfaces.operation_surface import OperationSurface
from tests.surfaces.lineage_seed import seed_entity_mention

# The step 2 rig: a fresh deployment with every P1 channel configured and the
# tags project:a, project:b, kind:decision and project:empty registered.
from tests.surfaces.test_document_tag_filters_pg import _claim
from tests.surfaces.test_document_tag_filters_pg import _DEPLOYMENT_ID
from tests.surfaces.test_document_tag_filters_pg import _FAR
from tests.surfaces.test_document_tag_filters_pg import _Lineage
from tests.surfaces.test_document_tag_filters_pg import _lineage
from tests.surfaces.test_document_tag_filters_pg import _NEAR
from tests.surfaces.test_document_tag_filters_pg import _PAST
from tests.surfaces.test_document_tag_filters_pg import _Rig
from tests.surfaces.test_document_tag_filters_pg import database_engine  # noqa: F401
from tests.surfaces.test_document_tag_filters_pg import rig  # noqa: F401


class _Registry:
    """The canonical operations, as a deployment's registry serves them."""

    def by_name(self, *, deployment_id: UUID, name: str) -> AssuredOperation | None:
        del deployment_id
        return next((op for op in CANONICAL_OPERATIONS if op.name.value == name), None)


def _surface(rig: _Rig) -> OperationSurface:  # noqa: F811
    return OperationSurface(
        registry=cast("object", _Registry()),  # type: ignore[arg-type]
        executor=OperationExecutor(query_engine=rig.query),
        deployment_id=_DEPLOYMENT_ID,
    )


def _run(rig: _Rig, operation: str, **arguments: object) -> Envelope:  # noqa: F811
    result = _surface(rig).run(name=operation, arguments=dict(arguments))
    assert isinstance(result, Envelope)
    return result


def _fact(
    rig: _Rig,  # noqa: F811
    *,
    label: str,
    supports: tuple[tuple[UUID, _Lineage], ...],
    contradicts: tuple[tuple[UUID, _Lineage], ...] = (),
    group: UUID | None = None,
    subject: UUID | None = None,
    vector: tuple[float, ...] = _NEAR,
) -> UUID:
    """One open relation with supporting and contradicting claims."""
    subject, obj, fact_id = subject or uuid4(), uuid4(), uuid4()
    with rig.engine.begin() as connection:
        for entity_id, name in ((subject, f"{label} subject"), (obj, f"{label} obj")):
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id, canonical_name,"
                    " normalized_name) VALUES (:e, :d, :name, lower(:name))"
                    " ON CONFLICT DO NOTHING"
                ),
                {"e": entity_id, "d": _DEPLOYMENT_ID, "name": name},
            )
        connection.execute(
            text(
                "INSERT INTO relations (relation_id, deployment_id,"
                " subject_entity_id, predicate, object_entity_id,"
                " normalizer_version, fact_label, ingested_at, valid_from,"
                " valid_precision, window_claim_ids, contradiction_group)"
                " VALUES (:fact, :d, :subject, 'works_for', :object,"
                " 'd156-test', :label, :at, :at, 'open', :witnesses, :group)"
            ),
            {
                "fact": fact_id,
                "d": _DEPLOYMENT_ID,
                "subject": subject,
                "object": obj,
                "label": label,
                "at": _PAST,
                "witnesses": [claim for claim, _ in supports],
                "group": group,
            },
        )
        for stance, evidence in (("supports", supports), ("contradicts", contradicts)):
            for claim_id, lineage in evidence:
                connection.execute(
                    text(
                        "INSERT INTO relation_evidence (deployment_id, relation_id,"
                        " claim_id, doc_id, stance, normalizer_version) VALUES"
                        " (:d, :fact, :claim, :doc, :stance, 'd156-test')"
                    ),
                    {
                        "d": _DEPLOYMENT_ID,
                        "fact": fact_id,
                        "claim": claim_id,
                        "doc": lineage.doc_id,
                        "stance": stance,
                    },
                )
        # entities are current only through a live mention (lineage_seed)
        claim_id, lineage = supports[0]
        for entity_id in (subject, obj):
            seed_entity_mention(
                connection=connection,
                deployment_id=_DEPLOYMENT_ID,
                entity_id=entity_id,
                doc_id=lineage.doc_id,
                chunk_id=lineage.chunk(),
                claim_id=claim_id,
                surface_form=f"anchor-{entity_id}",
                at=_PAST,
                resolver_version="d156-test",
            )
    rig.index.upsert_facts(
        rows=(
            P1FactRow(
                fact_id=fact_id,
                deployment_id=_DEPLOYMENT_ID,
                kind="relation",
                label=label,
                status="active",
                valid_from=_PAST,
                valid_until=None,
                valid_precision=ClaimValidPrecision.OPEN,
                ingested_at=_PAST,
                invalidated_at=None,
                vector=vector,
            ),
        )
    )
    return fact_id


@dataclass(frozen=True, slots=True)
class _Products:
    autoboard: _Lineage
    autoboard_review: _Lineage
    legalit: _Lineage
    untagged: _Lineage
    claims: dict[str, UUID]
    twelve: UUID
    six: UUID


def _products(rig: _Rig) -> _Products:  # noqa: F811
    """Autoboard (two lineages) says 12 hours, Legalit says 6, one untagged note agrees with 12.

    project:a is Autoboard, project:b is Legalit.
    """
    autoboard = _lineage(
        rig,
        label="autoboard-decisions",
        bodies=("worker runs restart after twelve hours",),
        tags=("project:a", "kind:decision"),
    )
    autoboard_review = _lineage(
        rig,
        label="autoboard-review",
        bodies=("the review confirms worker runs restart after twelve hours",),
        tags=("project:a",),
    )
    legalit = _lineage(
        rig,
        label="legalit-operations",
        bodies=("worker runs restart after six hours",),
        tags=("project:b",),
    )
    untagged = _lineage(
        rig,
        label="scratch-note",
        bodies=("someone noted worker runs restart after twelve hours",),
    )
    claims = {
        "autoboard": _claim(
            rig, lineage=autoboard, body="worker runs restart after twelve hours"
        ),
        "autoboard_review": _claim(
            rig,
            lineage=autoboard_review,
            body="the review confirms worker runs restart after twelve hours",
        ),
        "legalit": _claim(
            rig, lineage=legalit, body="worker runs restart after six hours"
        ),
        "untagged": _claim(
            rig,
            lineage=untagged,
            body="someone noted worker runs restart after twelve hours",
        ),
    }
    group = uuid4()
    twelve = _fact(
        rig,
        label="worker runs restart after 12 hours",
        supports=(
            (claims["autoboard"], autoboard),
            (claims["autoboard_review"], autoboard_review),
            (claims["untagged"], untagged),
        ),
        contradicts=((claims["legalit"], legalit),),
        group=group,
    )
    six = _fact(
        rig,
        label="worker runs restart after 6 hours",
        supports=((claims["legalit"], legalit),),
        contradicts=((claims["autoboard"], autoboard),),
        group=group,
    )
    return _Products(
        autoboard=autoboard,
        autoboard_review=autoboard_review,
        legalit=legalit,
        untagged=untagged,
        claims=claims,
        twelve=twelve,
        six=six,
    )


_AUTOBOARD = {"tags": {"all": ["project:a"]}}


def test_a_product_filtered_fact_read_discloses_the_other_products_contradiction(
    rig: _Rig,  # noqa: F811
) -> None:
    """§4.4 end to end: Autoboard's 12h fact, its support, Legalit's 6h marked out."""
    products = _products(rig)
    filtered = _run(rig, "facts_context", query="worker restart", documents=_AUTOBOARD)

    assert [fact.fact_id for fact in filtered.facts] == [products.twelve]
    (twelve,) = filtered.facts
    # supporting evidence: only the two Autoboard lineages, both marked in
    support = [
        (item.claim_id, item.in_documents_filter)
        for item in filtered.fact_evidence
        if item.stance == "supports"
    ]
    assert sorted(support) == sorted(
        [
            (products.claims["autoboard"], True),
            (products.claims["autoboard_review"], True),
        ]
    )
    # contradicting evidence from every document, the Legalit note marked out
    assert [
        (item.claim_id, item.in_documents_filter)
        for item in filtered.fact_evidence
        if item.stance == "contradicts"
    ] == [(products.claims["legalit"], False)]
    # the competing 6h fact is returned as an out-of-filter co-member
    assert twelve.contradiction is not None
    assert [
        (member.fact_id, member.in_documents_filter)
        for member in twelve.contradiction.co_members
    ] == [(products.six, False)]
    # two-sided totals: deployment-wide distinct lineages and the matching ones,
    # hand-counted (supports: Autoboard x2 + untagged; contradicts: Legalit)
    totals = {
        item.stance: (item.returned, item.total, item.in_documents_filter_total)
        for item in filtered.evidence_totals
    }
    assert totals == {"supports": (2, 3, 2), "contradicts": (1, 1, 0)}
    assert filtered.negative is None


def test_a_filtered_fact_is_never_re_adjudicated(rig: _Rig) -> None:  # noqa: F811
    """§4.3: value, window, support and contradiction status equal the unfiltered read."""
    products = _products(rig)
    unfiltered = _run(rig, "facts_context", query="worker restart")
    filtered = _run(rig, "facts_context", query="worker restart", documents=_AUTOBOARD)

    assert {fact.fact_id for fact in unfiltered.facts} == {
        products.twelve,
        products.six,
    }
    before = next(f for f in unfiltered.facts if f.fact_id == products.twelve)
    (after,) = filtered.facts
    assert after.contradiction is not None and before.contradiction is not None
    strip = {"contradiction": {"co_members": {"__all__": {"in_documents_filter"}}}}
    assert after.model_dump(exclude=strip) == before.model_dump(exclude=strip)
    # the unfiltered read carries no filter disclosure at all (byte-compatible)
    payload = unfiltered.model_dump_json()
    assert "in_documents_filter" not in payload
    assert "in_documents_filter_total" not in payload
    unfiltered_totals = {
        (item.fact_id, item.stance): item.total for item in unfiltered.evidence_totals
    }
    for item in filtered.evidence_totals:
        assert item.total == unfiltered_totals[(item.fact_id, item.stance)]


def test_testimony_reads_are_strictly_limited_to_matching_documents(
    rig: _Rig,  # noqa: F811
) -> None:
    """§4.4: claims_and_sources_context returns only what matching documents said."""
    products = _products(rig)
    filtered = _run(
        rig,
        "claims_and_sources_context",
        query="worker runs restart",
        documents=_AUTOBOARD,
    )
    autoboard = {products.autoboard.doc_id, products.autoboard_review.doc_id}
    assert {item.doc_id for item in filtered.evidence} == autoboard
    assert {item.doc_id for item in filtered.chunks} == autoboard
    unfiltered = _run(rig, "claims_and_sources_context", query="worker runs restart")
    assert {item.doc_id for item in unfiltered.evidence} == {
        *autoboard,
        products.legalit.doc_id,
        products.untagged.doc_id,
    }
    assert "in_documents_filter" not in unfiltered.model_dump_json()


def test_combined_context_passes_the_filter_unchanged_to_both_children(
    rig: _Rig,  # noqa: F811
) -> None:
    """combined_context@6: each member equals its direct filtered call."""
    products = _products(rig)
    bundle = _surface(rig).run(
        name="combined_context", arguments={"query": "worker", "documents": _AUTOBOARD}
    )
    assert isinstance(bundle, ContextBundleV2)
    assert [fact.fact_id for fact in bundle.facts.facts] == [products.twelve]
    assert {item.doc_id for item in bundle.claims_and_sources.evidence} == {
        products.autoboard.doc_id,
        products.autoboard_review.doc_id,
    }
    direct = _run(rig, "facts_context", query="worker", documents=_AUTOBOARD)
    assert bundle.facts.fact_evidence == direct.fact_evidence
    assert bundle.facts.evidence_totals == direct.evidence_totals


def test_each_context_grain_returns_a_matching_item_ranked_below_the_unfiltered_cut(
    rig: _Rig,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§4.6: eligibility before ranking on claims, chunks and facts (k=1).

    Facts are checked on both physical paths: the exact tier of a selective
    filter and the nominate-confirm loop of a broad one.
    """
    loud = _lineage(
        rig, label="loud", bodies=("zebra zebra zebra crossing",), tags=("project:b",)
    )
    quiet = _lineage(
        rig,
        label="quiet",
        bodies=("a long text that names a zebra once among many other words",),
        tags=("project:a",),
    )
    loud_claim = _claim(rig, lineage=loud, body="zebra zebra zebra crossing")
    quiet_claim = _claim(
        rig,
        lineage=quiet,
        body="a long text that names a zebra once among many other words",
    )
    loud_fact = _fact(
        rig, label="zebra loud", supports=((loud_claim, loud),), vector=_NEAR
    )
    quiet_fact = _fact(
        rig, label="zebra quiet", supports=((quiet_claim, quiet),), vector=_FAR
    )
    only_a = {"tags": {"any": ["project:a"]}}
    plain = _run(rig, "facts_context", query="zebra", k=1)
    assert [fact.fact_id for fact in plain.facts] == [loud_fact]
    scoped = _run(rig, "facts_context", query="zebra", k=1, documents=only_a)
    assert [fact.fact_id for fact in scoped.facts] == [quiet_fact]
    with monkeypatch.context() as broad:
        broad.setattr(p1_ranked, "EXACT_TIER_THRESHOLD", 0)
        looped = _run(rig, "facts_context", query="zebra", k=1, documents=only_a)
    assert [fact.fact_id for fact in looped.facts] == [quiet_fact]
    testimony = _run(
        rig, "claims_and_sources_context", query="zebra", k=1, documents=only_a
    )
    assert [item.claim_id for item in testimony.evidence] == [quiet_claim]
    assert [item.chunk_id for item in testimony.chunks] == [quiet.chunk()]


def test_entity_ids_and_documents_must_both_hold(rig: _Rig) -> None:  # noqa: F811
    """An item must mention a supplied entity and come from a matching document."""
    products = _products(rig)
    entity = uuid4()
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id, canonical_name,"
                " normalized_name) VALUES (:e, :d, 'Worker', 'worker')"
            ),
            {"e": entity, "d": _DEPLOYMENT_ID},
        )
        for key, lineage in (
            ("autoboard", products.autoboard),
            ("legalit", products.legalit),
        ):
            seed_entity_mention(
                connection=connection,
                deployment_id=_DEPLOYMENT_ID,
                entity_id=entity,
                doc_id=lineage.doc_id,
                chunk_id=lineage.chunk(),
                claim_id=products.claims[key],
                surface_form="worker",
                at=_PAST,
                resolver_version="d156-test",
            )
    scoped = _run(
        rig,
        "claims_and_sources_context",
        query="worker runs restart",
        entity_ids=[str(entity)],
        documents=_AUTOBOARD,
    )
    assert [item.claim_id for item in scoped.evidence] == [products.claims["autoboard"]]
    assert [item.chunk_id for item in scoped.chunks] == [products.autoboard.chunk()]
    legalit = _run(
        rig,
        "claims_and_sources_context",
        query="worker runs restart",
        entity_ids=[str(entity)],
        documents={"tags": {"none": ["project:a"]}},
    )
    assert [item.claim_id for item in legalit.evidence] == [products.claims["legalit"]]
    # facts anchored on the 12h fact's subject: matched only with Autoboard support
    with rig.engine.begin() as connection:
        subject = connection.execute(
            text("SELECT subject_entity_id FROM relations WHERE relation_id = :r"),
            {"r": products.twelve},
        ).scalar_one()
    anchored = rig.query.facts_context(
        deployment_id=_DEPLOYMENT_ID,
        query="worker restart",
        entity_ids=(subject,),
        documents=DocumentSearchFilters(tags=DocumentTagFilter(all=("project:b",))),
    )
    assert anchored.facts == ()
    assert anchored.negative is not None
    assert [
        fact.fact_id
        for fact in rig.query.facts_context(
            deployment_id=_DEPLOYMENT_ID,
            query="worker restart",
            entity_ids=(subject,),
            documents=DocumentSearchFilters(tags=DocumentTagFilter(all=("project:a",))),
        ).facts
    ] == [products.twelve]


def test_an_unknown_tag_fails_every_context_operation_before_retrieval(
    rig: _Rig,  # noqa: F811
) -> None:
    """D156 §4.1: unknown_tag names every unregistered tag; nothing is searched."""
    embed = cast(
        "MagicMock",
        rig.query._model_provider.embed,  # noqa: SLF001
    )
    calls = embed.call_count
    for name in ("claims_and_sources_context", "facts_context", "combined_context"):
        with pytest.raises(UnknownTagError) as refused:
            _surface(rig).run(
                name=name,
                arguments={
                    "query": "worker",
                    "documents": {"tags": {"all": ["project:typo"], "none": ["oops"]}},
                },
            )
        assert refused.value.tags == ("project:typo", "oops")
    assert embed.call_count == calls
    for documents in (
        {"tags": {}},
        {"tags": {"all": ["a", "A"]}},
        {"colour": "red"},
        {"doc_ids": ["not-a-uuid"]},
        ["project:a"],
    ):
        with pytest.raises(InvalidArgumentError):
            _surface(rig).run(
                name="facts_context",
                arguments={"query": "worker", "documents": documents},
            )


def test_a_completed_empty_filtered_context_read_says_which_empty_case_occurred(
    rig: _Rig,  # noqa: F811
) -> None:
    """§4.5 on the context operations."""
    _products(rig)
    no_document = "no live document matches the documents filter"
    nothing_relevant = (
        "live documents match the documents filter,"
        " but none of them says anything relevant"
    )
    nobody = {"tags": {"all": ["project:empty"]}}
    for name in ("claims_and_sources_context", "facts_context"):
        envelope = _run(rig, name, query="worker", documents=nobody)
        assert envelope.negative is not None
        assert envelope.negative.kind is NegativeKind.KNOWN_EMPTY
        assert envelope.negative.explanation.endswith(f": {no_document}")
    # a live document now carries the tag but supports no fact
    rig.tags.update_document_tags(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=_lineage(rig, label="minutes", bodies=("lunch menu for friday",)).doc_id,
        request=DocumentTagsUpdate(add=("project:empty",)),
    )
    facts = _run(rig, "facts_context", query="worker", documents=nobody)
    assert facts.negative is not None
    assert facts.negative.kind is NegativeKind.KNOWN_EMPTY
    assert facts.negative.explanation.endswith(f": {nothing_relevant}")
    # an unfiltered empty read keeps its ordinary explanation
    with rig.engine.begin() as connection:
        connection.execute(text("DELETE FROM relation_evidence"))
    plain = _run(rig, "facts_context", query="worker")
    assert plain.negative is not None
    assert "documents filter" not in plain.negative.explanation


def test_a_filtered_fact_read_stopped_by_its_work_bound_reports_boundary(
    rig: _Rig,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§4.5: an incomplete filtered read is a boundary, never known_empty."""
    loud = _lineage(rig, label="loud", bodies=("zebra",), tags=("project:b",))
    quiet = _lineage(rig, label="quiet", bodies=("zebra too",), tags=("project:a",))
    _fact(
        rig,
        label="zebra loud",
        supports=((_claim(rig, lineage=loud, body="zebra"), loud),),
        vector=_NEAR,
    )
    _fact(
        rig,
        label="zebra quiet",
        supports=((_claim(rig, lineage=quiet, body="zebra too"), quiet),),
        vector=_FAR,
    )
    # a broad filter (no exact tier) and one candidate of work: the first
    # ranked fact, which is not project:a
    monkeypatch.setattr(p1_ranked, "EXACT_TIER_THRESHOLD", 0)
    monkeypatch.setattr(p1_ranked, "MIN_WORK_BUDGET", 1)
    monkeypatch.setattr(p1_ranked, "WORK_BUDGET_PER_K", 0)
    envelope = _run(
        rig,
        "facts_context",
        query="zebra",
        k=1,
        documents={"tags": {"all": ["project:a"]}},
    )
    assert envelope.facts == ()
    assert envelope.negative is not None
    assert envelope.negative.kind is NegativeKind.BOUNDARY


def test_resolve_entity_is_unaffected_by_tag_state(rig: _Rig) -> None:  # noqa: F811
    """§4.2: name resolution has no document filter and ignores tags."""
    products = _products(rig)
    with rig.engine.begin() as connection:
        subject = connection.execute(
            text("SELECT subject_entity_id FROM relations WHERE relation_id = :r"),
            {"r": products.twelve},
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO aliases (alias_id, deployment_id, entity_id, alias_text,"
                " normalized_lemma, provenance) VALUES (:alias, :d, :entity,"
                " 'Worker Pool', 'worker pool', 'source')"
            ),
            {"alias": uuid4(), "d": _DEPLOYMENT_ID, "entity": subject},
        )
    before = _run(rig, "resolve_entity", name="Worker Pool")
    assert before.entities
    rig.tags.update_document_tags(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=products.autoboard.doc_id,
        request=DocumentTagsUpdate(set=()),
    )
    after = _run(rig, "resolve_entity", name="Worker Pool")
    assert json.loads(
        after.model_dump_json(exclude={"freshness", "temporal_scope"})
    ) == json.loads(before.model_dump_json(exclude={"freshness", "temporal_scope"}))
    assert "documents" not in json.dumps(
        next(
            op for op in CANONICAL_OPERATIONS if op.name.value == "resolve_entity"
        ).parameters
    )
