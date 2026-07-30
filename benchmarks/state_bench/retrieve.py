"""Convert RememberStack envelopes into STATE-Bench learning strings."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from benchmarks.state_bench.protocol import learning_string
from benchmarks.state_bench.protocol import RENDER_FORMAT_VERSION
from rememberstack.model import Envelope
from rememberstack.model import EnvelopePart
from rememberstack.model import EvidenceResult
from rememberstack.model import FactResult
from rememberstack.model import Grain


class RetrievalRenderError(ValueError):
    """Envelope could not be rendered into honest learning strings."""


def format_learnings_from_envelope(
    envelope: Envelope | Any, *, top_k: int
) -> list[str]:
    """Project an ordinary public recipe envelope into ≤ top_k strings.

    Ranking rows are never rendered: ``RankedItem`` carries no claim text and
    would only inject UUID noise into the agent context.
    """
    if top_k < 1:
        raise ValueError("top_k must be >= 1")

    items: list[str] = []
    for evidence in _iter_evidence(envelope):
        if len(items) >= top_k:
            break
        items.append(_from_evidence(evidence=evidence, rank=len(items) + 1))
    for fact in _iter_facts(envelope):
        if len(items) >= top_k:
            break
        items.append(_from_fact(fact=fact, rank=len(items) + 1))

    for item in items:
        if "FactResult(" in item or "EvidenceResult(" in item:
            raise RetrievalRenderError(
                f"unrendered model leaked into learning: {item[:80]}"
            )
    return items[:top_k]


def format_zero_hit_learning(*, top_k: int) -> list[str]:
    """Mark an honest empty retrieval so [] cannot mean 'empty arm'."""
    del top_k
    return [
        learning_string(
            source_id="rememberstack:zero_hit",
            text="retrieval_ok_but_empty: no evidence or facts matched the query",
            rank=1,
            extra=f"render={RENDER_FORMAT_VERSION}",
        )
    ]


def format_error_learning(*, error_class: str, top_k: int) -> list[str]:
    """Return a single redacted infrastructure failure marker (not raw exception text)."""
    del top_k
    safe = "".join(ch if ch.isalnum() or ch in "._:-" else "_" for ch in error_class)[
        :80
    ]
    return [
        learning_string(
            source_id="rememberstack:error",
            text=f"retrieval_failed: {safe}",
            rank=1,
            extra=f"render={RENDER_FORMAT_VERSION}",
        )
    ]


def _iter_evidence(envelope: Any) -> list[Any]:
    rows: list[Any] = []
    grain = getattr(envelope, "grain", None)
    if grain is Grain.COMPOSITE or getattr(envelope, "parts", ()):
        for part in getattr(envelope, "parts", ()) or ():
            if isinstance(part, EnvelopePart) and part.grain is Grain.EVIDENCE:
                rows.extend(part.evidence)
            else:
                rows.extend(getattr(part, "evidence", ()) or ())
    rows.extend(getattr(envelope, "evidence", ()) or ())
    return rows


def _iter_facts(envelope: Any) -> list[Any]:
    rows: list[Any] = []
    grain = getattr(envelope, "grain", None)
    if grain is Grain.COMPOSITE or getattr(envelope, "parts", ()):
        for part in getattr(envelope, "parts", ()) or ():
            if isinstance(part, EnvelopePart) and part.grain is Grain.FACT:
                rows.extend(part.facts)
            else:
                rows.extend(getattr(part, "facts", ()) or ())
    rows.extend(getattr(envelope, "facts", ()) or ())
    return rows


def _from_evidence(*, evidence: Any, rank: int) -> str:
    if isinstance(evidence, EvidenceResult):
        claim_text = evidence.claim_text
        source_id = str(evidence.claim_id)
        extras = [f"render={RENDER_FORMAT_VERSION}", "grain=evidence"]
        if evidence.claim_valid_from is not None:
            extras.append(f"valid_from={evidence.claim_valid_from.isoformat()}")
        if evidence.claim_valid_until is not None:
            extras.append(f"valid_until={evidence.claim_valid_until.isoformat()}")
        if evidence.is_current_testimony is False:
            extras.append("current_testimony=false")
        return learning_string(
            source_id=source_id, text=claim_text, rank=rank, extra="; ".join(extras)
        )
    claim_text = _first_str(
        evidence, "claim_text", "text", "content", "normalized_text"
    )
    source_id = (
        _first_str(evidence, "claim_id", "id", "source_ref") or f"evidence:{rank}"
    )
    if not claim_text:
        raise RetrievalRenderError(f"evidence row has no claim text (rank={rank})")
    return learning_string(
        source_id=source_id,
        text=claim_text,
        rank=rank,
        extra=f"render={RENDER_FORMAT_VERSION}; grain=evidence",
    )


def _from_fact(*, fact: Any, rank: int) -> str:
    if isinstance(fact, FactResult):
        source_id = str(fact.fact_id)
        extras = [
            f"render={RENDER_FORMAT_VERSION}",
            "grain=fact",
            f"kind={fact.kind}",
            f"support={fact.support}",
        ]
        if fact.contradiction_group is not None:
            extras.append(f"contradiction_group={fact.contradiction_group}")
        return learning_string(
            source_id=source_id, text=fact.label, rank=rank, extra="; ".join(extras)
        )
    text = _first_str(fact, "label", "text", "statement", "normalized_text", "summary")
    source_id = _first_str(fact, "fact_id", "id") or f"fact:{rank}"
    if not text:
        raise RetrievalRenderError(f"fact row has no label/text (rank={rank})")
    return learning_string(
        source_id=source_id,
        text=text,
        rank=rank,
        extra=f"render={RENDER_FORMAT_VERSION}; grain=fact",
    )


def _first_str(model: Any, *names: str) -> str | None:
    for name in names:
        if hasattr(model, name):
            value = getattr(model, name)
            if value is None:
                continue
            if isinstance(value, UUID):
                return str(value)
            text = str(value).strip()
            if text:
                return text
        if isinstance(model, dict) and name in model:
            value = model[name]
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
    return None
