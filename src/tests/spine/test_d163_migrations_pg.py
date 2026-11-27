"""D160/D161 handoff at the D163 revisions, with the actual reader roles."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timedelta
from datetime import UTC
import json
import logging
import logging.config
from pathlib import Path
import sys
from threading import Event
from time import monotonic
from typing import Any
from unittest.mock import MagicMock

from alembic import command
from benchmarks import d163_qualification as qualification
from benchmarks.d163_qualification import BASELINE_HEAD
from benchmarks.d163_qualification import TARGET_HEAD
from benchmarks.d163_staged_restore import preflight
from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.profiles.selfhost import _provision_query_role_password
from rememberstack.profiles.selfhost import _SelfHostSavedQueryReads
from rememberstack.spine.migrations.versions.p9_02_0023_query_space_roles import (
    query_role_name,
)
from rememberstack.spine.query_space import load_manifest
from rememberstack.surfaces.graph_queries import GraphQueries
from rememberstack.surfaces.http_api import build_api
from rememberstack.surfaces.query_sandbox.executor import QuerySandboxExecutor
from rememberstack.surfaces.query_sandbox.open_query import OpenQueryFacade
from rememberstack.surfaces.query_sandbox.saved_queries import OperatorFixture
from rememberstack.surfaces.query_sandbox.saved_queries import publish_surface_hash
from rememberstack.surfaces.query_sandbox.saved_queries import SavedQueryRegistry
from rememberstack.surfaces.query_sandbox.saved_queries import seed_shipped_examples
from tests.spine.d163_scale_fixture import _expand_facts
from tests.spine.test_d163_eligibility_pg import Corpus
from tests.spine.test_d163_eligibility_pg import migration_config
from tests.spine.test_d163_eligibility_pg import scratch_database
from tests.spine.test_d163_eligibility_pg import seed_corpus

_PREVIOUS_HASH = "8e526b54aca2bbac954a0b008156ff1ccb7bf498a9e1b384bd450ccd81b6301d"
_MEMORY_WALK = "p9_56_0077"
_GRAPH_PROJECTION = "p9_57_0078"
_SQL = "SELECT fact_id::text AS fact_id FROM facts_visible_history WHERE $1::boolean ORDER BY fact_id"
_ROUTE = "/query/saved/team/d163_survivors/run"


def _connect(engine: Engine, *, role: str | None = None) -> psycopg.Connection:
    url = engine.url.set(drivername="postgresql", username=role or engine.url.username)
    return psycopg.connect(
        url.render_as_string(hide_password=False), options="-c jit=off"
    )


def _executor(
    engine: Engine, corpus: Corpus, manifest_hash: str
) -> QuerySandboxExecutor:
    executor = QuerySandboxExecutor(
        deployment_id=corpus.case.deployment_id,
        connect=lambda: _connect(
            engine, role=f"rememberstack_query_{engine.url.database}"
        ),
    )
    executor._manifest_hash = manifest_hash
    return executor


def _client(engine: Engine, corpus: Corpus, manifest_hash: str) -> TestClient:
    deployment_id = corpus.case.deployment_id
    return TestClient(
        build_api(
            engine=MagicMock(),
            deployment_id=deployment_id,
            open_query=OpenQueryFacade(
                deployment_id=deployment_id,
                sql=_executor(engine, corpus, manifest_hash),
                saved_queries=_SelfHostSavedQueryReads(
                    engine=engine,
                    deployment_id=deployment_id,
                    manifest_hash=manifest_hash,
                ),
            ),
        )
    )


def _registry(
    connection: psycopg.Connection,
    corpus: Corpus,
    manifest: str,
    *,
    actor: str = "operator",
) -> SavedQueryRegistry:
    return SavedQueryRegistry(
        connection=connection,
        deployment_id=corpus.case.deployment_id,
        actor=actor,
        manifest_hash=manifest,
        can_activate=lambda actor: actor == "operator",
    )


def _fixtures() -> dict[str, OperatorFixture]:
    return {
        "positive": OperatorFixture(kind="positive", parameters=(True,)),
        "empty": OperatorFixture(kind="empty", parameters=(False,)),
        "tombstone": OperatorFixture(kind="tombstone", parameters=(False,)),
        "cap": OperatorFixture(kind="cap", parameters=(True,), max_rows=1),
    }


def _publish(
    engine: Engine, corpus: Corpus, manifest: str, *, still_visible: Any = None
) -> None:
    # Execute the release setup's registry transaction. External buckets and
    # unrelated setup work are already provisioned by the disposable fixture.
    with _connect(engine) as connection:
        publish_surface_hash(
            connection=connection,
            deployment_id=corpus.case.deployment_id,
            manifest_hash=manifest,
            actor="selfhost-setup",
        )
        seed_shipped_examples(
            connection=connection,
            deployment_id=corpus.case.deployment_id,
            manifest_hash=manifest,
        )
        _provision_query_role_password(connection=connection, engine=engine)
        if still_visible is not None:
            still_visible()


def _catalog(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql("""
            SELECT c.relname, pg_get_viewdef(c.oid), pg_get_userbyid(c.relowner),
                   c.relacl, obj_description(c.oid, 'pg_class'),
                   ARRAY(SELECT a.attacl::text FROM pg_attribute a
                         WHERE a.attrelid=c.oid AND a.attnum>0 ORDER BY a.attnum)
            FROM pg_class c
            WHERE c.oid IN (to_regclass('public.v_memory_entity_survivor'),
                            to_regclass('public.v_graph_survivor'),
                            to_regclass('public.v_memory_claim_history_by_id'))
        """)
        return {row[0]: tuple(row[1:]) for row in rows}


def test_d163_readers_reject_crossed_dsns_and_use_the_query_role_jit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PGOPTIONS", raising=False)
    with (
        scratch_database(BASELINE_HEAD) as baseline,
        scratch_database(TARGET_HEAD) as target,
    ):
        corpus = seed_corpus(target)
        query_dsns = []
        for engine in (baseline, target):
            assert engine.url.database is not None
            role = query_role_name(engine.url.database)
            with _connect(engine) as connection:
                _provision_query_role_password(connection=connection, engine=engine)
                connection.execute(
                    sql.SQL("ALTER ROLE {} SET jit = on").format(sql.Identifier(role))
                )
                connection.execute(
                    sql.SQL("ALTER ROLE {} SET jit_above_cost = 90000").format(
                        sql.Identifier(role)
                    )
                )
            query_dsns.append(
                engine.url.set(drivername="postgresql", username=role).render_as_string(
                    hide_password=False
                )
            )
        baseline_dsn = baseline.url.set(drivername="postgresql").render_as_string(
            hide_password=False
        )
        assert preflight(baseline_dsn, query_dsns[0])["visible_fact_rows"] == (0,)
        for invalid_dsn in (query_dsns[1], baseline_dsn):
            with pytest.raises(
                RuntimeError, match="verified database as its restricted query login"
            ):
                preflight(baseline_dsn, invalid_dsn)

        case, output = tmp_path / "case.json", tmp_path / "sandbox.json"
        case.write_text(corpus.case.model_dump_json())
        args = [
            "d163_qualification",
            "sandbox",
            "--database",
            target.url.render_as_string(hide_password=False),
            "--case",
            str(case),
            "--output",
            str(output),
            "--samples",
            "1",
            "--query-dsn",
        ]
        monkeypatch.setattr(sys, "argv", [*args, query_dsns[0]])
        with pytest.raises(
            RuntimeError, match="verified database as its restricted query login"
        ):
            qualification.main()
        assert not output.exists()

        monkeypatch.setattr(sys, "argv", [*args, query_dsns[1]])
        qualification.main()
        report = json.loads(output.read_text())
        assert report["sandbox_settings"]["jit"] == "on"
        assert report["sandbox_settings"]["jit_above_cost"] == "90000"
        assert report["sandbox"]["beam_predicates"]["termination"] == "completed"
        for view in qualification.BROAD_VIEWS:
            state = report["sandbox"][view]
            assert state["termination"] == "completed"
            assert state["limits"]["statement_timeout_ms"] == 5000
            plan = state["work"]["plan"]
            assert plan["Planning Time"] >= 0
            assert plan["Plan"]["Actual Rows"] > 0
            assert plan["Plan"]["Shared Hit Blocks"] > 0


def test_d163_compare_detects_public_view_value_and_multiplicity_changes() -> None:
    with scratch_database(TARGET_HEAD) as engine:
        corpus = seed_corpus(engine)
        with engine.begin() as connection:
            _expand_facts(connection, kind="observation", size=115)
        with _connect(engine) as connection:
            _provision_query_role_password(connection=connection, engine=engine)
        sandbox = _executor(engine, corpus, qualification.MANIFEST_BY_HEAD[TARGET_HEAD])
        case = corpus.case

        def snapshot(head: str) -> dict[str, Any]:
            record = qualification.capture(engine, case, timeout=20)
            sampler = qualification._SandboxSampler(sandbox)
            record["cuts"].update(
                qualification._broad_view_rows(
                    sandbox, evaluated_at=case.evaluated_at, timeout_ms=20_000
                )
            )
            return {
                **record,
                "schema_head": head,
                "surface_manifest_hash": qualification.MANIFEST_BY_HEAD[head],
                "case": case.model_dump(mode="json"),
                "sandbox_settings": {"jit": "off"},
                "sandbox": qualification._sandbox_queries(sampler, samples=0),
                "beam": qualification._beam_queries(sampler),
                "beam_ordered": qualification._beam_ordered_rows(
                    sandbox, evaluated_at=case.evaluated_at, timeout_ms=20_000
                ),
            }

        baseline = snapshot(BASELINE_HEAD)
        current = baseline["cuts"]["sandbox/facts_current"]
        assert current
        assert {
            datetime.fromisoformat(json.loads(row)["evaluated_at"]) for row in current
        } == {case.evaluated_at}
        early = qualification._broad_view_rows(
            sandbox, evaluated_at=datetime(2023, 1, 1, tzinfo=UTC), timeout_ms=20_000
        )
        assert early["sandbox/facts_current"] == []

        cut = "sandbox/evidence_lineage"
        original = baseline["cuts"][cut]
        state = baseline["sandbox"]["evidence_lineage"]
        assert state["last_rows"] == state["limits"]["row_cap"] < len(original)
        assert state["truncation"] == "row_cap"
        with engine.begin() as connection:
            definition = connection.exec_driver_sql(
                "SELECT pg_get_viewdef('memory_v1.evidence_lineage'::regclass)"
            ).scalar_one()
            changed = definition.replace(
                "lineage.source_handle,", "'changed'::text AS source_handle,"
            )
            assert changed != definition
            connection.exec_driver_sql(
                f"CREATE OR REPLACE VIEW memory_v1.evidence_lineage AS {changed}"
            )
        target = snapshot(TARGET_HEAD)
        with engine.connect() as connection:
            report = qualification.compare(connection, baseline, target)
            assert report["raw_batches_match"] and report["ordered_top_k_match"]
            assert all(report["beam_matches"].values())
            assert all(report["sandbox_completion_matches"].values())
            assert {
                name: (counts["missing"], counts["extra"])
                for name, counts in report["cuts"].items()
                if counts["missing"] or counts["extra"]
            } == {cut: (len(original), len(original))}

            target["cuts"][cut] = [*reversed(original), original[0]]
            assert qualification.compare(connection, baseline, target)["cuts"][cut] == {
                "baseline": len(original),
                "target": len(original) + 1,
                "missing": 0,
                "extra": 1,
            }


def test_beam_oracle_uses_the_case_clock_at_exclusive_validity_end() -> None:
    with scratch_database(BASELINE_HEAD) as engine:
        corpus = seed_corpus(engine)
        valid_until = datetime(2025, 1, 2, tzinfo=UTC)
        before_end = valid_until - timedelta(microseconds=1)
        with engine.begin() as connection:
            for table in ("relations", "observations"):
                connection.execute(
                    text(f"""
                        UPDATE {table} SET valid_until=:until, valid_precision='day'
                        WHERE valid_precision='open'
                    """),
                    {"until": valid_until},
                )
        with _connect(engine) as connection:
            _provision_query_role_password(connection=connection, engine=engine)

        ordered = []
        for head in (BASELINE_HEAD, TARGET_HEAD):
            if head == TARGET_HEAD:
                command.upgrade(config=migration_config(engine), revision=TARGET_HEAD)
            sandbox = _executor(engine, corpus, qualification.MANIFEST_BY_HEAD[head])
            before = qualification._beam_ordered_rows(
                sandbox, evaluated_at=before_end, timeout_ms=20_000
            )
            at = qualification._beam_ordered_rows(
                sandbox, evaluated_at=valid_until, timeout_ms=20_000
            )
            broad = qualification._broad_view_rows(
                sandbox, evaluated_at=before_end, timeout_ms=20_000
            )["sandbox/facts_current"]
            assert (
                sum(count for _, count in before["facts_predicates"]) == len(broad) > 0
            )
            assert (
                "relation",
                str(corpus.facts["relation", "multi_source"]),
                "works_for",
                "multi_source",
            ) in before["facts_sample"]
            assert at["facts_predicates"] == at["facts_sample"] == []
            live = qualification._beam_queries(qualification._SandboxSampler(sandbox))
            assert live["facts_predicates"]["rows"] == []
            assert live["facts_sample"]["error_code"] == "execution_error"
            ordered.append(before)
        assert ordered[0] == ordered[1]


def test_d163_upgrade_and_downgrade_with_previous_readers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with scratch_database(BASELINE_HEAD) as engine:
        corpus = seed_corpus(engine)
        _publish(engine, corpus, _PREVIOUS_HASH)
        version, query_id = _activate(engine, corpus)
        old_api = _client(engine, corpus, _PREVIOUS_HASH)
        new_hash = str(load_manifest()["surface_manifest_hash"])
        new_api = _client(engine, corpus, new_hash)
        graph = GraphQueries(engine=engine, deployment_id=corpus.case.deployment_id)
        request = {"version": version, "parameters": [True]}

        def read(client: TestClient, expected_hash: str) -> list[Any]:
            response = client.post(_ROUTE, json=request)
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["termination_reason"] == "completed", body
            assert body["surface_manifest_hash"] == expected_hash
            assert body["saved_query"]["query_id"] == str(query_id)
            assert body["saved_query"]["version"] == str(version)
            return body["rows"]

        expected_rows = read(old_api, _PREVIOUS_HASH)
        assert [str(corpus.facts["relation", "mixed"])] in expected_rows

        def graph_rows() -> tuple[str, ...]:
            result = graph.neighborhood(entity_id=corpus.survivor, hops=1)
            return tuple(sorted(str(node.entity_id) for node in result.nodes))

        expected_graph = graph_rows()
        assert corpus.case.entity_ids[1] in expected_graph
        baseline = _catalog(engine)
        commits: set[str] = set()

        def smoke() -> None:
            with engine.connect() as connection:
                revision = connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).scalar_one()
            assert read(old_api, _PREVIOUS_HASH) == expected_rows
            assert graph_rows() == expected_graph
            commits.add(revision)
            observed = _catalog(engine)
            for name, definition in baseline.items():
                # CREATE OR REPLACE preserves ownership and table and column ACLs.
                assert observed[name][1:3] == definition[1:3]
                assert observed[name][4] == definition[4]

        def migrate(*, up: bool) -> None:
            retry, resume = Event(), Event()
            logger = logging.getLogger("alembic.runtime.migration")
            start = monotonic()
            retry_times: list[float] = []
            original_config = logging.config.fileConfig

            class Observe(logging.Handler):
                def emit(self, record: logging.LogRecord) -> None:
                    message = record.getMessage()
                    if "40P01" in message:
                        raise AssertionError(message)
                    if "SQLSTATE 55P03" in message:
                        retry_times.append(monotonic() - start)
                        retry.set()
                        assert resume.wait(timeout=15)
                    if message.startswith(("Running upgrade", "Running downgrade")):
                        smoke()  # preceding revision has committed on the migration session

            observer = Observe()

            def configure(*args: Any, **kwargs: Any) -> None:
                original_config(*args, **kwargs)
                logger.addHandler(observer)

            try:
                with (
                    monkeypatch.context() as replacement,
                    ThreadPoolExecutor(max_workers=1) as executor,
                ):
                    replacement.setattr(logging.config, "fileConfig", configure)
                    with engine.connect() as reader:
                        reader.exec_driver_sql(
                            "SELECT entity_id FROM v_memory_entity_survivor LIMIT 1"
                        )
                        future = executor.submit(
                            command.upgrade if up else command.downgrade,
                            config=migration_config(engine),
                            revision="head" if up else BASELINE_HEAD,
                        )
                        try:
                            assert retry.wait(timeout=12), (
                                future.exception() if future.done() else "missing 55P03"
                            )
                            # The exclusive lock waiter closed; this read uses the normal
                            # sandbox 250 ms lock limit while the first reader still holds.
                            smoke()
                        finally:
                            reader.rollback()
                            resume.set()
                        future.result(timeout=15)
                assert len(retry_times) == 1 and 2.9 <= retry_times[0] < 12
                smoke()
            finally:
                logger.removeHandler(observer)

        migrate(up=True)
        assert commits == {BASELINE_HEAD, _MEMORY_WALK, _GRAPH_PROJECTION, TARGET_HEAD}
        incoming = _catalog(engine)
        assert "v_memory_entity_survivor" in incoming["v_graph_survivor"][0]
        assert (
            "Private deployment-scoped merge walk"
            in incoming["v_memory_entity_survivor"][3]
        )
        assert incoming["v_memory_claim_history_by_id"][1] == "rememberstack_view_owner"
        with _connect(
            engine, role=f"rememberstack_query_{engine.url.database}"
        ) as reader:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                reader.execute("SELECT * FROM public.v_memory_claim_history_by_id")
        commits.clear()
        migrate(up=False)
        assert commits == {BASELINE_HEAD, _MEMORY_WALK, _GRAPH_PROJECTION, TARGET_HEAD}
        assert _catalog(engine) == baseline

        command.upgrade(config=migration_config(engine), revision="head")
        _assert_cutover(
            engine, corpus, new_api, new_hash, _PREVIOUS_HASH, query_id, version
        )
        assert read(new_api, new_hash) == expected_rows
        # All D163 readers have finished before the reverse revisions.
        command.downgrade(config=migration_config(engine), revision=BASELINE_HEAD)
        assert _state(engine, corpus, query_id, version) == (new_hash, "active")
        _assert_cutover(
            engine, corpus, old_api, _PREVIOUS_HASH, new_hash, query_id, version
        )
        assert read(old_api, _PREVIOUS_HASH) == expected_rows


def _activate(engine: Engine, corpus: Corpus) -> tuple[int, Any]:
    with _connect(engine) as connection:
        author = _registry(connection, corpus, _PREVIOUS_HASH, actor="author")
        saved = author.draft(namespace="team", name="d163_survivors", sql=_SQL)
        report = author.validate_version(
            query_id=saved.query_id,
            version=saved.version,
            executor=_executor(engine, corpus, _PREVIOUS_HASH),
            fixtures=_fixtures(),
        )
        assert report.passed, report.diagnostics
        _registry(connection, corpus, _PREVIOUS_HASH).activate(
            query_id=saved.query_id, version=saved.version
        )
        return saved.version, saved.query_id


def _state(
    engine: Engine, corpus: Corpus, query_id: Any, version: int
) -> tuple[Any, ...]:
    with _connect(engine) as connection:
        row = connection.execute(
            b"SELECT r.surface_manifest_hash, v.status::text FROM saved_query_registry_state r "
            b"JOIN saved_query_versions v USING (deployment_id) "
            b"WHERE deployment_id=%s AND query_id=%s AND version=%s",
            (corpus.case.deployment_id, query_id, version),
        ).fetchone()
        assert row is not None
        return row


def _assert_cutover(
    engine: Engine,
    corpus: Corpus,
    api: TestClient,
    new: str,
    old: str,
    query_id: Any,
    version: int,
) -> None:
    def old_state() -> None:
        assert _state(engine, corpus, query_id, version) == (old, "active")

    _publish(engine, corpus, new, still_visible=old_state)
    assert _state(engine, corpus, query_id, version) == (new, "pending_revalidation")
    refused = api.post(_ROUTE, json={"version": version, "parameters": [True]})
    assert refused.json()["detail"]["code"] == "saved_query_revalidation_pending", (
        refused.text
    )
    with _connect(engine) as connection:
        status = _registry(connection, corpus, new).revalidate(
            query_id=query_id,
            version=version,
            started_against=new,
            executor=_executor(engine, corpus, new),
            fixtures=_fixtures(),
            minor_compatible=True,
        )
    assert status == "active"
    assert _state(engine, corpus, query_id, version) == (new, "active")
