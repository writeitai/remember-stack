"""D163 adversarial eligibility on the deployed and migrated PostgreSQL schema."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import UTC
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from benchmarks.d163_qualification import _arguments
from benchmarks.d163_qualification import _index
from benchmarks.d163_qualification import _plans
from benchmarks.d163_qualification import _record_nomination
from benchmarks.d163_qualification import BASELINE_HEAD
from benchmarks.d163_qualification import capture
from benchmarks.d163_qualification import Case
from benchmarks.d163_qualification import KINDS
from benchmarks.d163_qualification import TARGET_HEAD
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import make_url
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.adapters.bounded_postgres_read import BoundedPostgresReadPool
from rememberstack.adapters.postgres_p1 import FACT_INPUT_POLICY
from rememberstack.adapters.postgres_p1 import PostgresP1Index
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.document_search import DocumentSearch
from rememberstack.spine.effective_time import EffectiveTimeCatalog
from rememberstack.spine.retrieval_eligibility import fact_eligibility_ctes
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces.query_engine import QueryEngine
from tests.spine.d163_scale_fixture import _expand_chunks
from tests.spine.test_d153_eligibility_pg import _plan_nodes
from tests.surfaces.test_d140_scoped_retrieval import _claim
from tests.surfaces.test_d140_scoped_retrieval import _declare
from tests.surfaces.test_d140_scoped_retrieval import _DEPLOYMENT_ID
from tests.surfaces.test_d140_scoped_retrieval import _FAR
from tests.surfaces.test_d140_scoped_retrieval import _gate_corpus
from tests.surfaces.test_d140_scoped_retrieval import _lineage
from tests.surfaces.test_d140_scoped_retrieval import _MODEL
from tests.surfaces.test_d140_scoped_retrieval import _NEAR
from tests.surfaces.test_d140_scoped_retrieval import _NOW
from tests.surfaces.test_d140_scoped_retrieval import _PAST
from tests.surfaces.test_d140_scoped_retrieval import _pending_pair
from tests.surfaces.test_d140_scoped_retrieval import _REVISED
from tests.surfaces.test_d140_scoped_retrieval import _Rig
from tests.surfaces.test_d140_scoped_retrieval import _swap_reading

_ROOT = Path(__file__).resolve().parents[3]


def migration_config(engine: Engine) -> Config:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        engine.url.render_as_string(hide_password=False).replace("%", "%%"),
    )
    return config


@contextmanager
def scratch_database(revision: str) -> Iterator[Engine]:
    try:
        base = make_url(load_database_settings().sqlalchemy_url())
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL required for D163 PostgreSQL checks")
    name = f"d163_{uuid4().hex[:10]}"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    engine = create_engine(base.set(database=name))
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        command.upgrade(config=migration_config(engine), revision=revision)
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            for role in ("query", "graph"):
                connection.exec_driver_sql(
                    f"DROP ROLE IF EXISTS rememberstack_{role}_{name}"
                )
        admin.dispose()


@dataclass(frozen=True)
class Corpus:
    case: Case
    facts: dict[tuple[str, str], UUID]
    origin: UUID
    survivor: UUID
    source_only: UUID
    time_only: UUID


def seed_corpus(engine: Engine) -> Corpus:
    """Both fact kinds share adversarial inputs. All vectors are deterministic."""
    DeploymentBootstrapper(engine=engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="d163-oracle",
            name="D163 oracle",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    index = PostgresP1Index(engine=engine, embedding_model=_MODEL)
    index.configure_channels(deployment_id=_DEPLOYMENT_ID)
    provider = MagicMock()
    provider.embed.return_value = MagicMock(vectors=(_NEAR,), usage=None)
    rig = _Rig(
        engine=engine,
        index=index,
        query=QueryEngine(
            engine=engine,
            search_index=index,
            model_provider=provider,
            embedding_model=_MODEL,
            fact_read_pool=BoundedPostgresReadPool(
                engine=engine, max_concurrency=1, pool_wait_seconds=5
            ),
        ),
        periods=EffectiveTimeCatalog(engine=engine),
        documents=DocumentSearch(engine=engine),
    )
    ordinary = _gate_corpus(rig)
    undeclared = _lineage(
        rig, label="undeclared reuse", bodies=(("reused",), ("reused",))
    )
    time_only = _claim(
        rig,
        lineage=undeclared,
        origin=undeclared.chunk(0),
        body="reused",
        occurrences={undeclared.chunk(0): (0, 6), undeclared.chunk(1): (0, 6)},
    )
    periodised = _lineage(
        rig, label="declared reuse", bodies=(("carried",), ("carried", "carried again"))
    )
    _declare(rig, periodised, 0, (_PAST, _REVISED))
    _declare(rig, periodised, 1, (_REVISED, None))
    carried = _claim(
        rig,
        lineage=periodised,
        origin=periodised.chunk(0),
        body="carried",
        occurrences={
            periodised.chunk(0): (0, 7),
            periodised.chunk(1): (0, 7),
            periodised.chunk(1, 1): (0, 7),
        },
    )
    cross_doc = _claim(
        rig,
        lineage=periodised,
        origin=periodised.chunk(0),
        body="carried",
        occurrences={undeclared.chunk(1): (0, 6)},
    )
    replaced = _lineage(rig, label="replaced reading", bodies=(("old reading",),))
    replaced_claim = _claim(
        rig,
        lineage=replaced,
        origin=replaced.chunk(0),
        body="old",
        occurrences={replaced.chunk(0): (0, 3)},
    )
    _swap_reading(rig, replaced.version(0))
    pending = _pending_pair(rig, label="pending edition", body="pending", vector=_FAR)
    pending_claim = _claim(
        rig,
        lineage=pending,
        origin=pending.chunk(0),
        body="pending",
        occurrences={pending.chunk(0): (0, 7), pending.chunk(1): (0, 7)},
    )
    withdrawn = _lineage(rig, label="retracted period", bodies=(("retracted",),))
    _declare(rig, withdrawn, 0, (_PAST, None))
    _declare(rig, withdrawn, 0)
    retracted_claim = _claim(
        rig,
        lineage=withdrawn,
        origin=withdrawn.chunk(0),
        body="retracted",
        occurrences={withdrawn.chunk(0): (0, 9)},
    )
    cleared = _lineage(rig, label="cleared mode", bodies=(("cleared",),))
    _declare(rig, cleared, 0, (_NOW + timedelta(days=30), None))
    clear = rig.periods.clear_effective_time(
        deployment_id=_DEPLOYMENT_ID, doc_id=cleared.doc_id
    )
    assert clear.cleared_at is not None
    cleared_claim = _claim(
        rig,
        lineage=cleared,
        origin=cleared.chunk(0),
        body="cleared",
        occurrences={cleared.chunk(0): (0, 7)},
    )
    memo, source_only = ordinary.claims["memo"], ordinary.claims["repealed"]
    cases = {
        "split_links": ((source_only, "supports"), (time_only, "supports")),
        "multi_source": ((memo, "supports"), (ordinary.claims["in_force"], "supports")),
        "contradiction_only": ((memo, "contradicts"),),
        "contradiction_blocked": ((memo, "contradicts"), (source_only, "supports")),
        "absent": (),
        "carried": ((carried, "supports"),),
        "foreign_occurrence": ((cross_doc, "supports"),),
        "replaced_reading": ((replaced_claim, "supports"),),
        "unready_edition": ((pending_claim, "supports"),),
        "retracted_period": ((retracted_claim, "supports"),),
        "cleared_mode": ((cleared_claim, "supports"),),
    }
    facts: dict[tuple[str, str], UUID] = {
        ("relation", key): value for key, value in ordinary.facts.items()
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_versions SET deleted_at = :now WHERE version_id = ANY(:ids)"
            ),
            {"now": _NOW, "ids": [periodised.version(0), undeclared.version(0)]},
        )
        origin = connection.execute(
            text("SELECT object_entity_id FROM relations WHERE relation_id=:id"),
            {"id": ordinary.facts["mixed"]},
        ).scalar_one()
        neighbor = connection.execute(
            text("SELECT object_entity_id FROM relations WHERE relation_id=:id"),
            {"id": ordinary.facts["memo"]},
        ).scalar_one()
        connection.execute(
            text(
                "UPDATE entities SET status='merged', merged_into=:survivor WHERE entity_id=:origin"
            ),
            {"origin": origin, "survivor": ordinary.subject},
        )
        connection.execute(
            text(
                "UPDATE relations SET embedding=CAST(:vector AS vector) WHERE relation_id=:id"
            ),
            {"vector": str(list(_NEAR)), "id": ordinary.facts["memo"]},
        )
        for kind in KINDS:
            for name, links in cases.items():
                fact_id = uuid4()
                facts[kind, name] = fact_id
                table, key = f"{kind}s", f"{kind}_id"
                specific_columns = (
                    "object_entity_id, predicate, fact_label"
                    if kind == "relation"
                    else "statement, obs_label"
                )
                specific_values = (
                    ":object, 'works_for', :label"
                    if kind == "relation"
                    else ":label, :label"
                )
                connection.execute(
                    text(
                        f"INSERT INTO {table} ({key}, deployment_id, subject_entity_id, "
                        f"normalizer_version, ingested_at, valid_from, valid_precision, window_claim_ids, "
                        f"embedding_model, embedding_input_policy_version, embedding_text_hash, embedding, {specific_columns}) "
                        f"VALUES (:id, :deployment, :subject, 'd163-oracle', :past, :past, 'open', ARRAY[CAST(:witness AS uuid)], "
                        f":model, :policy, repeat('a',64), CAST(:vector AS vector), {specific_values})"
                    ),
                    {
                        "id": fact_id,
                        "deployment": _DEPLOYMENT_ID,
                        "subject": ordinary.subject,
                        "object": neighbor,
                        "past": _PAST,
                        "witness": memo,
                        "label": name,
                        "model": _MODEL,
                        "policy": FACT_INPUT_POLICY,
                        "vector": str(list(_FAR)),
                    },
                )
                for claim, stance in links:
                    connection.execute(
                        text(
                            f"INSERT INTO {kind}_evidence ({key}, deployment_id, claim_id, doc_id, "
                            "stance, normalizer_version) SELECT :fact, deployment_id, claim_id, "
                            "doc_id, CAST(:stance AS evidence_stance), 'd163-oracle' "
                            "FROM claims WHERE claim_id = :claim"
                        ),
                        {"fact": fact_id, "claim": claim, "stance": stance},
                    )
    return Corpus(
        case=Case(
            deployment_id=_DEPLOYMENT_ID,
            embedding_model=_MODEL,
            vector=_NEAR,
            evaluated_at=datetime.now(UTC),
            believed_at=clear.cleared_at - timedelta(microseconds=1),
            predicate="works_for",
            entity_ids=(str(ordinary.subject), str(neighbor)),
            ranking_entity_ids=(str(ordinary.subject),),
            k=1,
        ),
        facts=facts,
        origin=origin,
        survivor=ordinary.subject,
        source_only=source_only,
        time_only=time_only,
    )


@pytest.fixture(scope="module")
def d163_corpus() -> Iterator[tuple[Engine, Corpus]]:
    with scratch_database(BASELINE_HEAD) as engine:
        corpus = seed_corpus(engine)
        command.upgrade(config=migration_config(engine), revision="head")
        yield engine, corpus


def test_split_links_and_duplicate_survivors_at_every_eligible_cut(
    d163_corpus: tuple[Engine, Corpus],
) -> None:
    engine, corpus = d163_corpus
    snapshot = capture(engine, corpus.case, timeout=20)
    cuts = snapshot["cuts"]
    for kind in KINDS:
        final = {str(row[2]) for row in cuts[f"final/{kind}"]}
        for name in (
            "split_links",
            "carried",
            "multi_source",
            "contradiction_only",
            "cleared_mode",
        ):
            assert str(corpus.facts[kind, name]) in final
        for name in (
            "absent",
            "foreign_occurrence",
            "contradiction_blocked",
            "replaced_reading",
            "unready_edition",
            "retracted_period",
        ):
            assert str(corpus.facts[kind, name]) not in final
        assert len(cuts[f"repeated/{kind}"]) == 2
        assert [row[0] for row in cuts[f"repeated/{kind}"]] == [1, 2]
        assert corpus.facts[kind, "cleared_mode"] not in {
            row[1] for row in cuts[f"pinned/{kind}"]
        }
    merged = str(corpus.facts["relation", "mixed"])
    near = str(corpus.facts["relation", "memo"])
    for stage in ("default", "filtered"):
        (row,) = [row for row in cuts[stage] if row[0] == merged]
        assert row[2:] == (1.0, 1)
        assert snapshot["ordered"][stage][0][1] == near
    (row,) = [row for row in cuts["scoped_final/relation"] if str(row[2]) == merged]
    assert row[-1] == 1
    with engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT claim_id FROM v_memory_claim_history_by_id WHERE claim_id = ANY(:claims)"
                ),
                {"claims": [corpus.source_only, corpus.time_only]},
            )
            .scalars()
            .all()
        )
    assert rows == [corpus.source_only]


def _survivor(connection: Connection, *, origin: UUID) -> UUID | None:
    return connection.execute(
        text(
            "SELECT survivor_entity_id FROM v_memory_entity_survivor WHERE entity_id = :origin "
            "AND deployment_id = :deployment"
        ),
        {"origin": origin, "deployment": _DEPLOYMENT_ID},
    ).scalar_one_or_none()


@pytest.mark.parametrize("extra_links", [0, 16])
def test_negative_probes_prune_one_evidence_leaf_in_both_plan_modes(
    d163_corpus: tuple[Engine, Corpus], extra_links: int
) -> None:
    engine, corpus = d163_corpus
    trace, _ = _record_nomination(
        _index(engine, corpus.case), arguments=_arguments(corpus.case, timeout=20)
    )
    gates = {kind: statement for kind, statement, _ in trace.gates}
    with engine.connect() as connection:
        connection.exec_driver_sql("SET LOCAL jit = off")
        for kind in KINDS:
            # Raw evidence with missing claim IDs cannot pass either source arm.
            # Grow the rejected fact's links while holding all other inputs fixed.
            rejected = corpus.facts[kind, "foreign_occurrence"]
            connection.execute(
                text(
                    f"INSERT INTO {kind}_evidence ({kind}_id, deployment_id, claim_id, "
                    "doc_id, stance, normalizer_version) "
                    f"SELECT {kind}_id, deployment_id, gen_random_uuid(), doc_id, stance, normalizer_version "
                    f"FROM {kind}_evidence CROSS JOIN generate_series(1, CAST(:extra AS integer)) "
                    f"WHERE {kind}_id=:fact"
                ),
                {"extra": extra_links, "fact": rejected},
            )
            for name, expected_rows in (
                ("foreign_occurrence", 0),
                ("contradiction_only", 1),
            ):
                statement = gates[kind]
                statement.parameters["batch_ids"] = [str(corpus.facts[kind, name])]
                for mode, result in _plans(connection, statement).items():
                    plan = result["Plan"]
                    assert plan["Actual Rows"] == expected_rows, (kind, name, mode)
                    leaves = [
                        node
                        for node in _plan_nodes(node=plan)
                        if node.get("Actual Loops", 0) > 0
                        and str(node.get("Relation Name", "")).startswith(
                            f"{kind}_evidence_"
                        )
                    ]
                    assert len({leaf["Relation Name"] for leaf in leaves}) == 1, (
                        kind,
                        name,
                        mode,
                    )
                    if name == "contradiction_only":
                        assert any(
                            leaf["Alias"].startswith("support") for leaf in leaves
                        )
                    else:
                        assert any(
                            leaf["Actual Rows"] == 1 + extra_links for leaf in leaves
                        )
        relation = gates["relation"]
        relation.parameters["batch_ids"] = [str(corpus.facts["relation", "mixed"])]
        for result in _plans(connection, relation).values():
            entities = [
                node
                for node in _plan_nodes(node=result["Plan"])
                if node.get("Relation Name") == "entities"
                and node.get("Actual Loops", 0) > 0
            ]
            assert (
                sum(
                    node["Actual Loops"]
                    for node in entities
                    if str(node.get("Alias", "")).startswith("seed")
                )
                == 2
            )
            assert (
                sum(
                    node["Actual Rows"] * node["Actual Loops"]
                    for node in entities
                    if str(node.get("Alias", "")).startswith("next")
                )
                == 1
            )


def test_origin_probes_stay_keyed_as_their_own_documents_grow(
    d163_corpus: tuple[Engine, Corpus],
) -> None:
    engine, corpus = d163_corpus
    trace, _ = _record_nomination(
        _index(engine, corpus.case), arguments=_arguments(corpus.case, timeout=20)
    )
    gates = {kind: statement for kind, statement, _ in trace.gates}

    def keyed_work() -> dict[tuple[str, str, str], tuple[int, float, int]]:
        work = {}
        with engine.connect() as connection:
            connection.exec_driver_sql("SET LOCAL jit = off")
            for kind in KINDS:
                for name in ("multi_source", "carried"):
                    statement = gates[kind]
                    statement.parameters["batch_ids"] = [str(corpus.facts[kind, name])]
                    for mode, result in _plans(connection, statement).items():
                        plan = result["Plan"]
                        assert plan["Actual Rows"] == 1, (kind, name, mode)
                        leaves = [
                            node
                            for node in _plan_nodes(node=plan)
                            if str(node.get("Relation Name", "")).startswith("chunks_")
                            and node.get("Actual Loops", 0)
                        ]
                        assert leaves, (kind, name, mode)
                        work[kind, name, mode] = (
                            sum(leaf["Actual Loops"] for leaf in leaves),
                            sum(
                                leaf["Actual Rows"] * leaf["Actual Loops"]
                                for leaf in leaves
                            ),
                            sum(
                                leaf.get("Rows Removed by Filter", 0)
                                * leaf["Actual Loops"]
                                for leaf in leaves
                            ),
                        )
        return work

    initial = keyed_work()
    with engine.begin() as connection:
        _expand_chunks(connection, size=10_000)
        _expand_chunks(connection, size=20_000, claim_text="carried")
    sparse = keyed_work()
    with engine.begin() as connection:
        connection.exec_driver_sql("ANALYZE chunks")
    refreshed = keyed_work()
    for actual in (sparse, refreshed):
        for key, counters in actual.items():
            assert all(
                after <= before
                for after, before in zip(counters, initial[key], strict=True)
            ), (key, counters, initial[key])


def test_corrupt_round_trip_is_rejected_and_valid_walks_are_preserved(
    d163_corpus: tuple[Engine, Corpus],
) -> None:
    engine, corpus = d163_corpus
    with engine.connect() as connection:
        source = corpus.origin
        terminal = corpus.survivor
        ends = {"source": source, "terminal": terminal, "deployment": _DEPLOYMENT_ID}
        assert _survivor(connection, origin=source) == terminal
        assert _survivor(connection, origin=terminal) == terminal
        connection.execute(
            text(
                "UPDATE entities SET status='active', merged_into=NULL "
                "WHERE entity_id=:source"
            ),
            ends,
        )
        assert _survivor(connection, origin=source) == source
        connection.exec_driver_sql("SET LOCAL session_replication_role = replica")
        connection.execute(
            text(
                "UPDATE entities SET status='merged', merged_into=:terminal "
                "WHERE entity_id=:source"
            ),
            ends,
        )
        connection.execute(
            text(
                "UPDATE entities SET status='merged', merged_into=:source "
                "WHERE entity_id=:terminal"
            ),
            ends,
        )
        assert _survivor(connection, origin=source) is None
        intermediate = uuid4()
        other = uuid4()
        connection.execute(
            text(
                "UPDATE entities SET status='active', merged_into=NULL "
                "WHERE entity_id=:terminal"
            ),
            ends,
        )
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id, canonical_name, normalized_name, status, merged_into) "
                "VALUES (:middle, :other, 'Foreign', 'foreign', 'merged', :terminal)"
            ),
            {"middle": intermediate, "other": other, **ends},
        )
        connection.execute(
            text("UPDATE entities SET merged_into=:target WHERE entity_id=:source"),
            {"target": intermediate, **ends},
        )
        # The previous adapter checked only source and terminal deployment.
        from rememberstack.spine.migrations.versions.p9_45_0066_fact_counts_demand import (
            SURVIVOR_DEMAND_DDL,
        )

        old_walk = SURVIVOR_DEMAND_DDL.split(" AS\n", 1)[1].strip().removesuffix(";")
        assert (
            connection.execute(
                text(
                    f"SELECT survivor FROM ({old_walk}) old(entity_id, survivor) WHERE entity_id=:source"
                ),
                ends,
            ).scalar_one()
            == terminal
        )
        assert _survivor(connection, origin=source) is None
        candidate = "SELECT 'relation' AS fact_kind, CAST(:fact AS uuid) AS fact_id"
        statement = (
            "WITH "
            + fact_eligibility_ctes(candidates_sql=candidate, presentation="keys")
            + " SELECT * FROM eligible_facts"
        )
        parameters = {
            "deployment_id": _DEPLOYMENT_ID,
            "fact": corpus.facts["relation", "mixed"],
        }
        assert connection.execute(text(statement), parameters).all() == []
        connection.execute(
            text("DELETE FROM entities WHERE entity_id=:middle"),
            {"middle": intermediate},
        )
        assert _survivor(connection, origin=source) is None
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == TARGET_HEAD
        )
