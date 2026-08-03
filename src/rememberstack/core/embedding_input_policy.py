"""D80 embedding-input policy: pure location facts → embedding text (no LLM)."""

from __future__ import annotations

from enum import StrEnum
import hashlib
import json
import re
from typing import Final
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

# Policy-owned length counter: Unicode code points (not embedder tokens).
EMBEDDING_INPUT_POLICY_VERSION: Final = "e1-embed-input-v1:char"
"""Content-addressed policy generation for deterministic location headers."""

T_SHORT: Final = 48
H_MAX: Final = 48
ALPHA: Final = 1.0

_WS = re.compile(r"\s+")


class EmbedHeaderMode(StrEnum):
    """Whether embedding text includes a location header."""

    BODY_ONLY = "body_only"
    LOCATION_HEADER = "location_header"


class LocationElementKind(StrEnum):
    """Closed v1 kinds for E2 groundable location elements."""

    DOCUMENT_TITLE = "document_title"
    SECTION_TITLE = "section_title"
    CHANNEL = "channel"
    THREAD = "thread"
    AUTHOR = "author"
    TIMESTAMP = "timestamp"
    SOURCE_KIND = "source_kind"
    OTHER_SOURCE = "other_source"


class LocationProvenance(StrEnum):
    """Provenance for location fields and elements."""

    SOURCE = "source"
    CONNECTOR = "connector"
    DETERMINISTIC_DERIVED = "deterministic_derived"
    MODEL_DERIVED = "model_derived"


class LocationElement(BaseModel):
    """One typed groundable location atom for the E2 union (D80 §3.3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    element_id: str = Field(min_length=1)
    kind: LocationElementKind
    text: str = Field(min_length=1)
    provenance: LocationProvenance
    locator: str | None = None


class LocationFacts(BaseModel):
    """Structured coordinates for one chunk occurrence (D80)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_id: UUID
    doc_id: UUID
    version_id: UUID
    title: str | None = None
    source_kind: str
    source_shape: str = "document"
    section_title: str | None = None
    section_path: str
    section_role: str
    chunk_count: int = Field(ge=0)
    channel_ref: str | None = None
    thread_ref: str | None = None
    author_ref: str | None = None
    message_ts: str | None = None


