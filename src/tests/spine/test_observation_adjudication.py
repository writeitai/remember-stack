"""WP-2.5 acceptance: the D43 worked examples, the fail-safe contract, and
the contradiction eval gate (the shipping criterion)."""

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.eval import run_contradiction_suite
from rememberstack.eval import seed_contradiction_cases
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import ObservationAssertion
from rememberstack.model import P1ChunkText
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import OBSERVATION_ADJUDICATOR_VERSION
from rememberstack.spine import ObservationAdjudicator
from rememberstack.spine import ObservationSettings
from rememberstack.spine.settings import load_database_settings

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("f0000000-0000-0000-0000-000000000001")


def _semantic_router(prompt: str, type_name: str) -> dict[str, object]:
    """A deterministic D43 stand-in judging the worked examples correctly:
    same property + same period + different value -> contradict; changing
    state (no period marker on both) with different value -> supersede;
    same value -> evidence; different property/period -> new."""
    if type_name != "ObservationVerdict":
        raise AssertionError(f"unexpected generate call: {type_name}")
    lines = [
        line for line in prompt.splitlines() if line.startswith(("EXISTING:", "NEW:"))
    ]
    existing, new = (line.split(":", 1)[1].strip().strip("'\"") for line in lines)

    def parse(statement: str) -> tuple[str, str, str]:
        period = ""
        for marker in ("FY2023", "Q1-2023", "year-end 2023"):
            if marker in statement:
                period = marker
        prop = (
            "revenue"
            if "revenue" in statement
            else (
                "headcount"
                if "headcount" in statement
                else ("profit" if "profit" in statement else "other")
            )
        )
        value = statement.rsplit(" ", 1)[-1].rstrip(".")
        return prop, period, value

    p1, per1, v1 = parse(existing)
    p2, per2, v2 = parse(new)
    if p1 != p2 or (per1 and per2 and per1 != per2):
        return {"outcome": "new", "confidence": 0.9}
    if v1 == v2:
        return {"outcome": "evidence", "confidence": 0.9}
    if per1 and per2 and per1 == per2:
        return {"outcome": "contradict", "confidence": 0.9}
    return {"outcome": "supersede", "confidence": 0.9, "rationale": "state moved"}


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL integration engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real PostgreSQL D43 proofs"
        )
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def bootstrapped_deployment(database_engine: Engine) -> None:
    """A fresh deployment per proof."""
    with database_engine.begin() as connection:
        for table in (
            "observation_adjudications",
            "observation_evidence",
            "observations",
            "claims",
        ):
            connection.execute(statement=text(f"TRUNCATE TABLE {table} CASCADE"))
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="d43-test",
            name="Observation adjudication proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


def _adjudicator(
    *, engine: Engine, router=None, **overrides: object
) -> tuple[ObservationAdjudicator, FakeModelProvider]:
    """One composed adjudicator with the semantic router by default."""
    provider = FakeModelProvider(generate_router=router or _semantic_router)
    return (
        ObservationAdjudicator(
            engine=engine,
            model_provider=provider,
            settings=ObservationSettings(**overrides),  # type: ignore[arg-type]
        ),
        provider,
    )


def _entity(*, engine: Engine) -> UUID:
    """One Organization entity."""
    entity_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id,"
                " canonical_name, normalized_name)"
                " VALUES (:e, :d, 'Acme', 'acme')"
            ),
            {"e": entity_id, "d": _DEPLOYMENT_ID},
        )
    return entity_id


