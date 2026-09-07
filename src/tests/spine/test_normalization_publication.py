"""PostgreSQL proofs for complete normalization receipts and unattached assertions."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier
from typing import Literal
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.normalization import NormalizationOutput
from rememberstack.model.normalization import NormalizationReceipt
from rememberstack.model.normalization import NormalizedObservation
from rememberstack.model.normalization import NormalizedRelation
from rememberstack.spine.deployment_bootstrap import DeploymentBootstrapper
from rememberstack.spine.normalization import NormalizationCatalog
from rememberstack.spine.settings import load_database_settings
from rememberstack.spine.temporal_journal import TemporalNotReadyError
from rememberstack.spine.temporal_journal import TemporalWriteConflict

_ROOT = Path(__file__).resolve().parents[3]
_VERSION = "normalization-publication-proof"


@dataclass(frozen=True)
class PublicationInputs:
    """Independent retained claim and resolved entities for one publication proof."""

    deployment_id: UUID
    claim_id: UUID
    subject_id: UUID
    object_id: UUID
    other_id: UUID


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Use the full supported Alembic graph on an explicitly isolated test database."""
    try:
        url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for normalization publication proofs"
        )
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(url, connect_args={"options": "-c statement_timeout=10000"})
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def inputs(database_engine: Engine) -> PublicationInputs:
    """Create real source/identity rows and an explicit empty fact generation."""
    deployment_id = uuid4()
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=deployment_id,
            slug=str(deployment_id),
            name="Publication proof",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpus",
        )
    )
    result = PublicationInputs(
        deployment_id=deployment_id,
        claim_id=uuid4(),
        subject_id=uuid4(),
        object_id=uuid4(),
        other_id=uuid4(),
    )
    with database_engine.begin() as connection:
        doc_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO documents (deployment_id, doc_id, source_kind) VALUES (:dep, :doc, 'upload')"
            ),
            {"dep": deployment_id, "doc": doc_id},
        )
        connection.execute(
            text("""
            INSERT INTO claims (deployment_id, claim_id, doc_id, chunk_id, claim_text, source_span,
                char_start, char_end, anchor_ok, window_membership_ok, extractor_version,
                claim_valid_kind, claim_valid_from, claim_valid_precision)
            VALUES (:dep, :claim, :doc, :chunk, 'Ada works for Acme', 'Ada works for Acme',
                0, 18, true, true, 'source-proof', 'effective_period', '2019-01-01 00:00:00+00', 'open')
        """),
            {
                "dep": deployment_id,
                "claim": result.claim_id,
                "doc": doc_id,
                "chunk": uuid4(),
            },
        )
        connection.execute(
            text("""
            INSERT INTO entities (deployment_id, entity_id, canonical_name, normalized_name)
            VALUES (:dep, :id, :name, :name)
        """),
            [
                {"dep": deployment_id, "id": identity, "name": str(identity)}
                for identity in (result.subject_id, result.object_id, result.other_id)
            ],
        )
    return result


def _output(*, inputs: PublicationInputs, other: bool = False) -> NormalizationOutput:
    """A relation and observation share the source while retaining distinct output kinds."""
    return NormalizationOutput(
        outcome="accepted",
        relations=(
            NormalizedRelation(
                subject_entity_id=inputs.subject_id,
                predicate="works_for",
                object_entity_id=inputs.other_id if other else inputs.object_id,
                shape_kind=FactTemporalKind.OCCURRENCE,
            ),
        ),
        observations=(
            NormalizedObservation(
                subject_entity_id=inputs.subject_id,
                statement="Ada is employed by Beta"
                if other
                else "Ada is employed by Acme",
            ),
        ),
    )


def test_publication_is_complete_and_never_attaches_a_fact(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """The complete output and distinct assertions commit together without selecting fact identity."""
    catalog = NormalizationCatalog(engine=database_engine)
    prepared = catalog.input_snapshot(
        deployment_id=inputs.deployment_id, claim_id=inputs.claim_id
    )
    published = catalog.publish(
        prepared=prepared, normalizer_version=_VERSION, output=_output(inputs=inputs)
    )
    # Staging retains the normalizer's own shape judgment. Application later
    # prefers the immutable D41 claim kind, without rewriting this source.
    assert published.output.relations[0].shape_kind is FactTemporalKind.OCCURRENCE
    assert published.output.observations[0].shape_kind is FactTemporalKind.UNKNOWN
    assert (
        catalog.receipt(
            deployment_id=inputs.deployment_id,
            claim_id=inputs.claim_id,
            normalizer_version=_VERSION,
        )
        == published
    )
    with database_engine.connect() as connection:
        for table in (
            "relations",
            "observations",
            "relation_evidence",
            "observation_evidence",
        ):
            assert (
                connection.execute(
                    text(f"SELECT count(*) FROM {table} WHERE deployment_id = :dep"),
                    {"dep": inputs.deployment_id},
                ).scalar_one()
                == 0
            )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM normalize_relation_assertions WHERE receipt_id = :id"
                ),
                {"id": published.receipt_id},
            ).scalar_one()
            == 1
        )