class EmbeddingInputRender(BaseModel):
    """Result of the pure embedding-input policy for one chunk."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: EmbedHeaderMode
    location_header: str | None
    body: str
    embedding_text: str
    embedding_text_hash: str
    policy_generation: str
    location_elements: tuple[LocationElement, ...]
    skip_reason: str | None = None


def normalize_body(text: str) -> str:
    """Normalize body text for embedding and P1 storage."""
    return _WS.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def policy_length(text: str) -> int:
    """Policy-owned length: Unicode code points (model-independent)."""
    return len(text)


def embedding_text_hash(embedding_text: str) -> str:
    """Stable hash of the exact embedding input string."""
    return hashlib.sha256(embedding_text.encode("utf-8")).hexdigest()


def build_location_elements(*, facts: LocationFacts) -> tuple[LocationElement, ...]:
    """Build allowlisted E2 location elements from facts (no summaries)."""
    elements: list[LocationElement] = []
    if facts.title and facts.title.strip():
        elements.append(
            _element(
                kind=LocationElementKind.DOCUMENT_TITLE,
                text=facts.title.strip(),
                provenance=LocationProvenance.SOURCE,
                locator="document.title",
            )
        )
    if facts.section_title and facts.section_title.strip():
        elements.append(
            _element(
                kind=LocationElementKind.SECTION_TITLE,
                text=facts.section_title.strip(),
                provenance=LocationProvenance.DETERMINISTIC_DERIVED,
                locator=facts.section_path,
            )
        )
    if facts.source_kind.strip():
        elements.append(
            _element(
                kind=LocationElementKind.SOURCE_KIND,
                text=facts.source_kind.strip(),
                provenance=LocationProvenance.SOURCE,
                locator="document.source_kind",
            )
        )
    if facts.channel_ref:
        elements.append(
            _element(
                kind=LocationElementKind.CHANNEL,
                text=facts.channel_ref,
                provenance=LocationProvenance.CONNECTOR,
                locator="channel_ref",
            )
        )
    if facts.thread_ref:
        elements.append(
            _element(
                kind=LocationElementKind.THREAD,
                text=facts.thread_ref,
                provenance=LocationProvenance.CONNECTOR,
                locator="thread_ref",
            )
        )
    if facts.author_ref:
        elements.append(
            _element(
                kind=LocationElementKind.AUTHOR,
                text=facts.author_ref,
                provenance=LocationProvenance.CONNECTOR,
                locator="author_ref",
            )
        )
    if facts.message_ts:
        elements.append(
            _element(
                kind=LocationElementKind.TIMESTAMP,
                text=facts.message_ts,
                provenance=LocationProvenance.CONNECTOR,
                locator="message_ts",
            )
        )
    return tuple(elements)


def render_embedding_input(*, facts: LocationFacts, body: str) -> EmbeddingInputRender:
    """Total pure function: location facts + body → embedding text (D80 §4)."""
    normalized = normalize_body(body)
    elements = build_location_elements(facts=facts)
    if not normalized:
        return EmbeddingInputRender(
            mode=EmbedHeaderMode.BODY_ONLY,
            location_header=None,
            body="",
            embedding_text="",
            embedding_text_hash=embedding_text_hash(""),
            policy_generation=EMBEDDING_INPUT_POLICY_VERSION,
            location_elements=elements,
            skip_reason="empty_body",
        )

    mode = _decide_mode(facts=facts, body=normalized)
    header: str | None = None
    if mode is EmbedHeaderMode.LOCATION_HEADER:
        full = _render_full_header(facts=facts)
        compact = _render_compact_header(facts=facts)
        body_len = policy_length(normalized)
        if body_len <= T_SHORT or policy_length(full) >= ALPHA * body_len:
            header = _truncate_header(compact or full)
        else:
            header = _truncate_header(full)
        if not header:
            mode = EmbedHeaderMode.BODY_ONLY
            header = None

    if mode is EmbedHeaderMode.LOCATION_HEADER and header:
        embedding_text = f"{header}\n\n{normalized}"
    else:
        embedding_text = normalized
        mode = EmbedHeaderMode.BODY_ONLY
        header = None

    return EmbeddingInputRender(
        mode=mode,
        location_header=header,
        body=normalized,
        embedding_text=embedding_text,
        embedding_text_hash=embedding_text_hash(embedding_text),
        policy_generation=EMBEDDING_INPUT_POLICY_VERSION,
        location_elements=elements,
        skip_reason=None,
    )


def location_facts_json(*, facts: LocationFacts, elements: tuple[LocationElement, ...]) -> str:
    """Serialize location facts + elements for PG stamp."""
    payload = {
        "schema": "location_facts.v1",
        "facts": facts.model_dump(mode="json"),
        "elements": [element.model_dump(mode="json") for element in elements],
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _decide_mode(*, facts: LocationFacts, body: str) -> EmbedHeaderMode:
    """Ordered first-match mode decision (D80 §4.3)."""
    body_len = policy_length(body)
    has_message_coords = bool(facts.channel_ref or facts.author_ref or facts.message_ts)
    if facts.source_shape == "message_atom" and body_len <= T_SHORT:
        if has_message_coords:
            return EmbedHeaderMode.LOCATION_HEADER  # compact path
        return EmbedHeaderMode.BODY_ONLY
    if not _has_useful_coordinates(facts=facts):
        return EmbedHeaderMode.BODY_ONLY
    if facts.chunk_count >= 2 and (
        (facts.section_title and facts.section_title.strip())
        or facts.source_shape
        in {"transcript", "thread", "channel_export", "document"}
    ):
        return EmbedHeaderMode.LOCATION_HEADER
    if (
        facts.chunk_count <= 1
        and facts.source_shape == "document"
        and facts.title
        and facts.title.strip()
        and body_len > T_SHORT
    ):
        return EmbedHeaderMode.LOCATION_HEADER
    return EmbedHeaderMode.BODY_ONLY


def _has_useful_coordinates(*, facts: LocationFacts) -> bool:
    if facts.title and facts.title.strip() and facts.title.strip().lower() != "untitled":
        return True
    if facts.section_title and facts.section_title.strip():
        return True
    if facts.channel_ref or facts.author_ref or facts.message_ts:
        return True
    if facts.source_shape not in {"document", "other"}:
        return True
    return False


def _render_full_header(*, facts: LocationFacts) -> str:
    parts: list[str] = []
    if facts.title and facts.title.strip():
        parts.append(f"Document: {_escape(facts.title.strip())}")
    if facts.section_title and facts.section_title.strip():
        parts.append(f"Section: {_escape(facts.section_title.strip())}")
    elif facts.section_path:
        parts.append(f"Section path: {_escape(facts.section_path)}")
    if facts.section_role:
        parts.append(f"Role: {_escape(facts.section_role)}")
    if facts.channel_ref:
        parts.append(f"Channel: {_escape(facts.channel_ref)}")
    if facts.author_ref:
        parts.append(f"Author: {_escape(facts.author_ref)}")
    if facts.message_ts:
        parts.append(f"Time: {_escape(facts.message_ts)}")
    return "; ".join(parts)


def _render_compact_header(*, facts: LocationFacts) -> str:
    parts: list[str] = []
    if facts.channel_ref:
        parts.append(f"Channel: {_escape(facts.channel_ref)}")
    if facts.author_ref:
        parts.append(f"Author: {_escape(facts.author_ref)}")
    if facts.message_ts:
        parts.append(f"Time: {_escape(facts.message_ts)}")
    if not parts and facts.title and facts.title.strip():
        parts.append(f"Document: {_escape(facts.title.strip())}")
    if not parts and facts.section_title and facts.section_title.strip():
        parts.append(f"Section: {_escape(facts.section_title.strip())}")
    if facts.section_role and parts:
        parts.append(f"Role: {_escape(facts.section_role)}")
    return "; ".join(parts)


def _truncate_header(header: str) -> str:
    text = header.strip()
    if policy_length(text) <= H_MAX:
        return text
    # Truncate on code-point boundary.
    return text[:H_MAX].rstrip()


def _escape(value: str) -> str:
    return value.replace(";", ",").replace("\n", " ")


def _element(
    *,
    kind: LocationElementKind,
    text: str,
    provenance: LocationProvenance,
    locator: str | None,
) -> LocationElement:
    raw = f"{kind.value}|{locator or ''}|{text}"
    element_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return LocationElement(
        element_id=element_id,
        kind=kind,
        text=text,
        provenance=provenance,
        locator=locator,
    )
