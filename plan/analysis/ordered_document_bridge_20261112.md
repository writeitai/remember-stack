# Ordered document bridge: amendment to the D153 physical probe

Status: historical analysis of the accepted D153 physical amendment. Claude
Opus 5.5 HIGH approved revision 68e156154 on 2026-11-12 ([review](https://github.com/writeitai/remember-stack/pull/587#issuecomment-6830637190)).
The binding contract is [D153 §4.1](../designs/shared_retrieval_eligibility_design.md#41-entity-provenance-constrain-the-existing-probe-chain); implementation still requires
the parity, actual-plan and migration gates. This preserves D153's
visibility/ranking decisions; it corrects a failed physical-work hypothesis.

The one-million-document bridge fixture failed on implementation `3ca16ea0f`:
a member with no document bridge visited unrelated documents even through an
`OFFSET 0` correlated lookup. With one very common document entity, PostgreSQL
estimates that a matching row will occur near the beginning and selects a
sequential scan for an unknown member parameter. A missing member then scans
all documents. A syntactic equality predicate and lookup fence alone do not
establish the required work bound.

The disposable prototype widens the **existing** `ix_documents_entity` from
`(document_entity_id)` to `(document_entity_id, deployment_id, (doc_id::text COLLATE "C"))`, retaining its predicate
`document_entity_id IS NOT NULL`, and reads the first matching document ordered
by `doc_id::text COLLATE "C"` with `LIMIT 1 OFFSET 0`. That order has no membership meaning: any
live document suffices. It gives PostgreSQL an ordered index range tied to the member and deployment.
A raw UUID doc_id ordering is also supplied by the primary key and the
(deployment_id,doc_id) unique index. Those competing ordered paths still win
under some skew estimates, even with a three-UUID bridge index; sole-index
measurements actually scanned 1,000,007 tuples in a one-deployment fixture.
Ordering by the bounded canonical UUID text in fixed C collation has only the
bridge expression index as a matching ordered access path. It avoids that
false cheap-start choice. Text conversion never enters a public value, identity,
reuse key or ranking score: any live document proves membership. Its fixed
36-character input prevents an unbounded-text B-tree key. Only stable doc_id
and deployment/bridge keys are indexed, so observation-clock writes gain no
new indexed dependency. The actual-work tests still qualify a pinned planner;
this is not a universal query hint.
No covering columns, persistent eligibility state, query hint, provider call or
runtime fallback is introduced. All source/deployment/deletion predicates stay.

The initial **two-key prototype**, measured with both indexes present, changes from 1,000,013 examined document tuples to
1, and from 75.607ms to 1.161ms. These are single-run local plan observations,
not a throughput promise. Raw plans, the reproduction and hashes are in
`plan/analysis/shared_eligibility_qualification_20261112/`. The old and widened
indexes occupy 6,504,448 and 49,758,208 bytes on this highly duplicated synthetic
key distribution. The +43,253,760 bytes per million documents includes loss of
B-tree deduplication on that fixture; it is not a universal per-document price.
The final text-expression shape has additionally been measured with only its
one working bridge index, in 1/2/16-deployment distributions: common and absent
member probes examine 1 tuple in each million-document stratum (0.795–1.056ms).
An all-tombstoned member examines its 62,500 own rows in the sixteen-deployment
stratum (280.924ms), rather than unrelated deployments. Raw actual plans and
buffer counts (9 per pair) are recorded in bridge-text-summary.json and its linked gzip
plans. This index occupies 95,600,640 bytes in these skewed fixtures, versus
6,504,448 bytes for the old index; the earlier 49,758,208-byte two-key prototype
is not the proposed index cost. These are observations, not guaranteed latency.
An ordinary unique-document-entity distribution also needs measurement. During
online replacement both indexes coexist, with I/O/WAL and temporary peak disk
cost. Native storage capture must include the resulting allocation; this makes
no unmeasured margin or free-storage claim.

The approved delivery change extends this PR's **unreleased** `p9_47_0068` migration to build a
strictly validated own-named replacement concurrently, drop the old index
concurrently, and rename the valid replacement to `ix_documents_entity`. Under
the existing session migration lock a rerun recognizes the exact old shape,
the exact new shape, or the own replacement in progress; invalid own replacement
indexes are rebuilt, while mismatched/foreign objects fail setup. The installed
working schema has one document-entity index. Retain no obsolete alternate path.
The following unreleased `p9_48_0069` view uses the explicit ordering; it remains
an ordinary view transaction after the index revision commits. Existing
historical main migrations remain untouched. Qualification stores previously
created from PR prototypes are recreated from protected original volumes, so
an edited unreleased revision is never silently stamped over a test store.

## Exact catalog contract and replacement states

The original fixed name is `ix_documents_entity`; the only staging name is
`ix_documents_entity_d153`. Shape A is the old one-key definition; shape B is
`(document_entity_id, deployment_id, (doc_id::text COLLATE "C"))`. Both require public.documents,
ordinary B-tree, nonunique/nonexclusion, exactly the specified keys in order and
no INCLUDE columns. Shape A has one default pg_catalog UUID B-tree key, no
expression, and zero collation/order options. Shape B has two default
pg_catalog UUID B-tree keys then one default pg_catalog text B-tree expression
key, exactly the doc_id UUID-to-text cast, with pg_catalog C collation only on
that third key and zero order options. Both keep the exact `document_entity_id IS NOT NULL`
predicate. A valid serving/replacement index must have indisvalid, indisready
and indislive true. A differently shaped or wrongly bound object at either
name fails setup. An expected shape alone does not make an invalid index valid.

For upgrade, source shape is A and destination shape B. Downgrade reverses
those roles and restores the exact original one-key index, using the same
staging name under the same session migration lock. This is necessary for a
truthful old-head physical schema; it introduces no deployment rollback service.

| Fixed current name | Staging name | Action |
| --- | --- | --- |
| Valid source shape | absent | Build destination concurrently, then recheck |
| Valid source shape | invalid, exact destination shape | Drop only the own invalid staging index concurrently and rebuild it |
| Valid source shape | valid exact destination | Drop current concurrently, then rename staging |
| Invalid/not-ready source shape | valid exact destination | An interrupted concurrent drop: finish that drop, then rename staging |
| Invalid/not-ready source shape | absent or invalid | Fail; never remove the last object without a validated replacement |
| absent | valid exact destination | Drop already committed; rename staging |
| absent | absent or invalid | Fail; no validated native replacement exists |
| Valid destination shape | absent | Completed catalog change; no-op and continue revision stamping |
| Destination shape invalid, or destination plus an unexpected staging object | any | Fail for inspection |
| Any mismatched/foreign object | any | Fail without dropping it |

Every drop/rename is preceded by rechecking the valid own replacement. A
cancelled DROP INDEX CONCURRENTLY can leave the fixed old index invalid or
not ready: that is explicitly distinguished from an interrupted CREATE at the
staging name. No general-purpose recovery journal or release coordinator exists.
The final rename and revision update use native DDL transactions; each revision
commits before the following view revision waits on its readers.

Tombstoned documents retain their bridge. The predicate must **not** become
`deleted_at IS NULL`: native entity-FK cleanup must still find tombstoned rows.
An all-tombstoned member can therefore examine that member's own history; this
is measured separately from scanning unrelated documents and remains subject
to the operation deadline. Keep the OFFSET 0 fence explicitly documented: it
prevents EXISTS simplification from discarding the ordering/limit.

The current source has no active writer that assigns this compatibility bridge
(`rg document_entity_id` across src; D134 metadata design §7); archived/custom
stores can still contain it. Empty bridges have a small empty-index cost.
The populated synthetic cost above is deliberately shown rather than claimed
for every current deployment. Native allocation capture must measure each
store's actual index forks after the upgrade.

Required qualification adds common-key, absent-key and many-distinct-key strata;
actual tuple/buffer work with millions of unrelated documents; unchanged any-live
lineage/any-ready-reading semantics; old/new complete rows; concurrent normal
writers during build; cancellation/rerun at each replacement state; and the
existing ACL/manifest/capture gates. The new ordering changes the authored view
AST and requires a real current surface pin update. No ingestion or reuse key
changes are authorized by this amendment.


Primary PostgreSQL 19 documentation, retrieved 2026-11-12:
[ordered indexes and LIMIT](https://www.postgresql.org/docs/19/indexes-ordering.html),
[multicolumn leading-key constraints](https://www.postgresql.org/docs/19/indexes-multicolumn.html),
[concurrent index creation](https://www.postgresql.org/docs/19/sql-createindex.html).
These explain available paths; the prototype and required work strata establish
the observed planner behavior. They do not guarantee which path every future
planner will select.
