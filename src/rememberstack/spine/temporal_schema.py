"""Exact temporal schema checks shared by bootstrap, upgrades and serving gates."""

from sqlalchemy import text
from sqlalchemy.engine import Connection

from rememberstack.spine.query_space.ast_serializer import serialize_definition
from rememberstack.spine.query_space.canonical import CanonicalValue
from rememberstack.spine.temporal_journal import TemporalWriteConflict

TEMPORAL_FINAL_REVISION = "p9_30_0051"


def require_temporal_constraints_on(*, connection: Connection) -> None:
    """Reject missing, unvalidated or changed world-time constraints in either plane."""
    expected: dict[tuple[str, str], str] = {}
    for table, prefix in (("relations", "rel"), ("observations", "obs")):
        for suffix, definition in _CHECKS.items():
            expected[(table, f"ck_{prefix}_{suffix}")] = definition
    expected[("relations", "ex_rel_state_world_window")] = _EXCLUSION
    rows = connection.execute(_CONSTRAINTS).mappings()
    actual = {}
    for row in rows:
        key = (row["table_name"], row["conname"])
        if row["contype"] == "x" and key not in expected:
            raise TemporalWriteConflict(
                "unexpected temporal exclusion constraint remains"
            )
        if key in expected:
            if not row["convalidated"] or row["condeferrable"]:
                raise TemporalWriteConflict("temporal constraints are not finalized")
            actual[key] = _normalize_definition(value=row["definition"])
    if actual != {
        key: _normalize_definition(value=value) for key, value in expected.items()
    }:
        raise TemporalWriteConflict(
            "temporal constraint definitions differ from the accepted schema"
        )


def _normalize_definition(*, value: str) -> CanonicalValue:
    """Ignore display formatting while retaining expression grouping and every SQL operation."""
    for enum_type in (
        "fact_temporal_kind",
        "fact_temporal_basis",
        "claim_valid_precision",
    ):
        value = value.replace(f"::public.{enum_type}", f"::{enum_type}")
    return serialize_definition(
        authored_definition=f"ALTER TABLE relations ADD CONSTRAINT temporal_proof {value}"
    )


_CHECKS = {
    "state_nonempty": "CHECK (temporal_kind <> 'state'::fact_temporal_kind OR valid_from IS NULL OR valid_until IS NULL OR valid_until > valid_from)",
    "occurrence_uncapped": "CHECK (temporal_kind <> 'occurrence'::fact_temporal_kind OR valid_until IS NULL)",
    "occurs_nonempty": "CHECK (occurs_from IS NULL OR occurs_until IS NULL OR occurs_until > occurs_from)",
    "occurs_precision": "CHECK ((occurs_precision IS NULL AND occurs_from IS NULL AND occurs_until IS NULL) OR (occurs_precision IS NOT NULL AND occurs_precision <> 'unknown'::claim_valid_precision AND occurs_from IS NOT NULL))",
}
_EXCLUSION = """EXCLUDE USING gist (deployment_id WITH =, subject_entity_id WITH =,
predicate WITH =, object_entity_id WITH =, tstzrange(valid_from, valid_until, '[)'::text) WITH &&)
WHERE (temporal_kind = 'state'::fact_temporal_kind AND valid_from_basis <> 'erased'::fact_temporal_basis
AND valid_until_basis <> 'erased'::fact_temporal_basis AND invalidated_at IS NULL AND contradiction_group IS NULL)"""
_CONSTRAINTS = text("""
    SELECT relation.relname AS table_name, constraint_row.conname, constraint_row.contype,
      constraint_row.convalidated, constraint_row.condeferrable,
      pg_catalog.pg_get_constraintdef(constraint_row.oid) AS definition
    FROM pg_catalog.pg_constraint constraint_row
    JOIN pg_catalog.pg_class relation ON relation.oid = constraint_row.conrelid
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'public' AND relation.relname IN ('relations', 'observations')
      AND constraint_row.contype IN ('c', 'x')
""")
