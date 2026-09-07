"""Atomic ordinary fact identity, evidence, and single-window mutations (D114)."""

from datetime import datetime
from typing import Any
from uuid import UUID
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

from rememberstack.core.fact_application import ApplicationScope
from rememberstack.core.fact_application import new_fact_id
from rememberstack.core.fact_application import resolve_fact_reference
from rememberstack.core.fact_application import validate_application_scope
from rememberstack.core.fact_windows import fact_window_from_raw
from rememberstack.model.claims import ClaimValidPrecision
from rememberstack.model.fact_application import FactApplicationDecision
from rememberstack.model.fact_windows import FactWindow
from rememberstack.spine.fact_applications import canonical_json


def _uuid(value: object) -> UUID:
    """Normalize an ID from either database values or durable JSON."""
    return UUID(str(value))


def _instant(value: Any) -> datetime | None:
    """Decode a retained raw claim endpoint without canonicalizing it twice."""
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def apply_fact_decision(
    *,
    connection: Connection,
    snapshot: dict[str, Any],
    decision: FactApplicationDecision,
) -> dict[str, Any]:
    """Apply only a revalidated prepared snapshot on the caller's locked transaction.

    The caller commits this together with the application receipt. There is no
    independent correction transaction, evidence min/max, or date identity gate.
    """
    kind = snapshot["kind"]
    if kind not in ("relation", "observation"):
        raise ValueError("unsupported fact plane")
    table, id_column = (
        ("relations", "relation_id")
        if kind == "relation"
        else ("observations", "observation_id")
    )
    pointer, evidence = f"support_{kind}_id", f"{kind}_evidence"
    deployment_id, application_id = (
        _uuid(snapshot["deployment_id"]),
        _uuid(snapshot["application_id"]),
    )
    facts = {_uuid(row["fact_id"]): row for row in snapshot["facts"]}
    claims = {_uuid(row["claim_id"]): row for row in snapshot["claims"]}
    assertions = {_uuid(row["application_id"]): row for row in snapshot["assertions"]}
    links = {
        (_uuid(row["fact_id"]), _uuid(row["claim_id"])): row
        for row in snapshot["evidence"]
    }
    scope = ApplicationScope(
        incoming_application_id=application_id,
        fact_ids=frozenset(facts),
        claim_ids=frozenset(claims),
        assertion_targets={
            key: _uuid(row[pointer]) if row[pointer] else None
            for key, row in assertions.items()
        },
        legacy_links=frozenset(
            key for key, row in links.items() if row["legacy_stance"] is not None
        ),
    )
    validate_application_scope(decision=decision, scope=scope)
    for assertion in assertions.values():
        if assertion["output_kind"] != kind or _uuid(
            assertion["canonical_subject_id"]
        ) != _uuid(snapshot["root"]):
            raise ValueError(
                "assertion is outside the canonical subject and fact plane"
            )
    incoming = assertions[application_id]
    params: dict[str, Any] = {
        "deployment_id": deployment_id,
        "application_id": application_id,
        "claim_id": _uuid(incoming["claim_id"]),
    }
    created: list[UUID] = []
    changed: set[UUID] = set()
    pairs: set[tuple[UUID, UUID]] = set()
    before: dict[str, Any] = {
        str(key): {
            name: value[name]
            for name in (
                "valid_from",
                "valid_until",
                "valid_precision",
                "window_claim_ids",
            )
        }
        for key, value in facts.items()
    }
    window_updates: set[UUID] = set()
    for new in decision.new_facts:
        source = assertions[new.assertion_application_id]
        claim = claims[_uuid(source["claim_id"])]
        assertion = source["assertion"]
        fact_id = new_fact_id(application_id=application_id, handle=new.handle)
        window = (
            fact_window_from_raw(
                valid_from=_instant(claim["claim_valid_from"]),
                valid_until=_instant(claim["claim_valid_until"]),
                precision=ClaimValidPrecision(claim["claim_valid_precision"]),
            )
            if assertion.get("uses_claim_window", False)
            else FactWindow()
        )
        create = {
            **params,
            "fact_id": fact_id,
            "subject": _uuid(source["canonical_subject_id"]),
            "normalizer_version": source["normalizer_version"],
            "valid_from": window.valid_from,
            "valid_until": window.valid_until,
            "valid_precision": window.valid_precision.value,
            "window_claim_ids": [_uuid(source["claim_id"])]
            if window.valid_precision != ClaimValidPrecision.UNKNOWN
            else [],
        }
        if kind == "relation":
            create.update(
                predicate=assertion["predicate"],
                object=_uuid(source["canonical_object_id"]),
            )
            content_columns, content_values = (
                "predicate,object_entity_id",
                ":predicate,:object",
            )
        else:
            create["statement"] = assertion["statement"]
            content_columns, content_values = "statement", ":statement"
        connection.execute(
            text(f"""INSERT INTO {table}({id_column},deployment_id,subject_entity_id,
            {content_columns},normalizer_version,valid_from,valid_until,valid_precision,window_claim_ids)
            VALUES (:fact_id,:deployment_id,:subject,{content_values},:normalizer_version,
                    :valid_from,:valid_until,CAST(:valid_precision AS claim_valid_precision),:window_claim_ids)
        """),
            create,
        )
        created.append(fact_id)
        changed.add(fact_id)
    target = resolve_fact_reference(
        reference=decision.target, application_id=application_id
    )
    claim_id = _uuid(incoming["claim_id"])
    # Receipt+support pointer are set together by the caller at transaction end.
    # Existing applied assertions can move without changing their original result.
    for move in decision.support_moves:
        destination = resolve_fact_reference(
            reference=move.target, application_id=application_id
        )
        connection.execute(
            text(
                f"UPDATE fact_applications SET {pointer}=:target WHERE deployment_id=:deployment_id AND application_id=:moved"
            ),
            {**params, "target": destination, "moved": move.application_id},
        )
        moved_claim = _uuid(assertions[move.application_id]["claim_id"])
        pairs.update(((move.expected_fact_id, moved_claim), (destination, moved_claim)))
        changed.update((move.expected_fact_id, destination))
    for move in decision.legacy_support_moves:
        destination = resolve_fact_reference(
            reference=move.target, application_id=application_id
        )
        stance = links[(move.expected_fact_id, move.claim_id)]["legacy_stance"]
        connection.execute(
            text(
                f"UPDATE {evidence} SET legacy_stance=NULL WHERE deployment_id=:deployment_id AND {id_column}=:source AND claim_id=:moved_claim"
            ),
            {**params, "source": move.expected_fact_id, "moved_claim": move.claim_id},
        )
        connection.execute(
            text(f"""INSERT INTO {evidence}(deployment_id,{id_column},claim_id,doc_id,stance,normalizer_version,legacy_stance)
            SELECT :deployment_id,:destination,claim_id,doc_id,CAST(:stance AS evidence_stance),:normalizer_version,CAST(:stance AS evidence_stance)
            FROM claims WHERE deployment_id=:deployment_id AND claim_id=:moved_claim
            ON CONFLICT ({id_column},claim_id) DO UPDATE SET legacy_stance=
              CASE WHEN {evidence}.legacy_stance='supports' OR excluded.legacy_stance='supports'
                   THEN 'supports'::evidence_stance ELSE excluded.legacy_stance END
        """),
            {
                **params,
                "destination": destination,
                "moved_claim": move.claim_id,
                "stance": stance,
                "normalizer_version": snapshot["normalizer_version"],
            },
        )
        pairs.update(
            ((move.expected_fact_id, move.claim_id), (destination, move.claim_id))
        )
        changed.update((move.expected_fact_id, destination))
    replacements = [(update.target, update.window) for update in decision.updates]
    if decision.window is not None:
        replacements.append((decision.target, decision.window))
    for reference, replacement in replacements:
        fact_id = resolve_fact_reference(
            reference=reference, application_id=application_id
        )
        window_updates.add(fact_id)
        old = facts.get(fact_id)
        before[str(fact_id)] = (
            {
                key: old[key]
                for key in (
                    "valid_from",
                    "valid_until",
                    "valid_precision",
                    "window_claim_ids",
                )
            }
            if old
            else None
        )
        window = replacement.window
        connection.execute(
            text(f"""UPDATE {table} SET valid_from=:valid_from,valid_until=:valid_until,
            valid_precision=CAST(:valid_precision AS claim_valid_precision),window_claim_ids=:window_claim_ids,updated_at=now()
            WHERE deployment_id=:deployment_id AND {id_column}=:fact_id
        """),
            {
                **params,
                "fact_id": fact_id,
                "valid_from": window.valid_from,
                "valid_until": window.valid_until,
                "valid_precision": window.valid_precision.value,
                "window_claim_ids": sorted(replacement.supporting_claim_ids)
                if window.valid_precision != ClaimValidPrecision.UNKNOWN
                else [],
            },
        )
        changed.add(fact_id)
    grouped: set[UUID] = set()
    if decision.contradict_with:
        grouped = {target, *decision.contradict_with}
        groups = {
            facts[key]["contradiction_group"]
            for key in grouped
            if key in facts and facts[key]["contradiction_group"] is not None
        }
        if groups:
            existing_members = set(
                connection.execute(
                    text(
                        f"SELECT {id_column} FROM {table} WHERE deployment_id=:deployment_id AND contradiction_group=ANY(:groups)"
                    ),
                    {**params, "groups": [_uuid(group) for group in groups]},
                ).scalars()
            )
            if not existing_members <= set(facts):
                raise ValueError("contradiction group contains unsupplied participants")
            grouped.update(existing_members)
        group = min((_uuid(value) for value in groups), default=uuid4())
        connection.execute(
            text(
                f"UPDATE {table} SET contradiction_group=:group,updated_at=now() WHERE deployment_id=:deployment_id AND {id_column}=ANY(:fact_ids)"
            ),
            {**params, "group": group, "fact_ids": sorted(grouped)},
        )
        changed.update(grouped)
    # Mark applied inside the same transaction before rebuilding the aggregate link.
    result = {
        "application_id": str(application_id),
        "fact_id": str(target),
        "created_fact_ids": [str(value) for value in created],
    }
    connection.execute(
        text(f"""UPDATE fact_applications SET applied_at=now(),result=CAST(:result AS jsonb),
        {pointer}=:target,support_stance=CAST(:stance AS evidence_stance),
        prepared=NULL,decision=NULL,attempt_id=NULL,input_hash=NULL
        WHERE deployment_id=:deployment_id AND application_id=:application_id AND applied_at IS NULL RETURNING application_id
    """),
        {
            **params,
            "result": canonical_json(result),
            "target": target,
            "stance": decision.stance,
        },
    ).scalar_one()
    pairs.add((target, claim_id))
    changed.add(target)
    for fact_id, source_claim in sorted(pairs):
        _recount_link(
            connection=connection,
            kind=kind,
            deployment_id=deployment_id,
            fact_id=fact_id,
            claim_id=source_claim,
            normalizer_version=snapshot["normalizer_version"],
        )
    for fact_id in sorted(changed):
        _recount_fact(
            connection=connection,
            kind=kind,
            deployment_id=deployment_id,
            fact_id=fact_id,
        )
        label_clear = (
            "fact_label=NULL,fact_label_version=NULL,"
            if kind == "relation"
            else "obs_label=NULL,"
        )
        connection.execute(
            text(f"""UPDATE {table} SET {label_clear} embedding=NULL,embedding_model=NULL,
            embedding_input_policy_version=NULL,embedding_text_hash=NULL,updated_at=now()
            WHERE deployment_id=:deployment_id AND {id_column}=:fact_id
        """),
            {**params, "fact_id": fact_id},
        )
        after = (
            connection.execute(
                text(
                    f"SELECT valid_from,valid_until,valid_precision,window_claim_ids FROM {table} WHERE deployment_id=:deployment_id AND {id_column}=:fact_id"
                ),
                {**params, "fact_id": fact_id},
            )
            .mappings()
            .one()
        )
        features = {
            "application_id": application_id,
            "decision": decision.model_dump(mode="json"),
            "before_window": before.get(str(fact_id)),
            "after_window": dict(after),
        }
        connection.execute(
            text(f"""INSERT INTO {kind}_adjudications(adjudication_id,deployment_id,{id_column},
            outcome,method,confidence,triggering_claim_id,features,adjudicator_version,consumed_claim_ids)
            VALUES (:adjudication_id,:deployment_id,:fact_id,CAST(:outcome AS adjudication_outcome),'small_model',
                    :confidence,:claim_id,CAST(:features AS jsonb),:adjudicator_version,:consumed_claim_ids)
        """),
            {
                **params,
                "adjudication_id": uuid4(),
                "fact_id": fact_id,
                "outcome": "add"
                if fact_id in created
                else "update"
                if fact_id in window_updates
                else "contradict"
                if fact_id in grouped
                else "noop",
                "confidence": decision.confidence,
                "features": canonical_json(features),
                "adjudicator_version": snapshot["adjudicator_version"],
                "consumed_claim_ids": sorted(claims),
            },
        )
    connection.execute(
        text(
            """DELETE FROM normalize_observation_staging s WHERE s.deployment_id=:deployment_id AND s.application_id=:application_id
            AND EXISTS(SELECT 1 FROM obs_flush_entity_units u WHERE u.deployment_id=s.deployment_id
                AND u.version_id=s.version_id AND u.normalizer_version=s.normalizer_version AND u.subject_entity_id=s.subject_entity_id)"""
        ),
        params,
    )
    return result


