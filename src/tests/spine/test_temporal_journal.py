"""Real PostgreSQL proofs for D110 atomic authority and complete lock footprints."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.core.fact_temporal import cap_fact
from rememberstack.core.fact_temporal import compensate_window
from rememberstack.core.fact_temporal import correct_window
from rememberstack.core.fact_temporal import seed_fact
from rememberstack.model.claims import ClaimValidKind
from rememberstack.model.claims import ClaimValidPrecision
from rememberstack.model.fact_temporal import ClaimTemporalWindow
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.fact_temporal import FactTemporalState
from rememberstack.model.fact_temporal import OccurrenceWindow
from rememberstack.model.fact_temporal import ReversibleTemporalEffect
from rememberstack.model.fact_temporal import TemporalResult
from rememberstack.model.temporal_write import FactPlane
from rememberstack.model.temporal_write import TemporalBlock
from rememberstack.model.temporal_write import TemporalDecision
from rememberstack.model.temporal_write import TemporalEffect
from rememberstack.model.temporal_write import TemporalFactRef
from rememberstack.model.temporal_write import TemporalOperationKind
from rememberstack.model.temporal_write import TemporalSourceKind
from rememberstack.model.temporal_write import TemporalSourceRef
from rememberstack.spine.settings import load_database_settings
from rememberstack.spine.temporal_journal import load_temporal_evidence
from rememberstack.spine.temporal_journal import temporal_block_key
from rememberstack.spine.temporal_journal import TEMPORAL_FACT_GENERATION
from rememberstack.spine.temporal_journal import temporal_write
from rememberstack.spine.temporal_journal import TemporalNotReadyError
from rememberstack.spine.temporal_journal import TemporalWriteConflict

_ROOT = Path(__file__).resolve().parents[3]
_NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)
_START = datetime(2020, 1, 1, tzinfo=timezone.utc)
_EARLIER = datetime(2019, 1, 1, tzinfo=timezone.utc)
_END = datetime(2030, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Inputs:
    """Independent deployment and exact canonical identities for one proof."""

    deployment_id: UUID
    subject_id: UUID
    other_id: UUID
    claim_id: UUID
    doc_id: UUID

    def block(self, *, plane: FactPlane, other: bool = False) -> TemporalBlock:
        """Name the real subject block or a deliberately unrelated read block."""
        return TemporalBlock(
            plane=plane,
            subject_entity_id=self.other_id if other else self.subject_id,
            predicate="works_for" if plane is FactPlane.RELATION else None,
        )


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Run the full supported Alembic graph; temporal tests never stub journal tables."""
    try:
        url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for temporal journal proofs"
        )
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def inputs(database_engine: Engine) -> Inputs:
    """Create an explicitly empty certified test deployment before any fact insertion.

    This fixture isolates the journal. It does not exercise or substitute for
    runtime bootstrap or populated conversion, which have separate acceptance gates.
    """
    result = Inputs(
        deployment_id=uuid4(),
        subject_id=uuid4(),
        other_id=uuid4(),
        claim_id=uuid4(),
        doc_id=uuid4(),
    )
    with database_engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO deployments (deployment_id, slug, name, raw_bucket, artifacts_bucket, corpusfs_bucket)
            VALUES (:dep, :slug, 'Temporal journal proof', 'mem://raw', 'mem://artifacts', 'mem://corpus')
        """),
            {"dep": result.deployment_id, "slug": str(result.deployment_id)},
        )
        connection.execute(
            text("""
            INSERT INTO entity_types (deployment_id, type, description, tier)
            VALUES (:dep, 'Person', 'Journal subject', 'core')
        """),
            {"dep": result.deployment_id},
        )
        connection.execute(
            text("""
            INSERT INTO predicates (deployment_id, predicate, description, tier)
            VALUES (:dep, 'works_for', 'Employment', 'core')
        """),
            {"dep": result.deployment_id},
        )
        for entity_id in (result.subject_id, result.other_id):
            connection.execute(
                text("""
                INSERT INTO entities (deployment_id, entity_id, type, canonical_name, normalized_name)
                VALUES (:dep, :entity, 'Person', 'Subject', 'subject')
            """),
                {"dep": result.deployment_id, "entity": entity_id},
            )
        connection.execute(
            text("""
            INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id, claim_text, source_span,
              char_start, char_end, anchor_ok, window_membership_ok, extractor_version,
              claim_valid_from, claim_valid_precision, claim_valid_kind)
            VALUES (:claim, :dep, :doc, :chunk, 'CEO since 2020', 'CEO since 2020',
              0, 14, true, true, 'test', :start, 'open', 'effective_period')
        """),
            {
                "claim": result.claim_id,
                "dep": result.deployment_id,
                "doc": result.doc_id,
                "chunk": uuid4(),
                "start": _START,
            },
        )
        conversion_id = uuid4()
        connection.execute(
            text("""
            INSERT INTO temporal_conversion_runs (conversion_id, deployment_id, generation,
              input_generation, policy_fingerprint, state, expected_relations, expected_observations,
              captured_at, completed_at)
            VALUES (:id, :dep, :generation, 'empty-test', :fingerprint, 'complete', 0, 0, :now, :now)
        """),
            {
                "id": conversion_id,
                "dep": result.deployment_id,
                "generation": TEMPORAL_FACT_GENERATION,
                "fingerprint": "0" * 64,
                "now": _NOW,
            },
        )
        connection.execute(
            text("""
            INSERT INTO temporal_fact_generations (deployment_id, generation, conversion_id, verified_at)
            VALUES (:dep, :generation, :id, :now)
        """),
            {
                "dep": result.deployment_id,
                "generation": TEMPORAL_FACT_GENERATION,
                "id": conversion_id,
                "now": _NOW,
            },
        )
    return result


def _effect(
    *,
    connection: Connection,
    inputs: Inputs,
    fact: TemporalFactRef,
    kind: TemporalOperationKind,
    before: FactTemporalState,
    after: FactTemporalState,
    operation_id: UUID,
    result: TemporalResult = TemporalResult.APPLIED,
    reverses: UUID | None = None,
    candidate_claim_id: UUID | None = None,
) -> TemporalEffect:
    """Prepare one source-backed record without bypassing model coherence checks."""
    outcome = {
        TemporalOperationKind.SEED: "add",
        TemporalOperationKind.CAP: "supersede",
        TemporalOperationKind.CORRECTION: "temporal_correct",
        TemporalOperationKind.COMPENSATION: "temporal_compensate",
    }.get(kind, "noop")
    evidence = (
        load_temporal_evidence(
            connection=connection,
            deployment_id=inputs.deployment_id,
            claim_id=inputs.claim_id,
            role="support",
        ),
    )
    if candidate_claim_id is not None:
        evidence += (
            load_temporal_evidence(
                connection=connection,
                deployment_id=inputs.deployment_id,
                claim_id=candidate_claim_id,
                role="candidate_from",
            ),
        )
    return TemporalEffect(
        operation_id=operation_id,
        fact=fact,
        kind=kind,
        result=result,
        before=before,
        after=after,
        decision=TemporalDecision(
            adjudication_id=uuid4(),
            outcome=outcome,
            method="exact",
            triggering_claim_id=inputs.claim_id,
        ),
        evidence=evidence,
        input_fingerprint="1" * 64,
        identity_generation="test-identity",
        policy_generation="test-policy",
        reason="journal_proof",
        recorded_at=_NOW,
        reverses_operation_id=reverses,
        semantic_predecessors=(reverses,) if reverses else (),
    )


def _seed(
    *, engine: Engine, inputs: Inputs, plane: FactPlane, read_other: bool = False
) -> tuple[TemporalFactRef, FactTemporalState]:
    """Insert a real revision-zero fact and apply its seed within one guarded group."""
    fact = TemporalFactRef(plane=plane, fact_id=uuid4())
    before = FactTemporalState(kind=FactTemporalKind.UNKNOWN, ingested_at=_NOW)
    operation_id = uuid4()
    after = seed_fact(
        seed=ClaimTemporalWindow(
            claim_id=inputs.claim_id,
            kind=ClaimValidKind.EFFECTIVE_PERIOD,
            valid_from=_START,
            precision=ClaimValidPrecision.OPEN,
        ),
        shape=FactTemporalKind.STATE,
        ingested_at=_NOW,
    )
    after = after.model_copy(update={"revision": 1, "from_operation_id": operation_id})
    block = inputs.block(plane=plane)
    blocks = (block, inputs.block(plane=plane, other=True)) if read_other else (block,)
    with engine.begin() as connection:
        with temporal_write(
            connection=connection,
            deployment_id=inputs.deployment_id,
            blocks=blocks,
            facts=(fact,),
            new_facts=frozenset((fact,)),
        ) as session:
            fields = (
                ", predicate, object_entity_id"
                if plane is FactPlane.RELATION
                else ", statement"
            )
            values = (
                ", 'works_for', :object_id"
                if plane is FactPlane.RELATION
                else ", 'CEO since 2020'"
            )
            connection.execute(
                text(f"""
                INSERT INTO {plane.value}s ({plane.value}_id, deployment_id, subject_entity_id,
                    normalizer_version, ingested_at{fields})
                VALUES (:fact_id, :dep, :subject_id, 'test', :now{values})
            """),
                {
                    "fact_id": fact.fact_id,
                    "dep": inputs.deployment_id,
                    "subject_id": inputs.subject_id,
                    "object_id": inputs.other_id,
                    "now": _NOW,
                },
            )
            connection.execute(
                text(f"""
                INSERT INTO {plane.value}_evidence (deployment_id, {plane.value}_id, claim_id,
                    doc_id, stance, normalizer_version)
                VALUES (:dep, :fact_id, :claim_id, :doc_id, 'supports', 'test')
            """),
                {
                    "dep": inputs.deployment_id,
                    "fact_id": fact.fact_id,
                    "claim_id": inputs.claim_id,
                    "doc_id": inputs.doc_id,
                },
            )
            session.apply(
                effect=_effect(
                    connection=connection,
                    inputs=inputs,
                    fact=fact,
                    kind=TemporalOperationKind.SEED,
                    before=before,
                    after=after,
                    operation_id=operation_id,
                ),
                written_blocks=frozenset(
                    (
                        temporal_block_key(
                            deployment_id=inputs.deployment_id, block=block
                        ),
                    )
                ),
            )
    return fact, after


@pytest.mark.parametrize("plane", list(FactPlane))
def test_seed_commits_narrative_support_and_read_only_footprint(
    database_engine: Engine, inputs: Inputs, plane: FactPlane
) -> None:
    """One fact, receipt, complete evidence and cache leaf advance together."""
    fact, state = _seed(
        engine=database_engine, inputs=inputs, plane=plane, read_other=True
    )
    with database_engine.connect() as connection:
        row = connection.execute(
            text("""
            SELECT o.resulting_revision, s.expected_block_count, s.expected_claim_count,
              s.support_state FROM temporal_operations o JOIN temporal_operation_support s
              USING (deployment_id, operation_id) WHERE o.operation_id = :id
        """),
            {"id": state.from_operation_id},
        ).one()
        assert tuple(row) == (1, 2, 1, "complete")
        footprints = connection.execute(
            text("""
            SELECT writes_block, resulting_block_revision, sequence FROM temporal_operation_blocks
            WHERE operation_id = :id ORDER BY writes_block
        """),
            {"id": state.from_operation_id},
        ).all()
        assert [tuple(row) for row in footprints] == [(False, 0, 1), (True, 1, 1)]
        source = connection.execute(
            text("""
            SELECT revision, next_boundary_at, source_key FROM temporal_sources
            WHERE deployment_id = :dep AND source_kind = :plane AND source_id = :fact
        """),
            {"dep": inputs.deployment_id, "plane": plane.value, "fact": fact.fact_id},
        ).one()
        assert tuple(source) == (1, None, str(fact.fact_id))
        snapshot = connection.execute(
            text(f"""
            SELECT features FROM {plane.value}_adjudications WHERE temporal_operation_id = :id
        """),
            {"id": state.from_operation_id},
        ).scalar_one()
        assert snapshot["temporal_effect"]["after"] == state.model_dump(mode="json")


@pytest.mark.parametrize("plane", list(FactPlane))
def test_wrong_canonical_subject_block_is_refused(
    database_engine: Engine, inputs: Inputs, plane: FactPlane
) -> None:
    """A lock on another subject must never authorize this fact's update."""
    fact, _ = _seed(engine=database_engine, inputs=inputs, plane=plane)
    with database_engine.begin() as connection:
        with pytest.raises(TemporalWriteConflict, match="actual fact subject block"):
            with temporal_write(
                connection=connection,
                deployment_id=inputs.deployment_id,
                blocks=(inputs.block(plane=plane, other=True),),
                facts=(fact,),
            ):
                pytest.fail("wrong block was admitted")


