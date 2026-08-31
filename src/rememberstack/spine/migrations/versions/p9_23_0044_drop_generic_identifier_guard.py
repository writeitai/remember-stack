"""Drop the generic-identifier guard; a shared name is not a weaker name.

revision: p9_23_0044

The guard recorded, per normalized string, how many distinct entities it
linked, and flagged the string once that count reached two. Blocking then
sorted on that flag AHEAD of match score, so a 0.95 trigram hit on a shared
name ranked below a 0.31 hit on an unshared one and could be truncated out
of the candidate list entirely.

The premise was wrong. Counting entity rows is not counting people: D95
forbids T0 from auto-merging, so the resolver deliberately mints a second
row for one real person, and the guard then read its own conservatism as
proof that the name was generic. Two Jan Nováks -- whether one person
recorded twice or two unrelated people -- are exactly the case the resolver
exists to adjudicate, and demoting both made the adjudication harder.

The flag was never adjudication evidence either: it lived only in ORDER BY
clauses, never on a candidate, never in decision features, never seen by T3
or T4. That is not the same as harmless. Blocking order decides which
candidates survive truncation to blocking_limit and which one T4 is told to
prefer, so an inverted ranking could change an authoritative verdict while
leaving no trace in the decision record it helped produce.

D21's original intent -- a genuinely promiscuous signal like a role address
or a shared reception number should not weld strangers together -- is not
abandoned; it is left to the mechanisms that actually adjudicate identity
(T3 profile evidence, T4, and the D21 resolution_exclusions cannot-link
edges), rather than to a blocking-stage counter with a floor of two.

The table is dropped rather than left unread. It is a per-deployment cache
with no lineage provenance -- keeping a stale copy of erasable surface
strings that nothing consumes is a retention cost with no benefit.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "p9_23_0044"
down_revision: str | None = "p9_22_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# One statement, one transaction, and no scan. The table is small by
# construction (one row per distinct normalized lemma) and, as of the
# revision that accompanies this one, is written by nothing.
#
# It is NOT a single-table lock, which is worth stating because the obvious
# assumption is wrong. `generic_identifier_guard.deployment_id` REFERENCES
# `deployments`, and dropping the table must drop that constraint, so
# PostgreSQL takes AccessExclusiveLock on `deployments` as well -- measured:
#
#   public.deployments                    AccessExclusiveLock
#   public.generic_identifier_guard       AccessExclusiveLock
#   public.generic_identifier_guard_pkey  AccessExclusiveLock
#   (plus the table's TOAST relation and its index)
#
# `deployments` is the tenancy root that nearly every query joins, and
# AccessExclusiveLock blocks readers as well as writers. The saving grace is
# duration, not scope: there is no scan and nothing to rewrite, so the lock
# is held only for the catalog updates in this one short transaction. Treat
# it as a brief global stall, not as a free operation, and apply it the way
# any other DDL against `deployments` would be applied.
_DROP_TABLE = "DROP TABLE IF EXISTS generic_identifier_guard"


# Plain CREATE, deliberately. An earlier revision of this file used
# IF NOT EXISTS plus an ON CONFLICT upsert; that was wrong twice over.
# PostgreSQL DDL here is transactional, so a run interrupted between the two
# statements below rolls back BOTH -- the partial state it claimed to guard
# against cannot occur. And the upsert only rewrites rows that collide, so
# any pre-existing row with no surviving alias group would have been left
# behind, quietly producing something that is not the reconstruction it
# advertises.
#
# If this table exists when the downgrade runs, the database disagrees with
# its own revision stamp. That is a real inconsistency and the migration
# should stop, not paper over it.
_RECREATE_TABLE = """
CREATE TABLE generic_identifier_guard (
  deployment_id   uuid NOT NULL REFERENCES deployments,
  normalized_lemma text NOT NULL,
  distinct_entity_count integer NOT NULL,
  is_downweighted boolean NOT NULL DEFAULT true,
  reason          text,
  evaluated_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (deployment_id, normalized_lemma)
);
COMMENT ON TABLE generic_identifier_guard IS
  'Surfaces that link too many entities to be identifying (D21). Down-weighted so they stop driving merges; the merges they already caused are re-evaluated — enumerated via merge_events.trigger_lemmas (below).';
"""

# Recreating the table EMPTY would not restore prior behaviour, only prior
# compatibility. The old reader used coalesce(is_downweighted, false), so
# old code runs fine against an empty table -- but every previously flagged
# lemma silently becomes unflagged, and the first fuzzy resolution after a
# rollback would rank differently than it did before the upgrade. The old
# writer only refreshes a lemma when that lemma is touched again, so the
# gap persists for anything not re-ingested.
#
# So the downgrade rebuilds the cache from the aliases that are still there,
# using the same COUNT(DISTINCT entity_id) and the same floor of 2 the old
# writer used. This is a reconstruction of a derived cache from existing
# rows, not seed DML; `evaluated_at` is honestly stamped now() because that
# is when the reconstruction happened.
_REBUILD_TABLE = """
INSERT INTO generic_identifier_guard (
    deployment_id, normalized_lemma, distinct_entity_count,
    is_downweighted, reason, evaluated_at
)
SELECT deployment_id, normalized_lemma, COUNT(DISTINCT entity_id),
       COUNT(DISTINCT entity_id) >= 2, 'promiscuous-lemma', now()
FROM aliases
GROUP BY deployment_id, normalized_lemma
"""


def upgrade() -> None:
    """Remove the promiscuous-lemma cache and its inverted ranking input."""
    # Fail fast rather than queue. An AccessExclusiveLock REQUEST on
    # `deployments` blocks every later query behind it while it waits, so an
    # unbounded wait behind one long read would stall the tenancy root far
    # longer than this migration itself ever does. Timing out and retrying in
    # a quieter moment is strictly safer than blocking the queue.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(_DROP_TABLE)


def downgrade() -> None:
    """Recreate the cache and rebuild it from the surviving aliases."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(_RECREATE_TABLE)
    op.execute(_REBUILD_TABLE)