def _add(
    *,
    adjudicator: ObservationAdjudicator,
    entity: UUID,
    statement: str,
    engine: Engine | None = None,
    asserted_at: str | None = None,
    event_window: tuple[str, str] | None = None,
    doc_id: UUID | None = None,
) -> UUID:
    """One observation through the cascade with a fresh claim.

    With `asserted_at`, a real dated claim row backs the testimony (the D41
    seed the boundary math reads); without it, the testimony is undated.
    With `event_window`, the claim is a datable EVENT resolved to that
    day-precision `[from, until]` window (`claim_valid_kind = 'event_time'`),
    the input the D106 temporal-compatibility rung reads.
    """
    claim_id = uuid4()
    doc_id = doc_id or uuid4()
    if asserted_at is not None and engine is not None:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO claims (claim_id, deployment_id, doc_id,"
                    " chunk_id, claim_text, source_span, char_start, char_end,"
                    " anchor_ok, window_membership_ok, extractor_version,"
                    " asserted_at, claim_valid_kind, claim_valid_precision,"
                    " claim_valid_from, claim_valid_until)"
                    " VALUES (:c, :d, :doc, :ch, :s, :s, 0, 1, true, true,"
                    " 'test', CAST(:a AS timestamptz),"
                    " CAST(:kind AS claim_valid_kind),"
                    " CAST(:precision AS claim_valid_precision),"
                    " CAST(:vf AS timestamptz), CAST(:vu AS timestamptz))"
                ),
                {
                    "c": claim_id,
                    "d": _DEPLOYMENT_ID,
                    "doc": doc_id,
                    "ch": uuid4(),
                    "s": statement,
                    "a": asserted_at,
                    "kind": None if event_window is None else "event_time",
                    "precision": "unknown" if event_window is None else "day",
                    "vf": None if event_window is None else event_window[0],
                    "vu": None if event_window is None else event_window[1],
                },
            )
    return adjudicator.add_observation(
        deployment_id=_DEPLOYMENT_ID,
        subject_entity_id=entity,
        statement=statement,
        claim_id=claim_id,
        doc_id=doc_id,
    )


def _collapse_happy_router(prompt: str, type_name: str) -> dict[str, object]:
    """The failure the D106 rung exists to stop: a small model that reads
    "won a tournament last week" twice and calls the second a re-assertion.

    It answers `evidence` for any pair about the same recurring activity —
    exactly what the conv-42 run's adjudicator did to five of seven wins —
    so a test passes only when the deterministic rung keeps it from mattering.
    """
    if type_name != "ObservationVerdict":
        raise AssertionError(f"unexpected generate call: {type_name}")
    lower = prompt.lower()
    if "tournament" in lower or "participa" in lower or "competing" in lower:
        return {"outcome": "evidence", "confidence": 0.95}
    return {"outcome": "new", "confidence": 0.9}


def _observations(*, engine: Engine, entity: UUID) -> list[dict[str, object]]:
    """The entity's live observations, oldest window first."""
    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT observation_id, statement, valid_from, evidence_count"
                    " FROM observations"
                    " WHERE subject_entity_id = :e AND invalidated_at IS NULL"
                    " ORDER BY valid_from NULLS LAST, statement"
                ),
                {"e": entity},
            ).mappings()
        ]


def _verdict_calls(provider: FakeModelProvider) -> int:
    """How many verdict prompts the ladder actually bought."""
    return sum(1 for prompt in provider.generated_prompts if "EXISTING:" in prompt)


# Seven distinct wins as conv-42 reported them: same shape, months apart.
_SEVEN_WINS: tuple[tuple[str, str, str], ...] = (
    (
        "Nate said he won his first video game tournament last week.",
        "2022-01-21T19:31:00Z",
        "2022-01-14",
    ),
    (
        "Nate said that Nate won Nate's second tournament last week.",
        "2022-05-02T11:54:00Z",
        "2022-04-25",
    ),
    (
        "Nate just won another regional video game tournament last week.",
        "2022-06-03T17:44:00Z",
        "2022-05-27",
    ),
    (
        "Nate won Nate's fourth video game tournament on Friday.",
        "2022-07-10T14:34:00Z",
        "2022-07-08",
    ),
    (
        "Nate won an international tournament yesterday.",
        "2022-08-22T10:57:00Z",
        "2022-08-21",
    ),
    (
        "Nate said that Nate won a really big video game tournament last week.",
        "2022-10-06T11:15:00Z",
        "2022-09-29",
    ),
    (
        "Nate won the final of a big Valorant tournament last Saturday.",
        "2022-11-07T20:10:00Z",
        "2022-11-05",
    ),
)


