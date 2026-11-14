"""D153 independent old-head oracle, bounded coverage and endpoint work."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from datetime import UTC
from pathlib import Path
import random
from typing import Any
from typing import Final
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import make_url
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.model import DeploymentBootstrapInput
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.query_space import VIEW_CONTRACTS
from rememberstack.spine.retrieval_eligibility import candidate_mention_coverage_ctes
from rememberstack.spine.retrieval_eligibility import entity_mentions_ctes
from rememberstack.spine.retrieval_eligibility import fact_eligibility_ctes
from rememberstack.spine.settings import load_database_settings
from tests.spine.test_query_space_batch_a import _Corpus
from tests.spine.test_query_space_batch_a import _DEPLOYMENT_ID

_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class _Pair:
    """Identical data with independently migrated pre-change and current views."""

    reference: Engine
    target: Engine
    corpus: _Corpus


def _config(*, url: str) -> Config:
    """Bind migrations to the owned database, with no inherited default URL."""
    result = Config(str(_ROOT / "alembic.ini"))
    result.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return result


def _independent_pair() -> Iterator[_Pair]:
    """Clone an actual p9_46_0067 database, then migrate only its clone to head."""
    try:
        base = make_url(load_database_settings().sqlalchemy_url())
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL required for independent D153 oracle")
    suffix = uuid4().hex[:8]
    reference_name = f"d153_old_{suffix}"
    target_name = f"d153_new_{suffix}"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    reference_url = base.set(database=reference_name)
    target_url = base.set(database=target_name)
    reference = create_engine(reference_url)
    target = create_engine(target_url)
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{reference_name}"')
        command.upgrade(
            config=_config(url=reference_url.render_as_string(hide_password=False)),
            revision="p9_46_0067",
        )
        DeploymentBootstrapper(engine=reference).bootstrap_deployment(
            deployment_input=DeploymentBootstrapInput(
                deployment_id=_DEPLOYMENT_ID,
                slug="d153-oracle",
                name="Independent D153 oracle",
                default_language="en",
                raw_bucket="mem://raw",
                artifacts_bucket="mem://artifacts",
                corpusfs_bucket="mem://corpusfs",
            )
        )
        corpus = _Corpus(engine=reference)
        reference.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(
                f'CREATE DATABASE "{target_name}" TEMPLATE "{reference_name}"'
            )
        # A physical clone retains its original database-bound login name.
        # Install the owned target login explicitly; no runtime migration
        # tolerates an incomplete role/bootstrap contract.
        with admin.connect() as connection:
            connection.exec_driver_sql(
                f"CREATE ROLE rememberstack_query_{target_name} NOLOGIN"
            )
        command.upgrade(
            config=_config(url=target_url.render_as_string(hide_password=False)),
            revision="head",
        )
        yield _Pair(reference=reference, target=target, corpus=corpus)
    finally:
        reference.dispose()
        target.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(
                f'DROP DATABASE IF EXISTS "{target_name}" WITH (FORCE)'
            )
            connection.exec_driver_sql(
                f'DROP DATABASE IF EXISTS "{reference_name}" WITH (FORCE)'
            )
            for database in (reference_name, target_name):
                for prefix in ("rememberstack_query_", "rememberstack_graph_"):
                    connection.exec_driver_sql(
                        f"DROP ROLE IF EXISTS {prefix}{database}"
                    )
        admin.dispose()


@pytest.fixture(scope="module")
def pair() -> Iterator[_Pair]:
    """Share independent oracle data across transactionally isolated semantic tests."""
    yield from _independent_pair()


@pytest.fixture
def bridge_pair() -> Iterator[_Pair]:
    """Give each physical scale stratum a fresh index without rolled-back bloat."""
    yield from _independent_pair()


#: Public relations added after this proof's old-head oracle. D156's tag views
#: read only the tag tables, which no D153 eligibility helper touches, so the
#: oracle has no counterpart to compare them with.
_AFTER_ORACLE: Final = frozenset({"tags_current", "document_tags_live"})


def _snapshot(*, connection: Connection) -> dict[str, list[str]]:
    """Read every public row/column, without limits or a truncated denominator."""
    result: dict[str, list[str]] = {}
    for contract in VIEW_CONTRACTS:
        if contract.name in _AFTER_ORACLE:
            continue
        # Current views emit their read's statement timestamp. Independently
        # assert that clock, then normalize only that invocation-specific field.
        rows = (
            connection.execute(
                text(
                    f"SELECT (to_jsonb(row)-'evaluated_at')::text AS value,"
                    " (NOT (to_jsonb(row) ? 'evaluated_at') OR"
                    " (to_jsonb(row)->>'evaluated_at')::timestamptz=statement_timestamp()) AS clock_matches"
                    f" FROM memory_v1.{contract.name} row ORDER BY (to_jsonb(row)-'evaluated_at')::text"
                )
            )
            .mappings()
            .all()
        )
        assert all(row["clock_matches"] for row in rows)
        result[contract.name] = [str(row["value"]) for row in rows]
    return result


def _assert_coverage(*, old: Connection, new: Connection, entities: list[UUID]) -> None:
    """Both constrained scope directions equal the independent public filter."""
    parameters: dict[str, Any] = {
        "deployment_id": _DEPLOYMENT_ID,
        "entity_ids": entities,
    }
    for grain, table in (("claim", "claims"), ("chunk", "chunks")):
        column = grain + "_id"
        parameter = grain + "_ids"
        parameters[parameter] = list(
            new.execute(text(f"SELECT {column} FROM {table}")).scalars()
        )
        expected = list(
            old.execute(
                text(
                    f"SELECT {column}, count(DISTINCT resolved_entity_id)::integer"
                    " FROM memory_v1.mentions_live WHERE deployment_id=:deployment_id"
                    " AND resolved_entity_id=ANY(CAST(:entity_ids AS uuid[]))"
                    f" AND {column} IS NOT NULL GROUP BY {column} ORDER BY {column}"
                ),
                parameters,
            )
        )
        forward = candidate_mention_coverage_ctes(
            grain="claim" if grain == "claim" else "chunk",
            candidate_parameter="claim_ids" if grain == "claim" else "chunk_ids",
        )
        inverse = entity_mentions_ctes(grain="claim" if grain == "claim" else "chunk")
        for body in (forward, inverse):
            actual = list(
                new.execute(
                    text(
                        "WITH RECURSIVE "
                        + body
                        + " SELECT item_id,coverage FROM scope_coverage ORDER BY item_id"
                    ),
                    parameters,
                )
            )
            assert actual == expected


def _assert_facts(*, old: Connection, new: Connection) -> None:
    """Private bounded composition retains all public fields/counts and rejects orphans."""
    candidates = (
        "SELECT 'relation'::text AS fact_kind,relation_id AS fact_id FROM relations"
        " WHERE deployment_id=:deployment_id UNION ALL"
        " SELECT 'observation',observation_id FROM observations WHERE deployment_id=:deployment_id"
    )
    actual = list(
        new.execute(
            text(
                "WITH "
                + fact_eligibility_ctes(candidates_sql=candidates)
                + " SELECT to_jsonb(f)::text FROM eligible_facts f ORDER BY to_jsonb(f)::text"
            ),
            {"deployment_id": _DEPLOYMENT_ID},
        ).scalars()
    )
    expected = list(
        old.execute(
            text(
                "SELECT to_jsonb(f)::text FROM memory_v1.facts_visible_history f ORDER BY to_jsonb(f)::text"
            )
        ).scalars()
    )
    assert actual == expected


def test_complete_old_head_parity_across_visibility_mutations(pair: _Pair) -> None:
    """Actual old views independently qualify deletion, redirects and current coordinates."""
    corpus = pair.corpus
    instant = datetime(2025, 10, 1, tzinfo=UTC)
    changes: list[tuple[str, dict[str, Any]]] = [("SELECT 1", {})]
    changes.extend(
        (
            "UPDATE documents SET deleted_at=:at WHERE doc_id=:id",
            {"at": instant, "id": identifier},
        )
        for identifier in corpus.doc.values()
    )
    changes.extend(
        (
            "UPDATE document_versions SET deleted_at=:at WHERE version_id=:id",
            {"at": instant, "id": identifier},
        )
        for identifier in corpus.version.values()
    )
    changes.extend(
        (
            "UPDATE entities SET status='retired',merged_into=NULL WHERE entity_id=:id",
            {"id": identifier},
        )
        for identifier in corpus.entity.values()
    )
    changes.append(("UPDATE entities SET merged_into=NULL,status='active'", {}))
    rng = random.Random(153)
    entity_ids = list(corpus.entity.values())
    for _ in range(12):
        changes.append(
            (
                "UPDATE entities SET status='merged',merged_into=:target WHERE entity_id=:id",
                {"target": rng.choice(entity_ids), "id": rng.choice(entity_ids)},
            )
        )
    for statement, parameters in changes:
        with pair.reference.connect() as old, pair.target.connect() as new:
            old.execute(text(statement), parameters)
            new.execute(text(statement), parameters)
            assert _snapshot(connection=old) == _snapshot(connection=new), (
                statement,
                parameters,
            )
            _assert_coverage(
                old=old,
                new=new,
                entities=rng.sample(entity_ids, rng.randint(1, len(entity_ids))),
            )
            _assert_facts(old=old, new=new)
            # Both transactions roll back, so every state begins with identical data.


def test_private_current_definitions_match_actual_widened_old_source(
    pair: _Pair,
) -> None:
    """The manifest hashes the helper actually installed after D140, not its ancestor."""
    with pair.reference.connect() as old, pair.target.connect() as new:
        for helper in ("v_memory_fact_claim_live", "v_memory_claim_carried_periodised"):
            sql = text("SELECT pg_get_viewdef(CAST(:name AS regclass),true)")
            assert (
                old.execute(sql, {"name": helper}).scalar_one()
                == new.execute(sql, {"name": helper}).scalar_one()
            )
        # The new helper is owner-only, absent from public reachability.
        assert (
            new.execute(
                text(
                    "SELECT count(*) FROM information_schema.role_table_grants"
                    " WHERE table_schema='public' AND table_name='v_memory_fact_source_qualified'"
                    " AND grantee <> 'rememberstack_view_owner'"
                )
            ).scalar_one()
            == 0
        )


def test_scoped_context_statements_execute_with_canonical_coverage(pair: _Pair) -> None:
    """Execute actual hydration SQL, including the periodised/current scope builder."""
    from rememberstack.core.text_scope import TextScope
    from rememberstack.surfaces.query_engine import _CONFIRM_CHUNKS_SCOPED
    from rememberstack.surfaces.query_engine import _CONFIRM_CLAIMS_CURRENT_SCOPED
    from rememberstack.surfaces.query_engine import _confirm_claims_in_scope_statement

    parameters: dict[str, Any] = {
        "deployment_id": _DEPLOYMENT_ID,
        "entity_ids": list(pair.corpus.entity.values()),
        "claim_ids": list(pair.corpus.claim.values()),
        "chunk_ids": list(pair.corpus.chunk.values()),
        **TextScope.of(time=None, evaluated_at=datetime.now(UTC)).parameters(),
    }
    with pair.target.connect() as connection:
        for statement in (
            _CONFIRM_CLAIMS_CURRENT_SCOPED,
            _CONFIRM_CHUNKS_SCOPED,
            _confirm_claims_in_scope_statement(current_only=True, entity_scoped=True),
            _confirm_claims_in_scope_statement(current_only=False, entity_scoped=True),
        ):
            rows = connection.execute(statement, parameters).mappings().all()
            assert rows
            assert all(row["coverage"] > 0 for row in rows)


def _plan_nodes(*, node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Walk executed plan work without requiring any particular planner node type."""
    yield node
    for child in node.get("Plans", []):
        yield from _plan_nodes(node=child)


