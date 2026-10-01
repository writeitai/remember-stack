"""D140 time scope of a text read, compiled to SQL predicates (§3.2, §8.1).

Every read that returns document text, text-derived claims or facts under a
``time`` argument (``current``, ``at``, ``overlap``, ``history``) selects the
document versions in force for that scope. The rule lives once in SQL as
``memory_v1.versions_in_scope``; ranked statements cannot call a set-returning
function per candidate, so this module renders the same rule as a
primary-key probe on the current-belief projection ``document_version_scope``
that sits *inside* the ranked statement, before its ``ORDER BY … LIMIT``:

- a version is **selected** when its projection row is ``selectable`` (ready,
  with a ready current reading) and its in-force multirange overlaps the
  scope's window. A lineage without declared periods has its served version
  in the projection with an unbounded range, so the one probe covers every
  lineage and an undeclared corpus reads exactly as before;
- a **claim** of a periodised lineage is selected through any occurrence in
  a selected version; a claim of an undeclared lineage keeps today's
  current-testimony rule;
- a **fact** passes the evidence gate when a supporting claim occurs in a
  selected version or in any live version of an undeclared lineage. A fact
  with no live supporting occurrence at all rests on no document text, so
  the gate does not apply to it.

The window is the inclusive instant range the mode stands for: ``current``
is the evaluation instant, ``at`` one instant, ``overlap`` its inclusive
bounds and ``history`` everything up to the evaluation instant. Every
statement that uses these fragments binds ``:deployment_id``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from typing import Literal
from typing import Protocol

ScopeMode = Literal["current", "at", "overlap", "history"]


class _TimeSelector(Protocol):
    """The shared shape of ``ReadTime`` and ``FactTime`` selectors."""

    @property
    def mode(self) -> str: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class TextScope:
    """One evaluated time scope: its mode, inclusive window and clocks."""

    mode: ScopeMode
    lower: datetime | None
    upper: datetime | None
    at: datetime | None
    range_start: datetime | None
    range_end: datetime | None
    evaluated_at: datetime
    believed_at: datetime | None = None
    """``None`` reads current belief (the projection); an instant evaluates
    the declaration ledgers as known then (``versions_in_scope``)."""

    @classmethod
    def of(
        cls,
        *,
        time: _TimeSelector | None,
        evaluated_at: datetime,
        believed_at: datetime | None = None,
    ) -> TextScope:
        """Evaluate a ``ReadTime``/``FactTime`` selector (default ``current``)."""
        mode = "current" if time is None else time.mode
        if mode == "current":
            return cls(
                mode="current",
                lower=evaluated_at,
                upper=evaluated_at,
                at=None,
                range_start=None,
                range_end=None,
                evaluated_at=evaluated_at,
                believed_at=believed_at,
            )
        if mode == "at":
            at = getattr(time, "at")  # noqa: B009 -- discriminated by mode
            return cls(
                mode="at",
                lower=at,
                upper=at,
                at=at,
                range_start=None,
                range_end=None,
                evaluated_at=evaluated_at,
                believed_at=believed_at,
            )
        if mode == "overlap":
            start = getattr(time, "from_")  # noqa: B009 -- discriminated by mode
            end = getattr(time, "to")  # noqa: B009 -- discriminated by mode
            return cls(
                mode="overlap",
                lower=start,
                upper=end,
                at=None,
                range_start=start,
                range_end=end,
                evaluated_at=evaluated_at,
                believed_at=believed_at,
            )
        if mode == "history":
            return cls(
                mode="history",
                lower=None,
                upper=evaluated_at,
                at=None,
                range_start=None,
                range_end=None,
                evaluated_at=evaluated_at,
                believed_at=believed_at,
            )
        raise ValueError(f"unknown time scope mode {mode!r}")

    def parameters(self) -> dict[str, Any]:
        """Bound parameters every fragment of this module reads."""
        return {
            "scope_lower": self.lower,
            "scope_upper": self.upper,
            "scope_mode": self.mode,
            "scope_at": self.at,
            "scope_range_start": self.range_start,
            "scope_range_end": self.range_end,
            "scope_evaluated_at": self.evaluated_at,
            "scope_believed_at": self.believed_at,
        }


WINDOW_SQL = (
    "tstzrange(CAST(:scope_lower AS timestamptz),"
    " CAST(:scope_upper AS timestamptz), '[]')"
)
"""The scope's inclusive window; a null lower bound is unbounded (history)."""


def version_selected(*, version: str, alias: str = "scope_row") -> str:
    """One predicate: version ``version`` is selected by the scope (§3.2)."""
    return (
        f"EXISTS (SELECT 1 FROM public.document_version_scope {alias}"
        f" WHERE {alias}.deployment_id = :deployment_id"
        f" AND {alias}.version_id = {version}"
        f" AND {alias}.selectable"
        f" AND {alias}.in_force && {WINDOW_SQL})"
    )