def test_d90_staggered_late_arrival_resplit_shapes(database_engine: Engine) -> None:
    """D90 §5.5.3: A{t1,t3} first then B{t2} yields A[t1,t2), B[t2,t3), A[t3,∞)."""
    adjudicator, _provider = _adjudicator(engine=database_engine)
    acme = _entity(engine=database_engine)
    _add(
        adjudicator=adjudicator,
        entity=acme,
        statement="Acme's headcount is 500",
        engine=database_engine,
        asserted_at="2026-01-01T00:00:00Z",
    )
    _add(
        adjudicator=adjudicator,
        entity=acme,
        statement="Acme's headcount is 500",
        engine=database_engine,
        asserted_at="2026-03-01T00:00:00Z",
    )
    _add(
        adjudicator=adjudicator,
        entity=acme,
        statement="Acme's headcount is 600",
        engine=database_engine,
        asserted_at="2026-02-01T00:00:00Z",
    )
    with database_engine.connect() as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT statement, valid_from, valid_until"
                    " FROM observations"
                    " WHERE subject_entity_id = :e"
                    " ORDER BY valid_from NULLS LAST, statement"
                ),
                {"e": acme},
            ).mappings()
        ]
    assert len(rows) == 3
    assert rows[0]["statement"] == "Acme's headcount is 500"
    assert rows[0]["valid_from"] is not None and rows[0]["valid_from"].month == 1
    assert rows[0]["valid_until"] is not None and rows[0]["valid_until"].month == 2
    assert rows[1]["statement"] == "Acme's headcount is 600"
    assert rows[1]["valid_from"] is not None and rows[1]["valid_from"].month == 2
    assert rows[1]["valid_until"] is not None and rows[1]["valid_until"].month == 3
    assert rows[2]["statement"] == "Acme's headcount is 500"
    assert rows[2]["valid_from"] is not None and rows[2]["valid_from"].month == 3
    assert rows[2]["valid_until"] is None


def test_d90_copresent_global_order_shapes(database_engine: Engine) -> None:
    """D90 §5.5.1: co-present t1:A, t2:B, t3:A in one batch match source order."""
    adjudicator, _provider = _adjudicator(engine=database_engine)
    acme = _entity(engine=database_engine)
    claim_a1, claim_b, claim_a3 = uuid4(), uuid4(), uuid4()
    doc_id = uuid4()
    with database_engine.begin() as connection:
        for claim_id, statement, asserted_at in (
            (claim_a1, "Acme's headcount is 500", "2026-01-01T00:00:00Z"),
            (claim_b, "Acme's headcount is 600", "2026-02-01T00:00:00Z"),
            (claim_a3, "Acme's headcount is 500", "2026-03-01T00:00:00Z"),
        ):
            connection.execute(
                text(
                    "INSERT INTO claims (claim_id, deployment_id, doc_id,"
                    " chunk_id, claim_text, source_span, char_start, char_end,"
                    " anchor_ok, window_membership_ok, extractor_version,"
                    " asserted_at)"
                    " VALUES (:c, :d, :doc, :ch, :s, :s, 0, 1, true, true,"
                    " 'test', CAST(:a AS timestamptz))"
                ),
                {
                    "c": claim_id,
                    "d": _DEPLOYMENT_ID,
                    "doc": doc_id,
                    "ch": uuid4(),
                    "s": statement,
                    "a": asserted_at,
                },
            )
    adjudicator.add_observations(
        deployment_id=_DEPLOYMENT_ID,
        subject_entity_id=acme,
        assertions=(
            ObservationAssertion(
                statement="Acme's headcount is 500", claim_id=claim_a1, doc_id=doc_id
            ),
            ObservationAssertion(
                statement="Acme's headcount is 600", claim_id=claim_b, doc_id=doc_id
            ),
            ObservationAssertion(
                statement="Acme's headcount is 500", claim_id=claim_a3, doc_id=doc_id
            ),
        ),
    )
    with database_engine.connect() as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT statement, valid_from, valid_until"
                    " FROM observations"
                    " WHERE subject_entity_id = :e"
                    " ORDER BY valid_from NULLS LAST, statement"
                ),
                {"e": acme},
            ).mappings()
        ]
    assert len(rows) == 3
    assert rows[0]["statement"] == "Acme's headcount is 500"
    assert rows[0]["valid_until"] is not None and rows[0]["valid_until"].month == 2
    assert rows[1]["statement"] == "Acme's headcount is 600"
    assert rows[1]["valid_until"] is not None and rows[1]["valid_until"].month == 3
    assert rows[2]["statement"] == "Acme's headcount is 500"
    assert rows[2]["valid_until"] is None


