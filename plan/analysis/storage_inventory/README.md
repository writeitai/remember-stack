# Local PostgreSQL storage inventory

**Status: non-binding analysis evidence, not a storage meter or a customer
quantity.** On 2026-11-11 a disposable, loopback-only container used the already
cached image
`sha256:e97ef047169b272734e95f660cca3a4de74c637d0b7c3a7c633122a8f04af43e`.
Its OCI revision is the recorded deployed engine pin
`fb4a5f5a4cc2eb415df3a8f5b7908bb506bf58d0`; its version is
`19rc1-0.19.0`. No image/package download, provider call, customer data, live
service scan or credential was involved. The container was removed.

Two fresh databases ran the real Alembic chain independently from that engine
pin and accepted main `149def9e8ba0a7d7404292efc5af267d6ea167b5`. Both reached
`p9_46_0067`. Named public objects, kinds, index/view definitions and partition
bounds matched; generated TOAST names and allocated bytes are compared through
the owning relation, not assumed to be stable identifiers. Migration files
are unchanged between these pins. This proves the listed schema closure, not
that all newer engine behavior is deployed.

The command used `--pull=never`, tmpfs for `/var/lib/postgresql`, UTC,
`shared_preload_libraries=pg_textsearch`, and two database-specific role sets.
The pg_partman background worker was disabled. This was migration-only: no
engine start-up seed, customer Project, model/provider request, concurrent
mutation, capture lock or maintenance was exercised.

## What the files contain

[`catalog-query.sql`](catalog-query.sql) is the exact bounded, read-only query.
It lists non-system namespaces and only engine-owned TOAST relations/indexes;
it does not include unrelated PostgreSQL catalog TOAST. Every physical index
resolves through `pg_index`; every TOAST heap resolves through its table's
`reltoastrelid`; descendants resolve through `pg_inherits`. It records all four
fork sizes in order `main`, `fsm`, `vm`, `init`, index validity/readiness,
partition bounds, and definition digests. Ordinary views and composite types
have no relation-file payload. The three sequences are reported separately.

[`p9_46_0067-allocated-relation-profile.json`](p9_46_0067-allocated-relation-profile.json)
contains the complete observed set: 98 public roots, 350 physical public tables
(including 262 partitions), 10 partitioned parents, 860 physical public indexes,
33 partitioned indexes, 345 owned TOAST heaps and 345 TOAST indexes, 42 views,
three sequences and one composite type. All observed indexes were valid/ready.
Monthly partitions cover July 2026 through March 2027 plus defaults; the three
hash-partitioned families each have 64 children. These names/counts describe
this observation, not a fixed rule that would reject legitimate future months.

The 860 physical public indexes use btree (838), GIN (12), HNSW (5), BM25 (3)
and GiST (2). Empty extension indexes still allocate pages. Zero observed FSM,
VM and init sizes mean those forks were enumerated and empty in this fixture;
they do not establish that populated/maintained relations lack them.

Purpose labels group engine meaning without selecting a commercial charge.
A relation containing retained replay state, failed-attempt metadata and current
rows is not split into fictional physical row-byte totals. A label containing
“audit” is also not permission to erase a truth-defining provenance ledger.
Indexes and TOAST inherit the actual owning root's purpose. Unknown objects,
new indexes, invalid indexes and non-qualified extension storage fail a future
profile check; this artifact is not an allow-by-name-prefix policy.

## Allocation result and limits

The sum of each physical table's `pg_total_relation_size` was **10,051,584
bytes** on both databases. Independently summing each owned physical
relation/index/TOAST fork exactly once produced the same amount. The three
sequences add **24,576 bytes** outside that table sum. These are allocated
files, including empty index pages; they are not the number of live customer
content bytes or total database/server disk usage.

Applying the old UMC collector's eleven excluded root names and their children
arithmetically leaves **9,551,872 bytes**. That is a counterfactual comparison,
not a qualified receipt: the actual old collector refuses this schema because
it requires `p9_34_0055`. It also retains partition templates and extension
configuration in its included set. No changed billable mapping is selected.

A fresh migrated footprint is neither a guaranteed production minimum nor a
cost-floor proof. Start-up configuration, partitions, vacuum/reindex behavior,
TOAST, extension buffers/files, failed/rebuild copies, WAL, logs, backups,
provider staging and periods without customer-usable coverage still need their
own qualified bounds. In particular this experiment does not prove BM25/HNSW
maintenance obeys the proposed cut or that `pg_total_relation_size` includes
every extension resource. The existing price and limits remain unchanged.

The [completed local BM25 counterexample](pg19_bm25_heap_share_counterexample_20261111.md)
shows why the actual native guard must qualify maintenance admission: a
privileged spill grew an index while its heap SHARE lock remained held.
It does not establish the permissions of ordinary deployed writer LOGINs or
qualify a replacement capture profile.