def lineage_periodised(*, doc: str, alias: str = "mode_row") -> str:
    """One predicate: lineage ``doc`` has declared effective time (current belief)."""
    return (
        f"EXISTS (SELECT 1 FROM public.document_version_scope {alias}"
        f" WHERE {alias}.deployment_id = :deployment_id"
        f" AND {alias}.doc_id = {doc}"
        f" AND {alias}.periodised)"
    )


def occurrence_selected(*, claim: str, alias: str = "occurrence") -> str:
    """One predicate: claim ``claim`` occurs in a selected periodised version.

    The occurrence must lie in its version's current reading (D65), the same
    rule ``chunks_all_versions_live`` applies, so the returned coordinates are
    readable.
    """
    return (
        f"EXISTS (SELECT 1 FROM public.chunk_claims {alias}"
        f" JOIN public.chunks {alias}_chunk"
        f"   ON {alias}_chunk.deployment_id = {alias}.deployment_id"
        f"  AND {alias}_chunk.chunk_id = {alias}.chunk_id"
        f" JOIN public.document_versions {alias}_version"
        f"   ON {alias}_version.deployment_id = {alias}_chunk.deployment_id"
        f"  AND {alias}_version.version_id = {alias}_chunk.version_id"
        f"  AND {alias}_version.current_representation_id"
        f"      = {alias}_chunk.representation_id"
        f" JOIN public.document_version_scope {alias}_scope"
        f"   ON {alias}_scope.deployment_id = {alias}_chunk.deployment_id"
        f"  AND {alias}_scope.version_id = {alias}_chunk.version_id"
        f" WHERE {alias}.deployment_id = :deployment_id"
        f"   AND {alias}.claim_id = {claim}"
        f"   AND {alias}_scope.periodised"
        f"   AND {alias}_scope.selectable"
        f"   AND {alias}_scope.in_force && {WINDOW_SQL})"
    )


def claim_selected(*, claim: str, doc: str, current_testimony: str) -> str:
    """One predicate: the scope selects claim ``claim`` of lineage ``doc`` (§3.4).

    ``current_testimony`` is today's predicate for the claim (its origin is a
    live current-testimony occurrence); it decides claims of undeclared
    lineages unchanged. A claim of a periodised lineage is selected through an
    occurrence in a selected version, whatever happened to its origin.
    """
    return (
        f"((NOT {lineage_periodised(doc=doc)} AND {current_testimony})"
        f" OR {occurrence_selected(claim=claim)})"
    )


_EVIDENCE: dict[str, tuple[str, str]] = {
    "relation": ("public.relation_evidence", "relation_id"),
    "observation": ("public.observation_evidence", "observation_id"),
}


def _current_reading(*, chunk: str, alias: str) -> str:
    """JOIN that keeps a chunk only when it lies in its version's current reading.

    After a D65 representation swap the old reading's chunks (and their claim
    occurrences) stay stored; they must not count as evidence or handles.
    """
    return (
        f" JOIN public.document_versions {alias}"
        f"   ON {alias}.deployment_id = {chunk}.deployment_id"
        f"  AND {alias}.version_id = {chunk}.version_id"
        f"  AND {alias}.current_representation_id = {chunk}.representation_id"
    )


def _support_occurrences(
    *, fact_kind: str, fact_id: str, alias: str, stance: str | None = "supports"
) -> str:
    """FROM/JOIN/WHERE of a fact's evidence claim occurrences in live versions.

    Only occurrences in a version's current reading count (D65). ``stance``
    ``None`` takes evidence of either stance.
    """
    table, column = _EVIDENCE[fact_kind]
    stance_filter = (
        "" if stance is None else f"   AND {alias}_evidence.stance = '{stance}'"
    )
    return (
        f"FROM {table} {alias}_evidence"
        f" JOIN public.chunk_claims {alias}_occurrence"
        f"   ON {alias}_occurrence.deployment_id = {alias}_evidence.deployment_id"
        f"  AND {alias}_occurrence.claim_id = {alias}_evidence.claim_id"
        f" JOIN public.chunks {alias}_chunk"
        f"   ON {alias}_chunk.deployment_id = {alias}_occurrence.deployment_id"
        f"  AND {alias}_chunk.chunk_id = {alias}_occurrence.chunk_id"
        f"{_current_reading(chunk=f'{alias}_chunk', alias=f'{alias}_version')}"
        f" JOIN public.document_version_scope {alias}_scope"
        f"   ON {alias}_scope.deployment_id = {alias}_chunk.deployment_id"
        f"  AND {alias}_scope.version_id = {alias}_chunk.version_id"
        f" WHERE {alias}_evidence.deployment_id = :deployment_id"
        f"   AND {alias}_evidence.{column} = {fact_id}"
        f"{stance_filter}"
    )


