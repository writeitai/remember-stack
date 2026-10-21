# Proposal: index each distinct search input once

**Status:** not chosen (2026-10-21). Fully designed option for
[D142](../../decisions.md#d142-ranked-search-nominates-from-the-index-and-confirms-in-one-snapshot);
adoption is an **open owner decision** taken on the evidence named under
"Adoption trigger". **Analysis:**
[index-driven ranked search §7](../analysis/index_driven_ranked_search.md#7-duplicate-text-in-versioned-corpora).

## The problem it solves

D140 keeps one chunk row per document version, even when a paragraph did not
change. That is right for chunk rows: they anchor citations (character
positions differ between editions), claim occurrences, the D140 fact gate,
`adjacent_chunks` navigation and per-version deletion. But each chunk row
also has one `chunk_search` row, so a paragraph unchanged across 20 versions
is indexed 20 times.

With D142's index-first nomination this has three costs for
`current`/`at` searches:

1. most near-top candidates are copies in versions not in force, so the
   refill loop needs more rounds;
2. the copies tie on BM25 score, and the tie rule must read the whole run
   before cutting;
3. the indexes are larger than the distinct text they hold.

Embedding *calls* are not repeated — E1 already carries vectors forward
(D80) — so the cost is search work, not provider spend.

## The mechanism

**One search entry per distinct indexed input.** A new private table holds
the searchable text and its embedding once:

```sql
CREATE TABLE search_texts (
  deployment_id   uuid NOT NULL,
  text_id         uuid NOT NULL,
  search_text     text NOT NULL,              -- normalized body (BM25 input)
  search_text_hash text NOT NULL,
  embedding       vector(1536),
  embedding_model text,
  embedding_input_policy_version text,
  embedding_text_hash text,                   -- hash of the full embedding input
  PRIMARY KEY (deployment_id, text_id),
  UNIQUE (deployment_id, search_text_hash, embedding_text_hash,
          embedding_model, embedding_input_policy_version)
);
-- HNSW and BM25 indexes move from chunk_search to search_texts.
```

`chunk_search` becomes a membership row: `(deployment_id, chunk_id) →
text_id`, with an index on `(deployment_id, text_id)`.

**What counts as "the same".** Two chunks share an entry only when both the
BM25 body and the full embedding input are identical under the same model and
input policy. The embedding input includes the D80 location header, so the
same sentence under different section headings, or in different documents,
does **not** merge — those are different contexts and must stay separately
findable. The case the proposal targets — an unchanged paragraph in the next
edition of the same document under the same heading — does merge.

**Search.** D142's loop runs on `search_texts`:

1. the index returns the next batch of `text_id`s in score order;
2. confirmation expands each `text_id` to its member chunks and applies the
   unchanged chunk predicates (live lineage, D140 scope, documents filter,
   entity scope) to the members;
3. an entry is **visible if at least one member chunk passes**;
4. the hit is reported as a chunk: for `current` and `at`, the member in the
   version D140 selects; for `history`, the representative edition plus the
   matching editions, the rule D140 already defines for
   `search_documents` (`matching_editions`).

Ranking stays score, then stable ID — the stable ID of the reported chunk.

**Worked example.** § 5 of an act has text A in 2015–2023 (nine versions) and
text B from 2024.

| query | entry A (members: 2015…2023) | entry B (member: 2024) | answer |
| --- | --- | --- | --- |
| `current` (2026) | no member in force → skip | 2024 in force → hit | B, cited in the 2024 edition |
| `at 2019-06-01` | 2019 member in force → hit | not yet in force → skip | A, cited in the 2019 edition |
| `history` | hit | hit | both; A lists its nine editions |

These are exactly today's answers; the index simply holds A once instead of
nine times.

## Invariants and failure behaviour

- **Membership is written in the same transaction as the chunk.** E1 inserts
  the chunk row, finds or creates the `search_texts` row by its unique key,
  and inserts the membership — one transaction, so no committed chunk is ever
  unreachable. (Same pattern as D140's `document_version_scope` projection.)
- **Deletion unlinks, it does not delete text.** D135 soft delete hides the
  chunk through the normal predicates; the entry stays visible through other
  members. When the last member of an entry is hard-forgotten (D74), the
  `search_texts` row is deleted in the same forget transaction — the text of
  forgotten content must not survive in any index.
- **Text that returns (A → B → A).** The 2025 chunk with text A joins entry A
  again. No special case.
- **Reachability check.** A database-wide invariant test: every live chunk
  with search text has exactly one membership, and every `search_texts` row
  has at least one member that is not hard-forgotten.
- **Embedding-model change** (D94 maintenance) rebuilds `search_texts`
  embeddings once per distinct text instead of once per chunk.

## Consequences

- **BM25 scores shift.** BM25 weights a term by how many indexed rows contain
  it. Twenty copies of one paragraph inflate those counts today; one entry
  per distinct text removes the inflation. Rankings therefore differ slightly
  from `main` — arguably more faithful — and the equivalence test in D142
  becomes "equal to an exact full scan over `search_texts`", not over
  `chunk_search`.
- **One more join on the result path** (entry → members), bounded by the
  member count of returned entries.
- **No change** to chunk rows, claims, occurrences, provenance, the public
  API, or results for corpora without repeated text.

## Adoption trigger

The LegalIt pilot (20–50 heavily amended Czech acts with every consolidated
version) measures the **effective duplication factor**: for a set of
realistic `current`-scope queries, the number of index candidates D142's loop
examines per result returned, plus the length of BM25 tie runs at the cut.

Starting decision rule, for the owner to confirm before D142 implementation
starts:

- **median factor ≥ 5**, or p95 latency over the D9/§10 interactive budget
  when the pilot's numbers are extrapolated to the full corpus → adopt this
  proposal into the #504 delivery;
- otherwise → leave it unchosen; D142 alone suffices.

The thresholds are starting points, not commitments: the real cut-off is
wherever measured latency or LoCoMo-style answer quality becomes unacceptable.
