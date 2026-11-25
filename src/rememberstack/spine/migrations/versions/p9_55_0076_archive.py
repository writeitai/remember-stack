"""D159 archive: archived lineages, the renamed currency reason, the reopen outcome.

Archive replaces D135's public delete. An archived lineage keeps every row
(it stays readable as history) and is recorded on ``documents.archived_at`` and
``archived_by`` (``caller`` or ``source``); ``deleted_at`` remains only for
forget's hide. The currency reason ``version_deleted`` is renamed
``version_archived``, and ``reopened_unarchive`` records an unarchive (or a
returning claim) reopening a fact an archive closed, without erasing the close.

Upgrade of D135 data: every lineage D135 deleted (and no D74 forget owns),
recognised by its signature — the lineage tombstoned with no version left
live — becomes an archive — ``caller`` for an explicit delete, ``source`` for a
connector-observed deletion — with ``archived_at`` set to its deletion time.
The currency events and closures of that lineage's current deletion episode
(the delete or connector finalization, plus the late reconcile runs of the
episode) are re-labelled with the one archive reconciliation id unarchive looks
for, and only the version tombstones the lineage deletion set (same instant as
the lineage's) are cleared. An earlier deletion the lineage already came back
from is left alone, and so are internal version tombstones.

Belief intervals: once a fact can be reopened, ``invalidated_at`` holds only
its current state. ``rememberstack_graph_internal.fact_believed_at`` refines
the single-column test from the fact's close and reopen adjudications, and
``memory_v1.facts_as_of``, the graph traversal helpers and the Python graph
reads apply it, so a read inside an archive interval still sees the fact
closed.
"""

from collections.abc import Sequence
from datetime import datetime
from datetime import UTC
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid5

from alembic import op
from sqlalchemy import text

from rememberstack.spine.migrations._helpers import apply_view_ddl

revision: str = "p9_55_0076"
down_revision: str | None = "p9_54_0075"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def archive_reconciliation_id(*, doc_id: UUID, archived_at: datetime) -> UUID:
    """The archive episode's reconciliation id (D159 §2.1).

    A frozen copy of ``spine.lifecycle.archive_reconciliation_id`` (a test
    keeps the two equal), so a later change to the runtime cannot change what
    this upgrade wrote. The instant is rendered in UTC, so the id does not
    depend on the session time zone that read it.
    """
    return uuid5(
        NAMESPACE_URL,
        f"rememberstack:archive:{doc_id}:{archived_at.astimezone(UTC).isoformat()}",
    )


