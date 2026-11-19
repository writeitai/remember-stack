# D153 final qualification, 2026-11-12

This is empirical evidence, not a throughput SLA. Runtime SQL/migration source
is `fab0a9bda`; later commits add qualification tests,
operator measurement checks and this evidence. The current surface hash is
`6a5b81486cba3daccb109b3d1d936e048a6557afa7dd4eac1dbd6f9fde33578c`.
No model calls or ingestion occurred. Original campaign stores and answers
remain unchanged. All retained tests use fresh physical copies of cleanly
stopped originals; unmerged prototype targets were never silently restamped.

## Complete semantics and native operations

An independently migrated real `p9_46_0067` fixture, cloned before upgrading its
target, proves every public view column/value across 35+ mutations, plus ties,
foreign redirects, nullable claims and mismatched coordinates. Current scoped
content and D140 pending-lineage freshness retain their distinct authorities.

Retained conv26/conv30 comparisons include all public entities (122/49), facts
(584/514), mentions (951/803), evidence lineages (585/521), and fact/claim
associations (587/521), with no row cap. All full row hashes agree with the
old authority. Both shared coverage directions match the complete old mention
export (526/480 claim groups and 119/82 chunk groups). The full shared fact
builder agrees on every eighteen-column fact row, including support/contradiction
counts and precision. Receipts are `final-conv26-receipt.json` and
`final-conv30-receipt.json`.

Native k50/candidate200 requests with stored-vector inputs finish under the
stricter 20s diagnostic bound: scoped facts 9.17s/6.74s, scoped claims/chunks
2.79s/3.25s; broad conv26 facts 9.62s and claims/chunks 0.86s. The broad traces
show finite disjoint nomination, one fetch round per branch, unchanged bounds
and no repeated top-up loop. These are SQL-only observations, not question
embedding or model latency measurements. Eight-reader scoped and broad outputs
match their fixed-instant baseline, including negative/truncation values.
The final labelled scoped runs use the committed `2078bddf8` operator and verify
zero checked-out connections and zero open own snapshot transactions. Initial
unlabelled runs also had zero checked-out connections, but their server-side
application-name count was not discriminating; the labelled rerun corrects it.

Ordinary full public fact exports remain more expensive (73s/47s here) because
they request every presentation/count field of every historical fact. They use
an explicit independent 600s oracle allowance, not the normal native request
budget. No cap was substituted for a complete reference. No SLA or arbitrary
full graph/sandbox export speedup is asserted. Some timings overlap other SQL
qualification work on the VM; this is not an unloaded-host benchmark.

## Scale and online migration

`final-bridge-summary.json` links raw actual migrated-schema plans. Each read
stratum uses its own fresh index, avoiding rolled-back fixture bloat. With
one million documents across 1/2/16 deployments, the common+absent member pair
examines one document tuple under custom and generic prepared plans. A
100,000-distinct-entity stratum also examines one. An all-tombstoned member
examines only its own 62,500 rows (190–219ms), not unrelated deployments.
Existing complete prefix lookups remain valid and their plans are included.
120 monthly partitions plus the default retain the explicitly accepted linear
partition point-probe overhead; unrelated 10,000-row fixtures do not multiply
coordinate work. UUIDs remain opaque.

The actual native upgrade over one million committed documents completes in
3.86s here, with an ordinary second row writer completing while concurrent
index DDL is active. The old index occupies 6,504,448 bytes; the bulk-built new
index 95,600,640 bytes. This upgrade emitted 291,470,704 WAL bytes. Newly inserted
post-upgrade rows instead produce roughly 124–125MB per million in these
fixtures, and 12,492,800 bytes for 100,000 distinct keys. These are allocation
observations, not a universal storage price. Both shapes coexist temporarily.
Real cancelled concurrent CREATE/DROP states, exact-shape collisions, completed
rename/rerun, native downgrade and two simultaneous migration runners are tested.
No recovery journal, persistent eligibility state or legacy runtime fallback
was introduced.

The out-of-scope `_CONTRADICTION_MEMBERS_*` graph/lookup enrichment still uses
row-wise public fact authority and remains a known cost. Native migration
checks every mention-index shape; the current catalogue additionally checks
parent validity and complete valid attachment but does not repeat all per-leaf
shape properties at each startup. PostgreSQL itself requires attachment shape
compatibility. A native downgrade retains the safe view-owner ownership of
the carried-claim helper rather than restoring its older administrative owner.

## Checks

- 29 new cases: 16 semantic/snapshot/cancellation, 12 scale/index-state cases,
  and one populated native upgrade with concurrent writers.
- 19 migration checks; 130 related PostgreSQL/reuse regressions passed, with
  one pre-existing documented D122 window reuse-gap xfail.
- 14 native storage-capture checks with their own role-bound databases.
- 72 manifest/default/protocol checks; Ruff and targeted Pyright pass.
- Required CI and final Opus approval remain merge gates until their results
  are recorded in the PR. Autoboard status is unverified (credentials absent).

Full native outputs, traces, old/new row exports and these scale artifacts are
archived separately in GCS with complete independent download/hash verification;
the receipt is `gcs-qualification-receipt.json`. This is qualification evidence,
not another native full-store snapshot. The campaign's seven complete original
stores already have separately verified scoring-base and final six-archive
backups. The unrelated conv48 partial ingestion and queued conv49/50 are not
relabeled complete by this SQL qualification.
