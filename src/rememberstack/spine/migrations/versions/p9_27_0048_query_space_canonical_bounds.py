"""Expose canonical bounds in the query space (D107 §5, WP-T.0b).

``memory_v1.canonical_bounds`` wraps the public IMMUTABLE twins from
``p9_26_0047`` so open SQL can canonicalise a stored D41 window without
EXECUTE on the public schema. ``memory_v1.claims_canonical`` is
``claims_visible_history`` plus the half-open ``canon_start`` / ``canon_end``
columns those twins compute. Claim storage is unchanged.

revision: p9_27_0048
"""

from alembic import op

from rememberstack.spine.migrations._helpers import apply_view_ddl

revision: str = "p9_27_0048"
down_revision: str | None = "p9_26_0047"
branch_labels = None
depends_on = None

_VIEW_OWNER = "rememberstack_view_owner"

CANONICAL_BOUNDS_FUNCTION_DDL = r"""
CREATE FUNCTION memory_v1.canonical_bounds(
  valid_from timestamptz,
  valid_until timestamptz,
  valid_precision text
)
RETURNS TABLE(canon_start timestamptz, canon_end timestamptz)
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
  SELECT
    claim_canonical_start(
      valid_from, valid_precision::claim_valid_precision
    ) AS canon_start,
    claim_canonical_end(
      valid_from, valid_until, valid_precision::claim_valid_precision
    ) AS canon_end
$$;
"""

CLAIMS_CANONICAL_VIEW_DDL = r"""
CREATE VIEW memory_v1.claims_canonical (
  deployment_id,           -- The deployment that owns the claim.
  claim_id,                -- Stable identity of this immutable claim.
  doc_id,                  -- The live lineage that asserted the claim.
  version_id,              -- The non-tombstoned version the claim was extracted from.
  representation_id,       -- The reading whose character offsets the claim's anchors use.
  chunk_id,                -- The chunk the claim was extracted from.
  claim_text,              -- The standalone assertion as extracted, which is source testimony rather than adjudicated truth.
  source_span,             -- The verbatim slice of the source the claim derives from.
  char_start,              -- Start character offset of source_span within the named representation's markdown.
  char_end,                -- End character offset of source_span within the named representation's markdown.
  added_context,           -- The substrings decontextualization added, each with the bundle source it came from.
  temporal_class,          -- How the claim behaves over time, either static, dynamic, or atemporal; null when unclassified.
  is_attributed,           -- True when the claim preserves an attribution, so it entails that someone said it rather than that it holds.
  audit_status,            -- Result of the sampled independent grounding audit, defaulting to unaudited.
  kept_flagged,            -- True when selection kept the claim but marked it for review.
  extractor_version,       -- The extractor generation that produced the claim, which is part of the D54 extraction basis.
  asserted_at,             -- Assertion-event time: when the source asserted this, null when the source carries no date.
  claim_valid_from,        -- Immutable inclusive start of the world-time interval the SOURCE asserted, null for unbounded-before or unknown.
  claim_valid_until,       -- Immutable inclusive end of that interval, null for open-per-source or unknown as disambiguated by claim_valid_precision.
  claim_valid_precision,   -- Granularity of the asserted interval, from unknown through instant, day, month, quarter, and year to open.
  claim_valid_kind,        -- Which world-interval was asserted, such as event_time or measurement_period; null when unclassified.
  ingested_at,             -- Transaction-time: when this deployment extracted the claim.
  source_kind,             -- The connector family of the asserting lineage.
  source_handle,           -- Stable human-usable handle for the asserting lineage, formed from its connector-native identity.
  is_current_testimony,    -- True while this claim is the current transcription of its chunk under D54; false once a newer extraction generation or a living-mode version move superseded it.
  canon_start,             -- Inclusive start of the half-open canonical window (D107 §5); null when precision is unknown.
  canon_end                -- Exclusive end of the half-open canonical window; null when the window is open or unknown.
) AS
SELECT
  h.deployment_id,
  h.claim_id,
  h.doc_id,
  h.version_id,
  h.representation_id,
  h.chunk_id,
  h.claim_text,
  h.source_span,
  h.char_start,
  h.char_end,
  h.added_context,
  h.temporal_class,
  h.is_attributed,
  h.audit_status,
  h.kept_flagged,
  h.extractor_version,
  h.asserted_at,
  h.claim_valid_from,
  h.claim_valid_until,
  h.claim_valid_precision,
  h.claim_valid_kind,
  h.ingested_at,
  h.source_kind,
  h.source_handle,
  h.is_current_testimony,
  b.canon_start,
  b.canon_end
FROM memory_v1.claims_visible_history AS h
CROSS JOIN LATERAL memory_v1.canonical_bounds(
  h.claim_valid_from,
  h.claim_valid_until,
  h.claim_valid_precision
) AS b;
COMMENT ON VIEW memory_v1.claims_canonical IS
  'One row per historically visible claim with surviving lineage, keyed by (deployment_id, claim_id), carrying the stored inclusive D41 window beside the half-open canonical bounds that every overlap predicate must use (D107 §5). canon_start is inclusive and canon_end exclusive; both are null when precision is unknown, and canon_end is also null for an open window. Overlap is a.start < b.end AND b.start < a.end with a null end as unbounded. This relation is IMMUTABLE SOURCE TESTIMONY: it never answers what currently holds. Claims of forgotten lineages and tombstoned versions are absent.';
"""