@pytest.mark.parametrize("plane", list(FactPlane))
def test_compensation_preserves_later_cap_and_records_semantic_dependency(
    database_engine: Engine, inputs: Inputs, plane: FactPlane
) -> None:
    """Correct→cap→compensate restores the start while retaining independent end authority."""
    fact, seeded = _seed(engine=database_engine, inputs=inputs, plane=plane)
    block = inputs.block(plane=plane)
    key = temporal_block_key(deployment_id=inputs.deployment_id, block=block)
    with database_engine.begin() as connection:
        with temporal_write(
            connection=connection,
            deployment_id=inputs.deployment_id,
            blocks=(block,),
            facts=(fact,),
        ) as session:
            candidate_claim_id = uuid4()
            connection.execute(
                text("""
                INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id, claim_text, source_span,
                  char_start, char_end, anchor_ok, window_membership_ok, extractor_version,
                  claim_valid_from, claim_valid_precision, claim_valid_kind)
                VALUES (:id, :dep, :doc, :chunk, 'CEO since 2019', 'CEO since 2019',
                  0, 14, true, true, 'test', :start, 'open', 'effective_period')
            """),
                {
                    "id": candidate_claim_id,
                    "dep": inputs.deployment_id,
                    "doc": inputs.doc_id,
                    "chunk": uuid4(),
                    "start": _EARLIER,
                },
            )
            connection.execute(
                text(f"""
                INSERT INTO {plane.value}_evidence (deployment_id, {plane.value}_id, claim_id,
                    doc_id, stance, normalizer_version)
                VALUES (:dep, :fact, :claim, :doc, 'supports', 'test')
            """),
                {
                    "dep": inputs.deployment_id,
                    "fact": fact.fact_id,
                    "claim": candidate_claim_id,
                    "doc": inputs.doc_id,
                },
            )
            enriched = seeded.model_copy(
                update={
                    "revision": 2,
                    "occurrence": OccurrenceWindow(
                        start=_EARLIER, end=None, precision=ClaimValidPrecision.OPEN
                    ),
                }
            )
            session.apply(
                effect=_effect(
                    connection=connection,
                    inputs=inputs,
                    fact=fact,
                    kind=TemporalOperationKind.EVIDENCE,
                    before=seeded,
                    after=enriched,
                    operation_id=uuid4(),
                    candidate_claim_id=candidate_claim_id,
                ),
                written_blocks=frozenset((key,)),
            )
    seeded = enriched
    correction_id, cap_id, compensation_id = uuid4(), uuid4(), uuid4()
    corrected = correct_window(
        state=seeded,
        start=_EARLIER,
        end=None,
        operation_id=correction_id,
        neighbours=(),
    ).state
    capped = cap_fact(state=corrected, boundary=_END, operation_id=cap_id).state
    restored = compensate_window(
        state=capped,
        target=ReversibleTemporalEffect(
            operation_id=correction_id, before=seeded.verdict, after=corrected.verdict
        ),
        operation_id=compensation_id,
        neighbours=(),
    ).state
    with database_engine.begin() as connection:
        with temporal_write(
            connection=connection,
            deployment_id=inputs.deployment_id,
            blocks=(block,),
            facts=(fact,),
        ) as session:
            for kind, before, after, operation_id, reverses in (
                (
                    TemporalOperationKind.CORRECTION,
                    seeded,
                    corrected,
                    correction_id,
                    None,
                ),
                (TemporalOperationKind.CAP, corrected, capped, cap_id, None),
                (
                    TemporalOperationKind.COMPENSATION,
                    capped,
                    restored,
                    compensation_id,
                    correction_id,
                ),
            ):
                session.apply(
                    effect=_effect(
                        connection=connection,
                        inputs=inputs,
                        fact=fact,
                        kind=kind,
                        before=before,
                        after=after,
                        operation_id=operation_id,
                        reverses=reverses,
                        candidate_claim_id=candidate_claim_id
                        if kind is TemporalOperationKind.CORRECTION
                        else None,
                    ),
                    written_blocks=frozenset((key,)),
                )
            assert session.state(fact=fact) == restored
            assert (
                session.source_revision(
                    source=TemporalSourceRef(
                        kind=TemporalSourceKind(plane.value),
                        source_id=fact.fact_id,
                        key=str(fact.fact_id),
                    )
                )
                == 5
            )
    with database_engine.connect() as connection:
        row = connection.execute(
            text(f"""
            SELECT valid_from, valid_until, from_operation_id, until_operation_id, temporal_revision
            FROM {plane.value}s WHERE {plane.value}_id = :id
        """),
            {"id": fact.fact_id},
        ).one()
        assert tuple(row) == (_START, _END, compensation_id, cap_id, 5)
        dependencies = connection.execute(
            text("""
            SELECT predecessor_operation_id, required_for_semantics FROM temporal_operation_dependencies
            WHERE operation_id = :id
        """),
            {"id": compensation_id},
        ).all()
        assert set(map(tuple, dependencies)) == {(correction_id, True), (cap_id, False)}