DOCUMENTS_INVENTORY_DDL = r"""
CREATE OR REPLACE VIEW memory_v1.documents_live (
  deployment_id,               -- The deployment that owns this document lineage; every join to another memory_v1 relation carries it.
  doc_id,                      -- Stable lineage identity, unique within the deployment and never reused.
  source_kind,                 -- The connector family that produced the lineage, such as google_drive or upload.
  source_ref,                  -- The connector-native stable identifier, null for one-shot sources that have none.
  source_uri,                  -- The original location of the source, null when the source has no addressable location.
  title,                       -- Best-effort human title of the lineage, which is orientation text rather than asserted evidence.
  versioning_mode,             -- The D55 currency mode of the lineage, either snapshot or living.
  origin,                      -- The D42 provenance stamp, either external or system_generated.
  first_seen_at,               -- The instant this deployment first observed the lineage.
  last_observed_at,            -- The instant the connector last observed the lineage, null when it has never been re-observed.
  current_version_id,          -- The lineage's current snapshot, null when no non-tombstoned current version exists.
  current_version_no,          -- The one-based ordinal of the current version within the lineage, null when there is no visible current version.
  current_version_status,      -- Processing status of the current version, null when there is no visible current version.
  current_representation_id,   -- The current ready reading of the current version, null when no ready representation exists.
  has_current_ready_content,   -- True only when the lineage has a ready current version and a ready current representation, which is the precondition every current-content relation joins on.
  source_modified_at,          -- When the source says the current snapshot was authored, which dates derived testimony.
  published_at,                -- The document's own publication date on the current version, null when unknown.
  language,                    -- Detected primary language of the current version, null when undetected.
  archived_at                  -- When the lineage was archived (D159): its testimony ended and it is kept as history; null while live. Search and the context operations leave an archived lineage out unless asked.
) AS
SELECT
  d.deployment_id,
  d.doc_id,
  d.source_kind,
  d.source_ref,
  d.source_uri,
  d.title,
  d.versioning_mode::text,
  d.origin::text,
  d.first_seen_at,
  d.last_observed_at,
  v.version_id,
  v.version_no,
  v.status::text,
  r.representation_id,
  (r.representation_id IS NOT NULL AND v.status = 'ready'),
  v.source_modified_at,
  v.published_at,
  v.language,
  d.archived_at
FROM documents AS d
LEFT JOIN document_versions AS v
  ON v.deployment_id = d.deployment_id
 AND v.doc_id = d.doc_id
 AND v.version_id = d.current_version_id
 AND v.deleted_at IS NULL
LEFT JOIN document_representations AS r
  ON r.deployment_id = v.deployment_id
 AND r.version_id = v.version_id
 AND r.representation_id = v.current_representation_id
 AND r.status = 'ready'
WHERE d.deleted_at IS NULL;
COMMENT ON VIEW memory_v1.documents_live IS
  'One row per document lineage that has not been forgotten, keyed by (deployment_id, doc_id): the inventory. An archived lineage (D159) is listed with archived_at set; its claims are not current testimony, so every current-testimony relation leaves it out. A lineage hidden by forget is absent, and the current version and representation coordinates are produced only from a non-tombstoned version and a ready representation, so no column can name removed state. The two optional joins project coordinates of an already authorized lineage and admit no row of their own. This is a live-content relation, not a fact or evidence relation: title and source metadata are orientation, never asserted evidence, and the view carries no counts and no clock semantics beyond the observation instants it names.';

CREATE OR REPLACE VIEW memory_v1.testimony_currency_events_visible (
  deployment_id,
  event_id,
  claim_id,
  doc_id,
  reconciliation_id,
  became_current,
  reason,                  -- Why currency changed, one of reextracted, version_superseded, version_archived, or review_restored.
  from_extractor_version,
  from_version_id,
  occurred_at
) AS
SELECT
  e.deployment_id,
  e.event_id,
  e.claim_id,
  e.doc_id,
  e.reconciliation_id,
  e.became_current,
  e.reason::text,
  e.from_extractor_version,
  fv.version_id,
  e.occurred_at
FROM testimony_currency_events AS e
JOIN memory_v1.documents_live AS dl
  ON dl.deployment_id = e.deployment_id
 AND dl.doc_id = e.doc_id
JOIN memory_v1.claims_visible_history AS ch
  ON ch.deployment_id = e.deployment_id
 AND ch.claim_id = e.claim_id
 AND ch.doc_id = e.doc_id
LEFT JOIN memory_v1.document_versions_visible AS fv
  ON fv.deployment_id = e.deployment_id
 AND fv.version_id = e.from_version_id
 AND fv.doc_id = e.doc_id;
"""
"""The D159 inventory column on ``documents_live`` and the renamed currency
reason in the currency-event view's documentation."""


def _crossrefs_view(*, archived_target_hidden: bool) -> str:
    """``document_crossrefs_live`` as D140 authored it, or without archived targets."""
    from rememberstack.spine.migrations.versions.p9_38_0059_d140_effective_time import (
        MEMORY_V1_D140_VIEW_DDL,
    )

    start = MEMORY_V1_D140_VIEW_DDL.index(
        "CREATE VIEW memory_v1.document_crossrefs_live ("
    )
    end = MEMORY_V1_D140_VIEW_DDL.index("COMMENT ON VIEW", start)
    view = MEMORY_V1_D140_VIEW_DDL[start:end].replace(
        "CREATE VIEW", "CREATE OR REPLACE VIEW", 1
    )
    if not archived_target_hidden:
        return view
    old = "WHERE x.resolved AND x.to_doc_id IS NOT NULL;"
    if view.count(old) != 1:
        raise RuntimeError("unexpected document_crossrefs_live shape")
    return view.replace(
        old,
        "WHERE x.resolved AND x.to_doc_id IS NOT NULL\n"
        "  -- D159: an archived target is target_unavailable until it comes back\n"
        "  AND target.archived_at IS NULL;",
    )


CROSSREFS_ARCHIVE_DDL = _crossrefs_view(archived_target_hidden=True)


