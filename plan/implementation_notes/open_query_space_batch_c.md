# Batch C — the nomination bridge

An agent writes `FROM semantic_claims($1, 20)` and gets confirmed rows. This
note records how, and the places where the implementation departs from the
design's letter.

## The mechanism differs from §3.4, deliberately

§3.4 describes the public functions as in-database `SECURITY DEFINER`
functions owned by a bridge role. PostgreSQL cannot reach the Lance projection
without an untrusted procedural language (`plpython3u`), which this product
does not install and should not require operators to install.

So the bridge runs in the executor: the grammar already extracts each accepted
invocation into its own `MATERIALIZED` CTE, and the executor resolves those
CTEs — nomination in the projection, confirmation in PostgreSQL, inside the
request's own transaction — then substitutes the confirmed rows before the
statement is planned.

What this preserves, which is the part that matters:

- the caller-visible contract is unchanged: same call syntax, same columns;
- confirmation still happens before any row is exposed, against the same
  invariant views the rest of the surface reads (D48 fail-closed);
- the per-invocation and per-statement caps still bind, because the grammar
  counts invocations before anything runs;
- failure is still total: an unreachable projection or a failed confirmation
  fails the statement rather than returning a partial answer.

What it changes: the Lance-backed functions are not callable from a direct
`psql` session against the deployment database, only through the query
surface. That is consistent with the rest of the design — the query role holds
no privilege to call anything else either — but it is a real difference from
the written contract and should be reflected in the design when §3.4 is next
revised. `facts_as_of` is the exception: it needs no projection, so it ships as
an ordinary PostgreSQL function (migration `p9_03_0024`) and behaves the same
either way.

## Function attributes differ from §3.4, and less privilege is why

§3.4 describes the SQL-callable functions as `SECURITY DEFINER` and
`PARALLEL UNSAFE`. `facts_as_of` ships `SECURITY INVOKER` and `PARALLEL SAFE`:
the query role already holds `SELECT` on `facts_visible_history`, so definer
rights would add an escalation path that buys nothing, and the function reads
views with no side effects, so forbidding parallelism would only make it
slower. The `SECURITY DEFINER` language in §3.4 was written for a bridge that
needed to reach objects the caller cannot; this function does not.

## Signatures are enforced, not assumed

`SIGNATURES` in `bridge.py` is the arity contract: `query` and `k` are both
required, as §3.4's signature table has them (only `filters` and the two
generation pins carry defaults), and an argument beyond the declared maximum is
rejected rather than ignored. A caller who passes a sixth argument to
`semantic_claims` has misunderstood something, and completing the call as if
they had not would hide that.

The same signatures, the filter vocabulary per target, and the columns each
function answers with are published in the manifest's `function_signatures`
member, so an agent can read the contract without executing anything — and any
change to them rolls `surface_manifest_hash`.

## Filters are applied where they can be applied

A filter the projection understands is passed to Lance *before* top-k, so a
narrow search returns k matching rows rather than k rows that mostly get
discarded afterwards. `claims` can push `doc_id`; `chunks` can push `doc_id`,
`source_kind`, `source_shape`, and `section_role`; `facts` and `entities` push
their kind and type through the existing arguments. Everything else is repeated
in PostgreSQL against a column that actually exists there — the chunk filters
reach `documents_live` and `sections_live`, because `chunks_live` does not
carry `source_kind`, `section_role`, or `language` itself.

`source_shape` lives only in the projection: PostgreSQL cannot confirm it, so
it is applied in Lance and not repeated. That is why it is listed in
`PROJECTION_ONLY_FILTERS` — it is a real filter, not an ignored one.

Filter values are typed on the way in. A `doc_id` that is not a UUID, an
`asserted_from` that is not an instant, and a `section_role` outside the
vocabulary are all `invalid_parameter` before any store is read, rather than a
confirmation that mysteriously matches nothing.

## Drops say which kind of drop they were

The confirmation asks the filters as a question rather than using them to hide
rows, so the disclosure can separate them: `dropped_stale` is a row the view no
longer publishes (a tombstoned lineage, a superseded fact), `dropped_filtered`
is a current row that failed a caller filter, and for body fetches
`dropped_absent` and `dropped_body_mismatch` split "not there" from "there but
it disagrees with PostgreSQL". Calling all of these "stale" would misreport why
the projection's memory and PostgreSQL disagree.

## Values travel as parameters, not as text

The rewrite replaces a resolved call with the rows it confirmed. Those rows are
bound parameters cast to the types confirmation reported, never rendered SQL:
claim text is prose out of a document, so an apostrophe in an ordinary sentence
would otherwise be reparsed as syntax. A test plants SQL-looking claim text and
proves it arrives as text.

A search that nominates nothing still answers with a shape. The column contract
per target is declared in `EMPTY_CONTRACTS` beside the confirmation statements,
so a zero-result search produces a typed empty relation and the caller's join
still type-checks.

## One search, one vector space

A chunk exists once per D80 generation triple, so a search that does not bind a
pair nominates the same chunk once per generation it was ever embedded under —
the caller sees duplicates and spends their `k` on them. Every chunk search
therefore binds exactly one `(policy_generation, embedder_generation)` pair.

