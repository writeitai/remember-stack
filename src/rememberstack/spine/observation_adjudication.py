"""Legacy read-only contradiction diagnostic and retired writer compatibility.

Ordinary fact applications own identity and mutable windows. The bare statement
pair diagnostic remains for historical evaluation callers; it is not a write path.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Final
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
from sqlalchemy.engine import Engine

from rememberstack.model import ModelRequest
from rememberstack.model import ObservationAssertion
from rememberstack.model import ObservationOutcome
from rememberstack.model import ObservationVerdict
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.spine.rank_embed_cache import RankEmbedCache

OBSERVATION_ADJUDICATOR_VERSION: Final = (
    "obs-adjudicator-2026.09b:temp0-1:temporal-gate-1:canonical-bounds-1"
)
"""The observation adjudicator generation (D12; replayed on rebuild, D7).
07b pins temperature=0.0 — generation parameters are part of provenance.
09a (D106) adds the temporal-compatibility rung: two dated events with
disjoint resolved windows never collapse or supersede (they may only
contradict or stay distinct), a dated event never collapses as evidence onto
an undated statement (nor the reverse), identical text is collapsed only when
temporally compatible, open-ended windows stay unbounded, and the verdict
prompt shows both statements' said-on dates and is-about windows.
09b (D107 §5, WP-T.0a) compares canonical half-open bounds: a day covers
the whole calendar day, an instant is a non-empty point, adjacent units do
not overlap."""

_VERDICT_PROMPT: Final = """You adjudicate observations for a memory system.
Both statements are believed facts about the SAME entity:

EXISTING: {existing!r}
  said on: {existing_said_on}
  is about: {existing_about}
NEW: {new!r}
  said on: {new_said_on}
  is about: {new_about}

Two clocks are shown for each statement. "said on" is the source's own date —
when the document was written or the conversation took place — and is NOT
when the described thing happened. "is about" is the world-time the statement
refers to, resolved from the source's wording against its said-on date: "last
week" said on 2022-10-06 is about the week before that date, not the week
before 2022-01-21, so two statements can both say "last week" and be about
days months apart. When the source tied nothing to a date, "is about" says so.
When either statement was ingested is irrelevant here and is not shown.

Judge semantically (there are no typed columns — "FY2023" vs "fiscal 2023"
and "headcount" vs "staff count" are your equivalence calls):
- evidence: same property, same value, overlapping validity — the new
  statement re-asserts the existing one.
- supersede: same property, a CHANGING EFFECTIVE STATE (headcount, balance,
  status), and the value changed over time — the old window should cap.
  NEVER supersede a fixed-period measurement ("FY2023 revenue was $5M"): a
  figure does not stop being true at period-end.
- contradict: same property AND same reporting period, incompatible value —
  both must stand, surfaced together. (Different property, or different
  period, is NOT a contradiction.)
- new: a different property, period, or thing — no interaction.

