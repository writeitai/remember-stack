"""Drop the generic-identifier guard; a shared name is not a weaker name.

revision: p9_22_0043

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

Nor did the flag ever reach a decision: it lived only in ORDER BY clauses,
never on a candidate, never in decision features, never seen by T3 or T4.
Its whole effect was ranking, and the ranking it produced was inverted.

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

revision: str = "p9_22_0043"
down_revision: str | None = "p9_21_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# One statement, one transaction. Dropping a table takes AccessExclusiveLock
# on that table alone; nothing references it by foreign key, so no other
# relation is locked and no scan runs. The table is small by construction
# (one row per distinct normalized lemma) and, as of the revision that
# accompanies this one, is written by nothing.
_DROP_TABLE = "DROP TABLE IF EXISTS generic_identifier_guard"


# Recreated empty on downgrade, which is behaviourally exact rather than
# merely convenient: the resolver that read this table wrapped every lookup
# in coalesce(guard.is_downweighted, false), so "no row" and "not
# downweighted" were already the same state. An empty table therefore
# restores the older code to its no-lemma-flagged behaviour, and the older
# code repopulates it on the next resolve.
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


def upgrade() -> None:
    """Remove the promiscuous-lemma cache and its inverted ranking input."""
    op.execute(_DROP_TABLE)


def downgrade() -> None:
    """Restore the empty cache; an empty guard flags nothing, as before."""
    op.execute(_RECREATE_TABLE)