def test_headcount_supersession_caps_and_preserves_the_time_slice(
    database_engine: Engine,
) -> None:
    """The D43 worked example: 500 -> 600 caps O1 and keeps its statement —
    an observation is a time-slice, never an in-place edit."""
    adjudicator, provider = _adjudicator(engine=database_engine)
    acme = _entity(engine=database_engine)
    first = _add(
        adjudicator=adjudicator, entity=acme, statement="Acme's headcount is 500"
    )
    assert provider.generated_prompts == []  # first mention: zero LLM
    second = _add(
        adjudicator=adjudicator, entity=acme, statement="Acme's headcount is 600"
    )
    with database_engine.connect() as connection:
        rows = {
            row["observation_id"]: dict(row)
            for row in connection.execute(
                text(
                    "SELECT observation_id, statement, valid_until, invalidated_at"
                    " FROM observations"
                )
            ).mappings()
        }
        cap_reason = connection.execute(
            text(
                "SELECT count(*) FROM observation_adjudications"
                " WHERE observation_id = :o AND outcome = 'supersede'"
            ),
            {"o": first},
        ).scalar_one()
    assert rows[first]["statement"] == "Acme's headcount is 500"  # unchanged
    assert rows[first]["valid_until"] is not None  # capped
    assert rows[first]["invalidated_at"] is None  # ended, not wrong
    assert rows[second]["valid_until"] is None  # the current slice
    assert cap_reason == 1  # every cap writes its reason row


def test_fixed_period_figures_contradict_and_both_stand(
    database_engine: Engine,
) -> None:
    """The no-cap rule: conflicting FY2023 revenue figures are NEVER
    superseded — both stand with a shared contradiction_group."""
    adjudicator, _ = _adjudicator(engine=database_engine)
    acme = _entity(engine=database_engine)
    first = _add(
        adjudicator=adjudicator, entity=acme, statement="Acme's FY2023 revenue was $5M"
    )
    second = _add(
        adjudicator=adjudicator, entity=acme, statement="Acme's FY2023 revenue was $7M"
    )
    with database_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT observation_id, valid_until, contradiction_group"
                    " FROM observations"
                )
            )
            .mappings()
            .all()
        )
    by_id = {row["observation_id"]: dict(row) for row in rows}
    assert by_id[first]["valid_until"] is None  # NOT capped (no-cap rule)
    assert by_id[second]["valid_until"] is None
    groups = {row["contradiction_group"] for row in rows}
    assert len(groups) == 1 and None not in groups  # shared, both stand


def test_entity_batches_preserve_supersession_and_contradiction_sequence(
    database_engine: Engine,
) -> None:
    """Later assertions see earlier in-transaction caps and group updates."""
    adjudicator, _ = _adjudicator(engine=database_engine)
    headcount_entity = _entity(engine=database_engine)
    doc_id = uuid4()
    headcount_ids = adjudicator.add_observations(
        deployment_id=_DEPLOYMENT_ID,
        subject_entity_id=headcount_entity,
        assertions=tuple(
            ObservationAssertion(
                statement=f"Acme's headcount is {value}",
                claim_id=uuid4(),
                doc_id=doc_id,
            )
            for value in (500, 600, 700)
        ),
    )
    contradiction_entity = _entity(engine=database_engine)
    contradiction_ids = adjudicator.add_observations(
        deployment_id=_DEPLOYMENT_ID,
        subject_entity_id=contradiction_entity,
        assertions=tuple(
            ObservationAssertion(
                statement=f"Acme's FY2023 revenue was ${value}M",
                claim_id=uuid4(),
                doc_id=doc_id,
            )
            for value in (5, 7, 9)
        ),
    )

    with database_engine.connect() as connection:
        headcount_rows = (
            connection.execute(
                text(
                    "SELECT observation_id, valid_until FROM observations"
                    " WHERE observation_id = ANY(:ids)"
                ),
                {"ids": list(headcount_ids)},
            )
            .mappings()
            .all()
        )
        contradiction_groups = tuple(
            connection.execute(
                text(
                    "SELECT contradiction_group FROM observations"
                    " WHERE observation_id = ANY(:ids)"
                ),
                {"ids": list(contradiction_ids)},
            ).scalars()
        )
    windows = {row["observation_id"]: row["valid_until"] for row in headcount_rows}
    assert windows[headcount_ids[0]] is not None
    assert windows[headcount_ids[1]] is not None
    assert windows[headcount_ids[2]] is None
    assert len(set(contradiction_groups)) == 1
    assert contradiction_groups[0] is not None


