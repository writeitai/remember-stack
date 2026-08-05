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

## Bodies are confirmed before they are read

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
