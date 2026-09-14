"""Project prepared adjudication snapshots into compact model evidence (D121)."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
import re
from typing import Any
from uuid import UUID

from rememberstack.model.concise_adjudication import PromptFactDecision
from rememberstack.model.concise_adjudication import PromptGroundedWindow
from rememberstack.model.fact_application import AssertionSupportMove
from rememberstack.model.fact_application import FactApplicationDecision
from rememberstack.model.fact_application import FactReference
from rememberstack.model.fact_application import FactWindowUpdate
from rememberstack.model.fact_application import NewFact
from rememberstack.model.fact_windows import GroundedFactWindow

PROMPT_RENDERER_VERSION = "concise-handles-1"
"""Pinned projector/response-adapter generation included in the fingerprint."""

# F/C/A/E/S are citable supplied rows. T is factored text. W is a window
# witness that was not copied into the bounded claims payload and cannot be
# cited. New-fact names must not reuse any of these prefixes.
_TYPED_HANDLE = re.compile(r"^([FCAESTW])([1-9]\d*)$")


@dataclass(frozen=True)
class AttemptMapping:
    """Bijective attempt-local names rebuilt from one frozen snapshot's row order."""

    facts: Mapping[str, UUID]
    claims: Mapping[str, UUID]
    assertions: Mapping[str, UUID]
    entities: Mapping[str, UUID]
    sources: Mapping[str, UUID]
    incoming_assertion: str


def _as_id(value: object) -> UUID:
    """Accept database UUIDs or the JSON strings stored on a frozen attempt."""
    return value if isinstance(value, UUID) else UUID(str(value))