Time is decisive for EVENTS (a win, a visit, a purchase, a meeting). Two
statements about datable events whose "is about" windows do NOT overlap are
two different occurrences — `new` — even when the wording is identical ("won
a tournament last week" said in January and again in October are two wins,
not one re-asserted). The one exception: when they plainly name the SAME
single occurrence and merely disagree about its date ("the Valorant final on
Friday" vs "the Valorant final on Saturday"), answer `contradict` so both
stand. Overlapping windows of different precision (a year-level claim and a
day-level one) may well be the same occurrence — judge by the wording. A
specific dated event is never `evidence` for a vaguer summary ("has won a
few tournaments"), and a summary never re-asserts a specific event — keep
both."""


class ObservationSettings(BaseSettings):
    """The observation adjudicator's ladder and gate bindings (D4/D43)."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_OBS_")

    small_model: str = Field(default="openai/gpt-5.6-luna")
    frontier_model: str = Field(default="openai/gpt-5.6-sol")
    embedding_model: str = Field(default="qwen/qwen3-embedding-8b")
    confidence_floor: float = Field(default=0.75, ge=0.0, le=1.0)
    supersede_margin: float = Field(default=0.8, ge=0.0, le=1.0)
    novelty_floor: float = Field(default=0.3, ge=-1.0, le=1.0)
    hub_top_k: int = Field(default=5, ge=1)


class ObservationAdjudicator:
    """Legacy pair-evaluation seam; all old mutation entrypoints reject calls."""

    def __init__(
        self,
        *,
        engine: Engine,
        model_provider: ModelProviderPort,
        settings: ObservationSettings,
        rank_embed_cache: RankEmbedCache | None = None,
    ) -> None:
        """Bind the adjudicator to the spine and its ladder/gate models."""
        self._engine = engine
        self._model_provider = model_provider
        self._settings = settings
        del rank_embed_cache

    def add_observation(
        self,
        *,
        deployment_id: UUID,
        subject_entity_id: UUID,
        statement: str,
        claim_id: UUID,
        doc_id: UUID,
        meter: CostMeterPort | None = None,
        call_key: str = "observation",
    ) -> UUID:
        """Reject the superseded direct writer; stage through D118 fact applications."""
        raise RuntimeError(
            "direct fact writes are retired; use normalized fact applications"
        )

    def add_observations(
        self,
        *,
        deployment_id: UUID,
        subject_entity_id: UUID,
        assertions: tuple[ObservationAssertion, ...],
        meter: CostMeterPort | None = None,
        call_key: str = "observation",
        clear_staging: dict[str, object] | None = None,
        clear_staging_rows: Sequence[dict[str, object]] | None = None,
    ) -> tuple[UUID, ...]:
        """Reject the superseded direct writer; stage through D118 fact applications."""
        raise RuntimeError(
            "direct fact writes are retired; use normalized fact applications"
        )

    def flush_entity_global_staging(
        self,
        *,
        deployment_id: UUID,
        subject_entity_id: UUID,
        meter: CostMeterPort | None = None,
        call_key: str = "observation_flush",
    ) -> tuple[UUID, ...]:
        """Reject the superseded direct writer; stage through D118 fact applications."""
        raise RuntimeError(
            "direct fact writes are retired; use normalized fact applications"
        )

    def judge_statements(
        self, *, existing: str, new: str
    ) -> tuple[ObservationOutcome, float]:
        """The bare pair-decision function — the D43 eval gate's surface."""
        verdict, method = self._ladder(
            existing=existing, new=new, existing_timing=_UNDATED, new_timing=_UNDATED
        )
        del method  # the gate grades outcomes; rungs are graded per-run cost
        return verdict.outcome, verdict.confidence

    def _ladder(
        self,
        *,
        existing: str,
        new: str,
        existing_timing: _ClaimTiming,
        new_timing: _ClaimTiming,
        meter: CostMeterPort | None = None,
        call_key: str = "observation:verdict",
    ) -> tuple[ObservationVerdict, str]:
        """Small-model verdict, escalating to frontier below the floor."""
        prompt = _VERDICT_PROMPT.format(
            existing=existing,
            new=new,
            existing_said_on=_render_said_on(existing_timing),
            existing_about=_render_about(existing_timing),
            new_said_on=_render_said_on(new_timing),
            new_about=_render_about(new_timing),
        )
        verdict_call = self._model_provider.generate(
            request=ModelRequest(
                model=self._settings.small_model, prompt=prompt, temperature=0.0
            ),
            response_type=ObservationVerdict,
        )
        if meter is not None:
            meter.record(
                call_key=f"{call_key}:small",
                tier="small_model",
                usage=verdict_call.usage,
            )
        verdict = verdict_call.output
        if verdict.confidence >= self._settings.confidence_floor:
            return verdict, "small_model"
        frontier_call = self._model_provider.generate(
            request=ModelRequest(
                model=self._settings.frontier_model, prompt=prompt, temperature=0.0
            ),
            response_type=ObservationVerdict,
        )
        if meter is not None:
            meter.record(
                call_key=f"{call_key}:frontier",
                tier="frontier_llm",
                usage=frontier_call.usage,
            )
        return frontier_call.output, "frontier_llm"


@dataclass(frozen=True)
class _ClaimTiming:
    """What the D41 record says about WHEN one piece of testimony applies.

    Windows are D107 §5 canonical half-open intervals: ``about_from`` is the
    inclusive start aligned to the claim's precision unit, ``about_until`` the
    EXCLUSIVE end (``None`` = open). Two clocks. ``asserted_at`` is when the SOURCE said it — the document's
    or conversation's own timestamp (the supersession boundary the layer
    already used). ``about_from``/``about_until`` is the world-time the
    statement is ABOUT, resolved by the extractor from the source's wording
    against that date, and ``about_kind`` is the D41 ``claim_valid_kind`` that
    says what sort of interval it is: ``event_time`` (a datable event — the
    only kind the temporal-compatibility rung acts on), ``measurement_period``
    / ``effective_period`` / ``proposition_validity`` (a figure or state tied
    to a span), or ``period`` for a block row whose supporting claims are
    aggregated. All three are ``None`` when the source tied nothing to a date.
    When the statement was ingested is deliberately not part of this record.
    """

    asserted_at: object = None
    about_kind: str | None = None
    about_from: object = None
    about_until: object = None

    @property
    def is_event(self) -> bool:
        """True when the testimony is a datable event with a resolved window."""
        return self.about_kind == "event_time" and self.about_from is not None

    @property
    def event_from(self) -> object:
        """The event window start, or ``None`` when this is not a dated event."""
        return self.about_from if self.is_event else None

    @property
    def event_until(self) -> object:
        """The event window end, or ``None`` when this is not a dated event."""
        return self.about_until if self.is_event else None


_UNDATED: Final = _ClaimTiming()


def _render_said_on(timing: _ClaimTiming) -> str:
    """The "said on" prompt value: the source's own date, or its absence."""
    if timing.asserted_at is None:
        return "unknown (the source carries no date)"
    return _date_text(timing.asserted_at)


def _render_about(timing: _ClaimTiming) -> str:
    """The "is about" prompt value: the resolved world-time, or its absence."""
    if timing.about_from is None:
        return (
            "no specific time given (a state, summary, or figure the source"
            " did not tie to a date)"
        )
    start = _date_text(timing.about_from)
    if timing.about_until is None:
        span = f"from {start} onward (no end given)"
    else:
        # the stored end is exclusive; show the last instant inside the window
        end = _date_text(_last_inside(timing.about_until))
        span = start if start == end else f"{start} to {end}"
    if timing.is_event:
        return (
            f"a dated event on {span}"
            if " to " not in span and "onward" not in span
            else f"a dated event within {span}"
        )
    if " to " in span or "onward" in span:
        return f"the period {span} (a state or figure tied to that span, not a dated event)"
    return f"the day {span} (a state or figure tied to that day, not a dated event)"


def _last_inside(end_exclusive: object) -> object:
    """The last instant inside a half-open window, for display only."""
    try:
        return end_exclusive - timedelta(microseconds=1)  # type: ignore[operator]
    except TypeError:
        return end_exclusive


def _date_text(value: object) -> str:
    """Render a timestamp as its UTC calendar date; anything else verbatim.

    Driver rows arrive in the connection's session zone, and canonical bounds
    (D107 §5) are UTC-aligned, so the date is taken after converting to UTC —
    otherwise a canonical day could print as the evening before.
    """
    if isinstance(value, datetime):
        aware = (
            value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        )
        return str(aware.astimezone(timezone.utc).date())
    return str(value)