@pytest.mark.parametrize("outcome", ["empty", "soft_drop"])
def test_empty_publication_is_reused_instead_of_replaced(
    database_engine: Engine,
    inputs: PublicationInputs,
    outcome: Literal["empty", "soft_drop"],
) -> None:
    """An explicit empty answer is durable success, never inferred from absent facts."""
    catalog = NormalizationCatalog(engine=database_engine)
    prepared = catalog.input_snapshot(
        deployment_id=inputs.deployment_id, claim_id=inputs.claim_id
    )
    first = catalog.publish(
        prepared=prepared,
        normalizer_version=_VERSION,
        output=NormalizationOutput(outcome=outcome),
    )
    assert first.output.outcome == outcome
    assert (
        catalog.publish(
            prepared=prepared,
            normalizer_version=_VERSION,
            output=_output(inputs=inputs),
        )
        == first
    )
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM normalize_relation_assertions WHERE receipt_id = :id"
                ),
                {"id": first.receipt_id},
            ).scalar_one()
            == 0
        )


def test_competing_outputs_share_the_first_complete_receipt(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Two completed model alternatives cannot mix their assertion and observation sets."""
    catalog = NormalizationCatalog(engine=database_engine)
    prepared = catalog.input_snapshot(
        deployment_id=inputs.deployment_id, claim_id=inputs.claim_id
    )
    barrier = Barrier(2)

    def publish(*, other: bool) -> NormalizationReceipt:
        """Start both real publication transactions from the same prepared source."""
        barrier.wait(timeout=10)
        return catalog.publish(
            prepared=prepared,
            normalizer_version=_VERSION,
            output=_output(inputs=inputs, other=other),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(publish, other=other) for other in (False, True)]
        receipts = [future.result(timeout=20) for future in futures]
    assert receipts[0] == receipts[1]
    winner = receipts[0].output
    assert winner.observations[0].statement == (
        "Ada is employed by Beta"
        if winner.relations[0].object_entity_id == inputs.other_id
        else "Ada is employed by Acme"
    )
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM normalize_claim_receipts WHERE deployment_id = :dep"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM normalize_relation_assertions WHERE deployment_id = :dep"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 1
        )


def test_failure_after_first_assertion_rolls_back_the_entire_publication(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A late database error removes both the earlier assertion and its complete-output receipt."""
    catalog = NormalizationCatalog(engine=database_engine)
    prepared = catalog.input_snapshot(
        deployment_id=inputs.deployment_id, claim_id=inputs.claim_id
    )
    relations = tuple(
        NormalizedRelation(
            subject_entity_id=inputs.subject_id,
            predicate=predicate,
            object_entity_id=inputs.object_id,
        )
        for predicate in ("related_to", "works_for")
    )
    with database_engine.begin() as connection:
        connection.execute(
            text("""
            CREATE FUNCTION reject_late_normalize_assertion() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF NEW.predicate = 'works_for' THEN
                IF (SELECT count(*) FROM normalize_relation_assertions WHERE receipt_id = NEW.receipt_id) <> 1 THEN
                  RAISE EXCEPTION 'proof did not reach the second assertion';
                END IF;
                RAISE EXCEPTION 'failure after first complete assertion';
              END IF;
              RETURN NEW;
            END $$
        """)
        )
        connection.execute(
            text(
                "CREATE TRIGGER reject_late_normalize_assertion BEFORE INSERT ON normalize_relation_assertions FOR EACH ROW EXECUTE FUNCTION reject_late_normalize_assertion()"
            )
        )
    try:
        with pytest.raises(
            SQLAlchemyError, match="failure after first complete assertion"
        ):
            catalog.publish(
                prepared=prepared,
                normalizer_version=_VERSION,
                output=NormalizationOutput(outcome="accepted", relations=relations),
            )
    finally:
        with database_engine.begin() as connection:
            connection.execute(
                text(
                    "DROP TRIGGER reject_late_normalize_assertion ON normalize_relation_assertions"
                )
            )
            connection.execute(text("DROP FUNCTION reject_late_normalize_assertion()"))
    assert (
        catalog.receipt(
            deployment_id=inputs.deployment_id,
            claim_id=inputs.claim_id,
            normalizer_version=_VERSION,
        )
        is None
    )
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM normalize_relation_assertions WHERE deployment_id = :dep"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 0
        )
    assert (
        len(
            catalog.publish(
                prepared=prepared,
                normalizer_version=_VERSION,
                output=NormalizationOutput(outcome="accepted", relations=relations),
            ).output.relations
        )
        == 2
    )


