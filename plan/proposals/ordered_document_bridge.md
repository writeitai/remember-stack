# Ordered document bridge: amendment to the D153 physical probe

Status: proposed, not implemented. Adoption requires Claude Opus design approval
and the D153 parity, actual-plan and migration gates. This preserves D153's
visibility/ranking decisions; it corrects a failed physical-work hypothesis.

The one-million-document bridge fixture failed on implementation `3ca16ea0f`:
a member with no document bridge visited unrelated documents even through an
`OFFSET 0` correlated lookup. With one very common document entity, PostgreSQL
estimates that a matching row will occur near the beginning and selects a
sequential scan for an unknown member parameter. A missing member then scans
all documents. A syntactic equality predicate and lookup fence alone do not
establish the required work bound.

The disposable prototype widens the **existing** `ix_documents_entity` from
`(document_entity_id)` to `(document_entity_id, doc_id)`, retaining its predicate
`document_entity_id IS NOT NULL`, and reads the first matching document ordered
by `doc_id` with `LIMIT 1 OFFSET 0`. That order has no membership meaning: any
live document suffices. It gives PostgreSQL an ordered index range tied to the
member, instead of relying on a guessed position in the document heap. No
covering columns, persistent eligibility state, query hint, provider call or
runtime fallback is introduced. All source/deployment/deletion predicates stay.

The same million-row fixture changes from 1,000,013 examined document tuples to
1, and from 75.607ms to 1.161ms. These are single-run local plan observations,
not a throughput promise. Raw plans, the reproduction and hashes are in
`plan/analysis/shared_eligibility_qualification_20261112/`. The old and widened
indexes occupy 6,504,448 and 49,758,208 bytes on this highly duplicated synthetic
key distribution. The +43,253,760 bytes per million documents includes loss of
B-tree deduplication on that fixture; it is not a universal per-document price.
An ordinary unique-document-entity distribution also needs measurement. During
online replacement both indexes coexist, with I/O/WAL and temporary peak disk
cost. Native storage capture must include the resulting allocation; this makes
no unmeasured margin or free-storage claim.

After approval, extend this PR's **unreleased** `p9_47_0068` migration to build a
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

Required qualification adds common-key, absent-key and many-distinct-key strata;
actual tuple/buffer work with millions of unrelated documents; unchanged any-live
lineage/any-ready-reading semantics; old/new complete rows; concurrent normal
writers during build; cancellation/rerun at each replacement state; and the
existing ACL/manifest/capture gates. The new ordering changes the authored view
AST and requires a real current surface pin update. No ingestion or reuse key
changes are authorized by this amendment.