def _fact_source_view(*, moot_reviews_hidden: bool) -> str:
    """``v_memory_fact_source_qualified`` as D153 authored it, or with moot reviews."""
    from rememberstack.spine.migrations.versions.p9_48_0069_shared_eligibility import (
        FACT_SOURCE_QUALIFIED_DDL,
    )

    view = FACT_SOURCE_QUALIFIED_DDL.replace(
        "CREATE VIEW v_memory_fact_source_qualified",
        "CREATE OR REPLACE VIEW v_memory_fact_source_qualified",
        1,
    )
    if not moot_reviews_hidden:
        return view
    old = (
        "      AND queue.candidate ->> 'fact_id' = fact.fact_id::text\n"
        "  ) THEN 'withdrawn'"
    )
    if view.count(old) != 1:
        raise RuntimeError("unexpected v_memory_fact_source_qualified shape")
    return view.replace(
        old,
        "      AND queue.candidate ->> 'fact_id' = fact.fact_id::text\n"
        "      -- D159: a review on an archived document's claim is moot\n"
        "      AND NOT EXISTS (\n"
        "        SELECT 1 FROM claims AS moot_claim\n"
        "        JOIN documents AS moot_document\n"
        "          ON moot_document.deployment_id = moot_claim.deployment_id\n"
        "         AND moot_document.doc_id = moot_claim.doc_id\n"
        "        WHERE moot_claim.deployment_id = queue.deployment_id\n"
        "          AND moot_claim.claim_id = CAST(queue.candidate ->> 'claim_id' AS uuid)\n"
        "          AND moot_document.archived_at IS NOT NULL\n"
        "      )\n"
        "  ) THEN 'withdrawn'",
    )


FACT_SOURCE_ARCHIVE_DDL = _fact_source_view(moot_reviews_hidden=True)
"""A review on an archived document's claim no longer marks its fact withdrawn."""
"""References to an archived target leave the SQL reference view (D159 §2.2)."""


def archived_target_projection(*, sql: str) -> str:
    """The graph's citation projection without archived targets (D159)."""
    old = " AND target.deleted_at IS NULL\nWHERE"
    if sql.count(old) != 1:
        raise RuntimeError("unexpected citation projection shape")
    return sql.replace(
        old, " AND target.deleted_at IS NULL\n AND target.archived_at IS NULL\nWHERE"
    )


FACT_BELIEVED_AT_SIGNATURE = (
    "rememberstack_graph_internal.fact_believed_at(text, uuid, timestamptz)"
)

FACT_BELIEVED_AT_DDL = r"""
CREATE FUNCTION rememberstack_graph_internal.fact_believed_at(
  fact_kind text,
  fact_id uuid,
  believed_at timestamptz
)
RETURNS boolean
LANGUAGE sql
STABLE
PARALLEL SAFE
SECURITY DEFINER
-- two indexed probes of one fact's adjudications; costed as such so the
-- traversal plans (and JIT thresholds) stay what the evidence gate made them
COST 10
SET search_path = pg_catalog, public, pg_temp
AS $$
  -- not believed only when the fact was reopened at some point and its
  -- latest close or reopen event at or before the instant is a close
  SELECT NOT CASE $1
    WHEN 'relation' THEN
      EXISTS (
        SELECT 1 FROM public.relation_adjudications AS a
        WHERE a.relation_id = $2 AND a.outcome = 'reopened_unarchive'
      )
      AND coalesce((
        SELECT a.outcome <> 'reopened_unarchive'
        FROM public.relation_adjudications AS a
        WHERE a.relation_id = $2
          AND a.outcome IN ('retracted_source_removal', 'invalidated', 'reopened_unarchive')
          AND a.decided_at <= $3
        ORDER BY a.decided_at DESC, a.adjudication_id DESC
        LIMIT 1
      ), false)
    WHEN 'observation' THEN
      EXISTS (
        SELECT 1 FROM public.observation_adjudications AS a
        WHERE a.observation_id = $2 AND a.outcome = 'reopened_unarchive'
      )
      AND coalesce((
        SELECT a.outcome <> 'reopened_unarchive'
        FROM public.observation_adjudications AS a
        WHERE a.observation_id = $2
          AND a.outcome IN ('retracted_source_removal', 'invalidated', 'reopened_unarchive')
          AND a.decided_at <= $3
        ORDER BY a.decided_at DESC, a.adjudication_id DESC
        LIMIT 1
      ), false)
    ELSE false
  END
$$;
COMMENT ON FUNCTION rememberstack_graph_internal.fact_believed_at(text, uuid, timestamptz) IS
  'D159 refinement of belief at an instant for a fact whose single-column test (invalidated_at IS NULL OR invalidated_at > believed_at) already holds: false only when the fact was ever reopened by unarchive and its latest close or reopen adjudication at or before believed_at is a close. invalidated_at holds only the current state once a fact can reopen. SECURITY DEFINER over the adjudication transcripts; EXECUTE for the graph and query roles. Recreated with the graph schema by every graph rebuild; callers apply it after the cheap test, over anchored candidates.';
"""