def test_changed_source_refuses_unpublished_output(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A late helper cannot bind its prepared output to different source testimony."""
    catalog = NormalizationCatalog(engine=database_engine)
    prepared = catalog.input_snapshot(
        deployment_id=inputs.deployment_id, claim_id=inputs.claim_id
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE claims SET claim_text = 'Different source text' WHERE claim_id = :id"
            ),
            {"id": inputs.claim_id},
        )
    with pytest.raises(TemporalWriteConflict, match="source changed"):
        catalog.publish(
            prepared=prepared,
            normalizer_version=_VERSION,
            output=_output(inputs=inputs),
        )
    assert (
        catalog.receipt(
            deployment_id=inputs.deployment_id,
            claim_id=inputs.claim_id,
            normalizer_version=_VERSION,
        )
        is None
    )


def test_missing_assertion_is_not_treated_as_successful_empty_output(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """The stored output must match every assertion, even after receipt publication."""
    catalog = NormalizationCatalog(engine=database_engine)
    prepared = catalog.input_snapshot(
        deployment_id=inputs.deployment_id, claim_id=inputs.claim_id
    )
    receipt = catalog.publish(
        prepared=prepared, normalizer_version=_VERSION, output=_output(inputs=inputs)
    )
    with database_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM normalize_relation_assertions WHERE receipt_id = :id"),
            {"id": receipt.receipt_id},
        )
    with pytest.raises(
        TemporalWriteConflict, match="incomplete or changed relation assertions"
    ):
        catalog.receipt(
            deployment_id=inputs.deployment_id,
            claim_id=inputs.claim_id,
            normalizer_version=_VERSION,
        )


def test_normalizer_generation_does_not_reuse_another_generations_receipt(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A generation roll has its own complete output while exact retries preserve each winner."""
    catalog = NormalizationCatalog(engine=database_engine)
    prepared = catalog.input_snapshot(
        deployment_id=inputs.deployment_id, claim_id=inputs.claim_id
    )
    first = catalog.publish(
        prepared=prepared, normalizer_version=_VERSION, output=_output(inputs=inputs)
    )
    second = catalog.publish(
        prepared=prepared,
        normalizer_version=_VERSION + "-next",
        output=_output(inputs=inputs, other=True),
    )
    assert first.receipt_id != second.receipt_id
    assert first.output.relations != second.output.relations
    assert (
        catalog.receipt(
            deployment_id=inputs.deployment_id,
            claim_id=inputs.claim_id,
            normalizer_version=_VERSION,
        )
        == first
    )


def test_missing_fact_generation_blocks_publication(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Normalization is an admitted writer and cannot publish into an uncertified store."""
    catalog = NormalizationCatalog(engine=database_engine)
    prepared = catalog.input_snapshot(
        deployment_id=inputs.deployment_id, claim_id=inputs.claim_id
    )
    with database_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM temporal_fact_generations WHERE deployment_id = :dep"),
            {"dep": inputs.deployment_id},
        )
    with pytest.raises(TemporalNotReadyError):
        catalog.publish(
            prepared=prepared,
            normalizer_version=_VERSION,
            output=_output(inputs=inputs),
        )
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM normalize_claim_receipts WHERE deployment_id = :dep"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 0
        )


def test_redirects_are_resolved_before_distinct_assertions_are_published(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Concurrent identity changes cannot publish two aliases as distinct canonical triples."""
    catalog = NormalizationCatalog(engine=database_engine)
    prepared = catalog.input_snapshot(
        deployment_id=inputs.deployment_id, claim_id=inputs.claim_id
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE entities SET status = 'merged', merged_into = :survivor WHERE entity_id = :retired"
            ),
            {"survivor": inputs.other_id, "retired": inputs.object_id},
        )
    output = NormalizationOutput(
        outcome="accepted",
        relations=tuple(
            NormalizedRelation(
                subject_entity_id=inputs.subject_id,
                predicate="works_for",
                object_entity_id=identity,
            )
            for identity in (inputs.object_id, inputs.other_id)
        ),
    )
    published = catalog.publish(
        prepared=prepared, normalizer_version=_VERSION, output=output
    )
    assert len(published.output.relations) == 1
    assert published.output.relations[0].object_entity_id == inputs.other_id
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM normalize_relation_assertions WHERE receipt_id = :id"
                ),
                {"id": published.receipt_id},
            ).scalar_one()
            == 1
        )


def test_foreign_entity_cannot_be_published_under_this_claim(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A resolved identifier without deployment-local identity cannot enter a receipt."""
    catalog = NormalizationCatalog(engine=database_engine)
    prepared = catalog.input_snapshot(
        deployment_id=inputs.deployment_id, claim_id=inputs.claim_id
    )
    foreign_deployment, foreign_entity = uuid4(), uuid4()
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=foreign_deployment,
            slug=str(foreign_deployment),
            name="Foreign identity proof",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpus",
        )
    )
    with database_engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO entities (deployment_id, entity_id, canonical_name, normalized_name)
            VALUES (:dep, :id, 'Foreign subject', 'foreign subject')
        """),
            {"dep": foreign_deployment, "id": foreign_entity},
        )
    output = NormalizationOutput(
        outcome="accepted",
        relations=(
            NormalizedRelation(
                subject_entity_id=foreign_entity,
                predicate="works_for",
                object_entity_id=inputs.object_id,
            ),
        ),
    )
    with pytest.raises(TemporalWriteConflict):
        catalog.publish(prepared=prepared, normalizer_version=_VERSION, output=output)
    assert (
        catalog.receipt(
            deployment_id=inputs.deployment_id,
            claim_id=inputs.claim_id,
            normalizer_version=_VERSION,
        )
        is None
    )