def fact_in_scope(*, fact_kind: str, fact_id: str, pinned_belief: bool = False) -> str:
    """One predicate: the §8.1 evidence gate for one fact of a fixed kind.

    ``document_version_scope`` holds exactly the non-deleted versions of live
    lineages, so joining it is the "live occurrence" test. An occurrence is in
    scope when it lies in a version the scope selects, or in any live version
    of a lineage without declared periods (whose evidence D140 does not
    time-restrict), and in that version's current reading.

    A fact with supporting evidence needs an in-scope *supporting* occurrence;
    support left only in deleted versions or replaced readings does not
    count. A fact with no supporting evidence at all — a D54 zero-support,
    contradiction-only fact, which D54 flags rather than hides — needs an
    in-scope occurrence of any stance, so such facts read exactly as before in
    a corpus without declared periods and are still time-restricted in one
    with them. For current belief the selection is the projection probe; for
    a past belief instant the fact's bounded evidence lineages are evaluated
    from the ledgers through ``memory_v1.fact_in_scope_support``.
    """
    if fact_kind not in _EVIDENCE:
        raise ValueError(f"unknown fact kind {fact_kind!r}")
    alias = f"gate_{fact_kind}"
    table, column = _EVIDENCE[fact_kind]

    def in_scope_occurrence(*, stance: str | None, suffix: str) -> str:
        scoped = f"{alias}{suffix}"
        occurrences = _support_occurrences(
            fact_kind=fact_kind, fact_id=fact_id, alias=scoped, stance=stance
        )
        return (
            f"EXISTS (SELECT 1 {occurrences}"
            f" AND (NOT {scoped}_scope.periodised"
            f" OR ({scoped}_scope.selectable"
            f" AND {scoped}_scope.in_force && {WINDOW_SQL})))"
        )

    if not pinned_belief:
        unsupported = (
            f"NOT EXISTS (SELECT 1 FROM {table} {alias}_any"
            f" WHERE {alias}_any.deployment_id = :deployment_id"
            f" AND {alias}_any.{column} = {fact_id}"
            f" AND {alias}_any.stance = 'supports')"
        )
        in_scope = (
            f"{in_scope_occurrence(stance='supports', suffix='')}"
            f" OR ({unsupported}"
            f" AND {in_scope_occurrence(stance=None, suffix='_all')})"
        )
    else:
        in_scope = (
            "memory_v1.fact_in_scope_support("
            f":deployment_id, '{fact_kind}', {fact_id},"
            " CAST(:scope_mode AS text), CAST(:scope_at AS timestamptz),"
            " CAST(:scope_range_start AS timestamptz),"
            " CAST(:scope_range_end AS timestamptz),"
            " CAST(:scope_evaluated_at AS timestamptz),"
            " CAST(:scope_believed_at AS timestamptz))"
        )
    return f"({in_scope})"


def fact_in_scope_by_kind(*, kind: str, fact_id: str) -> str:
    """The current-belief gate for a row whose kind is the SQL expression ``kind``."""
    return (
        f"(CASE {kind}"
        f" WHEN 'relation' THEN"
        f" {fact_in_scope(fact_kind='relation', fact_id=fact_id)}"
        f" WHEN 'observation' THEN"
        f" {fact_in_scope(fact_kind='observation', fact_id=fact_id)}"
        " ELSE false END)"
    )


def claim_in_scope(*, claim: str, alias: str = "evidence_scope") -> str:
    """One predicate: claim ``claim`` may be shown as fact evidence (§8.1).

    The claim occurs, in a version's current reading, in a selected version or
    in a live version of an undeclared lineage. The same rule as the fact
    gate, at claim grain (current belief): an occurrence left only in a
    replaced reading or a deleted version is not evidence.
    """
    occurrences = (
        f"FROM public.chunk_claims {alias}"
        f" JOIN public.chunks {alias}_chunk"
        f"   ON {alias}_chunk.deployment_id = {alias}.deployment_id"
        f"  AND {alias}_chunk.chunk_id = {alias}.chunk_id"
        f"{_current_reading(chunk=f'{alias}_chunk', alias=f'{alias}_version')}"
        f" JOIN public.document_version_scope {alias}_scope"
        f"   ON {alias}_scope.deployment_id = {alias}_chunk.deployment_id"
        f"  AND {alias}_scope.version_id = {alias}_chunk.version_id"
        f" WHERE {alias}.deployment_id = :deployment_id"
        f"   AND {alias}.claim_id = {claim}"
    )
    return (
        f"EXISTS (SELECT 1 {occurrences}"
        f" AND (NOT {alias}_scope.periodised"
        f" OR ({alias}_scope.selectable AND {alias}_scope.in_force && {WINDOW_SQL})))"
    )