def test_corpus_redundancy_collapses_with_zero_llm(database_engine: Engine) -> None:
    """The biggest saver: an exact re-assertion adds evidence, no new row,
    no model call."""
    adjudicator, provider = _adjudicator(engine=database_engine)
    acme = _entity(engine=database_engine)
    first = _add(
        adjudicator=adjudicator, entity=acme, statement="Acme is based in Brno"
    )
    second = _add(
        adjudicator=adjudicator, entity=acme, statement="Acme is based in Brno"
    )
    assert second == first
    assert provider.generated_prompts == []
    with database_engine.connect() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM observations")
        ).scalar_one()
        links = connection.execute(
            text("SELECT count(*) FROM observation_evidence")
        ).scalar_one()
    assert count == 1
    assert links == 2


def test_supersede_below_margin_is_coerced_to_coexist(database_engine: Engine) -> None:
    """THE BINDING CONTRACT: below the supersede margin the cap is refused —
    the failure mode is a duplicate, never an overwrite."""

    def low_margin_router(prompt: str, type_name: str) -> dict[str, object]:
        # above the 0.75 ladder floor (no frontier re-ask), below the 0.8
        # supersede margin; rationale present so ONLY the margin coerces
        return {
            "outcome": "supersede",
            "confidence": 0.79,
            "rationale": "probably moved on",
        }

    adjudicator, _ = _adjudicator(engine=database_engine, router=low_margin_router)
    acme = _entity(engine=database_engine)
    first = _add(
        adjudicator=adjudicator, entity=acme, statement="Acme's headcount is 500"
    )
    second = _add(
        adjudicator=adjudicator, entity=acme, statement="Acme's headcount is 600"
    )
    with database_engine.connect() as connection:
        capped = connection.execute(
            text("SELECT count(*) FROM observations WHERE valid_until IS NOT NULL")
        ).scalar_one()
        coerced = connection.execute(
            text(
                "SELECT features->>'reason' FROM observation_adjudications"
                " WHERE observation_id = :o"
            ),
            {"o": second},
        ).scalar_one()
    assert capped == 0  # no silent caps — nothing was capped at all
    assert first != second  # both rows stand (the duplicate failure mode)
    assert "below margin" in str(coerced)


def test_contradiction_eval_gate_records_and_blocks(database_engine: Engine) -> None:
    """The D43 SHIPPING criterion: contradiction P/R over the golden set,
    green with the semantic judge, failing with a judge that never flags."""
    seed_contradiction_cases(engine=database_engine, deployment_id=_DEPLOYMENT_ID)
    adjudicator, _ = _adjudicator(engine=database_engine)
    report = run_contradiction_suite(
        engine=database_engine,
        adjudicator=adjudicator,
        deployment_id=_DEPLOYMENT_ID,
        component_version=OBSERVATION_ADJUDICATOR_VERSION,
    )
    assert report["passed"], report
    assert report["precision"] == 1.0
    assert report["recall"] == 1.0

    def never_flags(prompt: str, type_name: str) -> dict[str, object]:
        return {"outcome": "new", "confidence": 0.9}

    blind, _ = _adjudicator(engine=database_engine, router=never_flags)
    regression = run_contradiction_suite(
        engine=database_engine,
        adjudicator=blind,
        deployment_id=_DEPLOYMENT_ID,
        component_version=OBSERVATION_ADJUDICATOR_VERSION,
    )
    assert not regression["passed"]
    with database_engine.connect() as connection:
        runs = connection.execute(
            text("SELECT count(*) FROM eval_runs WHERE suite = 'contradiction'")
        ).scalar_one()
    assert runs == 2


