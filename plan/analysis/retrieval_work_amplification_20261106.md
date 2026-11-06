# Removing repeated retrieval work

Date: 2026-11-06. This analysis supports changes to D142's implementation
and D91's request embedding lifetime; it does not change retrieval eligibility.

## Problem and evidence

The saved conv-47 replay after PR #537 still spends a median 12.3 seconds
in fact SQL. Claims/sources SQL takes a median 1.2 seconds, but its two
sequential embeddings take a median 6.8 seconds together. These are separate
problems. The actual combined operation also makes three identical-query
embedding calls, so this is not peculiar to the diagnostic driver.

The archived observation confirmation plan runs an evidence-count aggregate
804 times even though confirmation selects only candidate IDs. Its aggregate
accounts for about 2.47 of the statement's 3.67 seconds. The scoped fact
nomination joins the same public fact view. `facts_visible_history` currently
uses `CROSS JOIN LATERAL` for an aggregate that always returns exactly one
row, including zero counts. This prevents pruning unrequested count work.

Evidence source: the immutable `locomo-retrieval-deadline-20261106` report in
the cloud repository, `experiments/locomo-retrieval-deadline-20261106/`, and
its GCS receipt. No new answer or grade follows from those retrieval replays.

## Choice and alternatives

Use a left lateral join for the one-row count aggregate, preserving all
published columns, values and membership. PostgreSQL can then remove the
join for ID-only confirmation, while final results still calculate requested
counts. Keep source visibility, support, temporal and entity eligibility in
the query before relevance truncation. Removing these predicates would let
deleted or superseded candidates displace valid evidence. Replacing the
bounded per-ID probes with an unrestricted authority join is also rejected:
D142 §8.3 records why it can scan the whole corpus.

Reuse an embedding within the existing D91 request scope, across claims,
chunks, facts and touched-candidate probes. Key by engine identity,
deployment, model, dimensions and exact text. A new request gets a new cache;
there is no shared cross-request cache, new persistence or eviction service.
Nested operations reuse the same scope. Synchronize cache access so sibling
callers in one inherited scope cannot bill the same embedding twice. Record
one cost receipt for each actual provider call and do not cache failures.
Delete the instance-wide `_last_embedding` shortcut. A process-wide cache is
rejected because it changes retention and attribution across requests.

The OpenRouter adapter already retains an HTTP client; it is not opening a
new client for every embedding. Provider latency varies even on the first
call. Removing duplicate calls reduces exposure to that latency; it does
not promise a latency bound for an external provider. Keep the existing
embedding model and vector dimensions so stored vectors remain compatible.

## Validation and operational consequences

Compare actual plans and complete values on an isolated copy of the saved
store, using saved query vectors so SQL measurements exclude provider noise.
Test eligibility, count equality, request isolation, nested reuse, failures,
concurrency and exact provider-call metering. Measure candidate work as the
corpus grows; do not reduce the configured pool to claim a speedup. The
50,000-row scoped exact-scan boundary remains explicit, not a claim of
constant cost for arbitrary high-degree entities.

The view definition changes the public query-space identity. Normal setup
must publish the new manifest and revalidate saved SQL under the existing
contract. No ingestion generation, extraction input, document identity,
embedding model or paid-product reuse boundary changes (D56).

The subsequent conv-47 answer/judge run uses a separate run identity and
the existing ingested store. Preserve old state and GCS archives and archive
the new answers and store independently. The full benchmark stays paused.

## Candidate entity membership and merge expansion

After count pruning, the relation confirmation plan still rescanned survivor
and mention provenance 33,171 times for 72 candidate relations. Reorient
`entities_current` membership from the candidate terminal entity: walk its
absorbed members through `entities.merged_into`, then look up only their
unsuperseded resolution decisions using the entity index. A lateral subquery
with `OFFSET 0` keeps that lookup before joining mentions and source coordinates;
without the fence PostgreSQL chose source-first joins and regressed badly.
The latest unsuperseded decision per mention is still checked by timestamp
and decision-ID order. Deleted lineages, unready representations, merge
redirects and document-entity bridges keep their previous meaning.

The forward survivor view also starts its recursion at the input source row,
so a by-ID lookup does not enumerate all entities. Reverse scoped nomination
starts at requested terminal IDs and follows the existing redirect index.
There is no maximum chain depth; `UNION` terminates forward cycles without
inventing a terminal, and a reverse walk from a terminal cannot reach a cycle
in this one-parent redirect graph. The standalone all-entity export still
returns all terminal resolutions, so it necessarily visits all input rows.

On the Beta 3 copy, all stored rows and complete entity/fact view values
matched the prior definitions. Relation checks fell from 3.69 seconds to
0.30 seconds with indexed provenance. RC1 measurements and a growing unrelated
entity set are still required before making a scale claim.
