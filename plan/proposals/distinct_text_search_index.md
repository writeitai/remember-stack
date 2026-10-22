# Proposal: index each distinct search input once

**Status:** not chosen (2026-10-21; revised after D142 review round 1). The
**owner approved the investigation and adoption rule below on 2026-10-21**;
the index itself is not implemented. Fully designed option for
[D142](../../decisions.md#d142-ranked-search-nominates-from-the-index-and-confirms-in-one-snapshot);
adoption is an **open owner decision** taken on the evidence named under
"Investigation trigger and adoption prerequisites". **Analysis:**
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

**Search — the chunk result grain does not change.** D142's loop runs on
`search_texts`, but every nominated entry is **fanned out to its member chunks
before the relevance cut**:

1. the index cursor returns the next batch of `text_id`s in score order;
2. each entry expands to all of its member chunk IDs; every member carries the
   entry's score;
3. confirmation applies the unchanged chunk predicates (live lineage, D140
   scope, documents filter, entity scope) to each **member**, exactly as it
   applies them to chunk candidates today;
4. confirmed members are ranked at member grain — coverage (when entity
   scoped), score, then the member's stable chunk ID — and the `k` cut, the
   tie rule and RRF membership all apply to members.

So a query returns the same chunk identities it returns today: separate chunk
results for separate eligible editions, in every scope (`current`, `at`,
`overlap`, `history`, and concurrent editions under overlapping declarations).
Edition aggregation (a representative plus `matching_editions`) stays a
`search_documents` concern. The work budget counts every **member row
examined**, not only entries returned, so an entry with thousands of members
cannot make a search unbounded. Any future change to the chunk result grain
would need its own design decision.

**Worked example.** § 5 of an act has text A in 2015–2023 (nine versions) and
text B from 2024.

| query | index entries read | members confirmed | chunk results |
| --- | --- | --- | --- |
| `current` (2026) | A, B | A: none in force; B: 2024 | the 2024 § 5 chunk |
| `at 2019-06-01` | A, B | A: 2019; B: none | the 2019 § 5 chunk |
| `history` | A, B | all ten | ten chunk results (nine A editions, one B), ordered by score then chunk ID |

These are today's results. What changes is index work: A is read once instead
of nine times, and its nine copies no longer fill a fetch batch.

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
- **Tests.** History, overlap and concurrent-edition scopes return every
  eligible member; duplicate chunks and deletion of one member leave the
  others findable; the work budget counts member rows.
- **Reachability check.** A database-wide invariant test: every live chunk
  with search text has exactly one membership, and every `search_texts` row
  has at least one member that is not hard-forgotten.
- **Embedding-model change** (D94 maintenance) rebuilds `search_texts`
  embeddings once per distinct text instead of once per chunk.

## Consequences

- **BM25 scores shift.** BM25 weights a term by how many indexed rows contain
  it. Copies of one paragraph raise those counts today; one entry per distinct
  input removes them. Rankings therefore differ from `main`. Whether that is
  better or worse is not known: adoption requires lexical and hybrid quality
  measurements (the D142 §9 gates) on the prototype, and the D142 BM25
  equivalence oracle becomes "exact scoring over `search_texts` under one
  scoring generation".
- **One more join on the result path** (entry → members), bounded by the
  member count of returned entries.
- **No change** to chunk rows, claims, occurrences, provenance, the public
  API, or results for corpora without repeated text.

## Investigation trigger and adoption prerequisites

The question is decided with evidence from real versioned data, not from the
D142 loop's own counters alone, because candidates examined per result also
include deletions, entity/document/time rejections and batch over-fetch, and a
high value can stay high after deduplication.

1. **Isolated prototype (prerequisite).** On a copy of the LegalIt pilot
   corpus (20–50 heavily amended Czech acts with every consolidated version),
   with a fixed set of realistic queries (keyword and semantic, `current`,
   `at` and `history` scopes, with and without entity filters), measure for
   D142 alone and for a prototype of this proposal:
   - candidates examined per returned result, broken down by rejection reason;
   - the share of examined candidates whose indexed input key
     (`search_text_hash`, `embedding_text_hash`, model, policy) is shared with
     another candidate — the work deduplication can actually remove;
   - BM25 tie-run lengths at the cut;
   - p95 latency, returned-`k` rate and truncation rate;
   - lexical/hybrid answer quality per the D142 §9 gates.
   Artifacts (dataset manifest, query set, raw results) are stored with the
   analysis.
2. **Investigation thresholds** (owner-approved 2026-10-21): a median
   of ≥ 5 examined candidates per result **attributable to shared inputs**, or
   p95 outside the interactive budget when extrapolated to the full corpus.
3. **Adoption** requires the prototype to show a latency or completeness
   benefit at equal or better quality. The adopting change then reconciles
   the binding corpus (P1 search design, schema design, forget/D74, E1) in the
   same pull request.

LegalIt is the motivating corpus; the mechanism and its trigger are general
(any corpus with many versions of mostly unchanged documents), and no
LegalIt-specific behaviour enters the engine.