@pytest.mark.parametrize("plane", list(FactPlane))
def test_caught_stale_effect_rolls_back_earlier_effect_in_same_group(
    database_engine: Engine, inputs: Inputs, plane: FactPlane
) -> None:
    """Catching an error outside the journal cannot commit a partially applied assertion."""
    fact, seeded = _seed(engine=database_engine, inputs=inputs, plane=plane)
    block = inputs.block(plane=plane)
    key = temporal_block_key(deployment_id=inputs.deployment_id, block=block)
    cap_id = uuid4()
    capped = cap_fact(state=seeded, boundary=_END, operation_id=cap_id).state
    with database_engine.begin() as connection:
        with pytest.raises(TemporalWriteConflict, match="failed application group"):
            with temporal_write(
                connection=connection,
                deployment_id=inputs.deployment_id,
                blocks=(block,),
                facts=(fact,),
            ) as session:
                cap = _effect(
                    connection=connection,
                    inputs=inputs,
                    fact=fact,
                    kind=TemporalOperationKind.CAP,
                    before=seeded,
                    after=capped,
                    operation_id=cap_id,
                )
                session.apply(effect=cap, written_blocks=frozenset((key,)))
                with pytest.raises(TemporalWriteConflict, match="prepared fact tuple"):
                    session.apply(effect=cap, written_blocks=frozenset((key,)))
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM temporal_operations WHERE operation_id = :id"
                ),
                {"id": cap_id},
            ).scalar_one()
            == 0
        )
    with database_engine.begin() as connection:
        with temporal_write(
            connection=connection,
            deployment_id=inputs.deployment_id,
            blocks=(block,),
            facts=(fact,),
        ) as session:
            assert session.state(fact=fact) == seeded
            assert session.block_states[0].sequence == 1