def _version_membership(
    *, database_engine: Engine, inputs: PublicationInputs, number: int
) -> tuple[UUID, UUID]:
    """Attach the same immutable claim to a new real version's chunk occurrence."""
    version_id, representation_id, chunk_id = uuid4(), uuid4(), uuid4()
    section_id = uuid4()
    with database_engine.begin() as connection:
        doc_id = connection.execute(
            text("SELECT doc_id FROM claims WHERE claim_id = :id"),
            {"id": inputs.claim_id},
        ).scalar_one()
        parameters = {
            "dep": inputs.deployment_id,
            "doc": doc_id,
            "version": version_id,
            "rep": representation_id,
            "chunk": chunk_id,
            "section": section_id,
            "claim": inputs.claim_id,
            "hash": str(version_id),
            "number": number,
        }
        connection.execute(
            text("""
            INSERT INTO content_objects (deployment_id, content_hash, byte_size, mime, raw_uri)
            VALUES (:dep, :hash, 1, 'text/plain', 'mem://source')
        """),
            parameters,
        )
        connection.execute(
            text("""
            INSERT INTO document_versions (deployment_id, doc_id, version_id, content_hash, version_no)
            VALUES (:dep, :doc, :version, :hash, :number)
        """),
            parameters,
        )
        connection.execute(
            text("""
            INSERT INTO document_representations (deployment_id, version_id, representation_id, route)
            VALUES (:dep, :version, :rep, 'text')
        """),
            parameters,
        )
        connection.execute(
            text("""
            INSERT INTO document_sections (section_id, deployment_id, doc_id, version_id,
                representation_id, node_path, block_start, block_end, role, char_start, char_end, ordinal)
            VALUES (:section, :dep, :doc, :version, :rep, '0', 0, 0, 'body', 0, 18, 0)
        """),
            parameters,
        )
        connection.execute(
            text("""
            INSERT INTO chunks (deployment_id, doc_id, version_id, representation_id, chunk_id,
                ordinal, block_start, block_end, chunk_content_hash, extraction_input_hash,
                char_start, char_end, chunker_version, section_id)
            VALUES (:dep, :doc, :version, :rep, :chunk, 0, 0, 0, :hash, :hash, 0, 18, 'chunk-proof', :section)
        """),
            parameters,
        )
        connection.execute(
            text("""
            INSERT INTO chunk_claims (deployment_id, chunk_id, claim_id) VALUES (:dep, :chunk, :claim)
        """),
            parameters,
        )
    return version_id, representation_id


def _open_observation_barrier(
    *,
    database_engine: Engine,
    inputs: PublicationInputs,
    version_id: UUID,
    representation_id: UUID,
    normalizer_version: str,
) -> None:
    """Run the actual closed-set materializer under production admission and barrier locks."""
    from rememberstack.model import ProcessingLane
    from rememberstack.spine.temporal_journal import temporal_identity_admission
    from rememberstack.spine.work_ledger import _ADVISORY_LOCK_NORMALIZE_BARRIER
    from rememberstack.spine.work_ledger import _enqueue_entity_obs_flush_fanout
    from rememberstack.workers.e3 import OBS_FLUSH_VERSION

    with (
        database_engine.begin() as connection,
        temporal_identity_admission(
            connection=connection, deployment_id=inputs.deployment_id
        ),
    ):
        connection.execute(
            _ADVISORY_LOCK_NORMALIZE_BARRIER, {"representation_id": representation_id}
        )
        _enqueue_entity_obs_flush_fanout(
            connection=connection,
            deployment_id=inputs.deployment_id,
            version_id=version_id,
            representation_id=representation_id,
            chunker_version="chunk-proof",
            extractor_version="source-proof",
            normalize_component_version=normalizer_version,
            obs_flush_component_version=OBS_FLUSH_VERSION,
            content_hash=str(version_id),
            lane=ProcessingLane.STEADY,
            doc_id=None,
        )