def test_s9_headcount_as_of_mid_window(database_engine: Engine) -> None:
    """S9: after the 500 -> 600 supersession, an as-of read inside O1's
    window answers 500 (the capped time-slice)."""
    from rememberstack.surfaces import QueryEngine

    class _NullSearch:
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

    adjudicator, provider = _adjudicator(engine=database_engine)
    acme = _entity(engine=database_engine)
    _add(
        adjudicator=adjudicator,
        entity=acme,
        statement="Acme's headcount is 500",
        engine=database_engine,
        asserted_at="2023-12-31+00",
    )
    _add(
        adjudicator=adjudicator,
        entity=acme,
        statement="Acme's headcount is 600",
        engine=database_engine,
        asserted_at="2025-01-15+00",
    )
    with database_engine.connect() as connection:
        cap = connection.execute(
            text("SELECT valid_until FROM observations WHERE valid_until IS NOT NULL")
        ).scalar_one()
    mid_window = cap - timedelta(days=200)  # mid-2024: inside O1's window

    engine = QueryEngine(
        engine=database_engine,
        search_index=_NullSearch(),
        model_provider=provider,
        embedding_model="qwen/qwen3-embedding-8b",
    )
    current = engine.lookup_observations(deployment_id=_DEPLOYMENT_ID, entity_id=acme)
    assert [fact.label for fact in current.facts] == ["Acme's headcount is 600"]
    era = engine.lookup_observations(
        deployment_id=_DEPLOYMENT_ID, entity_id=acme, valid_at=mid_window
    )
    # EXACTLY the capped slice (Codex review): the successor's valid_from is
    # the cap boundary, so 600 is not yet valid inside O1's window.
    assert [fact.label for fact in era.facts] == ["Acme's headcount is 500"]


def test_supersede_without_rationale_is_an_incomplete_comparison(
    database_engine: Engine,
) -> None:
    """Codex review: an undocumented cap is refused — a supersede verdict
    with no rationale coerces to coexist (no silent caps, ever)."""

    def no_rationale_router(prompt: str, type_name: str) -> dict[str, object]:
        return {"outcome": "supersede", "confidence": 0.95}

    adjudicator, _ = _adjudicator(engine=database_engine, router=no_rationale_router)
    acme = _entity(engine=database_engine)
    _add(adjudicator=adjudicator, entity=acme, statement="Acme's headcount is 500")
    second = _add(
        adjudicator=adjudicator, entity=acme, statement="Acme's headcount is 600"
    )
    with database_engine.connect() as connection:
        capped = connection.execute(
            text("SELECT count(*) FROM observations WHERE valid_until IS NOT NULL")
        ).scalar_one()
        reason = connection.execute(
            text(
                "SELECT features->>'reason' FROM observation_adjudications"
                " WHERE observation_id = :o"
            ),
            {"o": second},
        ).scalar_one()
    assert capped == 0
    assert "without rationale" in str(reason)


def test_one_sided_golden_set_never_passes_the_gate(database_engine: Engine) -> None:
    """Codex review: a golden set with only positives (or only negatives)
    cannot measure false positives — the gate blocks it."""
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO canary_cases (canary_id, deployment_id, suite,"
                " description, input, expected) VALUES (:i, :d, 'contradiction',"
                " 'only positive', CAST(:inp AS jsonb), CAST(:exp AS jsonb))"
            ),
            {
                "i": uuid4(),
                "d": _DEPLOYMENT_ID,
                "inp": '{"existing": "Acme FY2023 revenue was $5M",'
                ' "new": "Acme FY2023 revenue was $9M"}',
                "exp": '{"contradiction": true}',
            },
        )
    adjudicator, _ = _adjudicator(engine=database_engine)
    report = run_contradiction_suite(
        engine=database_engine,
        adjudicator=adjudicator,
        deployment_id=_DEPLOYMENT_ID,
        component_version=OBSERVATION_ADJUDICATOR_VERSION,
    )
    assert not report["passed"]  # one-sided: unmeasurable, never approved


def test_stance_content_never_becomes_a_fact(database_engine: Engine) -> None:
    """WP-2.7 / D59 guard: a stance observation anchors on the HOLDER only —
    nothing about the stance's content object is ever derived."""
    adjudicator, _ = _adjudicator(engine=database_engine)
    team = _entity(engine=database_engine)
    _add(
        adjudicator=adjudicator,
        entity=team,
        statement="The team considers Project Atlas a runaway success",
    )
    with database_engine.connect() as connection:
        subjects = (
            connection.execute(
                text("SELECT DISTINCT subject_entity_id FROM observations")
            )
            .scalars()
            .all()
        )
        relations = connection.execute(
            text("SELECT count(*) FROM relations")
        ).scalar_one()
    assert subjects == [team]  # anchored on the holder, nowhere else
    assert relations == 0  # no fact about Atlas was derived


