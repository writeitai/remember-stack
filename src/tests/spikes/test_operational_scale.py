"""WP-7.2: reproducible provider-neutral scale and batching measurements.

CI uses a capability-sized fixed profile. The same ungated synthetic shape
scales through ``REMEMBERSTACK_OPERATIONAL_SCALE_*`` settings. Timings are recorded
observations only; structural correctness and bounded I/O counts are gates.
"""

from collections.abc import Callable
from collections.abc import Iterator
import math
from pathlib import Path
import time
from typing import TypeVar
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import Field
from pydantic import ValidationError
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
import pytest
from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.eval import OPERATIONAL_SCALE_VERSION
from rememberstack.eval import record_operational_scale_report
from rememberstack.model import CurrencyTransition
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import ObservationAssertion
from rememberstack.model import OperationalScaleMeasurement
from rememberstack.model import OperationalScaleReport
from rememberstack.model import P1ChunkText
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.catalog_contract import CatalogInventory
from rememberstack.spine.catalog_contract import EXPECTED_HASH_PARENTS
from rememberstack.spine.catalog_contract import EXPECTED_RANGE_PARENTS
from rememberstack.spine.catalog_contract import verify_schema
from rememberstack.spine.lifecycle import LifecycleCatalog
from rememberstack.spine.observation_adjudication import ObservationAdjudicator
from rememberstack.spine.observation_adjudication import ObservationSettings
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import QueryEngine
from rememberstack.surfaces.query_engine import INTERACTIVE_HYDRATION_BATCH_SIZE

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("72000000-0000-0000-0000-000000000001")
_DOC_ID = UUID("72000000-0000-0000-0000-000000000002")
_HUB_ENTITY_ID = UUID("72000000-0000-0000-0000-000000000003")
_FACT_SUBJECT_ID = UUID("72000000-0000-0000-0000-000000000004")
_BATCH_ENTITY_ID = UUID("72000000-0000-0000-0000-000000000005")
_VERSION_ID = UUID("72000000-0000-0000-0000-000000000006")
_REPRESENTATION_ID = UUID("72000000-0000-0000-0000-000000000007")
_CHUNK_ID = UUID("72000000-0000-0000-0000-000000000008")
_CONTENT_HASH = "operational-scale-profile"

ResultT = TypeVar("ResultT")