def test_reused_claim_materializes_observations_for_later_version(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """D56 reuse opens a new version's observations from one saved result without republishing."""
    from rememberstack.workers.e3 import E3_NORMALIZER_VERSION

    catalog = NormalizationCatalog(engine=database_engine)
    receipt = catalog.publish(
        prepared=catalog.input_snapshot(
            deployment_id=inputs.deployment_id, claim_id=inputs.claim_id
        ),
        normalizer_version=E3_NORMALIZER_VERSION,
        output=_output(inputs=inputs),
    )
    versions: list[UUID] = []
    for number in (1, 2):
        version_id, representation_id = _version_membership(
            database_engine=database_engine, inputs=inputs, number=number
        )
        versions.append(version_id)
        _open_observation_barrier(
            database_engine=database_engine,
            inputs=inputs,
            version_id=version_id,
            representation_id=representation_id,
            normalizer_version=E3_NORMALIZER_VERSION,
        )
        # Re-evaluating a closed version cannot enlarge or duplicate its inputs.
        _open_observation_barrier(
            database_engine=database_engine,
            inputs=inputs,
            version_id=version_id,
            representation_id=representation_id,
            normalizer_version=E3_NORMALIZER_VERSION,
        )
    with database_engine.connect() as connection:
        rows = (
            connection.execute(
                text("""
            SELECT version_id, claim_id, statement FROM normalize_observation_staging
            WHERE deployment_id = :dep ORDER BY version_id
        """),
                {"dep": inputs.deployment_id},
            )
            .mappings()
            .all()
        )
        assert {row["version_id"] for row in rows} == set(versions)
        assert len(rows) == 2
        assert all(
            row["claim_id"] == inputs.claim_id
            and row["statement"] == receipt.output.observations[0].statement
            for row in rows
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM normalize_claim_receipts WHERE deployment_id = :dep"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM relation_flush_version_state WHERE deployment_id = :dep"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 0
        )


def test_relation_membership_requires_observations_complete_and_closes_once(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Relation units contain stable assertions, without premature fact identities or duplicate work."""
    from rememberstack.model import ProcessingLane
    from rememberstack.spine.temporal_journal import temporal_identity_admission
    from rememberstack.spine.work_ledger import _ADVISORY_LOCK_NORMALIZE_BARRIER
    from rememberstack.spine.work_ledger import _materialize_relation_units_on
    from rememberstack.workers.e3 import E3_NORMALIZER_VERSION

    catalog = NormalizationCatalog(engine=database_engine)
    output = _output(inputs=inputs)
    # Two distinct triples on one block must both survive publication and fan-out.
    output = output.model_copy(
        update={
            "relations": output.relations + _output(inputs=inputs, other=True).relations
        }
    )
    catalog.publish(
        prepared=catalog.input_snapshot(
            deployment_id=inputs.deployment_id, claim_id=inputs.claim_id
        ),
        normalizer_version=E3_NORMALIZER_VERSION,
        output=output,
    )
    version_id, representation_id = _version_membership(
        database_engine=database_engine, inputs=inputs, number=1
    )
    _open_observation_barrier(
        database_engine=database_engine,
        inputs=inputs,
        version_id=version_id,
        representation_id=representation_id,
        normalizer_version=E3_NORMALIZER_VERSION,
    )

    def materialize() -> int:
        """Own the real lock prefix and call the same function as the completed observation barrier."""
        with (
            database_engine.begin() as connection,
            temporal_identity_admission(
                connection=connection, deployment_id=inputs.deployment_id
            ),
        ):
            connection.execute(
                _ADVISORY_LOCK_NORMALIZE_BARRIER,
                {"representation_id": representation_id},
            )
            return len(
                _materialize_relation_units_on(
                    connection=connection,
                    deployment_id=inputs.deployment_id,
                    version_id=version_id,
                    representation_id=representation_id,
                    chunker_version="chunk-proof",
                    extractor_version="source-proof",
                    normalizer_version=E3_NORMALIZER_VERSION,
                    content_hash=str(version_id),
                    lane=ProcessingLane.STEADY,
                )
            )

    with pytest.raises(TemporalWriteConflict, match="completed observation"):
        materialize()
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE obs_flush_version_state SET fanout_status = 'barrier_complete', completed_at = clock_timestamp() WHERE deployment_id = :dep"
            ),
            {"dep": inputs.deployment_id},
        )
    assert materialize() == 1
    assert materialize() == 0
    with database_engine.connect() as connection:
        parameters = {"dep": inputs.deployment_id}
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM relation_flush_inputs WHERE deployment_id = :dep AND applied_at IS NULL"
                ),
                parameters,
            ).scalar_one()
            == 2
        )
        assert (
            connection.execute(
                text(
                    "SELECT expected_units FROM relation_flush_version_state WHERE deployment_id = :dep"
                ),
                parameters,
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM processing_state WHERE deployment_id = :dep AND stage = 'adjudicate_supersession' AND target_kind = 'entity'"
                ),
                parameters,
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM relations WHERE deployment_id = :dep"),
                parameters,
            ).scalar_one()
            == 0
        )


def test_missing_receipt_cannot_become_an_empty_completed_version(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Missing model output blocks the handoff and rolls back every membership/work row."""
    from rememberstack.workers.e3 import E3_NORMALIZER_VERSION

    version_id, representation_id = _version_membership(
        database_engine=database_engine, inputs=inputs, number=1
    )
    with pytest.raises(TemporalWriteConflict, match="missing complete receipts"):
        _open_observation_barrier(
            database_engine=database_engine,
            inputs=inputs,
            version_id=version_id,
            representation_id=representation_id,
            normalizer_version=E3_NORMALIZER_VERSION,
        )
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM obs_flush_version_state WHERE deployment_id = :dep"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 0
        )


def test_recorded_empty_answer_has_explicit_empty_relation_completion(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A real empty normalization answer completes both barriers and starts reconciliation."""
    from rememberstack.workers.e3 import E3_NORMALIZER_VERSION

    catalog = NormalizationCatalog(engine=database_engine)
    catalog.publish(
        prepared=catalog.input_snapshot(
            deployment_id=inputs.deployment_id, claim_id=inputs.claim_id
        ),
        normalizer_version=E3_NORMALIZER_VERSION,
        output=NormalizationOutput(outcome="empty"),
    )
    version_id, representation_id = _version_membership(
        database_engine=database_engine, inputs=inputs, number=1
    )
    _open_observation_barrier(
        database_engine=database_engine,
        inputs=inputs,
        version_id=version_id,
        representation_id=representation_id,
        normalizer_version=E3_NORMALIZER_VERSION,
    )
    with database_engine.connect() as connection:
        parameters = {"dep": inputs.deployment_id}
        row = (
            connection.execute(
                text(
                    "SELECT fanout_status, expected_units, completed_at FROM relation_flush_version_state WHERE deployment_id = :dep"
                ),
                parameters,
            )
            .mappings()
            .one()
        )
        assert row["fanout_status"] == "empty_complete"
        assert row["expected_units"] == 0
        assert row["completed_at"] is not None
        stages = set(
            connection.execute(
                text(
                    "SELECT stage::text FROM processing_state WHERE deployment_id = :dep"
                ),
                parameters,
            ).scalars()
        )
        assert stages == {"reconcile", "embed_claim"}


def test_active_forget_blocks_source_snapshot_and_publication(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """No saved or in-flight normalized payload may cross accepted deletion admission."""
    from rememberstack.model import ForgetInProgressError

    catalog = NormalizationCatalog(engine=database_engine)
    prepared = catalog.input_snapshot(
        deployment_id=inputs.deployment_id, claim_id=inputs.claim_id
    )
    with database_engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO forget_manifests (forget_id, deployment_id, doc_id, schema_version)
            VALUES (:id, :dep, :doc, 1)
        """),
            {"id": uuid4(), "dep": inputs.deployment_id, "doc": prepared.claim.doc_id},
        )
    with pytest.raises(ForgetInProgressError):
        catalog.input_snapshot(
            deployment_id=inputs.deployment_id, claim_id=inputs.claim_id
        )
    with pytest.raises(ForgetInProgressError):
        catalog.publish(
            prepared=prepared,
            normalizer_version=_VERSION,
            output=_output(inputs=inputs),
        )
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM normalize_claim_receipts WHERE deployment_id = :dep"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 0
        )