def test_registry_collision_rolls_back_new_block(
    database_engine: Engine, inputs: Inputs
) -> None:
    """A colliding source UUID cannot silently certify another registry key."""
    source_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO temporal_sources (deployment_id, source_kind, source_id, source_key)
            VALUES (:dep, 'predicate', :id, 'already-registered')
        """),
            {"dep": inputs.deployment_id, "id": source_id},
        )
        with pytest.raises(TemporalWriteConflict, match="registry identity collision"):
            with temporal_write(
                connection=connection,
                deployment_id=inputs.deployment_id,
                blocks=(inputs.block(plane=FactPlane.RELATION),),
                facts=(),
                sources=(
                    TemporalSourceRef(
                        kind=TemporalSourceKind.PREDICATE,
                        source_id=source_id,
                        key="different-registry-key",
                    ),
                ),
            ):
                pytest.fail("collision was admitted")
        assert (
            connection.execute(
                text("SELECT count(*) FROM temporal_blocks WHERE deployment_id = :dep"),
                {"dep": inputs.deployment_id},
            ).scalar_one()
            == 0
        )


def test_missing_generation_fails_before_any_block_write(
    database_engine: Engine, inputs: Inputs
) -> None:
    """Ordinary authority cannot use a deployment whose conversion is uncertified."""
    with database_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM temporal_fact_generations WHERE deployment_id = :dep"),
            {"dep": inputs.deployment_id},
        )
        with pytest.raises(TemporalNotReadyError):
            with temporal_write(
                connection=connection,
                deployment_id=inputs.deployment_id,
                blocks=(inputs.block(plane=FactPlane.RELATION),),
                facts=(),
            ):
                pytest.fail("uncertified deployment was admitted")


def test_redirect_requires_survivor_block(
    database_engine: Engine, inputs: Inputs
) -> None:
    """A merged subject remains addressable only through its current canonical block."""
    fact, seeded = _seed(
        engine=database_engine, inputs=inputs, plane=FactPlane.OBSERVATION
    )
    with database_engine.begin() as connection:
        connection.execute(
            text("""
            UPDATE entities SET status = 'merged', merged_into = :root WHERE entity_id = :id
        """),
            {"id": inputs.subject_id, "root": inputs.other_id},
        )
        with pytest.raises(TemporalWriteConflict, match="not its canonical survivor"):
            with temporal_write(
                connection=connection,
                deployment_id=inputs.deployment_id,
                blocks=(inputs.block(plane=FactPlane.OBSERVATION),),
                facts=(fact,),
            ):
                pytest.fail("obsolete subject admitted")
        with temporal_write(
            connection=connection,
            deployment_id=inputs.deployment_id,
            blocks=(inputs.block(plane=FactPlane.OBSERVATION, other=True),),
            facts=(fact,),
        ) as session:
            assert session.state(fact=fact) == seeded


@pytest.mark.parametrize("plane", list(FactPlane))
def test_unselected_correction_date_is_refused(
    database_engine: Engine, inputs: Inputs, plane: FactPlane
) -> None:
    """Even plausible earlier dates need an exact candidate from current linked evidence."""
    fact, seeded = _seed(engine=database_engine, inputs=inputs, plane=plane)
    block = inputs.block(plane=plane)
    operation_id = uuid4()
    corrected = correct_window(
        state=seeded, start=_EARLIER, end=None, operation_id=operation_id, neighbours=()
    ).state
    with database_engine.begin() as connection:
        with pytest.raises(
            TemporalWriteConflict, match="not a current supporting endpoint"
        ):
            with temporal_write(
                connection=connection,
                deployment_id=inputs.deployment_id,
                blocks=(block,),
                facts=(fact,),
            ) as session:
                session.apply(
                    effect=_effect(
                        connection=connection,
                        inputs=inputs,
                        fact=fact,
                        kind=TemporalOperationKind.CORRECTION,
                        before=seeded,
                        after=corrected,
                        operation_id=operation_id,
                        candidate_claim_id=inputs.claim_id,
                    ),
                    written_blocks=frozenset(
                        (
                            temporal_block_key(
                                deployment_id=inputs.deployment_id, block=block
                            ),
                        )
                    ),
                )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM temporal_operations WHERE operation_id = :id"
                ),
                {"id": operation_id},
            ).scalar_one()
            == 0
        )


def test_prepared_currency_is_revalidated(
    database_engine: Engine, inputs: Inputs
) -> None:
    """A prepared answer cannot acquire authority after its testimony was withdrawn."""
    fact, seeded = _seed(
        engine=database_engine, inputs=inputs, plane=FactPlane.OBSERVATION
    )
    block = inputs.block(plane=fact.plane)
    operation_id = uuid4()
    capped = cap_fact(state=seeded, boundary=_END, operation_id=operation_id).state
    with database_engine.connect() as connection:
        prepared = _effect(
            connection=connection,
            inputs=inputs,
            fact=fact,
            kind=TemporalOperationKind.CAP,
            before=seeded,
            after=capped,
            operation_id=operation_id,
        )
    with database_engine.begin() as connection:
        connection.execute(
            text("UPDATE claims SET is_current_testimony = false WHERE claim_id = :id"),
            {"id": inputs.claim_id},
        )
    with database_engine.begin() as connection:
        with pytest.raises(TemporalWriteConflict, match="prepared testimony changed"):
            with temporal_write(
                connection=connection,
                deployment_id=inputs.deployment_id,
                blocks=(block,),
                facts=(fact,),
            ) as session:
                session.apply(
                    effect=prepared,
                    written_blocks=frozenset(
                        (
                            temporal_block_key(
                                deployment_id=inputs.deployment_id, block=block
                            ),
                        )
                    ),
                )


def test_opposite_source_and_block_orders_do_not_deadlock(
    database_engine: Engine, inputs: Inputs
) -> None:
    """Actual concurrent callers acquire blocks and mixed cache keys in the same order."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)
    blocks = (
        inputs.block(plane=FactPlane.RELATION),
        inputs.block(plane=FactPlane.OBSERVATION),
    )
    sources = tuple(
        TemporalSourceRef(
            kind=TemporalSourceKind.ENTITY, source_id=entity, key=str(entity)
        )
        for entity in (inputs.subject_id, inputs.other_id)
    )

    def run(*, reverse: bool) -> None:
        """Start contenders together and retain locks briefly to force contention."""
        with database_engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '3s'"))
            barrier.wait(timeout=3)
            with temporal_write(
                connection=connection,
                deployment_id=inputs.deployment_id,
                blocks=tuple(reversed(blocks)) if reverse else blocks,
                facts=(),
                sources=tuple(reversed(sources)) if reverse else sources,
            ) as session:
                assert all(
                    session.source_revision(source=source) == 0 for source in sources
                )
                connection.execute(text("SELECT pg_sleep(0.05)"))

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run, reverse=reverse) for reverse in (False, True)]
        for future in futures:
            future.result(timeout=10)