def test_d106_seven_dated_wins_survive_as_seven_facts(database_engine: Engine) -> None:
    """The conv-42 counting case: seven same-shaped wins on seven dates must
    stay seven observations, and the rung must not even buy a verdict for
    pairs whose event windows are disjoint (they are different events)."""
    adjudicator, provider = _adjudicator(
        engine=database_engine, router=_collapse_happy_router
    )
    nate = _entity(engine=database_engine)
    for statement, asserted_at, day in _SEVEN_WINS:
        _add(
            adjudicator=adjudicator,
            entity=nate,
            statement=statement,
            engine=database_engine,
            asserted_at=asserted_at,
            event_window=(day, day),
        )
    rows = _observations(engine=database_engine, entity=nate)
    assert [row["statement"] for row in rows] == [win[0] for win in _SEVEN_WINS]
    assert all(row["evidence_count"] == 1 for row in rows)
    assert _verdict_calls(provider) == 0  # every pair was gated: no LLM spend
    with database_engine.connect() as connection:
        noops = connection.execute(
            text(
                "SELECT count(*) FROM observation_adjudications a"
                " JOIN observations o ON o.observation_id = a.observation_id"
                " WHERE o.subject_entity_id = :e AND a.outcome = 'noop'"
            ),
            {"e": nate},
        ).scalar_one()
    assert noops == 0


def test_d106_arrival_order_does_not_change_the_count(database_engine: Engine) -> None:
    """Reverse (and shuffled) arrival yields the same seven facts."""
    adjudicator, _provider = _adjudicator(
        engine=database_engine, router=_collapse_happy_router
    )
    nate = _entity(engine=database_engine)
    shuffled = (
        _SEVEN_WINS[6],
        _SEVEN_WINS[2],
        _SEVEN_WINS[0],
        _SEVEN_WINS[4],
        _SEVEN_WINS[1],
        _SEVEN_WINS[5],
        _SEVEN_WINS[3],
    )
    for statement, asserted_at, day in shuffled:
        _add(
            adjudicator=adjudicator,
            entity=nate,
            statement=statement,
            engine=database_engine,
            asserted_at=asserted_at,
            event_window=(day, day),
        )
    rows = _observations(engine=database_engine, entity=nate)
    assert len(rows) == 7
    assert [row["statement"] for row in rows] == [win[0] for win in _SEVEN_WINS]


def test_d106_same_event_mentioned_twice_still_collapses(
    database_engine: Engine,
) -> None:
    """The rung is about DIFFERENT events: a re-mention of one event (same
    resolved date) still collapses to one observation with two lineages."""
    adjudicator, provider = _adjudicator(
        engine=database_engine, router=_collapse_happy_router
    )
    nate = _entity(engine=database_engine)
    statement = "Nate won an international tournament yesterday."
    for asserted_at in ("2022-08-22T10:57:00Z", "2022-08-22T18:00:00Z"):
        _add(
            adjudicator=adjudicator,
            entity=nate,
            statement=statement,
            engine=database_engine,
            asserted_at=asserted_at,
            event_window=("2022-08-21", "2022-08-21"),
        )
    rows = _observations(engine=database_engine, entity=nate)
    assert len(rows) == 1 and rows[0]["evidence_count"] == 2
    assert _verdict_calls(provider) == 0  # exact re-assertion: zero LLM


def test_d106_identical_words_about_two_dates_are_two_events(
    database_engine: Engine,
) -> None:
    """ "won a tournament last week" in January and again in October is two
    wins even though the strings are byte-identical."""
    adjudicator, provider = _adjudicator(
        engine=database_engine, router=_collapse_happy_router
    )
    nate = _entity(engine=database_engine)
    statement = "Nate said that Nate won a video game tournament last week."
    for asserted_at, day in (
        ("2022-01-21T19:31:00Z", "2022-01-14"),
        ("2022-10-06T11:15:00Z", "2022-09-29"),
    ):
        _add(
            adjudicator=adjudicator,
            entity=nate,
            statement=statement,
            engine=database_engine,
            asserted_at=asserted_at,
            event_window=(day, day),
        )
    rows = _observations(engine=database_engine, entity=nate)
    assert len(rows) == 2 and all(row["evidence_count"] == 1 for row in rows)
    assert _verdict_calls(provider) == 0