def test_observation_only_answer_is_accepted_and_reusable(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A complete accepted result may contain observations without any relation assertion."""
    catalog = NormalizationCatalog(engine=database_engine)
    output = NormalizationOutput(
        outcome="accepted", observations=_output(inputs=inputs).observations
    )
    published = catalog.publish(
        prepared=catalog.input_snapshot(
            deployment_id=inputs.deployment_id, claim_id=inputs.claim_id
        ),
        normalizer_version=_VERSION,
        output=output,
    )
    assert published.output == output
    assert (
        catalog.receipt(
            deployment_id=inputs.deployment_id,
            claim_id=inputs.claim_id,
            normalizer_version=_VERSION,
        )
        == published
    )
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM normalize_relation_assertions WHERE deployment_id = :dep"
                ),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 0
        )


def test_missing_assertion_cannot_silently_close_relation_membership(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A damaged accepted receipt is not an empty relation answer at the version barrier."""
    from rememberstack.workers.e3 import E3_NORMALIZER_VERSION

    catalog = NormalizationCatalog(engine=database_engine)
    output = NormalizationOutput(
        outcome="accepted", relations=_output(inputs=inputs).relations
    )
    catalog.publish(
        prepared=catalog.input_snapshot(
            deployment_id=inputs.deployment_id, claim_id=inputs.claim_id
        ),
        normalizer_version=E3_NORMALIZER_VERSION,
        output=output,
    )
    version_id, representation_id = _version_membership(
        database_engine=database_engine, inputs=inputs, number=1
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM normalize_relation_assertions WHERE deployment_id = :dep"
            ),
            {"dep": inputs.deployment_id},
        )
    with pytest.raises(TemporalWriteConflict, match="missing complete receipts"):
        _open_observation_barrier(
            database_engine=database_engine,
            inputs=inputs,
            version_id=version_id,
            representation_id=representation_id,
            normalizer_version=E3_NORMALIZER_VERSION,
        )