FACT_BELIEVED_AT_GRANTS = r"""
REVOKE ALL ON FUNCTION rememberstack_graph_internal.fact_believed_at(text, uuid, timestamptz) FROM PUBLIC;
DO $do$
BEGIN
  EXECUTE format(
    'GRANT EXECUTE ON FUNCTION rememberstack_graph_internal.fact_believed_at(text, uuid, timestamptz) TO %I, %I',
    'rememberstack_graph_' || current_database(),
    'rememberstack_query_' || current_database()
  );
END
$do$;
"""

_HELPER_GATE = (
    "      ) AS ordered\n"
    "      WHERE rememberstack_graph_internal.relation_evidence_in_scope(\n"
)


def belief_interval_helper(*, sql: str) -> str:
    """A traversal helper whose belief clock also reads close/reopen events.

    The edge scan keeps its single-column test; the refinement runs with the
    D140 evidence gate, lazily over each level's ordered candidates.
    """
    if sql.count(_HELPER_GATE) != 1:
        raise RuntimeError("unexpected traversal helper gate")
    return sql.replace(
        _HELPER_GATE,
        "      ) AS ordered\n"
        "      WHERE (valid_at IS NULL OR rememberstack_graph_internal.fact_believed_at(\n"
        "          'relation', ordered.relation_id, clock_believed))\n"
        "        AND rememberstack_graph_internal.relation_evidence_in_scope(\n",
    )


def facts_as_of_ddl() -> str:
    """``memory_v1.facts_as_of`` with belief intervals from close/reopen events."""
    from rememberstack.spine.migrations.versions.p9_30_0051_mutable_fact_windows import (
        FACTS_AS_OF_DDL,
    )

    old = "AND (f.invalidated_at IS NULL OR f.invalidated_at > facts_as_of.believed_at)"
    new = (
        old + "\n    AND rememberstack_graph_internal.fact_believed_at("
        "f.fact_kind, f.fact_id, facts_as_of.believed_at)"
    )
    if FACTS_AS_OF_DDL.count(old) != 1:
        raise RuntimeError("unexpected facts_as_of belief predicate")
    return FACTS_AS_OF_DDL.replace(old, new).replace(
        "CREATE FUNCTION memory_v1.facts_as_of(",
        "CREATE OR REPLACE FUNCTION memory_v1.facts_as_of(",
    )


_SCHEMA = (
    "ALTER TABLE documents ADD COLUMN archived_at timestamptz",
    "ALTER TABLE documents ADD COLUMN archived_by text"
    " CHECK (archived_by IN ('caller', 'source'))",
    "ALTER TABLE documents ADD COLUMN archived_cycle_id uuid",
    "COMMENT ON COLUMN documents.archived_cycle_id IS"
    " 'D159 the sync cycle that observed a source archive; its cascade waits for"
    " that cycle''s finalization (the retract-timing barrier). NULL otherwise.'",
    "ALTER TABLE documents ADD CONSTRAINT documents_archived_pair"
    " CHECK ((archived_at IS NULL) = (archived_by IS NULL))",
    "COMMENT ON COLUMN documents.archived_at IS"
    " 'D159 archive: when the lineage stopped testifying; it stays readable as"
    " history (include_archived). NULL while live.'",
    "COMMENT ON COLUMN documents.archived_by IS"
    " 'D159 who archived: caller (an explicit archive) or source (a connector saw"
    " the file deleted). Identical bytes never undo a caller archive.'",
    "COMMENT ON COLUMN documents.deleted_at IS"
    " 'Forget''s hide tombstone (D159 §3.2); every read filters it.'",
    "ALTER TYPE currency_reason RENAME VALUE 'version_deleted' TO 'version_archived'",
)

_DELETED_LINEAGES = text(
    """
    SELECT d.doc_id, d.deleted_at, d.deleted_sync_cycle_id,
           CASE WHEN d.deleted_sync_cycle_id IS NULL THEN 'caller' ELSE 'source' END
             AS archived_by
    FROM documents d
    WHERE d.deleted_at IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM {forget_table} f WHERE f.doc_id = d.doc_id)
      -- D135's delete (caller or connector) tombstoned the lineage and its
      -- live versions at one instant (all of them, so none is left live; one
      -- removed earlier keeps its own instant). A lineage tombstone that
      -- left a live version behind is not a D135 deletion.
      AND (
          EXISTS (
              SELECT 1 FROM document_versions v
              WHERE v.doc_id = d.doc_id AND v.deleted_at = d.deleted_at
          )
          OR NOT EXISTS (
              SELECT 1 FROM document_versions v
              WHERE v.doc_id = d.doc_id AND v.deleted_at IS NULL
          )
      )
    ORDER BY d.doc_id
    """
)