def test_d106_a_vague_summary_never_absorbs_a_dated_event(
    database_engine: Engine,
) -> None:
    """ "has been winning a few tournaments" (undated) must not swallow the
    later dated international win as evidence, whatever the model says —
    and the verdict prompt must have shown the model both timelines."""
    adjudicator, provider = _adjudicator(
        engine=database_engine, router=_collapse_happy_router
    )
    nate = _entity(engine=database_engine)
    _add(
        adjudicator=adjudicator,
        entity=nate,
        statement="Nate said that Nate has been winning a few gaming tournaments.",
        engine=database_engine,
        asserted_at="2022-05-02T11:54:00Z",
    )
    dated = _add(
        adjudicator=adjudicator,
        entity=nate,
        statement="Nate won an international tournament yesterday.",
        engine=database_engine,
        asserted_at="2022-08-22T10:57:00Z",
        event_window=("2022-08-21", "2022-08-21"),
    )
    rows = _observations(engine=database_engine, entity=nate)
    assert len(rows) == 2 and all(row["evidence_count"] == 1 for row in rows)
    assert _verdict_calls(provider) == 1  # mixed pair: judged, then coerced
    last_prompt = provider.generated_prompts[-1]
    assert "EXISTING TIME: asserted 2022-05-02; no resolved event date" in last_prompt
    assert "NEW TIME: asserted 2022-08-22; datable event resolved to 2022-08-21" in (
        last_prompt
    )
    with database_engine.connect() as connection:
        features = connection.execute(
            text(
                "SELECT features FROM observation_adjudications"
                " WHERE observation_id = :o AND outcome = 'add'"
            ),
            {"o": dated},
        ).scalar_one()
    assert features["temporal_gate"][0]["reason"].startswith("evidence coerced to new")


def test_d106_a_dated_event_never_absorbs_a_later_summary(
    database_engine: Engine,
) -> None:
    """The mirror image: a summary arriving after a dated event coexists."""
    adjudicator, _provider = _adjudicator(
        engine=database_engine, router=_collapse_happy_router
    )
    nate = _entity(engine=database_engine)
    _add(
        adjudicator=adjudicator,
        entity=nate,
        statement="Nate won an international tournament yesterday.",
        engine=database_engine,
        asserted_at="2022-08-22T10:57:00Z",
        event_window=("2022-08-21", "2022-08-21"),
    )
    _add(
        adjudicator=adjudicator,
        entity=nate,
        statement="Nate said that Nate has been winning a few gaming tournaments.",
        engine=database_engine,
        asserted_at="2022-10-06T11:15:00Z",
    )
    assert len(_observations(engine=database_engine, entity=nate)) == 2


def test_d106_boilerplate_state_never_absorbs_dated_participation(
    database_engine: Engine,
) -> None:
    """conv-42's "Nate is a participant." (document-header boilerplate, nine
    lineages) swallowed two real tournament entries. The boilerplate may
    still collapse onto itself; the dated events stay distinct facts."""
    adjudicator, _provider = _adjudicator(
        engine=database_engine, router=_collapse_happy_router
    )
    nate = _entity(engine=database_engine)
    for asserted_at in (
        "2022-01-21T19:31:00Z",
        "2022-02-07T09:00:00Z",
        "2022-03-01T09:00:00Z",
    ):
        _add(
            adjudicator=adjudicator,
            entity=nate,
            statement="Nate is a participant.",
            engine=database_engine,
            asserted_at=asserted_at,
        )
    for statement, asserted_at, day in (
        (
            "Nate said that Nate is currently participating in the video game tournament again.",
            "2022-03-24T15:00:00Z",
            "2022-03-24",
        ),
        (
            "Nate said that Nate tried playing in the local Street Fighter tournament this time.",
            "2022-04-25T15:00:00Z",
            "2022-04-25",
        ),
    ):
        _add(
            adjudicator=adjudicator,
            entity=nate,
            statement=statement,
            engine=database_engine,
            asserted_at=asserted_at,
            event_window=(day, day),
        )
    rows = _observations(engine=database_engine, entity=nate)
    assert len(rows) == 3
    boilerplate = next(
        row for row in rows if row["statement"] == "Nate is a participant."
    )
    assert boilerplate["evidence_count"] == 3