class _ScaleSettings(BaseSettings):
    """Portable fixed-profile sizes; larger local runs use the same test."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_OPERATIONAL_SCALE_")

    profile: str = "ci"
    hub_aliases: int = Field(default=2_000, ge=100)
    lineage_relations: int = Field(default=1_000, ge=100)
    lineage_observations: int = Field(default=1_000, ge=100)
    hydration_ids: int = Field(default=513, ge=257)
    entity_batch_assertions: int = Field(default=5, ge=2)
    injected_latency_ms: float = Field(default=1.0, ge=0.0, le=100.0)


_SETTINGS = _ScaleSettings()


class _NullSearchIndex:
    """The scale battery calls the real confirmation method directly."""

    def search_claims(self, **_: object) -> tuple[str, ...]:
        return ()

    def search_claims_lexical(self, **_: object) -> tuple[str, ...]:
        return ()

    def search_chunks(self, **_: object) -> tuple[str, ...]:
        return ()

    def search_chunks_lexical(self, **_: object) -> tuple[str, ...]:
        return ()

    def chunk_texts(self, **_: object) -> dict[str, P1ChunkText]:
        return {}

    def search_facts(self, **_: object) -> tuple[str, ...]:
        return ()


class _SqlProbe:
    """Count real-engine transactions/statements and inject portable latency."""

    def __init__(self, *, engine: Engine, latency_ms: float) -> None:
        self.engine = engine
        self.latency_ms = latency_ms
        self.statements: list[str] = []
        self.transactions = 0

    def __enter__(self) -> "_SqlProbe":
        event.listen(self.engine, "before_cursor_execute", self._before_execute)
        event.listen(self.engine, "begin", self._begin)
        return self

    def __exit__(self, *_: object) -> None:
        event.remove(self.engine, "before_cursor_execute", self._before_execute)
        event.remove(self.engine, "begin", self._begin)

    def _before_execute(
        self,
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        self.statements.append(statement)
        if self.latency_ms:
            time.sleep(self.latency_ms / 1_000.0)

    def _begin(self, _connection: object) -> None:
        self.transactions += 1


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head on the caller-provided isolated PostgreSQL DB."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for operational scale runs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def schema_inventory(database_engine: Engine) -> CatalogInventory:
    """Capture the migration-head contract before the profile seeds rows."""
    with database_engine.connect() as connection:
        return verify_schema(connection=connection)


@pytest.fixture(scope="module")
def seeded_profile(
    database_engine: Engine, schema_inventory: CatalogInventory
) -> dict[str, tuple[UUID, ...]]:
    """Seed one deterministic-shape, ungated full-extraction profile."""
    del schema_inventory  # fixture dependency guarantees head was verified first
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="operational-scale",
            name="WP-7.2 operational scale",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                " source_ref, title) VALUES (:doc_id, :deployment_id, 'scale',"
                " 'ungated-profile', 'Ungated scale profile')"
            ),
            {"doc_id": _DOC_ID, "deployment_id": _DEPLOYMENT_ID},
        )
        connection.execute(
            text(
                "INSERT INTO content_objects (deployment_id, content_hash, mime,"
                " raw_uri) VALUES (:deployment_id, :content_hash, 'text/plain',"
                " 'mem://operational-scale-profile')"
            ),
            {"deployment_id": _DEPLOYMENT_ID, "content_hash": _CONTENT_HASH},
        )
        connection.execute(
            text(
                "INSERT INTO document_versions (version_id, deployment_id, doc_id,"
                " content_hash, version_no, status) VALUES (:version_id,"
                " :deployment_id, :doc_id, :content_hash, 1, 'ready')"
            ),
            {
                "version_id": _VERSION_ID,
                "deployment_id": _DEPLOYMENT_ID,
                "doc_id": _DOC_ID,
                "content_hash": _CONTENT_HASH,
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_representations (representation_id,"
                " deployment_id, version_id, route, status) VALUES"
                " (:representation_id, :deployment_id, :version_id,"
                " 'passthrough', 'ready')"
            ),
            {
                "representation_id": _REPRESENTATION_ID,
                "deployment_id": _DEPLOYMENT_ID,
                "version_id": _VERSION_ID,
            },
        )
        connection.execute(
            text(
                "INSERT INTO chunks (chunk_id, deployment_id, doc_id, version_id,"
                " representation_id, ordinal, block_start, block_end,"
                " chunk_content_hash, extraction_input_hash, char_start, char_end)"
                " VALUES (:chunk_id, :deployment_id, :doc_id, :version_id,"
                " :representation_id, 0, 0, 0, :content_hash, :content_hash, 0, 8)"
            ),
            {
                "chunk_id": _CHUNK_ID,
                "deployment_id": _DEPLOYMENT_ID,
                "doc_id": _DOC_ID,
                "version_id": _VERSION_ID,
                "representation_id": _REPRESENTATION_ID,
                "content_hash": _CONTENT_HASH,
            },
        )
        connection.execute(
            text(
                "UPDATE document_versions SET current_representation_id ="
                " :representation_id WHERE deployment_id = :deployment_id"
                " AND version_id = :version_id"
            ),
            {
                "representation_id": _REPRESENTATION_ID,
                "deployment_id": _DEPLOYMENT_ID,
                "version_id": _VERSION_ID,
            },
        )
        connection.execute(
            text(
                "UPDATE documents SET current_version_id = :version_id"
                " WHERE deployment_id = :deployment_id AND doc_id = :doc_id"
            ),
            {
                "version_id": _VERSION_ID,
                "deployment_id": _DEPLOYMENT_ID,
                "doc_id": _DOC_ID,
            },
        )
        claim_ids = tuple(
            connection.execute(
                text(
                    "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                    " claim_text, source_span, char_start, char_end, anchor_ok,"
                    " window_membership_ok, extractor_version)"
                    " SELECT gen_random_uuid(), :deployment_id, :doc_id,"
                    " :chunk_id, 'ungated claim ' || n, 'source ' || n,"
                    " 0, 8, true, true, 'scale-extractor'"
                    " FROM generate_series(1, :rows) n RETURNING claim_id"
                ),
                {
                    "deployment_id": _DEPLOYMENT_ID,
                    "doc_id": _DOC_ID,
                    "chunk_id": _CHUNK_ID,
                    "rows": _SETTINGS.hydration_ids,
                },
            ).scalars()
        )
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id,"
                " canonical_name, normalized_name) VALUES"
                " (:hub, :deployment_id, 'Alexander Hub',"
                " 'alexander hub'),"
                " (:subject, :deployment_id, 'Fact Subject',"
                " 'fact subject'),"
                " (:batch, :deployment_id, 'Batch Subject',"
                " 'batch subject')"
            ),
            {
                "hub": _HUB_ENTITY_ID,
                "subject": _FACT_SUBJECT_ID,
                "batch": _BATCH_ENTITY_ID,
                "deployment_id": _DEPLOYMENT_ID,
            },
        )
        connection.execute(
            text(
                "INSERT INTO aliases (alias_id, deployment_id, entity_id,"
                " alias_text, normalized_lemma, provenance)"
                " SELECT gen_random_uuid(), :deployment_id, :entity_id,"
                " 'Alexander Hub ' || n, 'alexander hub ' || n, 'source'"
                " FROM generate_series(1, :rows) n"
            ),
            {
                "deployment_id": _DEPLOYMENT_ID,
                "entity_id": _HUB_ENTITY_ID,
                "rows": _SETTINGS.hub_aliases,
            },
        )
        relation_ids = tuple(
            connection.execute(
                text(
                    "WITH objects AS ("
                    " INSERT INTO entities (entity_id, deployment_id,"
                    " canonical_name, normalized_name)"
                    " SELECT gen_random_uuid(), :deployment_id,"
                    " 'Scale Object ' || n, 'scale object ' || n"
                    " FROM generate_series(1, :rows) n RETURNING entity_id"
                    ") INSERT INTO relations (relation_id, deployment_id,"
                    " subject_entity_id, predicate, object_entity_id,"
                    " normalizer_version, evidence_count)"
                    " SELECT gen_random_uuid(), :deployment_id, :subject_id,"
                    " 'related_to', entity_id, 'scale-normalizer', 1 FROM objects"
                    " RETURNING relation_id"
                ),
                {
                    "deployment_id": _DEPLOYMENT_ID,
                    "subject_id": _FACT_SUBJECT_ID,
                    "rows": _SETTINGS.lineage_relations,
                },
            ).scalars()
        )
        observation_ids = tuple(
            connection.execute(
                text(
                    "INSERT INTO observations (observation_id, deployment_id,"
                    " subject_entity_id, statement, normalizer_version,"
                    " evidence_count)"
                    " SELECT gen_random_uuid(), :deployment_id, :subject_id,"
                    " 'Scale observation ' || n, 'scale-normalizer', 1"
                    " FROM generate_series(1, :rows) n RETURNING observation_id"
                ),
                {
                    "deployment_id": _DEPLOYMENT_ID,
                    "subject_id": _FACT_SUBJECT_ID,
                    "rows": _SETTINGS.lineage_observations,
                },
            ).scalars()
        )
        first_claim = claim_ids[0]
        connection.execute(
            text(
                "INSERT INTO relation_evidence (deployment_id, relation_id,"
                " claim_id, doc_id, stance, normalizer_version)"
                " SELECT :deployment_id, relation_id, :claim_id, :doc_id,"
                " 'supports', 'scale-normalizer'"
                " FROM unnest(CAST(:relation_ids AS uuid[])) relation_id"
            ),
            {
                "deployment_id": _DEPLOYMENT_ID,
                "claim_id": first_claim,
                "doc_id": _DOC_ID,
                "relation_ids": list(relation_ids),
            },
        )
        connection.execute(
            text(
                "INSERT INTO observation_evidence (deployment_id, observation_id,"
                " claim_id, doc_id, stance, normalizer_version)"
                " SELECT :deployment_id, observation_id, :claim_id, :doc_id,"
                " 'supports', 'scale-normalizer'"
                " FROM unnest(CAST(:observation_ids AS uuid[])) observation_id"
            ),
            {
                "deployment_id": _DEPLOYMENT_ID,
                "claim_id": first_claim,
                "doc_id": _DOC_ID,
                "observation_ids": list(observation_ids),
            },
        )
    return {
        "claims": claim_ids,
        "relations": relation_ids,
        "observations": observation_ids,
    }


def test_operational_scale_battery_records_complete_report(
    database_engine: Engine,
    schema_inventory: CatalogInventory,
    seeded_profile: dict[str, tuple[UUID, ...]],
) -> None:
    """Measure every WP-7.2 shape and persist one attributable report."""
    report = OperationalScaleReport(
        measurements=(
            _schema_shape(engine=database_engine, inventory=schema_inventory),
            _hub_registry_blocking(engine=database_engine),
            _hub_lineage_recount(
                engine=database_engine,
                relation_ids=seeded_profile["relations"],
                observation_ids=seeded_profile["observations"],
            ),
            _provider_neutral_batching(
                engine=database_engine, claim_ids=seeded_profile["claims"]
            ),
        )
    )
    run_id = record_operational_scale_report(
        engine=database_engine, deployment_id=_DEPLOYMENT_ID, report=report
    )

    assert report.passed, report.model_dump()
    with database_engine.connect() as connection:
        stored = (
            connection.execute(
                text(
                    "SELECT suite::text, component_version, metrics, passed"
                    " FROM eval_runs WHERE eval_run_id = :run_id"
                ),
                {"run_id": run_id},
            )
            .mappings()
            .one()
        )
    assert stored["suite"] == "operational"
    assert stored["component_version"] == OPERATIONAL_SCALE_VERSION
    assert stored["passed"] is True
    assert len(stored["metrics"]["measurements"]) == 4


def test_operational_scale_report_rejects_an_incomplete_named_battery() -> None:
    """Four duplicate rows cannot masquerade as a complete report."""
    duplicate = OperationalScaleMeasurement(
        name="d23_schema_shape", profile="ci", scale={}, metrics={}, passed=True
    )
    with pytest.raises(ValidationError, match="operational scale names mismatch"):
        OperationalScaleReport(measurements=(duplicate,) * 4)


def _schema_shape(
    *, engine: Engine, inventory: CatalogInventory
) -> OperationalScaleMeasurement:
    """Record the exact D23 partition and blocking-index estate."""
    with engine.connect() as connection:
        registry_kinds = {
            str(row["relname"]): str(row["relkind"])
            for row in connection.execute(
                text(
                    "SELECT relname, relkind FROM pg_class"
                    " WHERE relname IN ('entities', 'aliases')"
                )
            ).mappings()
        }
        index_defs = {
            str(row["indexname"]): str(row["indexdef"])
            for row in connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes"
                    " WHERE schemaname = 'public'"
                )
            ).mappings()
        }
        hot_index_defs = tuple(
            connection.execute(
                text(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public'"
                    " AND tablename = ANY(:tables)"
                ),
                {"tables": [*EXPECTED_RANGE_PARENTS, *EXPECTED_HASH_PARENTS]},
            ).scalars()
        )
        blocking_index_bytes = {
            str(row["indexname"]): int(row["index_bytes"])
            for row in connection.execute(
                text(
                    "SELECT indexname, pg_relation_size(indexname::regclass)"
                    " AS index_bytes"
                    " FROM pg_indexes WHERE schemaname = 'public'"
                    " AND indexname = ANY(:names)"
                ),
                {
                    "names": [
                        "ix_entities_name_trgm",
                        "ix_aliases_lemma_trgm",
                        "ix_aliases_lemma_dm",
                        "ix_relations_block_subj",
                    ]
                },
            ).mappings()
        }
    expected_range = tuple(sorted(EXPECTED_RANGE_PARENTS))
    expected_hash = tuple(sorted(EXPECTED_HASH_PARENTS))
    blocking = {
        name: index_defs.get(name, "")
        for name in (
            "ix_entities_name_trgm",
            "ix_aliases_lemma_trgm",
            "ix_aliases_lemma_dm",
            "ix_relations_block_subj",
        )
    }
    no_heavy_hot_index = not any(
        token in definition.lower()
        for definition in hot_index_defs
        for token in (" using gin ", " using gist ", "hnsw")
    )
    p1_hnsw_indexes = {
        name for name, definition in index_defs.items() if "hnsw" in definition.lower()
    }
    expected_p1_hnsw_indexes = {
        "ix_chunk_search_embedding_hnsw",
        "ix_claims_current_embedding_hnsw",
        "ix_relations_embedding_hnsw",
        "ix_observations_embedding_hnsw",
        "ix_entities_embedding_hnsw",
    }
    passed = (
        inventory.range_parents == expected_range
        and inventory.hash_parents == expected_hash
        and inventory.hash_child_counts
        == {parent: 64 for parent in EXPECTED_HASH_PARENTS}
        and registry_kinds == {"aliases": "r", "entities": "r"}
        and "gin_trgm_ops" in blocking["ix_entities_name_trgm"]
        and "gin_trgm_ops" in blocking["ix_aliases_lemma_trgm"]
        and "daitch_mokotoff" in blocking["ix_aliases_lemma_dm"]
        and "subject_entity_id, predicate, object_entity_id"
        in blocking["ix_relations_block_subj"]
        and no_heavy_hot_index
        and p1_hnsw_indexes == expected_p1_hnsw_indexes
    )
    return OperationalScaleMeasurement(
        name="d23_schema_shape",
        profile=_SETTINGS.profile,
        scale={
            "partition_parents": len(expected_range),
            "hash_children_per_parent": 64,
        },
        metrics={
            "range_parents": inventory.range_parents,
            "hash_parents": inventory.hash_parents,
            "hash_child_counts": inventory.hash_child_counts,
            "registry_relkind": registry_kinds,
            "blocking_indexes": blocking,
            "blocking_index_bytes": blocking_index_bytes,
            "hot_indexes_are_btree_only": no_heavy_hot_index,
            "p1_hnsw_indexes": sorted(p1_hnsw_indexes),
        },
        passed=passed,
    )


def _hub_registry_blocking(*, engine: Engine) -> OperationalScaleMeasurement:
    """Exercise the real trigram/phonetic blocking target at hub shape."""

    def query() -> tuple[UUID, ...]:
        with engine.connect() as connection:
            return tuple(
                connection.execute(
                    text(
                        "SELECT DISTINCT entity_id FROM aliases"
                        " WHERE similarity(normalized_lemma, :lemma) >= 0.3"
                        " OR daitch_mokotoff(normalized_lemma)"
                        "    && daitch_mokotoff(:lemma)"
                        " ORDER BY entity_id LIMIT 64"
                    ),
                    {"lemma": "alexander hub 17"},
                ).scalars()
            )

    ids, elapsed_ms, probe = _measure(engine=engine, operation=query)
    return OperationalScaleMeasurement(
        name="hub_registry_blocking",
        profile=_SETTINGS.profile,
        scale={"aliases_on_one_entity": _SETTINGS.hub_aliases},
        metrics={
            "returned_candidates": len(ids),
            "bounded_limit": 64,
            "sql_statements": len(probe.statements),
            "elapsed_ms_with_injected_latency": elapsed_ms,
            "injected_latency_ms_per_statement": _SETTINGS.injected_latency_ms,
        },
        limitations=(
            "Capability-sized synthetic aliases validate the hook and query shape, not a corpus forecast or hosted SLA.",
        ),
        passed=ids == (_HUB_ENTITY_ID,) and len(probe.statements) == 1,
    )


def _hub_lineage_recount(
    *, engine: Engine, relation_ids: tuple[UUID, ...], observation_ids: tuple[UUID, ...]
) -> OperationalScaleMeasurement:
    """Recount thousands of facts in two set statements, independent of N."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE claims SET is_current_testimony = false"
                " WHERE deployment_id = :deployment_id"
            ),
            {"deployment_id": _DEPLOYMENT_ID},
        )
    catalog = LifecycleCatalog(engine=engine)
    (changed_relations, changed_observations), elapsed_ms, probe = _measure(
        engine=engine,
        operation=lambda: catalog.recount(
            relation_ids=relation_ids, observation_ids=observation_ids
        ),
    )
    with engine.connect() as connection:
        nonzero = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM relations WHERE relation_id = ANY(:relations)"
                " AND (evidence_count <> 0 OR contradict_count <> 0))"
                " + (SELECT count(*) FROM observations WHERE observation_id = ANY(:observations)"
                " AND (evidence_count <> 0 OR contradict_count <> 0))"
            ),
            {"relations": list(relation_ids), "observations": list(observation_ids)},
        ).scalar_one()
    return OperationalScaleMeasurement(
        name="hub_lineage_recount",
        profile=_SETTINGS.profile,
        scale={"relations": len(relation_ids), "observations": len(observation_ids)},
        metrics={
            "changed_relations": len(changed_relations),
            "changed_observations": len(changed_observations),
            "recount_sql_statements": len(probe.statements),
            "transactions": probe.transactions,
            "elapsed_ms_with_injected_latency": elapsed_ms,
            "injected_latency_ms_per_statement": _SETTINGS.injected_latency_ms,
        },
        limitations=(
            "Synthetic one-lineage fan-out measures the binding worst-case shape; elapsed time is not a hosted SLA.",
        ),
        passed=(
            len(changed_relations) == len(relation_ids)
            and len(changed_observations) == len(observation_ids)
            and len(probe.statements) == 2
            and probe.transactions == 1
            and nonzero == 0
        ),
    )