def _clone_fact_scope(*, connection: Connection, fact_id: UUID) -> None:
    """Add a hundred supported facts sharing endpoints and inherited source counts."""
    columns = list(
        connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema='public' AND table_name='relations' AND is_generated='NEVER' ORDER BY ordinal_position"
            )
        ).scalars()
    )
    names = ",".join('"' + name + '"' for name in columns)
    selection = ",".join('copy."' + name + '"' for name in columns)
    connection.execute(
        text(
            f"INSERT INTO relations ({names}) SELECT {selection} FROM relations original"
            " CROSS JOIN generate_series(1,100) AS series(n) CROSS JOIN LATERAL"
            " jsonb_populate_record(NULL::relations,to_jsonb(original) || jsonb_build_object("
            " 'relation_id',md5('d153-fact-' || n)::uuid,"
            " 'contradiction_group','cd153000-0000-0000-0000-000000000001'::uuid)) copy"
            " WHERE original.relation_id=:fact_id"
        ),
        {"fact_id": fact_id},
    )
    columns = list(
        connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public'"
                " AND table_name='relation_evidence' AND is_generated='NEVER' ORDER BY ordinal_position"
            )
        ).scalars()
    )
    names = ",".join('"' + name + '"' for name in columns)
    selection = ",".join('copy."' + name + '"' for name in columns)
    connection.execute(
        text(
            f"INSERT INTO relation_evidence ({names}) SELECT {selection} FROM relation_evidence original"
            " CROSS JOIN generate_series(1,100) AS series(n) CROSS JOIN LATERAL"
            " jsonb_populate_record(NULL::relation_evidence,to_jsonb(original) || jsonb_build_object("
            " 'relation_id',md5('d153-fact-' || n)::uuid)) copy WHERE original.relation_id=:fact_id"
        ),
        {"fact_id": fact_id},
    )