def upgrade() -> None:
    """Publish the wrapper function and the claims_canonical view."""
    op.execute(
        "GRANT EXECUTE ON FUNCTION"
        " claim_canonical_start(timestamptz, claim_valid_precision)"
        f" TO {_VIEW_OWNER}"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION"
        " claim_canonical_end(timestamptz, timestamptz, claim_valid_precision)"
        f" TO {_VIEW_OWNER}"
    )
    op.execute(CANONICAL_BOUNDS_FUNCTION_DDL)
    op.execute(
        "COMMENT ON FUNCTION memory_v1.canonical_bounds(timestamptz, timestamptz, text)"
        " IS "
        + _quote(
            "D107 §5: half-open canonical bounds [canon_start, canon_end) for one "
            "stored D41 window. valid_precision is the claim_valid_precision "
            "vocabulary as text; unknown yields a null interval; open yields a "
            "null exclusive end. Wraps public.claim_canonical_start/end so the "
            "query role never needs EXECUTE on the public schema."
        )
    )
    op.execute(
        "ALTER FUNCTION memory_v1.canonical_bounds(timestamptz, timestamptz, text)"
        f" OWNER TO {_VIEW_OWNER}"
    )
    apply_view_ddl(sql=CLAIMS_CANONICAL_VIEW_DDL)
    op.execute(f"ALTER VIEW memory_v1.claims_canonical OWNER TO {_VIEW_OWNER}")
    op.execute(
        """
        DO $do$
        DECLARE
          query_role text := 'rememberstack_query_' || current_database();
        BEGIN
          EXECUTE format(
            'GRANT EXECUTE ON FUNCTION'
            ' memory_v1.canonical_bounds(timestamptz, timestamptz, text) TO %I',
            query_role
          );
          EXECUTE format(
            'GRANT SELECT ON memory_v1.claims_canonical TO %I',
            query_role
          );
        END
        $do$;
        """
    )


def downgrade() -> None:
    """Drop the published view and wrapper; public twins stay."""
    op.execute("DROP VIEW IF EXISTS memory_v1.claims_canonical")
    op.execute(
        "DROP FUNCTION IF EXISTS"
        " memory_v1.canonical_bounds(timestamptz, timestamptz, text)"
    )


def _quote(value: str) -> str:
    """One SQL string literal."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"