_EPISODE_RUN_IDS = text(
    """
    SELECT DISTINCT e.reconciliation_id
    FROM testimony_currency_events e
    WHERE e.doc_id = :doc_id
      AND e.reason = 'version_archived'
      AND NOT e.became_current
      AND e.occurred_at >= :deleted_at
    UNION
    SELECT p.processing_id
    FROM processing_state p
    JOIN document_versions v
      ON v.deployment_id = p.deployment_id AND v.version_id = p.target_id
    WHERE p.target_kind = 'document_version'
      AND p.stage = 'reconcile'
      AND v.doc_id = :doc_id
    """
)

_RELABEL_EVENTS = text(
    """
    UPDATE testimony_currency_events
    SET reconciliation_id = :rid
    WHERE doc_id = :doc_id
      AND reason = 'version_archived'
      AND NOT became_current
      AND occurred_at >= :deleted_at
    """
)

_RELABEL_CLOSURES = tuple(
    text(
        f"""
        UPDATE {table}
        SET features = coalesce(features, '{{}}'::jsonb)
            || jsonb_build_object('reconciliation_id', CAST(:rid AS text),
                                  'archive', true)
        WHERE outcome = 'retracted_source_removal'
          AND decided_at >= :deleted_at
          AND features ->> 'reconciliation_id' = ANY(CAST(:run_ids AS text[]))
        """
    )
    for table in ("relation_adjudications", "observation_adjudications")
)

_CLEAR_EPISODE_TOMBSTONES = text(
    """
    UPDATE document_versions SET deleted_at = NULL
    WHERE doc_id = :doc_id AND deleted_at = :deleted_at
    """
)

_ARCHIVE_LINEAGE = text(
    """
    UPDATE documents
    SET archived_at = deleted_at, archived_by = :archived_by,
        archived_cycle_id = :cycle_id, deleted_at = NULL
    WHERE doc_id = :doc_id
    """
)


def _d135_run_ids(*, doc_id: UUID, at: datetime) -> tuple[UUID, ...]:
    """The run ids D135 derived for a lineage's deletion episode.

    A run that closed a fact without retiring a claim (its claim was already
    non-current) left no currency event to find it by: the caller's delete,
    the connector finalization, and the operator grain. D135 rendered the
    instant with ``isoformat()`` in the session time zone: ``at`` as this
    session reads it carries that offset, and the UTC rendering covers a UTC
    session.
    """
    instants = sorted({at.isoformat(), at.astimezone(UTC).isoformat()})
    return tuple(
        uuid5(NAMESPACE_URL, f"rememberstack:{kind}:{suffix}")
        for kind, suffix in (
            *(("delete-lineage", f"{doc_id}:{instant}") for instant in instants),
            *(("finalize-delete", f"{doc_id}:{instant}") for instant in instants),
            ("delete-lineage", f"{doc_id}"),
        )
    )