def test_fact_endpoint_work_is_shared_and_unrequested_counts_stay_prunable(
    pair: _Pair,
) -> None:
    """Both ranking arms share endpoint proof even when hundreds of facts reuse it."""
    with pair.reference.connect() as old, pair.target.connect() as new:
        for connection in (old, new):
            _clone_fact_scope(
                connection=connection, fact_id=pair.corpus.fact["current"]
            )
        _assert_facts(old=old, new=new)
        candidates = (
            "SELECT 'relation'::text AS fact_kind,relation_id AS fact_id FROM relations"
            " WHERE deployment_id=:deployment_id UNION ALL SELECT 'observation',observation_id"
            " FROM observations WHERE deployment_id=:deployment_id"
        )
        sql = (
            "WITH "
            + fact_eligibility_ctes(candidates_sql=candidates)
            + (
                " SELECT fact_id FROM eligible_facts WHERE fact_kind='relation' UNION ALL"
                " SELECT fact_id FROM eligible_facts WHERE fact_kind='observation'"
            )
        )
        new.exec_driver_sql("SET LOCAL jit=off")
        plan = new.execute(
            text("EXPLAIN(ANALYZE,BUFFERS,FORMAT JSON) " + sql),
            {"deployment_id": _DEPLOYMENT_ID},
        ).scalar_one()[0]["Plan"]
        nodes = list(_plan_nodes(node=plan))
        # Alias e is the entities_current identity source, distinct from the
        # cheap survivor adapter. Its work is per distinct endpoint, not fact.
        probes = [
            node
            for node in nodes
            if node.get("Alias") == "e" and node.get("Relation Name") == "entities"
        ]
        assert probes
        assert sum(node["Actual Loops"] for node in probes) <= len(pair.corpus.entity)
        assert not any(node.get("Relation Name") == "review_queue" for node in nodes)
        assert plan["Actual Rows"] >= 100


def test_chunk_coverage_keeps_index_probes_across_120_monthly_partitions(
    pair: _Pair,
) -> None:
    """Partition fanout is accepted; unrelated chunks/mentions cannot multiply probes."""
    with pair.target.connect() as connection:
        # Empty old monthly ranges avoid moving any existing source row. DDL
        # and fixture expansion are rolled back with this test transaction.
        for table in ("chunks", "mentions", "resolution_decisions"):
            for number in range(120):
                year, month = 2000 + number // 12, 1 + number % 12
                next_year, next_month = (
                    (year + 1, 1) if month == 12 else (year, month + 1)
                )
                connection.exec_driver_sql(
                    f"CREATE TABLE {table}_d153_{year}_{month:02d} PARTITION OF {table}"
                    f" FOR VALUES FROM ('{year}-{month:02d}-01') TO ('{next_year}-{next_month:02d}-01')"
                )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM pg_inherits WHERE inhparent='mentions'::regclass"
                )
            ).scalar_one()
            >= 121
        )
        assert connection.execute(
            text(
                "SELECT bool_and(idx.indisvalid) FROM pg_inherits child JOIN pg_index idx"
                " ON idx.indexrelid=child.inhrelid WHERE child.inhparent='ix_mentions_deployment_chunk'::regclass"
            )
        ).scalar_one()
        # One long source with many unrelated chunks: copy complete stored
        # rows with new identities, retaining all real coordinate columns.
        connection.execute(
            text(
                "INSERT INTO chunks SELECT copy.* FROM chunks original CROSS JOIN generate_series(1,10000) series(n)"
                " CROSS JOIN LATERAL jsonb_populate_record(NULL::chunks,to_jsonb(original) || jsonb_build_object("
                " 'chunk_id',md5('d153-chunk-'||n)::uuid,'ordinal',n+10000)) copy WHERE original.chunk_id=:chunk"
            ),
            {"chunk": pair.corpus.chunk["primary.v2"]},
        )
        connection.execute(
            text(
                "INSERT INTO mentions SELECT copy.* FROM mentions original CROSS JOIN generate_series(1,10000) series(n)"
                " CROSS JOIN LATERAL jsonb_populate_record(NULL::mentions,to_jsonb(original) || jsonb_build_object("
                " 'mention_id',md5('d153-mention-'||n)::uuid,'chunk_id',md5('d153-chunk-'||n)::uuid)) copy"
                " WHERE original.mention_id=:mention"
            ),
            {"mention": next(iter(pair.corpus.mention.values()))},
        )
        connection.exec_driver_sql("ANALYZE mentions")
        connection.exec_driver_sql("ANALYZE chunks")
        connection.exec_driver_sql("SET LOCAL jit=off")
        parameters = {
            "deployment_id": _DEPLOYMENT_ID,
            "entity_ids": list(pair.corpus.entity.values()),
            "chunk_ids": [pair.corpus.chunk["primary.v2"]],
        }
        sql = (
            "WITH RECURSIVE "
            + candidate_mention_coverage_ctes(
                grain="chunk", candidate_parameter="chunk_ids"
            )
            + " SELECT * FROM scope_coverage"
        )
        plan = connection.execute(
            text("EXPLAIN(ANALYZE,BUFFERS,FORMAT JSON) " + sql), parameters
        ).scalar_one()[0]["Plan"]
        assert plan["Actual Rows"] == 1
        nodes = list(_plan_nodes(node=plan))
        # Count tuple work, not empty partition probes or a particular plan
        # node. The 10k unrelated mentions must not be visited by the lookup.
        mentions = [
            node
            for node in nodes
            if str(node.get("Relation Name", "")).startswith("mentions")
        ]
        assert mentions
        assert (
            sum(
                node.get("Actual Rows", 0) * node.get("Actual Loops", 0)
                for node in mentions
            )
            < 100
        )


