# PostgreSQL 19 live-graph implementation review — closure record

**Status:** non-binding review evidence; exact Claude and exact `agy` closure
reviews approved. **Reviewed:** 2026-08-27.

The exact Claude Opus xhigh review found and then verified remediation of the
live-evidence, admission sequencing, release-contract, protocol-identity,
runtime PGDG archive, helper-ACL, result-materialization, and dense-hub guard
gaps. Its final delta review returned `APPROVED` with no remaining finding. The
fresh exact `agy` closure review independently inspected both resulting trees,
reran the declared validation, found no blocker/high/medium issue, and returned
`APPROVED`.

Current validation is clean: inventory `unit=75`, `integration=50`,
`discovered=125`; five import contracts; Ruff format/lint; Pyright; docs
typecheck/build; and `1203 passed, 625 skipped`. The PostgreSQL graph acceptance
cases are among the environment-gated skips because the local Docker overlay
store remains unavailable.

After rebasing onto the accepted hard entity-type cut, global-resolution eval,
and profile work, the reviewers found five integration regressions: one stale
`entities.type` fixture, an old LoCoMo normalizer identity, a snapshot-era view
comment, graph examples that attempted to expose sandbox-hidden terminal fields,
and stale version/deferral wording. Reviewing that example correction then
exposed a pre-existing implementation gap: the binding result design required
`QueryResult.graph_invocations`, but the model retained only aggregate
truncation. The result model and executor now preserve each validated terminal
status as typed per-invocation work/budget disclosure, while examples project
only real data fields. The migration was also renumbered from the now-occupied
`p9_14` slot to linear head `p9_17_0038`. All findings were corrected, the query
manifest and LoCoMo protocol fingerprint were regenerated, and both exact
reviewers approved the remediated tree.

The final wire-contract pass then found two more integration defects: explicit
`NULL` depth produced a nullable truncation flag despite using the default
depth, and manifest `SELECT *` examples still invited callers to read the old
data-row status values. Helper initialization now compares the coalesced depth;
data rows repeat the final invocation truncation/counters; discovery examples
project only useful data fields; and mixed semantic/graph ordinals,
per-invocation warnings, first-cap aggregation, explicit-`NULL`, and broad-row
truthfulness have direct regression coverage.

Claude was invoked read-only with the operator-required command form:

```text
claude --dangerously-skip-permissions --model claude-opus-5 --effort xhigh -p "<final frozen implementation-review prompt>"
```

The initial `antigravity` spelling recorded in rounds one and two was not an
installed executable. The operator-required `agy` CLI was then found and run
read-only with the exact form:

```text
agy --dangerously-skip-permissions --print-timeout 180m0s -p "<implementation-review prompt>"
```

After the provider quota reset, the exact closure rerun completed and returned
`APPROVED` with only the already-recorded Docker-gated execution advisory.

## Confirmed implementation outcome

- LadybugDB, public Cypher, P2 graph workers/analytics, graph snapshot
  generations, and their test/runtime modules are removed.
- PostgreSQL 19 fixed shallow graph patterns use server-owned SQL/PGQ over live
  authority views; bounded recursive helpers own deeper shortest-tier entity
  and directed citation traversal.
- Paired temporal clocks, deployment binding, self-loop/repeated-edge
  exclusion, typed per-invocation terminal status, resource budgets, graph
  role/pool isolation, catalog/readiness, typed HTTP/SDK operations, saved-query
  examples, and removal/provenance audits fail closed.
- pg_textsearch source, compatibility patch, PostgreSQL License, NOTICE, and
  installed per-architecture artifacts have explicit checksum contracts.

## Release execution gates

Approval does not waive execution evidence. The local Docker overlay store
returns input/output errors, so local PostgreSQL 19 acceptance remains
unavailable. The GitHub PR and release workflows therefore own the current-tree
PostgreSQL 19 database suite, both-architecture image builds/digests, and
extension regression evidence. Restore/load drills and dogfood cutover remain
separate operational gates. No Docker daemon restart was performed because it
would disrupt unrelated local containers without operator authorization.
