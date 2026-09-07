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
    assert published.output.relations[0].shape_kind is FactTemporalKind.STATE
    assert published.output.observations[0].shape_kind is FactTemporalKind.STATE
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
