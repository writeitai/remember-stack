"""Shared D112 complete-target certificate predicate for replay and completion."""


def relation_receipt_valid_sql(*, alias: str) -> str:
    """Build an internal receipt predicate including digest, cardinality and live targets."""
    if not alias.isidentifier():
        raise ValueError("receipt alias must be an internal SQL identifier")
    return f"""EXISTS (
        SELECT 1 FROM relation_application_targets rt
        LEFT JOIN relations rf ON rf.deployment_id=rt.deployment_id AND rf.relation_id=rt.relation_id
        WHERE rt.deployment_id={alias}.deployment_id AND rt.assertion_id={alias}.assertion_id
          AND rt.adjudicator_version={alias}.adjudicator_version
        HAVING count(*)={alias}.target_count AND count(*) > 0
          AND ({alias}.identity_outcome='evidence' OR ({alias}.identity_outcome='new' AND count(*)=1))
          AND bool_and(rf.relation_id IS NOT NULL)
          AND encode(sha256(convert_to('[' || string_agg(to_json(rt.relation_id::text)::text, ',' ORDER BY rt.relation_id) || ']', 'UTF8')), 'hex')={alias}.target_digest
    )"""