The pair is resolved BEFORE the query is embedded, and PostgreSQL is the
authority: an unpinned search runs under the generation the spine currently
stamps chunks with, not under whichever pair happens to sort highest among
whatever the projection still holds. A pin is honoured when it names that same
current generation and refused otherwise — this surface can resolve what is
current but cannot reconstruct what was current before, and guessing would be
worse than saying so.

The pin is matched by the name §3.4 publishes. `embedding_input_policy_version`
and `policy_generation` are two different values of the same chunk — the policy
and the generation that policy was applied as — and matching the published pin
against the second would refuse every caller who used the documented name and
the column of that name. Bodies are then read under the same pair the
coordinate was confirmed under: a re-embedded chunk exists under more than one
pair, and an unscoped read hands back a body that fails its hash and is
withheld as a mismatch although the right one was there all along.
The resolved embedder generation is then passed to the embedder — comparing an
old document vector to a new query vector is not a worse search, it is a
meaningless one — and the projection refuses a pair it does not hold rather
than returning an empty result that reads as "nothing matched".

## Generation pins bind, or the request fails

Only the chunk projection is stamped with a D80 generation triple. A pin on
`semantic_claims` or `semantic_facts` is therefore refused with
`generation_unavailable` rather than accepted, ignored, and then disclosed as
though it had been applied. A chunk pin naming a generation the projection has
never held fails the same way, because searching for it and finding nothing
would read as "your query matched no chunks" when it means "the thing you
pinned to is not here".

## Confirmation and execution share one snapshot

Both run in the same transaction, at `REPEATABLE READ`. The isolation level is
the other half of the claim: under `READ COMMITTED` each statement takes a new
snapshot, so confirmation and the caller's statement would still see different
states of the database inside one transaction. Confirming in a separate one would freeze
rows that were live then into a statement that runs now, and a lineage
tombstoned in between would come back in a result the live views no longer
publish — exactly the D48 leak confirmation exists to prevent.

## A fact's identity is (fact_kind, fact_id)

Relations and observations carry independent identifiers, so one UUID can name
two facts. The fact channel's nominations carry the kind, and confirmation
matches on the pair: a projection that still remembers `(relation, X)` after
the spine has moved on cannot be answered with a current `(observation, X)`
that happens to share the id, which would hand the caller a real row carrying a
score computed for a different fact.

A nomination that arrives WITHOUT its kind is a different case, and the one
`dropped_ambiguous` exists for: where the id names two current facts there is
no way to tell which was meant, so neither is published. A qualified nomination
is never ambiguous — it said which one it meant — so that counter fires only on
the unqualified path.

Chunk confirmation is qualified the same way, by the generation pair the search
bound. During a cutover the projection can still hold a chunk under the old
pair while PostgreSQL has moved it to the new one, and confirming on id alone
would return current metadata carrying a score computed in a different vector
space.

## Bodies are confirmed before they are read

The chunk channels and the body fetch share one path, so a nominated chunk
carries its verified source text out with it: `semantic_chunks` and
`lexical_chunks` answer with `source_text` beside `location_header`, and a
chunk whose text fails verification keeps its metadata row out of the result
rather than appearing with an empty body.

P1 stores the chunk BODY; PostgreSQL stores the D80 header separately and the
hash of the text that was actually embedded, which the policy composes as
header, blank line, body. The verifier recomposes that text and hashes it,
which is what proves the two halves belong together — it verifies the
separation rather than assuming it, and it is why `source_text` is returned as
the body alone with the header in its own column. An earlier version of this
path expected P1 to hold the composed text; it could not have verified a
single row written by the real pipeline.

What is verified is the embedding-text hash. §3.4 also names the source-content
hash, and that one is NOT checked here: `chunk_content_hash` is a hash of the
chunk's ordered block hashes, which cannot be derived from the body text the
projection stores, so there is nothing to compare it against without carrying
it into P1. That is an ingest-side change and is recorded here rather than
described as done.

A chunk with no recorded embedding-text hash has nothing to verify against, so
its body is not returned. Unverifiable is not the same as verified, and this
path exists precisely so that PostgreSQL decides.

PostgreSQL is asked first and the projection second, in both the direct fetch
and the chunk channels, and each body is verified against the same PostgreSQL
row its metadata came from. Reading the store first, or confirming twice, would
leave a window in which the coordinate a body was verified against is not the
coordinate the row reports.

`fetch_chunk_bodies(chunk_ids)` is the body path minus nomination. PostgreSQL
decides which chunks still exist and what their current coordinate, hashes, and
D80 header are; only then is projection text admitted, and only if it hashes to
what the spine recorded and still carries exactly the header the spine
generated. The body and the header are returned in separate columns, because
the header is generated orientation text and is never asserted evidence. Ids
de-duplicate to first input position, more than 50 fails before any store read,
and chunks spanning more than one D80 generation fail the whole invocation.

## Still absent from this batch

`graph_neighborhood` and `graph_path` are in the grammar's public-function list
but belong to Batch D; calling one today is rejected by name rather than
reaching PostgreSQL and failing on an undefined function.

## Deferred, as the design records

`lexical_facts` stays deferred (§10): fact labels carry no lexical index in P1,
and a PostgreSQL full-text substitute would be a second implementation of the
lexical channel, which §3.4 forbids.

## Scoring

Each channel carries out the score it already computed rather than a second
search being run to recover it. A vector distance is inverted so that larger is
better *within* a channel; a BM25 score and a cosine similarity are never
comparable, which is why the channel name travels with every row.
