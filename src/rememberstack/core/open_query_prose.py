"""Shared product prose for open-query discovery, skill, and OSS docs (§6).

One authority owns the bound two-layer headline, the three neutral retrieval
choices, the four bound SQL examples, honesty warnings, the native Cypher
worked example, the semantic-to-relational purpose/SQL, and the structured
worked-example set. Discovery, the consumption skill, and
``examples.claims_verbatim`` import from here so those strings cannot drift
between surfaces. Public docs copy the bound text and tests pin equality;
they do not import this module.

The checked-in limits manifest keeps its own valid ``query_cypher`` node-list
example (Batch D/E surface). First-call discovery and the skill use
``NATIVE_CYPHER_TRAVERSAL_AGGREGATION`` as the traversal/aggregation worked
example — that constant is not a manifest hash input and must not be imported
into the hashed surface solely to improve prose.

This module stays pure: core may depend only on model (import-linter).
"""

from __future__ import annotations

from typing import Final

# Bound in the design's opening block ("Bound two-layer retrieval headline
# (reused verbatim)") — discovery, the consumption skill, and the OSS docs
# all open with this exact text.
TWO_LAYER_HEADLINE: Final = (
    "RememberStack has two deliberately separate truth layers. Claims are"
    " immutable source testimony (“what was asserted, by whom, when”);"
    " facts—relations and observations—are the adjudicated worldview (“what the"
    " system holds or held true”):"
    " supersession-adjudicated, clocked on two time axes (when a fact held in"
    " the world, and when the system learned it), evidence-counted per"
    " distinct source—repetition is not corroboration—and"
    " contradiction-tracked. The `fact_claim_evidence` association is the"
    " auditable bridge between the layers, recording which claims support or"
    " contradict each fact. Query claims to inspect testimony; query facts to"
    " answer current or historical truth questions, then follow the bridge to"
    " see why the system believes or believed the fact."
)

#: The design's separate final parenthetical under the bound headline block.
TWO_LAYER_HEADLINE_NOTE: Final = (
    "(Internally these guarantees are decisions D41 and D54.)"
)

#: Full bound headline for skill and docs openings (paragraph + note).
TWO_LAYER_HEADLINE_FULL: Final = f"{TWO_LAYER_HEADLINE}\n\n{TWO_LAYER_HEADLINE_NOTE}"

#: The three neutral first-call choices (§6); no language is preferred.
RETRIEVAL_CHOICES: Final[tuple[str, ...]] = (
    "Cypher gives native graph power over a complete, point-in-time P2"
    " snapshot with mandatory built_at and age.",
    "SQL gives live PostgreSQL state and direct evidence composition.",
    "The four assured operations (resolve_entity, testimony_context,"
    " fact_context, answer_context) give one-call typed answers with explicit"
    " Envelope or ContextBundle/v1 guarantees.",
)

#: Bound wrong current-truth query: claim windows are testimony, not verdict.
WRONG_CLAIM_WINDOW_CURRENT_TRUTH_SQL: Final = """\
SELECT claim_id, claim_text, claim_valid_from, claim_valid_until
FROM claims_live
WHERE claim_valid_from <= $1::timestamptz
  AND (claim_valid_until IS NULL
       OR claim_valid_until >= $1::timestamptz);\
"""

#: Bound correct current-truth replacement: adjudicated facts plus evidence.
CORRECT_FACTS_CURRENT_SQL: Final = """\
SELECT f.*, e.claim_id, e.stance, e.source_handle
FROM facts_current AS f
JOIN fact_claim_evidence_live AS e
  USING (deployment_id, fact_kind, fact_id)
ORDER BY f.fact_kind, f.fact_id, e.stance, e.claim_id;\
"""

#: Bound predicate-vocabulary discovery before writing filters.
PREDICATE_VOCABULARY_SQL: Final = """\
SELECT predicate, count(*) FROM facts_current GROUP BY 1 ORDER BY 2 DESC;\
"""

#: Bound full audit: fact → evidence → claim → live document lineage.
FULL_AUDIT_TRAIL_SQL: Final = """\
SELECT f.fact_kind, f.fact_id, f.predicate,
       e.stance, e.source_handle,
       c.claim_id, c.claim_text, c.asserted_at,
       d.doc_id
FROM facts_current AS f
JOIN fact_claim_evidence_live AS e
  USING (deployment_id, fact_kind, fact_id)
JOIN claims_live AS c
  USING (deployment_id, claim_id)
JOIN documents_live AS d
  ON d.deployment_id = c.deployment_id
 AND d.doc_id = c.doc_id
WHERE f.fact_id = $1::uuid
ORDER BY e.stance, c.asserted_at DESC, d.doc_id, c.claim_id;\
"""

#: Bound two-layer divergence: newest current testimony contradicts the fact.
LATEST_CONTRADICTING_TESTIMONY_SQL: Final = """\
WITH ranked_testimony AS (
  SELECT e.deployment_id, e.fact_kind, e.fact_id,
         e.claim_id, e.stance, c.claim_text, c.asserted_at,
         row_number() OVER (
           PARTITION BY e.deployment_id, e.fact_kind, e.fact_id
           ORDER BY c.asserted_at DESC NULLS LAST, c.claim_id
         ) AS testimony_rank
  FROM fact_claim_evidence_live AS e
  JOIN claims_live AS c
    USING (deployment_id, claim_id)
)
SELECT f.*, r.claim_id, r.claim_text, r.asserted_at, r.stance
FROM facts_current AS f
JOIN ranked_testimony AS r
  USING (deployment_id, fact_kind, fact_id)
WHERE r.testimony_rank = 1
  AND r.stance = 'contradicts';\
"""

