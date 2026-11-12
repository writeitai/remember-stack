"""D153 independent old-head oracle, bounded coverage and endpoint work."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from datetime import UTC
from pathlib import Path
import random
from typing import Any
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


@pytest.fixture(scope="module")
def pair() -> Iterator[_Pair]:
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


def _snapshot(*, connection: Connection) -> dict[str, list[str]]:
    """Read every public row/column, without limits or a truncated denominator."""
    return {
        contract.name: list(
            connection.execute(
                text(
                    f"SELECT to_jsonb(row)::text FROM memory_v1.{contract.name} row ORDER BY to_jsonb(row)::text"
                )
            ).scalars()
        )
        for contract in VIEW_CONTRACTS
    }


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
        ("UPDATE entities SET status='retired' WHERE entity_id=:id", {"id": identifier})
        for identifier in corpus.entity.values()
    )
    changes.append(("UPDATE entities SET merged_into=NULL,status='active'", {}))
    rng = random.Random(153)
    entity_ids = list(corpus.entity.values())
    for _ in range(12):
        changes.append(
            (
                "UPDATE entities SET merged_into=:target WHERE entity_id=:id",
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
