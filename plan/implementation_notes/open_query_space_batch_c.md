# Batch C — the nomination bridge

An agent writes `FROM semantic_claims($1, 20)` and gets confirmed rows. This
note records how, and one place where the implementation departs from the
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

What it changes: the functions are not callable by a direct `psql` session
against the deployment database, only through the query surface. That is
consistent with the rest of the design — the query role holds no privilege to
call anything else either — but it is a real difference from the written
contract and should be reflected in the design when §3.4 is next revised.

## Deferred, as the design records

`lexical_facts` stays deferred (§10): fact labels carry no lexical index in
P1, and a PostgreSQL full-text substitute would be a second implementation of
the lexical channel, which §3.4 forbids.

## Scoring

Each channel carries out the score it already computed rather than a second
search being run to recover it. A vector distance is inverted so that larger
is better *within* a channel; a BM25 score and a cosine similarity are never
comparable, which is why the channel name travels with every row.