def upgrade() -> None:
    """Add the archive columns, rename the reason, convert D135 deletions."""
    # The new outcome must be committed before the belief function below can
    # name it (PostgreSQL refuses a new enum value in its own transaction).
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE adjudication_outcome ADD VALUE IF NOT EXISTS 'reopened_unarchive'"
        )
    for statement in _SCHEMA:
        op.execute(statement)
    connection = op.get_bind()
    # A D74 forget owns its lineage whether the forget slice has already turned
    # its manifests into forget records or not.
    forget_table = connection.execute(
        text(
            "SELECT coalesce(to_regclass('public.forgets'),"
            " to_regclass('public.forget_manifests'))::text"
        )
    ).scalar_one()
    rows = (
        connection.execute(
            text(
                _DELETED_LINEAGES.text.format(
                    forget_table=forget_table or "(SELECT NULL::uuid AS doc_id)"
                )
            )
        )
        .mappings()
        .all()
    )
    # Before any row update: an ALTER TABLE after one would meet the update's
    # pending foreign-key trigger events.
    op.execute("ALTER TABLE documents DROP COLUMN deleted_sync_cycle_id")
    for row in rows:
        doc_id: UUID = row["doc_id"]
        deleted_at: datetime = row["deleted_at"]
        rid = archive_reconciliation_id(doc_id=doc_id, archived_at=deleted_at)
        params = {"doc_id": doc_id, "deleted_at": deleted_at}
        run_ids = [
            str(run_id)
            for run_id in connection.execute(_EPISODE_RUN_IDS, params).scalars()
        ] + [str(run_id) for run_id in _d135_run_ids(doc_id=doc_id, at=deleted_at)]
        connection.execute(_RELABEL_EVENTS, {**params, "rid": rid})
        for statement in _RELABEL_CLOSURES:
            connection.execute(
                statement, {**params, "rid": str(rid), "run_ids": run_ids}
            )
        connection.execute(_CLEAR_EPISODE_TOMBSTONES, params)
        connection.execute(
            _ARCHIVE_LINEAGE,
            {
                "doc_id": doc_id,
                "archived_by": row["archived_by"],
                "cycle_id": row["deleted_sync_cycle_id"],
            },
        )
    apply_view_ddl(sql=DOCUMENTS_INVENTORY_DDL)
    op.execute(CROSSREFS_ARCHIVE_DDL)
    apply_view_ddl(sql=FACT_SOURCE_ARCHIVE_DDL)
    _install_belief_intervals()


def _install_belief_intervals() -> None:
    """facts_as_of, then the graph rebuild that installs the belief predicate.

    The predicate lives in the graph's internal schema (the query and graph
    roles already use it), so the graph rebuild — also the catalog repair
    path — recreates it with the traversal helpers that call it.
    """
    from rememberstack.spine.fact_graph_contract import rebuild_fact_graphs

    rebuild_fact_graphs(connection=op.get_bind())
    op.execute(facts_as_of_ddl())


def downgrade() -> None:
    """Drop the archive columns; refused while any lineage is archived.

    An archive cannot be turned back into a D135 deletion faithfully, and the
    reopen outcome stays (PostgreSQL cannot drop an enum value in place).
    ``documents_live`` keeps its appended ``archived_at`` column as a typed
    NULL: a view column cannot be dropped in place, and dropping the view would
    take every dependent query-space view with it. Upgrading again restores it.
    """
    connection = op.get_bind()
    if connection.execute(
        text("SELECT EXISTS (SELECT 1 FROM documents WHERE archived_at IS NOT NULL)")
    ).scalar_one():
        raise RuntimeError("D159 downgrade refuses a store with archived documents")
    if connection.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM relation_adjudications"
            " WHERE outcome = 'reopened_unarchive')"
            " OR EXISTS (SELECT 1 FROM observation_adjudications"
            " WHERE outcome = 'reopened_unarchive')"
        )
    ).scalar_one():
        raise RuntimeError("D159 downgrade refuses a store with reopened facts")
    from rememberstack.spine.fact_graph_contract import rebuild_fact_graphs
    from rememberstack.spine.migrations.versions.p9_30_0051_mutable_fact_windows import (
        FACTS_AS_OF_DDL,
    )

    op.execute(
        FACTS_AS_OF_DDL.replace(
            "CREATE FUNCTION memory_v1.facts_as_of(",
            "CREATE OR REPLACE FUNCTION memory_v1.facts_as_of(",
        )
    )
    op.execute(_crossrefs_view(archived_target_hidden=False))
    apply_view_ddl(sql=_fact_source_view(moot_reviews_hidden=False))
    # The prior graph shape first: its projection must stop reading the
    # archive column before the column goes.
    rebuild_fact_graphs(connection=connection, archive=False)
    inert = DOCUMENTS_INVENTORY_DDL.split("COMMENT ON VIEW memory_v1.documents_live")[0]
    if inert.count("  d.archived_at\n") != 1:
        raise RuntimeError("unexpected documents_live inventory shape")
    op.execute(inert.replace("  d.archived_at\n", "  NULL::timestamptz\n"))
    op.execute("ALTER TABLE documents ADD COLUMN deleted_sync_cycle_id uuid")
    op.execute(
        "ALTER TYPE currency_reason RENAME VALUE 'version_archived' TO 'version_deleted'"
    )
    op.execute("ALTER TABLE documents DROP CONSTRAINT documents_archived_pair")
    op.execute("ALTER TABLE documents DROP COLUMN archived_cycle_id")
    op.execute("ALTER TABLE documents DROP COLUMN archived_by")
    op.execute("ALTER TABLE documents DROP COLUMN archived_at")