def _recount_link(
    *,
    connection: Connection,
    kind: str,
    deployment_id: UUID,
    fact_id: UUID,
    claim_id: UUID,
    normalizer_version: str,
) -> None:
    """Aggregate assertion pointers plus the retained legacy stance for one link."""
    evidence, id_column, pointer = (
        f"{kind}_evidence",
        f"{kind}_id",
        f"support_{kind}_id",
    )
    params = {
        "deployment_id": deployment_id,
        "fact_id": fact_id,
        "claim_id": claim_id,
        "normalizer_version": normalizer_version,
    }
    stances = list(
        connection.execute(
            text(f"""SELECT support_stance::text FROM fact_applications
        WHERE deployment_id=:deployment_id AND {pointer}=:fact_id AND claim_id=:claim_id
        UNION ALL SELECT legacy_stance::text FROM {evidence}
        WHERE deployment_id=:deployment_id AND {id_column}=:fact_id AND claim_id=:claim_id AND legacy_stance IS NOT NULL
    """),
            params,
        ).scalars()
    )
    if not stances:
        connection.execute(
            text(
                f"DELETE FROM {evidence} WHERE deployment_id=:deployment_id AND {id_column}=:fact_id AND claim_id=:claim_id"
            ),
            params,
        )
        return
    params["stance"] = "supports" if "supports" in stances else "contradicts"
    connection.execute(
        text(f"""INSERT INTO {evidence}(deployment_id,{id_column},claim_id,doc_id,stance,normalizer_version,legacy_stance)
      SELECT :deployment_id,:fact_id,claim_id,doc_id,CAST(:stance AS evidence_stance),:normalizer_version,NULL
      FROM claims WHERE deployment_id=:deployment_id AND claim_id=:claim_id
      ON CONFLICT ({id_column},claim_id) DO UPDATE SET stance=excluded.stance
    """),
        params,
    )


def _recount_fact(
    *, connection: Connection, kind: str, deployment_id: UUID, fact_id: UUID
) -> None:
    """Recount distinct current source lineages; completed world periods still count."""
    table = "relations" if kind == "relation" else "observations"
    connection.execute(
        text(f"""UPDATE {table} f SET evidence_count=x.supports,contradict_count=x.contradicts,updated_at=now()
        FROM (SELECT count(DISTINCT e.doc_id) FILTER(WHERE e.stance='supports') AS supports,
                     count(DISTINCT e.doc_id) FILTER(WHERE e.stance='contradicts') AS contradicts
              FROM {kind}_evidence e JOIN claims c USING(deployment_id,claim_id)
              WHERE e.deployment_id=:deployment_id AND e.{kind}_id=:fact_id AND c.is_current_testimony) x
        WHERE f.deployment_id=:deployment_id AND f.{kind}_id=:fact_id
    """),
        {"deployment_id": deployment_id, "fact_id": fact_id},
    )