def test_native_migration_serializes_autocommit_and_rebuilds_interrupted_own_leaf(
    pair: _Pair, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancel a real concurrent leaf build, then run two native migration processes."""
    from concurrent.futures import ThreadPoolExecutor
    import subprocess
    import sys
    from threading import Event
    from time import monotonic
    from time import sleep

    from sqlalchemy.exc import DBAPIError

    url = pair.target.url.render_as_string(hide_password=False)
    pair.target.dispose()
    command.downgrade(config=_config(url=url), revision="p9_46_0067")
    for keys in ("chunk_id,deployment_id", "deployment_id,chunk_id DESC"):
        with pair.target.begin() as connection:
            connection.exec_driver_sql(
                f"CREATE INDEX ix_mentions_deployment_chunk ON ONLY mentions({keys})"
            )
        with pytest.raises(RuntimeError, match="collides"):
            command.upgrade(config=_config(url=url), revision="head")
        with pair.target.begin() as connection:
            connection.exec_driver_sql("DROP INDEX ix_mentions_deployment_chunk")
    with pair.target.begin() as connection:
        leaf_oid, leaf_name, mention_id = connection.execute(
            text(
                "SELECT m.tableoid::oid,c.relname,m.mention_id FROM mentions m"
                " JOIN pg_class c ON c.oid=m.tableoid LIMIT 1"
            )
        ).one()
    own_index = f"ix_mentions_dc_{leaf_oid}"
    ready = Event()
    backend: list[int] = []

    def interrupted_build() -> str:
        """Wait behind a writer, then leave the actual invalid catalog index."""
        with pair.target.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            backend.append(
                int(connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one())
            )
            ready.set()
            try:
                connection.exec_driver_sql(
                    f'CREATE INDEX CONCURRENTLY {own_index} ON "{leaf_name}"(deployment_id,chunk_id)'
                )
            except DBAPIError as error:
                return str(getattr(error.orig, "sqlstate", None))
        return "unexpected_success"

    with pair.target.connect() as writer, ThreadPoolExecutor(max_workers=1) as executor:
        writer.execute(
            text(
                f'UPDATE "{leaf_name}" SET surface_form=surface_form WHERE mention_id=:id'
            ),
            {"id": mention_id},
        )
        future = executor.submit(interrupted_build)
        assert ready.wait(timeout=5)
        try:
            end = monotonic() + 10
            while True:
                with pair.target.connect() as observer:
                    invalid = observer.execute(
                        text(
                            "SELECT NOT indisvalid FROM pg_index WHERE indexrelid=to_regclass(:name)"
                        ),
                        {"name": own_index},
                    ).scalar_one_or_none()
                    if invalid:
                        assert observer.execute(
                            text("SELECT pg_cancel_backend(:pid)"), {"pid": backend[0]}
                        ).scalar_one()
                        break
                assert monotonic() < end, (
                    "concurrent build never reached its native invalid-index state"
                )
                sleep(0.02)
            assert future.result(timeout=10) == "57014"
        finally:
            writer.rollback()
    command.upgrade(config=_config(url=url), revision="head")
    with pair.target.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT indisvalid FROM pg_index WHERE indexrelid='ix_mentions_deployment_chunk'::regclass"
        ).scalar_one()
        assert connection.execute(
            text("SELECT indisvalid FROM pg_index WHERE indexrelid=to_regclass(:name)"),
            {"name": own_index},
        ).scalar_one()
    pair.target.dispose()
    monkeypatch.setenv("REMEMBERSTACK_DATABASE_URL", url)
    processes: list[subprocess.Popen[str]] = []
    with pair.target.connect() as gate:
        gate.exec_driver_sql("SELECT pg_advisory_lock(1919251316,153)")
        gate.commit()
        try:
            for _ in range(2):
                processes.append(
                    subprocess.Popen(
                        [
                            sys.executable,
                            "-c",
                            "from alembic import command; from alembic.config import Config; command.upgrade(Config('alembic.ini'),'head')",
                        ],
                        cwd=_ROOT,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                    )
                )
            end = monotonic() + 15
            while True:
                with pair.target.connect() as observer:
                    waiting = observer.execute(
                        text(
                            "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()"
                            " AND query LIKE '%SELECT pg_advisory_lock(1919251316, 153)%' AND wait_event_type='Lock'"
                        )
                    ).scalar_one()
                if waiting == 2:
                    break
                assert monotonic() < end, (
                    "both native upgraders did not wait before their revision read"
                )
                sleep(0.02)
        finally:
            gate.exec_driver_sql("SELECT pg_advisory_unlock(1919251316,153)")
            gate.commit()
            for process in processes:
                output, _ = process.communicate(timeout=30)
                assert process.returncode == 0, output[-1500:]


def test_equality_entity_filter_is_candidate_bound_and_preserves_public_membership(
    pair: _Pair,
) -> None:
    """A fixed entity equality has one visibility gate and only candidate mention work."""
    from rememberstack.spine.retrieval_eligibility import claim_entity_filter

    with pair.reference.connect() as old, pair.target.connect() as new:
        for identifier in pair.corpus.entity.values():
            parameters = {
                "deployment_id": _DEPLOYMENT_ID,
                "filter_entity_id": identifier,
            }
            actual = list(
                new.execute(
                    text(
                        "SELECT indexed.claim_id FROM claims indexed WHERE indexed.deployment_id=:deployment_id AND "
                        + claim_entity_filter()
                        + " ORDER BY indexed.claim_id"
                    ),
                    parameters,
                ).scalars()
            )
            expected = list(
                old.execute(
                    text(
                        "SELECT DISTINCT claim_id FROM memory_v1.mentions_live WHERE deployment_id=:deployment_id"
                        " AND resolved_entity_id=:filter_entity_id AND claim_id IS NOT NULL ORDER BY claim_id"
                    ),
                    parameters,
                ).scalars()
            )
            assert actual == expected
        claim = pair.corpus.claim["a"]
        predicate = claim_entity_filter()
        plan = new.execute(
            text(
                "EXPLAIN(ANALYZE,FORMAT JSON) SELECT indexed.claim_id FROM claims indexed"
                " WHERE indexed.claim_id=:claim_id AND " + predicate
            ),
            {
                "deployment_id": _DEPLOYMENT_ID,
                "filter_entity_id": pair.corpus.entity["alice"],
                "claim_id": claim,
            },
        ).scalar_one()[0]["Plan"]
        nodes = list(_plan_nodes(node=plan))
        entity_work = [
            n
            for n in nodes
            if n.get("Alias") == "e" and n.get("Relation Name") == "entities"
        ]
        assert sum(n["Actual Loops"] for n in entity_work) == 1


def test_duplicate_fact_nomination_keeps_original_multiplicity(pair: _Pair) -> None:
    """Endpoint sharing deduplicates proof keys, not the caller's nomination ranks."""
    from rememberstack.core.text_scope import TextScope
    from rememberstack.surfaces.query_engine import _CONFIRM_FACTS_CONTEXT_BY_KIND

    parameters: dict[str, Any] = {
        "deployment_id": _DEPLOYMENT_ID,
        "entity_ids": [],
        "ranking_entity_ids": [],
        "fact_ids": [pair.corpus.fact["current"]] * 2,
        "predicate": None,
        "time_mode": "current",
        "evaluated_at": datetime.now(UTC),
        "at": None,
        "from": None,
        "to": None,
        **TextScope.of(time=None, evaluated_at=datetime.now(UTC)).parameters(),
    }
    with pair.target.connect() as connection:
        rows = (
            connection.execute(_CONFIRM_FACTS_CONTEXT_BY_KIND["relation"], parameters)
            .mappings()
            .all()
        )
        assert len(rows) == 2
        assert [row["nomination_rank"] for row in rows] == [1, 2]


@pytest.mark.parametrize(
    "deployments,tombstones,distinct",
    [
        (1, False, False),
        (2, False, False),
        (16, False, False),
        (16, True, False),
        (1, False, True),
    ],
)
def test_document_bridge_work_ignores_unrelated_lineages(
    bridge_pair: _Pair,
    record_property: Any,
    deployments: int,
    tombstones: bool,
    distinct: bool,
) -> None:
    """Qualify actual migrated bridge work under skew, generic plans and ordinary keys."""
    import json

    from pydantic import Field
    from pydantic_settings import BaseSettings
    from pydantic_settings import SettingsConfigDict

    class Scale(BaseSettings):
        """Opt in to million-document qualification only on a disposable DB."""

        model_config = SettingsConfigDict(
            env_prefix="REMEMBERSTACK_D153_QUALIFICATION_"
        )
        documents: int = Field(default=1000, ge=1000, le=2000000)
        evidence_dir: Path | None = None

    pair = bridge_pair
    scale = Scale()
    count = min(scale.documents, 100000) if distinct else scale.documents
    owners = [pair.corpus.entity["ghost"]] + [uuid4() for _ in range(deployments - 1)]
    deployment_ids = [_DEPLOYMENT_ID] + [uuid4() for _ in range(deployments - 1)]
    with pair.target.connect() as connection:
        for number in range(1, deployments):
            connection.execute(
                text(
                    "INSERT INTO deployments(deployment_id,slug,name,raw_bucket,artifacts_bucket,corpusfs_bucket) VALUES(:id,:slug,'D153 synthetic','mem://r','mem://a','mem://c')"
                ),
                {"id": deployment_ids[number], "slug": "d153-bridge-" + str(number)},
            )
            connection.execute(
                text(
                    "INSERT INTO entities(entity_id,deployment_id,canonical_name,normalized_name) VALUES(:entity,:deployment,'Bridge owner','bridge owner')"
                ),
                {"entity": owners[number], "deployment": deployment_ids[number]},
            )
        if distinct:
            connection.execute(
                text(
                    "INSERT INTO entities(entity_id,deployment_id,canonical_name,normalized_name) SELECT md5('d153-distinct-'||n)::uuid,:deployment,'Distinct bridge '||n,'distinct bridge '||n FROM generate_series(2,:count) series(n)"
                ),
                {"deployment": _DEPLOYMENT_ID, "count": count},
            )
        connection.execute(
            text(
                "INSERT INTO documents SELECT copy.* FROM documents original CROSS JOIN generate_series(1,:count) series(n)"
                " CROSS JOIN LATERAL jsonb_populate_record(NULL::documents,to_jsonb(original)||jsonb_build_object("
                " 'doc_id',md5('d153-bridge-'||n)::uuid,'source_ref','d153-bridge-'||n,"
                " 'current_version_id',NULL,'deployment_id',(CAST(:deployments AS uuid[]))[1+mod(n,:total)],"
                " 'document_entity_id',CASE WHEN CAST(:distinct AS boolean) AND n>1 THEN md5('d153-distinct-'||n)::uuid ELSE (CAST(:owners AS uuid[]))[1+mod(n,:total)] END,"
                " 'deleted_at',CASE WHEN CAST(:tombstones AS boolean) AND mod(n,:total)=0 THEN '2025-01-01'::timestamptz ELSE NULL END)) copy"
                " WHERE original.doc_id=:doc"
            ),
            {
                "count": count,
                "deployments": deployment_ids,
                "owners": owners,
                "total": deployments,
                "distinct": distinct,
                "tombstones": tombstones,
                "doc": pair.corpus.doc["primary"],
            },
        )
        connection.exec_driver_sql("ANALYZE documents")
        connection.exec_driver_sql("ANALYZE entities")
        connection.exec_driver_sql("SET LOCAL jit=off")
        assert connection.exec_driver_sql(
            "SELECT count(*)=1 FROM pg_indexes WHERE starts_with(indexname,'ix_documents_entity')"
        ).scalar_one()
        # Explicit PREPARE exercises both server plan modes. Membership sees
        # outer variables in either mode; both must retain the same work bound.
        connection.exec_driver_sql(
            "PREPARE d153_bridge(uuid,uuid[]) AS SELECT entity_id FROM memory_v1.entities_current WHERE deployment_id=$1 AND entity_id=ANY($2)"
        )
        report: dict[str, Any] = {
            "documents": count,
            "deployments": deployments,
            "tombstones": tombstones,
            "distinct": distinct,
            "source": "actual head migration, no injected DDL",
            "provider_calls": 0,
            "plans": {},
            "index_bytes": connection.exec_driver_sql(
                "SELECT pg_relation_size('ix_documents_entity')"
            ).scalar_one(),
        }
        try:
            ids = [pair.corpus.entity["alice"], pair.corpus.entity["ghost"]]
            execute = (
                "EXECUTE d153_bridge('"
                + str(_DEPLOYMENT_ID)
                + "'::uuid,ARRAY["
                + ",".join("'" + str(identifier) + "'::uuid" for identifier in ids)
                + "] )"
            )
            for mode in ("force_custom_plan", "force_generic_plan"):
                connection.exec_driver_sql("SET LOCAL plan_cache_mode=" + mode)
                plan = connection.exec_driver_sql(
                    "EXPLAIN(ANALYZE,BUFFERS,FORMAT JSON) " + execute
                ).scalar_one()[0]
                assert plan["Plan"]["Actual Rows"] == (1 if tombstones else 2)
                nodes = [
                    node
                    for node in _plan_nodes(node=plan["Plan"])
                    if node.get("Alias") == "document"
                ]
                assert nodes
                work = sum(
                    (node.get("Actual Rows", 0) + node.get("Rows Removed by Filter", 0))
                    * node.get("Actual Loops", 0)
                    for node in nodes
                )
                assert work <= (count // deployments + 10 if tombstones else 10)
                report["plans"][mode] = {"tuple_work": work, "plan": plan}
                record_property(mode + "_bridge_work", work)
                record_property(mode + "_execution_ms", plan["Execution Time"])
            # The leading entity key also serves older prefix callers and
            # tombstoned-row FK cleanup; qualify their complete required range.
            prefix = connection.execute(
                text(
                    "EXPLAIN(ANALYZE,BUFFERS,FORMAT JSON) SELECT doc_id FROM documents WHERE document_entity_id=:entity"
                ),
                {"entity": owners[0]},
            ).scalar_one()[0]
            report["prefix_plan"] = prefix
            assert prefix["Plan"]["Actual Rows"] == (
                1 if distinct else count // deployments
            )
            if scale.evidence_dir is not None:
                scale.evidence_dir.mkdir(parents=True, exist_ok=True)
                (
                    scale.evidence_dir
                    / f"bridge-{deployments}-{tombstones}-{distinct}.json"
                ).write_text(json.dumps(report, indent=2, default=str) + "\n")
            record_property("synthetic_bridge_documents", count)
            record_property("ordered_index_bytes", report["index_bytes"])
        finally:
            connection.exec_driver_sql("DEALLOCATE d153_bridge")


def test_statement_proofs_do_not_outlive_their_repeatable_read_snapshot(
    pair: _Pair,
) -> None:
    """A held reader keeps its source snapshot; a subsequent reader sees deletion."""
    candidates = "SELECT 'relation'::text AS fact_kind,relation_id AS fact_id FROM relations WHERE deployment_id=:deployment_id"
    sql = text(
        "WITH "
        + fact_eligibility_ctes(candidates_sql=candidates, presentation="keys")
        + " SELECT fact_id FROM eligible_facts ORDER BY fact_id"
    )
    parameters = {"deployment_id": _DEPLOYMENT_ID}
    doc = pair.corpus.doc["forgotten"]
    try:
        with pair.target.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as reader:
            reader.exec_driver_sql("SET TRANSACTION READ ONLY")
            before = list(reader.execute(sql, parameters).scalars())
            assert pair.corpus.fact["lonely"] in before
            with pair.target.begin() as writer:
                writer.execute(
                    text("UPDATE documents SET deleted_at=now() WHERE doc_id=:doc"),
                    {"doc": doc},
                )
            assert list(reader.execute(sql, parameters).scalars()) == before
            with pair.target.connect() as fresh:
                after = list(fresh.execute(sql, parameters).scalars())
            assert pair.corpus.fact["lonely"] not in after
    finally:
        with pair.target.begin() as writer:
            writer.execute(
                text("UPDATE documents SET deleted_at=NULL WHERE doc_id=:doc"),
                {"doc": doc},
            )


def test_cancelled_shared_fact_read_releases_connection_and_admission(
    pair: _Pair,
) -> None:
    """A blocked SQL read cancels and the same bounded pool admits its next request."""
    from time import monotonic

    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.pool import QueuePool

    from rememberstack.adapters.bounded_postgres_read import BoundedPostgresReadPool

    pool = BoundedPostgresReadPool(
        engine=pair.target, max_concurrency=1, pool_wait_seconds=1
    )
    sql = text(
        "WITH "
        + fact_eligibility_ctes(
            candidates_sql="SELECT 'relation'::text AS fact_kind,CAST(:fact AS uuid) AS fact_id",
            presentation="keys",
        )
        + " SELECT fact_id FROM eligible_facts"
    )
    parameters = {"deployment_id": _DEPLOYMENT_ID, "fact": pair.corpus.fact["current"]}
    with pair.target.connect() as writer:
        writer.exec_driver_sql("LOCK TABLE relations IN ACCESS EXCLUSIVE MODE")
        try:
            with pytest.raises(DBAPIError):
                with pool.snapshot(deadline=monotonic() + 0.5):
                    with pool.connect(deadline=monotonic() + 0.5) as reader:
                        reader.exec_driver_sql("SET LOCAL statement_timeout='100ms'")
                        reader.execute(sql, parameters).all()
        finally:
            writer.rollback()
    with pool.snapshot(deadline=monotonic() + 5):
        with pool.connect(deadline=monotonic() + 5) as reader:
            assert (
                reader.execute(sql, parameters).scalar_one()
                == pair.corpus.fact["current"]
            )
    assert isinstance(pair.target.pool, QueuePool)
    assert pair.target.pool.checkedout() == 0


@pytest.mark.parametrize("winner_superseded", (False, True))
def test_latest_decision_ties_are_global_across_reverse_members(
    pair: _Pair, winner_superseded: bool
) -> None:
    """A same-time higher-ID decision moving out of a member wins everywhere."""
    parameters = {
        "deployment": _DEPLOYMENT_ID,
        "mention": pair.corpus.mention["alice"],
        "member": pair.corpus.entity["alice_dup"],
        "other": pair.corpus.entity["acme"],
        "older": UUID("ffffffff-ffff-ffff-ffff-fffffffffffe"),
        "newer": UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
        "at": datetime(2025, 1, 1, tzinfo=UTC),
    }
    with pair.reference.connect() as old, pair.target.connect() as new:
        for connection in (old, new):
            connection.execute(
                text(
                    "INSERT INTO resolution_decisions(decision_id,deployment_id,mention_id,entity_id,method,confidence,is_new_entity,resolver_version,decided_at)"
                    " VALUES (:older,:deployment,:mention,:member,'T0',0.9,false,'d153-tie',:at),"
                    " (:newer,:deployment,:mention,:other,'T0',0.9,false,'d153-tie',:at)"
                ),
                parameters,
            )
            if winner_superseded:
                connection.execute(
                    text(
                        "UPDATE resolution_decisions SET superseded_by=:older WHERE decision_id=:newer"
                    ),
                    parameters,
                )
        assert _snapshot(connection=old) == _snapshot(connection=new)
        _assert_coverage(old=old, new=new, entities=list(pair.corpus.entity.values()))
        _assert_facts(old=old, new=new)


@pytest.mark.parametrize("corruption", ("nullable_claim", "wrong_document"))
def test_scope_preserves_nullable_claim_and_own_document_rules(
    pair: _Pair, corruption: str
) -> None:
    """A hidden claim can retain chunk coverage; a borrowed document cannot."""
    with pair.reference.connect() as old, pair.target.connect() as new:
        for connection in (old, new):
            if corruption == "nullable_claim":
                connection.execute(
                    text(
                        "UPDATE mentions SET claim_id=:claim WHERE mention_id=:mention"
                    ),
                    {
                        "claim": pair.corpus.claim["c"],
                        "mention": pair.corpus.mention["alice"],
                    },
                )
            else:
                connection.execute(
                    text("UPDATE mentions SET doc_id=:doc WHERE mention_id=:mention"),
                    {
                        "doc": pair.corpus.doc["second"],
                        "mention": pair.corpus.mention["alice"],
                    },
                )
        assert _snapshot(connection=old) == _snapshot(connection=new)
        _assert_coverage(old=old, new=new, entities=list(pair.corpus.entity.values()))
        _assert_facts(old=old, new=new)


@pytest.mark.parametrize("dangling", (False, True))
def test_legacy_invalid_redirects_cannot_borrow_foreign_provenance(
    pair: _Pair, dangling: bool
) -> None:
    """Privileged corrupt legacy rows still fail closed across deployment adapters."""
    foreign_deployment, foreign_entity, foreign_doc = uuid4(), uuid4(), uuid4()
    parameters = {
        "deployment": foreign_deployment,
        "entity": foreign_entity,
        "doc": foreign_doc,
        "at": datetime(2024, 1, 1, tzinfo=UTC),
        "root": pair.corpus.entity["alice"],
        "target": uuid4() if dangling else foreign_entity,
    }
    with pair.reference.connect() as old, pair.target.connect() as new:
        for connection in (old, new):
            connection.execute(
                text(
                    "INSERT INTO deployments(deployment_id,slug,name,raw_bucket,artifacts_bucket,corpusfs_bucket) VALUES(:deployment,'d153-foreign','Foreign','mem://r','mem://a','mem://c')"
                ),
                parameters,
            )
            connection.execute(
                text(
                    "INSERT INTO entities(entity_id,deployment_id,canonical_name,normalized_name,created_at,updated_at) VALUES(:entity,:deployment,'Foreign','foreign',:at,:at)"
                ),
                parameters,
            )
            connection.execute(
                text(
                    "INSERT INTO documents(doc_id,deployment_id,source_kind,source_ref,title,document_entity_id,first_seen_at,last_observed_at) VALUES(:doc,:deployment,'upload','d153-foreign','Foreign source',:entity,:at,:at)"
                ),
                parameters,
            )
            # Normal writes are constrained by the composite FK. Only this
            # disposable legacy-corruption fixture bypasses it, restoring the
            # normal trigger regime before exercising any authority query.
            connection.exec_driver_sql("SET LOCAL session_replication_role=replica")
            connection.execute(
                text(
                    "UPDATE entities SET status='merged',merged_into=:target WHERE entity_id=:root"
                ),
                parameters,
            )
            connection.exec_driver_sql("SET LOCAL session_replication_role=origin")
        assert _snapshot(connection=old) == _snapshot(connection=new)
        _assert_coverage(
            old=old, new=new, entities=[*pair.corpus.entity.values(), foreign_entity]
        )
        _assert_facts(old=old, new=new)


@pytest.mark.parametrize(
    "state",
    ["built", "dropped", "renamed", "wrong_staging", "wrong_serving", "leftover"],
)
def test_document_index_replacement_catalog_states(pair: _Pair, state: str) -> None:
    """Complete only native own-name states and refuse colliding objects intact."""
    from rememberstack.spine.migrations.versions.p9_47_0068_mention_chunk_lookup import (
        _replace_document_index,
    )

    with pair.target.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as connection:
        connection.exec_driver_sql("SELECT pg_advisory_lock(1919251316,153)")
        try:
            _replace_document_index(connection=connection, ordered=False)
            if state in ("built", "dropped"):
                connection.exec_driver_sql(
                    'CREATE INDEX ix_documents_entity_d153 ON documents(document_entity_id,deployment_id,((doc_id::text) COLLATE pg_catalog."C")) WHERE document_entity_id IS NOT NULL'
                )
                if state == "dropped":
                    connection.exec_driver_sql("DROP INDEX ix_documents_entity")
            if state in ("renamed", "leftover"):
                _replace_document_index(connection=connection, ordered=True)
            if state in ("wrong_staging", "leftover"):
                connection.exec_driver_sql(
                    "CREATE INDEX ix_documents_entity_d153 ON documents(document_entity_id) WHERE document_entity_id IS NOT NULL"
                )
            if state == "wrong_serving":
                connection.exec_driver_sql("DROP INDEX ix_documents_entity")
                connection.exec_driver_sql(
                    "CREATE INDEX ix_documents_entity ON documents(document_entity_id DESC) WHERE document_entity_id IS NOT NULL"
                )
            if state in ("wrong_staging", "wrong_serving", "leftover"):
                before = list(
                    connection.exec_driver_sql(
                        "SELECT indexname,indexdef FROM pg_indexes WHERE starts_with(indexname,'ix_documents_entity') ORDER BY indexname"
                    )
                )
                with pytest.raises(RuntimeError, match="collides|staging"):
                    _replace_document_index(connection=connection, ordered=True)
                assert (
                    list(
                        connection.exec_driver_sql(
                            "SELECT indexname,indexdef FROM pg_indexes WHERE starts_with(indexname,'ix_documents_entity') ORDER BY indexname"
                        )
                    )
                    == before
                )
            else:
                _replace_document_index(connection=connection, ordered=True)
                assert connection.exec_driver_sql(
                    "SELECT indisvalid AND indisready AND indislive FROM pg_index WHERE indexrelid='ix_documents_entity'::regclass"
                ).scalar_one()
                assert connection.exec_driver_sql(
                    "SELECT to_regclass('ix_documents_entity_d153') IS NULL"
                ).scalar_one()
        finally:
            connection.exec_driver_sql("DROP INDEX IF EXISTS ix_documents_entity_d153")
            if state == "wrong_serving":
                connection.exec_driver_sql("DROP INDEX ix_documents_entity")
                connection.exec_driver_sql(
                    "CREATE INDEX ix_documents_entity ON documents(document_entity_id) WHERE document_entity_id IS NOT NULL"
                )
            _replace_document_index(connection=connection, ordered=True)
            connection.exec_driver_sql("SELECT pg_advisory_unlock(1919251316,153)")


def test_document_index_replacement_resumes_cancelled_native_ddl(pair: _Pair) -> None:
    """Real cancelled CREATE and DROP phases complete without losing the serving path."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic
    from time import sleep

    from sqlalchemy.exc import DBAPIError

    from rememberstack.spine.migrations.versions.p9_47_0068_mention_chunk_lookup import (
        _replace_document_index,
    )

    ready = Event()
    backend: list[int] = []

    def execute_ddl(*, sql: str) -> str:
        """Leave PostgreSQL's own interrupted catalog state, never edit pg_index."""
        with pair.target.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            backend.append(
                int(connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one())
            )
            ready.set()
            try:
                connection.exec_driver_sql(sql)
            except DBAPIError as error:
                return str(getattr(error.orig, "sqlstate", None))
        return "unexpected_success"

    with pair.target.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as migration:
        migration.exec_driver_sql("SELECT pg_advisory_lock(1919251316,153)")
        try:
            for phase in ("create", "drop"):
                _replace_document_index(connection=migration, ordered=False)
                if phase == "drop":
                    migration.exec_driver_sql(
                        'CREATE INDEX CONCURRENTLY ix_documents_entity_d153 ON documents(document_entity_id,deployment_id,((doc_id::text) COLLATE pg_catalog."C")) WHERE document_entity_id IS NOT NULL'
                    )
                ready.clear()
                backend.clear()
                with (
                    pair.target.connect() as writer,
                    ThreadPoolExecutor(max_workers=1) as executor,
                ):
                    writer.execute(
                        text("UPDATE documents SET title=title WHERE doc_id=:doc"),
                        {"doc": pair.corpus.doc["primary"]},
                    )
                    sql = (
                        'CREATE INDEX CONCURRENTLY ix_documents_entity_d153 ON documents(document_entity_id,deployment_id,((doc_id::text) COLLATE pg_catalog."C")) WHERE document_entity_id IS NOT NULL'
                        if phase == "create"
                        else "DROP INDEX CONCURRENTLY ix_documents_entity"
                    )
                    future = executor.submit(execute_ddl, sql=sql)
                    assert ready.wait(timeout=5)
                    try:
                        end = monotonic() + 10
                        name = (
                            "ix_documents_entity_d153"
                            if phase == "create"
                            else "ix_documents_entity"
                        )
                        while True:
                            with pair.target.connect() as observer:
                                invalid = observer.execute(
                                    text(
                                        "SELECT NOT indisvalid FROM pg_index WHERE indexrelid=to_regclass(:name)"
                                    ),
                                    {"name": name},
                                ).scalar_one_or_none()
                                if invalid:
                                    # The index DDL is still active behind the
                                    # first writer. A different row writer must
                                    # finish before that writer or DDL ends.
                                    with pair.target.begin() as concurrent_writer:
                                        concurrent_writer.exec_driver_sql(
                                            "SET LOCAL lock_timeout='1s'"
                                        )
                                        concurrent_writer.exec_driver_sql(
                                            "SET LOCAL statement_timeout='2s'"
                                        )
                                        assert (
                                            concurrent_writer.execute(
                                                text(
                                                    "UPDATE documents SET title=title"
                                                    " WHERE doc_id=:doc RETURNING doc_id"
                                                ),
                                                {"doc": pair.corpus.doc["forgotten"]},
                                            ).scalar_one()
                                            == pair.corpus.doc["forgotten"]
                                        )
                                    assert observer.execute(
                                        text("SELECT pg_cancel_backend(:pid)"),
                                        {"pid": backend[0]},
                                    ).scalar_one()
                                    break
                            assert monotonic() < end, (
                                f"{phase} did not reach its invalid native state"
                            )
                            sleep(0.02)
                        assert future.result(timeout=10) == "57014"
                    finally:
                        writer.rollback()
                # Ordinary inserts still succeed while an interrupted build is
                # present; an interrupted drop has its valid staging replacement.
                with pair.target.begin() as writer:
                    writer.execute(
                        text("UPDATE documents SET title=title WHERE doc_id=:doc"),
                        {"doc": pair.corpus.doc["primary"]},
                    )
                _replace_document_index(connection=migration, ordered=True)
                assert migration.exec_driver_sql(
                    "SELECT indisvalid AND indisready AND indislive FROM pg_index WHERE indexrelid='ix_documents_entity'::regclass"
                ).scalar_one()
                assert migration.exec_driver_sql(
                    "SELECT to_regclass('ix_documents_entity_d153') IS NULL"
                ).scalar_one()
        finally:
            migration.exec_driver_sql("SELECT pg_advisory_unlock(1919251316,153)")


def test_populated_document_index_upgrade_allows_writers(
    bridge_pair: _Pair, record_property: Any
) -> None:
    """Build the real concurrent replacement over committed rows while writers run."""
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic
    from time import sleep

    from pydantic import Field
    from pydantic_settings import BaseSettings
    from pydantic_settings import SettingsConfigDict

    class Scale(BaseSettings):
        """Use the same owned synthetic corpus bound as the bridge read tests."""

        model_config = SettingsConfigDict(
            env_prefix="REMEMBERSTACK_D153_QUALIFICATION_"
        )
        documents: int = Field(default=1000, ge=1000, le=2000000)
        evidence_dir: Path | None = None

    import json

    scale = Scale()
    pair = bridge_pair
    url = pair.target.url.render_as_string(hide_password=False)
    pair.target.dispose()
    command.downgrade(config=_config(url=url), revision="p9_46_0067")
    with pair.target.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO documents SELECT copy.* FROM documents original CROSS JOIN generate_series(1,:count) series(n) CROSS JOIN LATERAL jsonb_populate_record(NULL::documents,to_jsonb(original)||jsonb_build_object('doc_id',md5('d153-populated-'||n)::uuid,'source_ref','d153-populated-'||n,'current_version_id',NULL,'document_entity_id',CAST(:entity AS uuid))) copy WHERE original.doc_id=:doc"
            ),
            {
                "count": scale.documents,
                "entity": pair.corpus.entity["ghost"],
                "doc": pair.corpus.doc["primary"],
            },
        )
        connection.exec_driver_sql("ANALYZE documents")
    with pair.target.connect() as observer:
        before_lsn = observer.exec_driver_sql(
            "SELECT pg_current_wal_insert_lsn()::text"
        ).scalar_one()
        old_bytes = observer.exec_driver_sql(
            "SELECT pg_relation_size('ix_documents_entity')"
        ).scalar_one()
    started = monotonic()
    with (
        pair.target.connect() as first_writer,
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        first_writer.execute(
            text("UPDATE documents SET title=title WHERE doc_id=:doc"),
            {"doc": pair.corpus.doc["primary"]},
        )
        future = executor.submit(
            command.upgrade, config=_config(url=url), revision="head"
        )
        try:
            end = monotonic() + 30
            while True:
                with pair.target.connect() as observer:
                    building = observer.exec_driver_sql(
                        "SELECT NOT indisvalid FROM pg_index WHERE indexrelid=to_regclass('ix_documents_entity_d153')"
                    ).scalar_one_or_none()
                if building:
                    break
                if future.done():
                    future.result()
                    pytest.fail("build did not encounter the held ordinary writer")
                assert monotonic() < end, (
                    "index migration never reached its own concurrent build"
                )
                sleep(0.02)
            with pair.target.begin() as second_writer:
                second_writer.exec_driver_sql("SET LOCAL lock_timeout='1s'")
                second_writer.exec_driver_sql("SET LOCAL statement_timeout='2s'")
                assert (
                    second_writer.execute(
                        text(
                            "UPDATE documents SET title=title WHERE doc_id=:doc RETURNING doc_id"
                        ),
                        {"doc": pair.corpus.doc["forgotten"]},
                    ).scalar_one()
                    == pair.corpus.doc["forgotten"]
                )
        finally:
            first_writer.rollback()
        future.result(timeout=120)
    report = {
        "documents": scale.documents,
        "source": "actual p9_46_0067 populated schema upgraded through native Alembic",
        "old_index_bytes": old_bytes,
        "migration_seconds": monotonic() - started,
        "concurrent_ordinary_writer_completed": True,
        "provider_calls": 0,
    }
    with pair.target.connect() as connection:
        report["new_index_bytes"] = connection.exec_driver_sql(
            "SELECT pg_relation_size('ix_documents_entity')"
        ).scalar_one()
        report["migration_wal_bytes"] = int(
            connection.execute(
                text(
                    "SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(),CAST(:lsn AS pg_lsn))"
                ),
                {"lsn": before_lsn},
            ).scalar_one()
        )
        assert connection.exec_driver_sql(
            "SELECT indisvalid AND indisready AND indislive FROM pg_index WHERE indexrelid='ix_documents_entity'::regclass"
        ).scalar_one()
        assert connection.exec_driver_sql(
            "SELECT to_regclass('ix_documents_entity_d153') IS NULL"
        ).scalar_one()
    for key, value in report.items():
        record_property(key, value)
    if scale.evidence_dir is not None:
        scale.evidence_dir.mkdir(parents=True, exist_ok=True)
        (scale.evidence_dir / "populated-document-upgrade.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