def _provider_neutral_batching(
    *, engine: Engine, claim_ids: tuple[UUID, ...]
) -> OperationalScaleMeasurement:
    """Count real-pool hydration, currency-write, and E3 entity-batch I/O."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE claims SET is_current_testimony = true"
                " WHERE claim_id = ANY(:claim_ids)"
            ),
            {"claim_ids": list(claim_ids)},
        )
    query = QueryEngine(
        engine=engine,
        search_index=_NullSearchIndex(),
        model_provider=FakeModelProvider(),
        embedding_model="scale-embedding",
    )
    (confirmed, dropped, _coverage), hydration_ms, hydration_probe = _measure(
        engine=engine,
        operation=lambda: query._confirm_claims(  # pyright: ignore[reportPrivateUsage]
            deployment_id=_DEPLOYMENT_ID, claim_ids=claim_ids
        ),
    )

    assertions = tuple(
        ObservationAssertion(
            statement="Batch Subject has a portable profile.",
            claim_id=claim_id,
            doc_id=_DOC_ID,
        )
        for claim_id in claim_ids[: _SETTINGS.entity_batch_assertions]
    )
    adjudicator = ObservationAdjudicator(
        engine=engine,
        model_provider=FakeModelProvider(),
        settings=ObservationSettings(),
    )
    observation_ids, observation_ms, observation_probe = _measure(
        engine=engine,
        operation=lambda: adjudicator.add_observations(
            deployment_id=_DEPLOYMENT_ID,
            subject_entity_id=_BATCH_ENTITY_ID,
            assertions=assertions,
        ),
    )

    transitions = tuple(
        CurrencyTransition(
            claim_id=claim_id,
            doc_id=_DOC_ID,
            became_current=False,
            reason="reextracted",
            from_extractor_version="scale-extractor",
        )
        for claim_id in claim_ids
    )
    applied, currency_ms, currency_probe = _measure(
        engine=engine,
        operation=lambda: LifecycleCatalog(engine=engine).apply_transitions(
            deployment_id=_DEPLOYMENT_ID,
            reconciliation_id=uuid4(),
            transitions=transitions,
        ),
    )

    expected_hydration_statements = math.ceil(
        len(claim_ids) / INTERACTIVE_HYDRATION_BATCH_SIZE
    )
    # The front-loaded block and the batch's claim-timing read must each run
    # once per entity batch (D43/D88). Match stable fragments of the two
    # statements: the block reads `observations o` with its D106 timing
    # lateral; the timing read selects `asserted_at` plus the D41 valid-time
    # columns for the batch's claim ids.
    block_reads = sum(
        "FROM observations o" in statement and "invalidated_at IS NULL" in statement
        for statement in observation_probe.statements
    )
    timestamp_reads = sum(
        "SELECT claim_id, asserted_at" in statement
        and "FROM claims WHERE claim_id = ANY(" in statement
        for statement in observation_probe.statements
    )
    return OperationalScaleMeasurement(
        name="provider_neutral_batching",
        profile=_SETTINGS.profile,
        scale={
            "hydration_ids": len(claim_ids),
            "ungated_claims": len(claim_ids),
            "interactive_batch_size": INTERACTIVE_HYDRATION_BATCH_SIZE,
            "currency_transitions": len(transitions),
            "entity_batch_assertions": len(assertions),
        },
        metrics={
            "hydration_sql_statements": len(hydration_probe.statements),
            "hydration_expected_statements": expected_hydration_statements,
            "hydration_transactions": hydration_probe.transactions,
            "hydration_elapsed_ms": hydration_ms,
            "currency_sql_statements": len(currency_probe.statements),
            "currency_transactions": currency_probe.transactions,
            "currency_elapsed_ms": currency_ms,
            "entity_batch_transactions": observation_probe.transactions,
            "entity_block_reads": block_reads,
            "entity_claim_timestamp_reads": timestamp_reads,
            "entity_batch_elapsed_ms": observation_ms,
            "injected_latency_ms_per_statement": _SETTINGS.injected_latency_ms,
        },
        limitations=(
            "Injected latency is a portable model input on the real SQLAlchemy engine, not a provider or region commitment.",
            "Timing values are measurements only; query and transaction counts are the acceptance gates.",
        ),
        passed=(
            len(confirmed) == len(claim_ids)
            and dropped == 0
            and len(hydration_probe.statements) == expected_hydration_statements
            and hydration_probe.transactions == 1
            and len(set(observation_ids)) == 1
            and observation_probe.transactions == 1
            and block_reads == 1
            and timestamp_reads == 1
            and applied == len(transitions)
            and len(currency_probe.statements) == 2
            and currency_probe.transactions == 1
        ),
    )


def _measure(
    *, engine: Engine, operation: Callable[[], ResultT]
) -> tuple[ResultT, float, _SqlProbe]:
    """Run one operation through the real engine plus fixed injected RTT."""
    probe = _SqlProbe(engine=engine, latency_ms=_SETTINGS.injected_latency_ms)
    started = time.perf_counter()
    with probe:
        result = operation()
    elapsed_ms = (time.perf_counter() - started) * 1_000.0
    return result, elapsed_ms, probe