def _jsonable(value: object) -> object:
    """Render timestamps as UTC ISO strings; other JSON values pass through."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


def _handle(prefix: str, index: int) -> str:
    """1-based names: F1, C1, A1. Index 0 is F1."""
    return f"{prefix}{index + 1}"


def _typed_kind(name: str) -> str | None:
    """Return the reserved prefix when the name is a typed attempt-local handle."""
    match = _TYPED_HANDLE.fullmatch(name)
    return None if match is None else match.group(1)


def _entity_name(*, row: Mapping[str, Any], field: str) -> str | None:
    """Read a displayed name from normalized assertion content when present."""
    content = row.get("assertion")
    if not isinstance(content, Mapping):
        return None
    item = content.get(field)
    if isinstance(item, Mapping):
        name = item.get("name")
        return str(name) if name else None
    return None


def _collect_texts(*, snapshot: Mapping[str, Any]) -> Counter[str]:
    """Count exact strings so only repeated evidence text is factored."""
    counts: Counter[str] = Counter()
    for claim in snapshot.get("claims", ()):
        for key in ("claim_text", "source_span"):
            value = claim.get(key)
            if isinstance(value, str) and value:
                counts[value] += 1
    for fact in snapshot.get("facts", ()):
        value = fact.get("statement")
        if isinstance(value, str) and value:
            counts[value] += 1
    for assertion in snapshot.get("assertions", ()):
        content = assertion.get("assertion")
        if isinstance(content, Mapping):
            statement = content.get("statement")
            if isinstance(statement, str) and statement:
                counts[statement] += 1
    return counts


def _text_value(
    *,
    text: object,
    repeats: Counter[str],
    dictionary: dict[str, str],
    assigned: dict[str, str],
) -> tuple[str, str | None]:
    """Inline unique wording; repeated wording becomes a T-name in the dictionary."""
    if not isinstance(text, str) or not text:
        return "text", None if text in (None, "") else str(text)
    if repeats[text] < 2:
        return "text", text
    handle = assigned.get(text)
    if handle is None:
        handle = _handle("T", len(assigned))
        assigned[text] = handle
        dictionary[handle] = text
    return "text_ref", handle


def _put_text(
    payload: dict[str, Any],
    key: str,
    value: object,
    *,
    repeats: Counter[str],
    dictionary: dict[str, str],
    assigned: dict[str, str],
) -> None:
    """Store unique text inline or a dictionary reference for repeats."""
    field, rendered = _text_value(
        text=value, repeats=repeats, dictionary=dictionary, assigned=assigned
    )
    if rendered is None:
        return
    payload[key if field == "text" else f"{key}_ref"] = rendered


def _compact_content(
    content: object,
    *,
    repeats: Counter[str],
    dictionary: dict[str, str],
    assigned: dict[str, str],
) -> object:
    """Keep assertion qualifiers; factor a repeated statement inside content."""
    if not isinstance(content, Mapping):
        return content
    compacted = dict(content)
    field, rendered = _text_value(
        text=compacted.get("statement"),
        repeats=repeats,
        dictionary=dictionary,
        assigned=assigned,
    )
    if field == "text_ref" and rendered is not None:
        del compacted["statement"]
        compacted["statement_ref"] = rendered
    return compacted


def _remember_entity(
    *,
    entity_ids: list[UUID],
    names: dict[UUID, str],
    entity_id: UUID,
    name: str | None = None,
) -> None:
    """Track one frozen entity id in first-seen order."""
    if entity_id not in names:
        entity_ids.append(entity_id)
        names[entity_id] = ""
    if name:
        names[entity_id] = name


def project_concise_inputs(
    *, snapshot: Mapping[str, Any]
) -> tuple[dict[str, Any], AttemptMapping]:
    """Derive compact semantic evidence and the typed mapping for one attempt.

    Candidate and witness membership are unchanged. Omitted fields are
    administrative. Exact shared wording is storage deduplication, not evidence
    identity deduplication. Canonical subject/object links stay visible.
    Unhydrated window witnesses are named but not citable.
    """
    fact_rows = list(snapshot.get("facts", ()))
    claim_rows = list(snapshot.get("claims", ()))
    assertion_rows = list(snapshot.get("assertions", ()))
    facts = {
        _handle("F", index): _as_id(row["fact_id"])
        for index, row in enumerate(fact_rows)
    }
    claims = {
        _handle("C", index): _as_id(row["claim_id"])
        for index, row in enumerate(claim_rows)
    }
    assertions = {
        _handle("A", index): _as_id(row["application_id"])
        for index, row in enumerate(assertion_rows)
    }
    fact_by_id = {uuid: name for name, uuid in facts.items()}
    claim_by_id = {uuid: name for name, uuid in claims.items()}

    entity_ids: list[UUID] = []
    entity_names: dict[UUID, str] = {}
    same_as: dict[UUID, UUID] = {}
    root_id = _as_id(snapshot["root"])
    _remember_entity(entity_ids=entity_ids, names=entity_names, entity_id=root_id)
    same_as[root_id] = root_id
    for row in (*fact_rows, *assertion_rows):
        subject_raw = row.get("subject_entity_id")
        if subject_raw is not None:
            subject_id = _as_id(subject_raw)
            _remember_entity(
                entity_ids=entity_ids,
                names=entity_names,
                entity_id=subject_id,
                name=_entity_name(row=row, field="subject"),
            )
            # Facts and assertions in this snapshot are locked to root's members.
            same_as[subject_id] = root_id
        object_raw = row.get("object_entity_id")
        if object_raw is not None:
            object_id = _as_id(object_raw)
            _remember_entity(
                entity_ids=entity_ids,
                names=entity_names,
                entity_id=object_id,
                name=_entity_name(row=row, field="object"),
            )
        canonical_subject = row.get("canonical_subject_id")
        if subject_raw is not None and canonical_subject is not None:
            canonical_id = _as_id(canonical_subject)
            _remember_entity(
                entity_ids=entity_ids, names=entity_names, entity_id=canonical_id
            )
            same_as[_as_id(subject_raw)] = canonical_id
            same_as[canonical_id] = canonical_id
        canonical_object = row.get("canonical_object_id")
        if object_raw is not None and canonical_object is not None:
            canonical_id = _as_id(canonical_object)
            _remember_entity(
                entity_ids=entity_ids, names=entity_names, entity_id=canonical_id
            )
            same_as[_as_id(object_raw)] = canonical_id
            same_as[canonical_id] = canonical_id
    for entity_id, canonical_id in same_as.items():
        if entity_names.get(entity_id) and not entity_names.get(canonical_id):
            entity_names[canonical_id] = entity_names[entity_id]

    entities = {
        _handle("E", index): entity_id for index, entity_id in enumerate(entity_ids)
    }
    entity_by_id = {uuid: name for name, uuid in entities.items()}
    source_ids: list[UUID] = []
    seen_sources: set[UUID] = set()
    for row in claim_rows:
        source_id = _as_id(row["doc_id"])
        if source_id in seen_sources:
            continue
        seen_sources.add(source_id)
        source_ids.append(source_id)
    sources = {
        _handle("S", index): source_id for index, source_id in enumerate(source_ids)
    }
    source_by_id = {uuid: name for name, uuid in sources.items()}
    incoming_id = _as_id(snapshot["application_id"])
    incoming = next(name for name, uuid in assertions.items() if uuid == incoming_id)
    repeats = _collect_texts(snapshot=snapshot)
    dictionary: dict[str, str] = {}
    assigned_text: dict[str, str] = {}
    unhydrated_by_id: dict[UUID, str] = {}

    def window_fields(row: Mapping[str, Any]) -> dict[str, Any]:
        """Name hydrated witnesses; disclose uncopied ones without making them citable."""
        hydrated: list[str] = []
        missing: list[str] = []
        for raw in row.get("window_claim_ids") or ():
            claim_id = _as_id(raw)
            if claim_id in claim_by_id:
                hydrated.append(claim_by_id[claim_id])
                continue
            handle = unhydrated_by_id.get(claim_id)
            if handle is None:
                handle = _handle("W", len(unhydrated_by_id))
                unhydrated_by_id[claim_id] = handle
            missing.append(handle)
        fields: dict[str, Any] = {"window_claims": hydrated}
        if missing:
            fields["window_claims_not_supplied"] = missing
        return fields

    def entity_ref(raw: object) -> tuple[str, str | None]:
        """Return stored handle and canonical handle when they differ."""
        entity_id = _as_id(raw)
        handle = entity_by_id[entity_id]
        canonical_id = same_as.get(entity_id, entity_id)
        canonical = entity_by_id.get(canonical_id)
        if canonical is None or canonical == handle:
            return handle, None
        return handle, canonical

    presented_facts = []
    contradiction_groups: dict[str, list[str]] = {}
    for index, row in enumerate(fact_rows):
        handle = _handle("F", index)
        item: dict[str, Any] = {
            "handle": handle,
            "chosen_from": _jsonable(row.get("valid_from")),
            "chosen_until": _jsonable(row.get("valid_until")),
            "chosen_precision": row.get("valid_precision"),
            **window_fields(row),
            "evidence_count": row.get("evidence_count"),
            "contradict_count": row.get("contradict_count"),
        }
        _put_text(
            item,
            "statement",
            row.get("statement"),
            repeats=repeats,
            dictionary=dictionary,
            assigned=assigned_text,
        )
        if row.get("subject_entity_id") is not None:
            subject, canonical_subject = entity_ref(row["subject_entity_id"])
            item["subject"] = subject
            if canonical_subject is not None:
                item["canonical_subject"] = canonical_subject
        if row.get("object_entity_id") is not None:
            obj, canonical_object = entity_ref(row["object_entity_id"])
            item["object"] = obj
            if canonical_object is not None:
                item["canonical_object"] = canonical_object
        if row.get("predicate") is not None:
            item["predicate"] = row["predicate"]
        group = row.get("contradiction_group")
        if group is not None:
            contradiction_groups.setdefault(str(group), []).append(handle)
        presented_facts.append(item)

    presented_claims = []
    for index, row in enumerate(claim_rows):
        item = {
            "handle": _handle("C", index),
            "source": source_by_id[_as_id(row["doc_id"])],
            "source_said_at": _jsonable(row.get("asserted_at")),
            "source_world_from": _jsonable(row.get("claim_valid_from")),
            "source_world_until": _jsonable(row.get("claim_valid_until")),
            "source_world_precision": row.get("claim_valid_precision"),
            "source_world_kind": row.get("claim_valid_kind"),
            "current_testimony": row.get("is_current_testimony"),
            "attributed": row.get("is_attributed"),
        }
        _put_text(
            item,
            "claim_text",
            row.get("claim_text"),
            repeats=repeats,
            dictionary=dictionary,
            assigned=assigned_text,
        )
        _put_text(
            item,
            "source_span",
            row.get("source_span"),
            repeats=repeats,
            dictionary=dictionary,
            assigned=assigned_text,
        )
        presented_claims.append(item)

    presented_assertions = []
    for index, row in enumerate(assertion_rows):
        handle = _handle("A", index)
        assigned = row.get("support_relation_id") or row.get("support_observation_id")
        item = {
            "handle": handle,
            "incoming": handle == incoming,
            "kind": row.get("output_kind"),
            "stance": row.get("support_stance"),
            "content": _compact_content(
                row.get("assertion"),
                repeats=repeats,
                dictionary=dictionary,
                assigned=assigned_text,
            ),
        }
        claim_id = _as_id(row["claim_id"])
        if claim_id in claim_by_id:
            item["claim"] = claim_by_id[claim_id]
        else:
            item["claim_not_supplied"] = True
        if assigned:
            assigned_id = _as_id(assigned)
            if assigned_id in fact_by_id:
                item["assigned_to"] = fact_by_id[assigned_id]
            else:
                item["assigned_to_not_supplied"] = True
        else:
            item["assigned_to"] = None
        if row.get("subject_entity_id") is not None:
            subject, canonical_subject = entity_ref(row["subject_entity_id"])
            item["subject"] = subject
            if canonical_subject is not None:
                item["canonical_subject"] = canonical_subject
        if row.get("object_entity_id") is not None:
            obj, canonical_object = entity_ref(row["object_entity_id"])
            item["object"] = obj
            if canonical_object is not None:
                item["canonical_object"] = canonical_object
        presented_assertions.append(item)

    evidence = []
    evidence_not_supplied = 0
    for row in snapshot.get("evidence", ()):
        fact_id = _as_id(row["fact_id"])
        claim_id = _as_id(row["claim_id"])
        if fact_id in fact_by_id and claim_id in claim_by_id:
            evidence.append(
                {
                    "fact": fact_by_id[fact_id],
                    "claim": claim_by_id[claim_id],
                    "stance": row.get("stance"),
                }
            )
        else:
            evidence_not_supplied += 1

    presented_entities = []
    for handle, uuid in entities.items():
        item = {"handle": handle}
        if entity_names.get(uuid):
            item["name"] = entity_names[uuid]
        canonical_id = same_as.get(uuid, uuid)
        if canonical_id != uuid and canonical_id in entity_by_id:
            item["same_as"] = entity_by_id[canonical_id]
        presented_entities.append(item)

    presentation = {
        "renderer_version": PROMPT_RENDERER_VERSION,
        "kind": snapshot.get("kind"),
        "incoming_assertion": incoming,
        "subject": entity_by_id[root_id],
        "limits": snapshot.get("limits"),
        "potentially_truncated": snapshot.get("potentially_truncated"),
        "text": dictionary,
        "entities": presented_entities,
        "sources": [{"handle": handle} for handle in sources],
        "facts": presented_facts,
        "claims": presented_claims,
        "assertions": presented_assertions,
        "evidence": evidence,
        "contradiction_sets": [
            names for names in contradiction_groups.values() if len(names) > 1
        ],
    }
    if evidence_not_supplied:
        presentation["evidence_not_supplied"] = evidence_not_supplied
    mapping = AttemptMapping(
        facts=facts,
        claims=claims,
        assertions=assertions,
        entities=entities,
        sources=sources,
        incoming_assertion=incoming,
    )
    return presentation, mapping


def _require(*, name: str, table: Mapping[str, UUID], kind: str) -> UUID:
    """Reject unknown, wrong-kind, or ambiguous names without guessing."""
    typed = _typed_kind(name)
    if typed is None:
        raise ValueError(f"unknown {kind} handle {name}")
    expected = {
        "fact": "F",
        "claim": "C",
        "assertion": "A",
        "entity": "E",
        "source": "S",
    }[kind]
    if typed != expected:
        raise ValueError(f"handle {name} is not a {kind} name")
    if name not in table:
        raise ValueError(f"unknown {kind} handle {name}")
    return table[name]


def _fact_reference(*, name: str, mapping: AttemptMapping) -> FactReference:
    """Translate an F-name or a declared new-fact name; never guess."""
    typed = _typed_kind(name)
    if typed == "F":
        return FactReference(
            fact_id=_require(name=name, table=mapping.facts, kind="fact")
        )
    if typed is not None:
        raise ValueError(f"handle {name} is not a fact name")
    return FactReference(new_handle=name)


def _translate_window(
    *, window: PromptGroundedWindow, mapping: AttemptMapping
) -> GroundedFactWindow:
    """Convert supporting C-names into store claim IDs. W-names are not citable."""
    return GroundedFactWindow(
        window=window.window,
        supporting_claim_ids=tuple(
            _require(name=name, table=mapping.claims, kind="claim")
            for name in window.supporting_claims
        ),
    )


def translate_prompt_decision(
    *, response: PromptFactDecision, mapping: AttemptMapping
) -> FactApplicationDecision:
    """Turn one closed handle answer into the existing writer decision.

    Unknown names, wrong kinds, names from another attempt, and unhydrated
    witness names fail. There is no fuzzy recovery.
    """
    declared = {fact.handle for fact in response.new_facts}
    for handle in declared:
        if _typed_kind(handle) is not None:
            raise ValueError(f"new-fact handle {handle} collides with a supplied name")
    target = _fact_reference(name=response.target, mapping=mapping)
    if target.new_handle is not None and target.new_handle not in declared:
        raise ValueError("new handles must be declared and used in the decision")
    new_facts = tuple(
        NewFact(
            handle=fact.handle,
            assertion_application_id=_require(
                name=fact.assertion, table=mapping.assertions, kind="assertion"
            ),
        )
        for fact in response.new_facts
    )
    window = (
        None
        if response.window is None
        else _translate_window(window=response.window, mapping=mapping)
    )
    updates = tuple(
        FactWindowUpdate(
            target=_fact_reference(name=update.target, mapping=mapping),
            window=_translate_window(window=update.window, mapping=mapping),
        )
        for update in response.updates
    )
    support_moves = tuple(
        AssertionSupportMove(
            application_id=_require(
                name=move.assertion, table=mapping.assertions, kind="assertion"
            ),
            expected_fact_id=_require(
                name=move.expected_fact, table=mapping.facts, kind="fact"
            ),
            target=_fact_reference(name=move.target, mapping=mapping),
        )
        for move in response.support_moves
    )
    if any(
        move.application_id == mapping.assertions[mapping.incoming_assertion]
        for move in support_moves
    ):
        raise ValueError("incoming support is assigned by target, not a move")
    return FactApplicationDecision(
        target=target,
        stance=response.stance,
        new_facts=new_facts,
        window=window,
        updates=updates,
        support_moves=support_moves,
        contradict_with=tuple(
            _require(name=name, table=mapping.facts, kind="fact")
            for name in response.contradict_with
        ),
        confidence=response.confidence,
        rationale=response.rationale,
    )
