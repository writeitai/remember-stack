"""Bounded adjudication inputs re-read under D118 claim and fact row locks."""

from collections.abc import Mapping
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection

from rememberstack.spine.fact_applications import ApplicationInputChanged
from rememberstack.spine.fact_applications import canonical_entity
from rememberstack.spine.fact_applications import canonical_json

# Bounds limit model input, never certify complete identity/evidence coverage.
_FACT_LIMIT = 20
_CLAIM_LIMIT = 100
_ASSERTION_LIMIT = 100


def _rows(
    *, connection: Connection, sql: str, params: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Materialize only explicitly bounded payload queries."""
    return [dict(row) for row in connection.execute(text(sql), dict(params)).mappings()]


def _digest_rows(*, connection: Connection, sql: str, params: Mapping[str, Any]) -> str:
    """Stream complete membership/accounting into a hash without retaining payloads."""
    digest = sha256()
    result = connection.execution_options(stream_results=True).execute(
        text(sql), dict(params)
    )
    try:
        for row in result.mappings():
            digest.update(canonical_json(dict(row)).encode())
            digest.update(b"\n")
    finally:
        result.close()
        connection.execution_options(stream_results=False)
    return digest.hexdigest()


def application_snapshot(
    *,
    connection: Connection,
    deployment_id: UUID,
    root: UUID,
    members: list[UUID],
    application: Mapping[str, Any],
    include_withdrawn: bool = False,
) -> dict[str, Any]:
    """Build coherent inputs after ordered locks, rejecting a changed participant set.

    The caller holds forget/identity/canonical entity locks. Candidate selection
    considers completed windows, and never uses dates as identity predicates.
    Model operations can address only the payload participants, not unseen members.
    """
    kind = application["output_kind"]
    if kind not in ("relation", "observation"):
        raise ValueError("unsupported fact plane")
    table, id_column = (
        ("relations", "relation_id")
        if kind == "relation"
        else ("observations", "observation_id")
    )
    evidence, pointer = f"{kind}_evidence", f"support_{kind}_id"
    params: dict[str, Any] = {
        "deployment_id": deployment_id,
        "members": members,
        "claim_id": application["claim_id"],
        "application_id": application["application_id"],
        "normalizer_version": application["normalizer_version"],
        "include_withdrawn": include_withdrawn,
    }
    incoming = _rows(
        connection=connection,
        sql="""
      SELECT a.application_id,a.claim_id,a.subject_entity_id,a.object_entity_id,
             a.output_kind,a.output_ordinal,a.normalizer_version,a.adjudicator_version,
             a.support_relation_id,a.support_observation_id,a.support_stance,
             CASE WHEN a.output_kind='relation' THEN n.output->'relations'->a.output_ordinal
                  ELSE n.output->'observations'->a.output_ordinal END AS assertion
      FROM fact_applications a JOIN normalization_outputs n USING(deployment_id,claim_id,normalizer_version)
      WHERE a.application_id=:application_id AND a.deployment_id=:deployment_id
    """,
        params=params,
    )
    if len(incoming) != 1:
        raise ApplicationInputChanged("incoming application disappeared")
    assertion = incoming[0]["assertion"]
    params["query"] = assertion.get("statement") or " ".join(
        (
            assertion["subject"]["name"],
            assertion["predicate"],
            assertion["object"]["name"],
        )
    )
    content = (
        "f.statement"
        if kind == "observation"
        else "concat(s.canonical_name,' ',f.predicate,' ',o.canonical_name)"
    )
    joins = (
        ""
        if kind == "observation"
        else "JOIN entities s ON s.entity_id=f.subject_entity_id JOIN entities o ON o.entity_id=f.object_entity_id"
    )
    selection = f"""SELECT f.{id_column} AS fact_id FROM {table} f {joins}
        WHERE f.deployment_id=:deployment_id AND f.subject_entity_id=ANY(:members)
          AND (:include_withdrawn OR f.invalidated_at IS NULL)
        ORDER BY ts_rank_cd(to_tsvector('simple',{content}),plainto_tsquery('simple',:query)) DESC,
                 f.{id_column} LIMIT {_FACT_LIMIT}"""
    nominated = _rows(connection=connection, sql=selection, params=params)
    params["fact_ids"] = sorted(row["fact_id"] for row in nominated)
    # Keep complete support membership in the fingerprint, but cap model copies.
    claim_selection = f"""SELECT c.claim_id FROM claims c
        WHERE c.deployment_id=:deployment_id AND (c.claim_id=:claim_id OR EXISTS (
          SELECT 1 FROM {evidence} e WHERE e.deployment_id=c.deployment_id
            AND e.claim_id=c.claim_id AND e.{id_column}=ANY(:fact_ids)))
        ORDER BY (c.claim_id=:claim_id) DESC,c.is_current_testimony DESC,c.asserted_at DESC NULLS LAST,c.claim_id
        LIMIT {_CLAIM_LIMIT}"""
    selected_claims = _rows(connection=connection, sql=claim_selection, params=params)
    params["claim_ids"] = sorted(row["claim_id"] for row in selected_claims)
    connection.execute(
        text(
            "SELECT claim_id FROM claims WHERE deployment_id=:deployment_id AND claim_id=ANY(:claim_ids) ORDER BY claim_id FOR UPDATE"
        ),
        params,
    ).all()
    connection.execute(
        text(
            f"SELECT {id_column} FROM {table} WHERE deployment_id=:deployment_id AND {id_column}=ANY(:fact_ids) ORDER BY {id_column} FOR UPDATE"
        ),
        params,
    ).all()
    # A lifecycle/review writer may have completed while these row locks waited.
    if {
        row["fact_id"]
        for row in _rows(connection=connection, sql=selection, params=params)
    } != set(params["fact_ids"]) or {
        row["claim_id"]
        for row in _rows(connection=connection, sql=claim_selection, params=params)
    } != set(params["claim_ids"]):
        raise ApplicationInputChanged(
            "candidate/evidence nomination changed during lock wait"
        )
    facts = _rows(
        connection=connection,
        sql=f"""
      SELECT f.{id_column} AS fact_id,f.subject_entity_id,{content} AS statement,
             f.valid_from,f.valid_until,f.valid_precision,f.window_claim_ids,
             f.ingested_at,f.invalidated_at,f.contradiction_group,f.evidence_count,f.contradict_count
             {",f.predicate,f.object_entity_id" if kind == "relation" else ""}
      FROM {table} f {joins} WHERE f.deployment_id=:deployment_id AND f.{id_column}=ANY(:fact_ids)
      ORDER BY f.{id_column}
    """,
        params=params,
    )
    for fact in facts:
        fact["window_claim_ids"] = sorted(fact["window_claim_ids"])
    claims = _rows(
        connection=connection,
        sql="""
      SELECT claim_id,doc_id,claim_text,source_span,asserted_at,claim_valid_from,claim_valid_until,
             claim_valid_precision,claim_valid_kind,is_current_testimony,is_attributed,extractor_version
      FROM claims WHERE deployment_id=:deployment_id AND claim_id=ANY(:claim_ids) ORDER BY claim_id
    """,
        params=params,
    )
    if application["claim_id"] not in {claim["claim_id"] for claim in claims}:
        raise ApplicationInputChanged("incoming source disappeared")
    supports = _rows(
        connection=connection,
        sql=f"""
      SELECT e.{id_column} AS fact_id,e.claim_id,e.stance,e.legacy_stance
      FROM {evidence} e WHERE e.deployment_id=:deployment_id AND e.{id_column}=ANY(:fact_ids)
        AND e.claim_id=ANY(:claim_ids) ORDER BY e.{id_column},e.claim_id
    """,
        params=params,
    )
    assertions = _rows(
        connection=connection,
        sql=f"""
      SELECT a.application_id,a.claim_id,a.subject_entity_id,a.object_entity_id,a.output_kind,
             a.output_ordinal,a.normalizer_version,a.adjudicator_version,
             a.support_relation_id,a.support_observation_id,a.support_stance,
             CASE WHEN a.output_kind='relation' THEN n.output->'relations'->a.output_ordinal
                  ELSE n.output->'observations'->a.output_ordinal END AS assertion
      FROM fact_applications a JOIN normalization_outputs n USING(deployment_id,claim_id,normalizer_version)
      WHERE a.deployment_id=:deployment_id AND a.{pointer}=ANY(:fact_ids) AND a.claim_id=ANY(:claim_ids)
        AND a.subject_entity_id=ANY(:members) AND a.application_id<>:application_id
      ORDER BY a.application_id LIMIT {_ASSERTION_LIMIT}
    """,
        params=params,
    )
    # Fact locks serialize every support pointer mutation before application locks.
    params["application_ids"] = sorted(
        [application["application_id"], *(row["application_id"] for row in assertions)]
    )
    connection.execute(
        text(
            "SELECT application_id FROM fact_applications WHERE deployment_id=:deployment_id AND application_id=ANY(:application_ids) ORDER BY application_id FOR UPDATE"
        ),
        params,
    ).all()
    member_hash = _digest_rows(
        connection=connection,
        sql=f"""
      SELECT {id_column} FROM {table} WHERE deployment_id=:deployment_id AND subject_entity_id=ANY(:members)
        AND (:include_withdrawn OR invalidated_at IS NULL) ORDER BY {id_column}
    """,
        params=params,
    )
    evidence_hash = _digest_rows(
        connection=connection,
        sql=f"""
      SELECT {id_column},claim_id,stance,legacy_stance FROM {evidence}
      WHERE deployment_id=:deployment_id AND {id_column}=ANY(:fact_ids) ORDER BY {id_column},claim_id
    """,
        params=params,
    )
    support_hash = _digest_rows(
        connection=connection,
        sql=f"""
      SELECT application_id,claim_id,{pointer},support_stance FROM fact_applications
      WHERE deployment_id=:deployment_id AND {pointer}=ANY(:fact_ids) ORDER BY application_id
    """,
        params=params,
    )
    all_assertions = sorted(
        [*incoming, *assertions], key=lambda row: row["application_id"]
    )
    for row in all_assertions:
        row["canonical_subject_id"] = canonical_entity(
            connection=connection,
            deployment_id=deployment_id,
            entity_id=row["subject_entity_id"],
        )
        row["canonical_object_id"] = (
            canonical_entity(
                connection=connection,
                deployment_id=deployment_id,
                entity_id=row["object_entity_id"],
            )
            if row["object_entity_id"]
            else None
        )
    return {
        "deployment_id": deployment_id,
        "root": root,
        "kind": kind,
        "application_id": application["application_id"],
        "normalizer_version": application["normalizer_version"],
        "adjudicator_version": application["adjudicator_version"],
        "assertions": all_assertions,
        "facts": facts,
        "claims": claims,
        "evidence": supports,
        "membership_hash": member_hash,
        "evidence_hash": evidence_hash,
        "support_hash": support_hash,
        "limits": {
            "facts": _FACT_LIMIT,
            "claims": _CLAIM_LIMIT,
            "assertions": _ASSERTION_LIMIT,
        },
        "potentially_truncated": len(facts) == _FACT_LIMIT
        or len(claims) == _CLAIM_LIMIT
        or len(assertions) == _ASSERTION_LIMIT,
    }
