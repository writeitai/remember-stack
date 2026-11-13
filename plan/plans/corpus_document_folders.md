# Corpus document folders — implementation order (D155)

Date: 2026-11-13. Design-only plan; none of these slices is implemented by the
amendment. Authority: [E0 §§5–7](../designs/e0_files_design.md#5-mounting--the-corpus-is-the-customer-filesystem),
[D155](../../decisions.md#d155-the-corpus-filesystem-is-a-mutable-tree-of-document-folders).
[Analysis](../analysis/corpus_document_folders.md) records the tradeoffs.

Each numbered slice is a separate, independently mergeable PR with its stated
tests. Supporting capabilities can merge before the profile switches publishers;
the profile switch must include purge/readiness, so no active consumption surface
is exposed without its privacy path. No old-tree migration, compatibility alias or
dual publication is part of the work. Public docs describe a capability only in
the PR that actually enables it.

## 1. Object operations and provider-independent contract

Add streamed whole-object replacement and fully paged prefix listing to the
mutable capability beside immutable `ObjectStorePort`; reuse exact idempotent
deletion in purge implementations. Implement the same operations in the current
S3 and local-FS adapters. Keep E0 create-only writes unchanged. Stream transfers
across different endpoints/credentials, and abort/clean failed multipart uploads.
Keep local class bookkeeping outside the exported tree. Do not add server-side
copy, rename or a native GCS client.

Tests: shared port contracts for replace/create/delete/missing-key, boundary-safe
prefixes, empty prefix, all listing pages, missing/repeated continuation tokens,
large streamed objects and multipart failure cleanup. Run these against a real
**MinIO** fixture, not just mocked boto3/local FS, with more than one listing page
and a multipart-sized original. Confirm immutable overwrite still raises and
declared storage classes/transfer accounting are preserved. Wire the MinIO test
job explicitly even if the ordinary Compose profile uses another S3 provider.

## 2. Selection, paths and pure reader export

Add lineage suffix reservation/uniqueness, durable corpus revision and the projection tables in
[schema §10](../designs/postgres_schema_design.md#10-corpus-publication-progress-and-live-graph-catalog-d155-d98).
Implement the ordered type taxonomy, topic/slug validation, reader-kind registry,
active-generation selector and deterministic member/id-map indexes/sharding as a renderer
without changing the profile's serving composition. Read E0 URIs and inventories,
including D147 registered-upload keys and the generation-addressed PageIndex.

Tests: all type branches/fallbacks; email attachments classified independently;
same/empty/non-ASCII/hostile/long titles; a forced suffix collision retries before
commit and existing suffixes never change. Explicit no-topic, invalid/deep topic,
unknown reader kind, `provider_response` under `media/`, references/checkpoints
excluded, every expected reader asset included. Original/Markdown/source-map and
PageIndex bytes match authority; relative image links resolve. Seed v1 ready with
v2 stored/parked and prove every folder file is v1; prove a raw-only lineage has
no folder. Test shard split **and merge**, many distinct topics, bounded member/id-map indexes,
id lookup without scanning folders, large asset-table continuation, and byte-identical output for identical intent.

## 3. Incremental publisher and repair

Implement deployment mutation locking, per-document desired/applied revisions,
pending paths/affected-index journal, streamed publication and index-after-files
ordering. Include same-path updating/ready markers, stale-file removal, moves and
branch split/merge moves. Replace the builder's repair implementation with the
same selector/renderer, bounded document export/listing, orphan cleanup and
metadata reconstruction. Keep the new handler unbound to ordinary profile
serving until slice 5. Retire snapshot renderer code as the composition switches,
without retaining an alternate output contract.

Tests: fault injection after each publication step; duplicate/reordered delivery;
an authority revision changing during a copy; A→B→A; losing a lease/lock with
in-flight store IO; two writers sharing an ancestor; deletion during publication;
empty directories and stale assets. **MinIO-backed** create/update/title-topic-type
move/delete tests inspect actual object bytes and index links after every retry.
Compare a sequence of incremental edits with repair into an empty store: identical
key set and byte hashes, including no orphan folders. Count reads/writes to prove
an ordinary edit never recopies unrelated document originals or media. A large
stream test bounds memory, not only the number of returned files.

## 4. Work scheduling at authoritative boundaries

Register unlaned `publish_corpus` on the small `corpus_publication` request target,
in the non-convert self-host class. Enqueue a fresh corpus revision in the same transaction as served
version/representation changes, active structure/topic/title/type updates and
version/lineage/source deletion. Insert a deterministic request target per document/revision/policy so the
existing ledger uniqueness can distinguish another run; changing diagnostic
content hash alone cannot do it. Do not rearm/reset existing attempts or register
one component policy per revision. Include current-
pointer rollback and no-folder/removal. Reuse `enqueue_on`, collector/announcements,
attempts, lease, DLQ and lost-delivery janitor. No periodic projection scheduler.

Tests: transactional request/enqueue rollback; updates racing success, retry and
DLQ completion are never lost; A→B→A creates a fresh target after a previous
success; duplicate revision enqueue is a no-op; completion emits one intent rather
than work per chunk; parked/new raw upload does not publish prematurely; current
pointer changes in sources-only/claims/standard profiles all enqueue. Execute via
both a self-host class claim and D147 `run_task`; assert announcements, duplicate
delivery and lost-wake recovery. Policy seeding republishes without repeating paid
ingestion. Managed integration: register the same engine stage/route in the cloud
repo's pushed class map and test one pushed task there. Engine tests must succeed
without any commercial control plane.

## 5. Activate with deletion, forget and restore together

Compose the new publisher/repair in both profiles. Normal delete finishes its
incremental corpus removal or reports failure with durable retry work. Adapt the
D74 coordinator and existing projection-purge capability: stop controlled mounts,
drain/fence external writes, reset the entire dedicated corpus store including
retained versions/parts when configured, clear publication metadata/caches, repair
from scrubbed authority and verify before reopening. Remove snapshot registry,
pointer serving and obsolete mounts from the composition. Readiness replay covers
all manifests, even locally complete ones; one reset may cover their union.

Tests: extend `test_hard_forget_handler.py`, `test_selfhost_forget.py` and S55.
The mandatory **PostgreSQL + MinIO forget test** plants a unique token in the
original, Markdown, media, title/topic, root/member indexes, an old moved folder,
and an interrupted unindexed copy. Keep an independently supported control
document. Forget, then list/read every corpus key and assert no source copy or
member/id-map index mention remains, while the control folder is rebuilt. Inject delete,
multipart/cache-ack, repair and verification failures; the barrier stays closed,
retry completes, and delayed ordinary work cannot recreate the source.

Save a pre-forget database/bucket fixture outside the active store. Independently
restore (a) the corpus bucket alone with PostgreSQL still `complete`, (b) the old
database with the clean bucket, and (c) both, preserving portable intent. Readiness
must reset/replay before any API or controlled mount reopens; verify no unique
token, stale index line, old path or retained object version survives. Test session
cache revocation acknowledgement; do not mock the storage purge itself. No hosted
model calls. Owner-operated mounts' remount obligation must be documented.

## 6. Consumption paths, readiness and user documentation

Replace computed `documents/<doc_id>` paths with publication-receipt lookup in
document search, SDK types/descriptions and the shared MCP catalogue. Use the same
guard for current and periodised/history results. Make `p3` certify per-document
revision/inventory/index/cleanup, with truthful lag, failure and version reasons.
Update the consumption skill/navigation evaluation, all shipped mount/readiness
docs and `/docs/project/not-built-yet` as the new behavior becomes available.

Tests: no path before publication; path during a completed v1 with pending v2;
null for in-progress moves, failed/dirty publications, raw-only, deleted and
historical selections; resolve `doc_id` through bounded `_paths/` pages after
moves and shard changes, including periodised lineages whose search selection is
not their served version or has no edition in force. A recent
unrelated publication cannot satisfy readiness. Claims/facts switching changes
neither P3 content nor topology, while K remains separately gated. Exercise the
`llms.txt → indexes → actual original/derivatives` ladder with no raw/artifact
mount. Update contract fixtures deliberately; test full default and layers-off
catalogues. Preserve exact-version `source_open` parity.

## 7. Mount wrapper and provider qualification

Add `remember mount DIR` / `remember unmount DIR` as the thin CLI contract in E0
§5, paired with the cloud repo's ticket/session integration. Assert stdin-only
ticket delivery, direct subprocess invocation, no secret in argv/env/logs/files,
JSON/exit handling, missing executable and failed/expired ticket behavior.
Unmount uses the installed mountable CLI's supported unmount invocation, does not
fetch another ticket, and reports failure. The cloud route itself is outside this
repo's design. Keep self-host S3 FUSE instructions provider-neutral and read-only.

Run the same adapter conformance suite against credentialed AWS S3, GCS XML/HMAC
and mountable.io sandboxes as release qualification, recording actual supported
signing/addressing/checksum/multipart behavior. Test bucket=filesystem-id on
mountable.io, raw/artifact/corpus endpoints that differ, provider access logs,
and read-only session/cache purge acknowledgement. MinIO remains a required
repeatable engine integration gate, not evidence that other endpoints were tested.

## Cross-slice gates

Preserve Rule 4: provider-call counts/identities for two dated lineage versions
with untouched sections before/after an edited section remain unchanged by P3.
Run the existing reuse lifecycle/text-origin proofs appropriate to implementation;
do not add projection metadata or topic/suffix/revision to any paid-work key.
Accept successful `placement=null` as cacheable by changing only placement
schema/generation and root output handling, reusing stored section summaries.
Explicitly test that a topic/title-only projection change calls no model. Existing
known Rule-4 gaps remain marked, never silently weakened. Each implementation PR
lists actual checks and any unqualified provider behavior; none merges a new
serving path that can bypass D74.