#: Snapshot-ID-to-live-SQL composition: Cypher returns entity ids; live SQL
#: re-grounds them without claiming the graph row is current.
SNAPSHOT_ID_TO_LIVE_SQL: Final = """\
-- After query_cypher returns entity ids as of built_at, re-ground live:
SELECT e.entity_id, e.canonical_name, e.profile_summary
FROM entities_current AS e
WHERE e.entity_id = ANY($1::uuid[]);\
"""

#: Native Cypher worked example: hop traversal plus aggregation (not a node list).
#: Single authority for first-call discovery/skill worked examples (§6).
#: Not imported by the hashed limits manifest (see module docstring).
NATIVE_CYPHER_TRAVERSAL_AGGREGATION: Final = (
    "MATCH (a:Entity)-[r:RELATES]->(b:Entity) "
    "RETURN a.name AS subject, r.predicate AS predicate, count(*) AS n "
    "ORDER BY n DESC, subject, predicate "
    "LIMIT 20"
)

#: Purpose string for the shipped ``examples.claims_verbatim`` identity.
CLAIMS_VERBATIM_PURPOSE: Final = (
    "Claims as asserted, nominated semantically and joined to live testimony"
)

#: SQL body for the shipped ``examples.claims_verbatim`` identity (byte-stable).
CLAIMS_VERBATIM_SQL: Final = (
    "SELECT s.claim_id, s.rank, s.channel, c.claim_text, c.source_handle,"
    "       c.source_kind, c.asserted_at"
    " FROM semantic_claims($1, 20) AS s"
    " JOIN claims_live AS c ON c.claim_id = s.claim_id"
    " ORDER BY s.rank, s.claim_id"
    " LIMIT 20"
)

HONESTY_WARNINGS: Final[tuple[str, ...]] = (
    "Claims are immutable source testimony; they do not answer current-truth"
    " questions.",
    "Empty SQL is untyped exploratory_tabular: a view's source grain is never"
    " a claim about an arbitrary outer query's result grain.",
    "Cypher absence and aggregates are snapshot-scoped: correct as of"
    " built_at, not a claim about state after that cut.",
    "Outer queries can erase grain and evidence context; inspect joins and"
    " projections before treating rows as adjudicated facts.",
    "Every cap and drop is part of the contract; inspect truncation_reason,"
    " warnings, and confirmation/nomination drop counts.",
)


def bound_worked_examples() -> tuple[dict[str, object], ...]:
    """Structured first-call worked examples shared by discovery and the skill.

    Eight examples: the four design-bound SQL bodies, two-layer divergence,
    native Cypher traversal/aggregation, snapshot-ID re-ground, and
    semantic-to-relational (``examples.claims_verbatim`` purpose/SQL). All
    string bodies live in this module so core stays pure.
    """
    return (
        {
            "key": "wrong_claim_window_current_truth",
            "title": "Contrast pair — wrong current-truth",
            "purpose": (
                "WRONG: a claim's immutable validity window says when a"
                " source's testimony applies, not what the system currently"
                " believes."
            ),
            "language": "sql",
            "body": WRONG_CLAIM_WINDOW_CURRENT_TRUTH_SQL,
            "role": "wrong",
            "source": "open_query_prose",
        },
        {
            "key": "correct_facts_current",
            "title": "Contrast pair — right current-truth",
            "purpose": (
                "RIGHT: start from adjudicated current facts and join each"
                " fact to the current testimony that supports or contradicts"
                " it."
            ),
            "language": "sql",
            "body": CORRECT_FACTS_CURRENT_SQL,
            "role": "right",
            "source": "open_query_prose",
        },
        {
            "key": "predicate_vocabulary",
            "title": "Predicate-vocabulary discovery",
            "purpose": (
                "Discover the deployed fact vocabulary before writing"
                " predicate filters."
            ),
            "language": "sql",
            "body": PREDICATE_VOCABULARY_SQL,
            "role": None,
            "source": "open_query_prose",
        },
        {
            "key": "full_audit_trail",
            "title": "Full audit trail",
            "purpose": (
                "Walk fact → live evidence association → immutable claim →"
                " live source lineage."
            ),
            "language": "sql",
            "body": FULL_AUDIT_TRAIL_SQL,
            "role": None,
            "source": "open_query_prose",
        },
        {
            "key": "latest_contradicting_testimony",
            "title": "Two-layer divergence",
            "purpose": (
                "Find a current adjudicated fact whose newest current"
                " testimony contradicts it."
            ),
            "language": "sql",
            "body": LATEST_CONTRADICTING_TESTIMONY_SQL,
            "role": None,
            "source": "open_query_prose",
        },
        {
            "key": "native_cypher_traversal_aggregation",
            "title": "Native Cypher traversal/aggregation",
            "purpose": (
                "Native graph power over the disclosed P2 snapshot; absence"
                " and aggregates are correct as of built_at only."
            ),
            "language": "cypher",
            "body": NATIVE_CYPHER_TRAVERSAL_AGGREGATION,
            "role": None,
            "source": "open_query_prose",
        },
        {
            "key": "snapshot_id_to_live_sql",
            "title": "Snapshot-ID-to-live-SQL composition",
            "purpose": (
                "After query_cypher returns entity ids as of built_at,"
                " re-ground them in live SQL without treating the graph row"
                " as current."
            ),
            "language": "sql",
            "body": SNAPSHOT_ID_TO_LIVE_SQL,
            "role": None,
            "source": "open_query_prose",
        },
        {
            "key": "semantic_to_relational",
            "title": "Semantic-to-relational composition",
            "purpose": CLAIMS_VERBATIM_PURPOSE,
            "language": "sql",
            "body": CLAIMS_VERBATIM_SQL,
            "role": None,
            "source": "examples.claims_verbatim",
        },
    )