def test_claim_worker_publishes_real_receipt_and_reuses_it_on_retry(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """The shipped worker resolves/publishes once, leaves facts untouched, and returns a barrier."""
    from rememberstack.adapters.testing import FakeModelProvider
    from rememberstack.adapters.testing import NoopCostMeter
    from rememberstack.model import ClaimedWork
    from rememberstack.model import EntityRef
    from rememberstack.model import PipelineStage
    from rememberstack.model import ProcessingLane
    from rememberstack.model import ProcessingTarget
    from rememberstack.model import ResolvedEntity
    from rememberstack.spine.chunk_catalog import ChunkCatalog
    from rememberstack.spine.claim_catalog import ClaimCatalog
    from rememberstack.spine.fact_catalog import FactCatalog
    from rememberstack.workers.e3 import E3_NORMALIZER_VERSION
    from rememberstack.workers.e3 import E3Settings
    from rememberstack.workers.e3 import NormalizeRelationsHandler

    class Resolver:
        """Return fixture identities; actual model identity resolution has separate proofs."""

        def resolve(self, *, reference: EntityRef, **kwargs: object) -> ResolvedEntity:
            """Resolve the two named entities without writing any fact or evidence."""
            del kwargs
            return ResolvedEntity(
                entity_id=inputs.subject_id
                if reference.name == "Ada"
                else inputs.object_id,
                created=False,
            )

    provider = FakeModelProvider(
        generate_payload={
            "relations": [
                {
                    "subject": {"name": "Ada"},
                    "predicate": "works_for",
                    "object": {"name": "Acme"},
                    "shape_kind": "state",
                }
            ],
            "observations": [
                {
                    "subject": {"name": "Ada"},
                    "statement": "Ada is employed",
                    "shape_kind": "state",
                }
            ],
        }
    )
    catalog = NormalizationCatalog(engine=database_engine)
    prepared = catalog.input_snapshot(
        deployment_id=inputs.deployment_id, claim_id=inputs.claim_id
    )
    handler = NormalizeRelationsHandler(
        normalizations=catalog,
        claim_catalog=ClaimCatalog(engine=database_engine),
        chunk_catalog=ChunkCatalog(engine=database_engine),
        resolver=Resolver(),  # type: ignore[arg-type]
        facts=FactCatalog(engine=database_engine),
        model_provider=provider,
        settings=E3Settings(normalize_model="proof"),
        chunker_version="chunk-proof",
    )
    for number in (1, 2):
        version_id, representation_id = _version_membership(
            database_engine=database_engine, inputs=inputs, number=number
        )
        work = ClaimedWork(
            processing_id=uuid4(),
            deployment_id=inputs.deployment_id,
            target_kind=ProcessingTarget.CLAIM,
            target_id=inputs.claim_id,
            stage=PipelineStage.NORMALIZE_RELATIONS,
            component_version=E3_NORMALIZER_VERSION,
            content_hash=str(version_id),
            lane=ProcessingLane.STEADY,
            attempt=number,
            payload={
                "version_id": str(version_id),
                "representation_id": str(representation_id),
                "doc_id": str(prepared.claim.doc_id),
                "chunker_version": "chunk-proof",
                "extractor_version": "source-proof",
            },
        )
        outcome = handler.handle(work=work, meter=NoopCostMeter())
        assert outcome.claim_normalize_barrier is not None
        assert outcome.claim_normalize_barrier.version_id == version_id
    assert len(provider.generated_requests) == 1
    receipt = catalog.receipt(
        deployment_id=inputs.deployment_id,
        claim_id=inputs.claim_id,
        normalizer_version=E3_NORMALIZER_VERSION,
    )
    assert receipt is not None
    assert len(receipt.output.relations) == len(receipt.output.observations) == 1
    with database_engine.connect() as connection:
        for table in ("relations", "observations", "normalize_observation_staging"):
            assert (
                connection.execute(
                    text(f"SELECT count(*) FROM {table} WHERE deployment_id = :dep"),
                    {"dep": inputs.deployment_id},
                ).scalar_one()
                == 0
            )


def test_claim_completion_cannot_mark_missing_receipt_succeeded(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """The real work ledger rolls back completion when the worker has not published an answer."""
    from rememberstack.model import EnqueueWork
    from rememberstack.model import PipelineStage
    from rememberstack.model import ProcessingLane
    from rememberstack.model import ProcessingTarget
    from rememberstack.spine.work_ledger import WorkLedger
    from rememberstack.spine.work_ledger import WorkLedgerSettings
    from rememberstack.workers.base import ClaimNormalizeBarrier
    from rememberstack.workers.e3 import E3_NORMALIZER_VERSION
    from rememberstack.workers.e3 import OBS_FLUSH_VERSION

    version_id, representation_id = _version_membership(
        database_engine=database_engine, inputs=inputs, number=1
    )
    ledger = WorkLedger(engine=database_engine, settings=WorkLedgerSettings())
    job = ledger.enqueue(
        work=EnqueueWork(
            deployment_id=inputs.deployment_id,
            target_kind=ProcessingTarget.CLAIM,
            target_id=inputs.claim_id,
            stage=PipelineStage.NORMALIZE_RELATIONS,
            component_version=E3_NORMALIZER_VERSION,
            content_hash=str(version_id),
            lane=ProcessingLane.STEADY,
        )
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE processing_state SET status = 'running' WHERE processing_id = :id"
            ),
            {"id": job.processing_id},
        )
        doc_id = connection.execute(
            text("SELECT doc_id FROM claims WHERE claim_id = :id"),
            {"id": inputs.claim_id},
        ).scalar_one()
    barrier = ClaimNormalizeBarrier(
        deployment_id=inputs.deployment_id,
        version_id=version_id,
        representation_id=representation_id,
        doc_id=doc_id,
        chunker_version="chunk-proof",
        extractor_version="source-proof",
        content_hash=str(version_id),
        lane=ProcessingLane.STEADY,
        normalize_component_version=E3_NORMALIZER_VERSION,
        obs_flush_component_version=OBS_FLUSH_VERSION,
    )
    with pytest.raises(
        TemporalWriteConflict, match="requires a complete normalization receipt"
    ):
        ledger.complete_claim_normalize(
            processing_id=job.processing_id, barrier=barrier
        )
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT status::text FROM processing_state WHERE processing_id = :id"
                ),
                {"id": job.processing_id},
            ).scalar_one()
            == "running"
        )