def test_session_timezone_does_not_change_state_or_prepared_fingerprint(
    database_engine: Engine, inputs: Inputs
) -> None:
    """PostgreSQL session display zones cannot invalidate identical source instants."""
    fact, seeded = _seed(
        engine=database_engine, inputs=inputs, plane=FactPlane.OBSERVATION
    )
    block = inputs.block(plane=fact.plane)
    with database_engine.begin() as connection:
        connection.execute(text("SET LOCAL TIME ZONE 'Europe/Prague'"))
        prepared = load_temporal_evidence(
            connection=connection,
            deployment_id=inputs.deployment_id,
            claim_id=inputs.claim_id,
            role="support",
        )
        connection.execute(text("SET LOCAL TIME ZONE 'America/New_York'"))
        assert (
            load_temporal_evidence(
                connection=connection,
                deployment_id=inputs.deployment_id,
                claim_id=inputs.claim_id,
                role="support",
            )
            == prepared
        )
        with temporal_write(
            connection=connection,
            deployment_id=inputs.deployment_id,
            blocks=(block,),
            facts=(fact,),
        ) as session:
            assert session.state(fact=fact) == seeded


def test_read_only_witness_advances_order_without_invalidating_preparation(
    database_engine: Engine, inputs: Inputs
) -> None:
    """Read footprints participate in replay order while truth revisions remain unchanged."""
    fact, seeded = _seed(
        engine=database_engine, inputs=inputs, plane=FactPlane.OBSERVATION
    )
    block = inputs.block(plane=fact.plane)
    with database_engine.begin() as connection:
        with temporal_write(
            connection=connection,
            deployment_id=inputs.deployment_id,
            blocks=(block,),
            facts=(fact,),
        ) as session:
            prepared = session.block_states
            session.apply(
                effect=_effect(
                    connection=connection,
                    inputs=inputs,
                    fact=fact,
                    kind=TemporalOperationKind.EVIDENCE,
                    before=seeded,
                    after=seeded,
                    result=TemporalResult.NOOP,
                    operation_id=uuid4(),
                ),
                written_blocks=frozenset(),
            )
            session.require_revisions(prepared=prepared)
            assert session.block_states[0].sequence == prepared[0].sequence + 1
            assert session.block_states[0].revision == prepared[0].revision
            with pytest.raises(TemporalWriteConflict, match="footprint differs"):
                session.require_revisions(prepared=())