def test_relation_work_enqueue_failure_rolls_back_the_entire_handoff(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """A failure after membership inserts cannot leave a closed barrier without its work."""
    from rememberstack.workers.e3 import E3_NORMALIZER_VERSION

    catalog = NormalizationCatalog(engine=database_engine)
    catalog.publish(
        prepared=catalog.input_snapshot(
            deployment_id=inputs.deployment_id, claim_id=inputs.claim_id
        ),
        normalizer_version=E3_NORMALIZER_VERSION,
        output=NormalizationOutput(
            outcome="accepted", relations=_output(inputs=inputs).relations
        ),
    )
    version_id, representation_id = _version_membership(
        database_engine=database_engine, inputs=inputs, number=1
    )
    # A real database failure occurs after both version markers, the unit, and
    # its input rows have been inserted. It must abort that whole transaction.
    with database_engine.begin() as connection:
        connection.execute(
            text("""
            CREATE FUNCTION normalization_handoff_proof_failure() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                IF NEW.stage = 'adjudicate_supersession' AND NEW.target_kind = 'entity' THEN
                    RAISE EXCEPTION 'relation enqueue proof failure';
                END IF;
                RETURN NEW;
            END $$
        """)
        )
        connection.execute(
            text("""
            CREATE TRIGGER normalization_handoff_proof_failure
            BEFORE INSERT ON processing_state FOR EACH ROW
            EXECUTE FUNCTION normalization_handoff_proof_failure()
        """)
        )
    try:
        with pytest.raises(SQLAlchemyError, match="relation enqueue proof failure"):
            _open_observation_barrier(
                database_engine=database_engine,
                inputs=inputs,
                version_id=version_id,
                representation_id=representation_id,
                normalizer_version=E3_NORMALIZER_VERSION,
            )
    finally:
        with database_engine.begin() as connection:
            connection.execute(
                text(
                    "DROP TRIGGER normalization_handoff_proof_failure ON processing_state"
                )
            )
            connection.execute(
                text("DROP FUNCTION normalization_handoff_proof_failure()")
            )
    with database_engine.connect() as connection:
        for table in (
            "obs_flush_version_state",
            "relation_flush_version_state",
            "relation_flush_block_units",
            "relation_flush_inputs",
            "processing_state",
        ):
            assert (
                connection.execute(
                    text(f"SELECT count(*) FROM {table} WHERE deployment_id = :dep"),
                    {"dep": inputs.deployment_id},
                ).scalar_one()
                == 0
            )


def test_shared_claim_completion_keeps_each_versions_own_hash_and_lane(
    database_engine: Engine, inputs: PublicationInputs
) -> None:
    """Completing one D56 claim must not copy steady/version-one coordinates into its backfill sibling."""
    from rememberstack.model import EnqueueWork
    from rememberstack.model import PipelineStage
    from rememberstack.model import ProcessingLane
    from rememberstack.model import ProcessingTarget
    from rememberstack.spine.work_ledger import WorkLedger
    from rememberstack.spine.work_ledger import WorkLedgerSettings
    from rememberstack.workers.base import ClaimNormalizeBarrier
    from rememberstack.workers.e3 import E3_NORMALIZER_VERSION
    from rememberstack.workers.e3 import OBS_FLUSH_VERSION

    catalog = NormalizationCatalog(engine=database_engine)
    prepared = catalog.input_snapshot(
        deployment_id=inputs.deployment_id, claim_id=inputs.claim_id
    )
    catalog.publish(
        prepared=prepared,
        normalizer_version=E3_NORMALIZER_VERSION,
        output=NormalizationOutput(
            outcome="accepted", relations=_output(inputs=inputs).relations
        ),
    )
    ledger = WorkLedger(engine=database_engine, settings=WorkLedgerSettings())
    coordinates: list[tuple[UUID, UUID, ProcessingLane]] = []
    for number, lane in ((1, ProcessingLane.STEADY), (2, ProcessingLane.BACKFILL)):
        version_id, representation_id = _version_membership(
            database_engine=database_engine, inputs=inputs, number=number
        )
        coordinates.append((version_id, representation_id, lane))
        with database_engine.connect() as connection:
            chunk_id = connection.execute(
                text("SELECT chunk_id FROM chunks WHERE representation_id=:id"),
                {"id": representation_id},
            ).scalar_one()
        job = ledger.enqueue(
            work=EnqueueWork(
                deployment_id=inputs.deployment_id,
                target_kind=ProcessingTarget.CHUNK,
                target_id=chunk_id,
                stage=PipelineStage.EXTRACT_CLAIMS,
                component_version="source-proof",
                content_hash=str(version_id),
                lane=lane,
            )
        )
        ledger_id = job.processing_id
        with database_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE processing_state SET status='succeeded' WHERE processing_id=:id"
                ),
                {"id": ledger_id},
            )
    version_id, representation_id, lane = coordinates[0]
    job = ledger.enqueue(
        work=EnqueueWork(
            deployment_id=inputs.deployment_id,
            target_kind=ProcessingTarget.CLAIM,
            target_id=inputs.claim_id,
            stage=PipelineStage.NORMALIZE_RELATIONS,
            component_version=E3_NORMALIZER_VERSION,
            content_hash=str(version_id),
            lane=lane,
        )
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE processing_state SET status='running' WHERE processing_id=:id"
            ),
            {"id": job.processing_id},
        )
    ledger.complete_claim_normalize(
        processing_id=job.processing_id,
        barrier=ClaimNormalizeBarrier(
            deployment_id=inputs.deployment_id,
            version_id=version_id,
            representation_id=representation_id,
            doc_id=prepared.claim.doc_id,
            chunker_version="chunk-proof",
            extractor_version="source-proof",
            content_hash=str(version_id),
            lane=lane,
            normalize_component_version=E3_NORMALIZER_VERSION,
            obs_flush_component_version=OBS_FLUSH_VERSION,
        ),
    )
    with database_engine.connect() as connection:
        for version_id, _representation_id, lane in coordinates:
            row = (
                connection.execute(
                    text(
                        "SELECT content_hash, lane::text FROM relation_flush_version_state WHERE deployment_id=:dep AND version_id=:version"
                    ),
                    {"dep": inputs.deployment_id, "version": version_id},
                )
                .mappings()
                .one()
            )
            assert row["content_hash"] == str(version_id)
            assert row["lane"] == lane.value
