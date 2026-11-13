# Corpus folders: copies for readers, incremental maintenance

Date: 2026-11-13. Analysis for [D155](../../decisions.md#d155-the-corpus-filesystem-is-a-mutable-tree-of-document-folders).
The binding contract is [E0 §§5–7](../designs/e0_files_design.md#5-mounting--the-corpus-is-the-customer-filesystem).
This analysis does not claim that the new projection or mount commands ship.

## Problem and constraints

The owner requires one browsable folder per document: its original beside its
reader-useful derivatives. Raw stays authoritative; paying for a second original
is accepted. Types lead, an optional model-chosen topic follows, names are readable,
and customers mount the resulting filesystem read-only. P3 must work through the
existing S3 adapter on mountable.io, AWS S3, GCS XML/HMAC, and MinIO. K is outside
this tree. There are no users requiring a migration or compatibility layout.

Inspection of `workers/p3.py`, `spine/projection.py`, `spine/readiness.py`,
`spine/document_search.py`, `profiles/selfhost.py` and `adapters/selfhost/mounts.py`
found whole-tree text snapshots, `documents/<doc_id>/` stubs, duplicated facet
stubs, entity folders, computed rather than publication-checked MCP paths, and
readiness based on a global build timestamp. `run_projection(plane="p3")` is the
operational build entry point; setup also calls it on facts-layer changes.
`workers/forget.py` rebuilds and then purges old P3 snapshots. Existing P3 tests
mostly prove these old contracts with PostgreSQL and a local object store.

E0 is already the source for the replacement: raw keys normally use
`<doc_id>/<content_hash>/original<suffix>`; D147's registered uploads can retain
`uploads/<id>` instead. The stored URI, not a reconstructed filename, is authority.
Representations contain `document.md`, `blocks.json`, `conversion.json`,
`meta.json`, optional `source_map.json`, and `media/`. The active PageIndex sidecar
is generation-addressed under `structure/<generation_id>/pageindex.json`.
The conversion inventory already supplies asset kinds, hashes and locators.
Crucially, `media/` can contain `kind=provider_response` OCR JSON: copying an
artifact prefix wholesale would expose pipeline internals. Supplied references
NDJSON and conversion checkpoints are also private pipeline objects.

D117 deliberately separates latest stored from processed currency. D55/D65 retain
the last completed version/representation while a successor processes. D143 makes
sources always available with claims/facts optional. Hard-forget's actual authority
is D74; D147 supplies explicit contexts and separately durable object-store
manifests. The brief's “D101-style stage classes” refers here to D146 (classes)
and D147 (`run_task`); D101 itself concerns ingest attribution.

## Choices and alternatives

| Question | Chosen | Why the alternative loses |
|---|---|---|
| What is mounted? | Actual copies in one P3 bucket; immutable E0 stores stay private | Stubs and a union of raw/artifact mounts do not give the requested self-contained folder |
| Which version? | The served completed version, including its original | Latest raw plus older derivatives makes an ordinary folder contain two different testimonies; publishing raw-only folders needs another state and trigger |
| What about parked uploads? | Retain D117 storage/parking and raw API access; no folder until a version completes | Losing early mount visibility is explicit, smaller than a mixed-version tree, and never promotes unfinished testimony |
| Type/topic | Engine-derived small type enum; stored nullable topic from root placement | Asking the model for types or reconciling topics corpus-wide adds unstable choices and extra paid work |
| Stable handle | Existing `doc_id`; a deterministic 96-bit SHA-256 suffix; MCP `p3_path` or mounted suffix search | A suffix reservation and radix id index add state without improving the required lookup or erasure contract |
| Maintenance | Idempotent `publish_corpus(doc_id)` recomputes paths/files and indexes from the DB; one deployment advisory lock serializes publication | Revision counters, path journals, publication receipts and membership tables duplicate authority and introduce recovery state |
| Read consistency | Atomic objects, explicit folder-level in-progress marker, indexes written after files | S3 has no portable multi-object directory transaction; snapshot isolation would restore the recopy cost or add a pointer filesystem |
| Hard forget | List by deterministic document suffix, erase that document's copies, then regenerate indexes from scrubbed DB authority under D74 | Resetting the store empties every customer's mount and recopies every survivor to forget one document; recording historic friendly paths in portable intent is unnecessary |
| Provider API | S3 operations, streamed across stores when necessary | Native GCS and mandatory server-side copy add provider-specific behavior with no required benefit |
| Navigation | Deterministic radix pages for bounded type/topic member tables; physical document paths stay independent of page membership | Moving document folders for page splits/merges would recopy survivors during forget and couple unrelated publication markers |

**Reader naming and family corrections (2026-11-13).** Slice 2 review found that
replacing non-ASCII runs after NFKC normalization
destroyed Latin words such as Czech `dílo`. Use standard-library NFKD
decomposition, remove combining marks, and explicitly fold common Latin letters
that do not decompose (`ß`, `æ`, `œ`, `ø`, `ł`, `đ`, `ð`, `þ`) before ASCII
slugging. This preserves readable Latin titles and topics without a general
transliteration dependency or a change to path validation and length limits.
Scripts with no ASCII form retain the existing title fallback/topic rejection.

Type rules must use the actual names in
[`format_registry.FAMILIES`](../../src/rememberstack/core/format_registry.py).
Audio and video share the `media` family, but their stored MIME types retain
`audio/` or `video/`; the registry's canonical media MIME is `audio/mpeg`.
[`core/conversion.py`](../../src/rememberstack/core/conversion.py) routes both to
the same `card` converter, and E0 stores the converter name as the representation
route. There is no shipped media route or source-kind subtype that provides a
second discriminator. Use MIME for audio/video; a media row without either MIME
prefix belongs to `other`. Defaulting that row to video would invent a subtype.
An exhaustive registry-family test makes every new family require an explicit
type expectation. These reader-policy corrections change only P3 paths/digests;
they do not change converter generations or any paid-work reuse identity.

The suffix is `sha256(doc_id.bytes).hexdigest()[:24]`: 24 lowercase hex characters,
or 96 bits. For n distinct document UUIDs, the accidental collision probability
is approximately n(n−1)/(2 × 2^96), below 6.4 × 10^-14 even at 100 million
lineages. Twelve hex characters would provide only 48 bits and about a 16% chance
of at least one collision at ten million lineages. The 96-bit choice needs neither
reservation nor collision-extension machinery. An unkeyed hash also avoids a
secret whose loss or rotation would break erasure after restore. The UUID remains
the identity; the suffix is a deterministic way to find every moved or interrupted
copy, even after the document's source metadata has been scrubbed.

The existing work ledger is unique by target/stage/component version, not content
hash: enqueueing an already succeeded document target would lose a subsequent
update. Use a fresh event UUID as the `corpus_publication` target for each committed
trigger, with only `doc_id` in the existing work payload; the event lives solely
in `processing_state`, with no request table or lineage revision counter. Transport
retries deliver that same processing row. A→B→A commits get separate ordinary work
rows, but every handler reads current authority, so their delivery order cannot
restore an obsolete reading. Existing attempts, leases and DLQ rules stay intact.

The sole new per-document state is nullable `corpus_published_revision`, a digest
of the current DB-selected export inputs, desired path and builder policy. Clear
it when those inputs change and before publication; set it only after files,
cleanup and index regeneration succeed and authority still matches. MCP computes
the current path only behind that match; readiness uses the same guard. No stored
published path, reader-inventory receipt or pending-path list is needed. Type/
topic/title changes invalidate the digest by comparison; navigation pagination
changes indexes only, not physical document paths or other documents' markers.

One deployment advisory lock keeps concurrent publishers from overwriting shared
indexes with competing snapshots and also serializes same-document copies. Each
publish lists existing suffix-bearing folders, streams the desired files, deletes
other copies, and renders affected ancestor indexes and root `llms.txt` afresh from
the DB. Never patch old index contents or replay queued paths. If a crash deletes
the last old-folder object before its parent index is repaired, a paged scan of
navigation indexes can still discover the stale member link by `doc_id`/suffix;
regenerate that entire index from the DB. Reconcile renders all current navigation
indexes and prunes obsolete ones. These are recomputable operations, not a journal.
The inventory's member links identify documents explicitly, so retry discovery
does not depend on titles. Empty obsolete topic/page indexes are pruned against
DB membership. Lost or reordered shared-index writes are corrected by the next
affected publish or reconcile.

Suffix discovery requires a paged key scan because the navigation prefix can move;
it is O(corpus keys), not O(corpus bytes), and ordinary publication recopies only
the selected document. Navigation page splits/merges rewrite metadata only.
Measure listing cost, lock wait and large-media transfer cost. Full reconcile is
repair, not a prerequisite for every edit. There is no new scheduler or persistent
navigation-membership cache.

Hard forget uses the same suffix scan after authority scrub. It also enumerates
navigation-index keys and regenerates/prunes them against the scrubbed DB, covering
index-only remnants after a prior interrupted delete without any historic path
list. This can rewrite shared navigation metadata; surviving originals/media stay
in place and are never recopied. The existing D74 barrier, external-write drain,
verification and replay of every portable manifest remain essential: a late upload
could otherwise recreate erased bytes, or an independently restored bucket could
bring back a former path while PostgreSQL still says `complete`. Engine-controlled
mount/cache copies must acknowledge purge before serving reopens; external client
downloads are outside library-controlled active stores. An expired worker lease
alone does not prove that an object request stopped.

## Slice 3 lock-loss counterexample (2026-11-13)

**Historical stop, resolved by the reviewer disposition below.** Implementation
originally stopped under the owner's explicit instruction: if a single
idempotent recomputation is insufficient, report the concrete failure instead of
adding coordination machinery. The disposition below amends D155 and its binding
design to accept and repair this specific ordinary-publication race.

[The executable reproducer](../../src/tests/adapters/test_minio_corpus_lock_loss.py)
uses the real PostgreSQL test database and real MinIO started with
`.github/ci/start-minio.sh`. A small HTTP relay delays delivery of one complete
signed PUT after boto3 has sent it. It forwards the request and MinIO's response
without mocking storage. The test controls ordering with thread events, not
timing sleeps:

1. Writer A owns a deployment-scoped PostgreSQL advisory lock and sends the
   ready document `_index.md` for the original export identity. The relay receives
   its complete request and holds it in flight.
2. The test terminates A's PostgreSQL backend with `pg_terminate_backend`. Writer
   B now successfully acquires the same advisory lock. The test also confirms
   A's next DB operation fails: lock loss is observable, but the issued PUT
   cannot be retracted by that observation.
3. A canonical title change from `Annual Report` to `ANNUAL REPORT` changes the
   export identity while preserving its normalized physical path. B re-reads
   authority, renders and writes every selected reader file and navigation index,
   then locks the document row, confirms the desired digest and sets its marker.
   The ready index contains B's identity and the old request is still in flight.
4. The relay releases A's old PUT. Real MinIO atomically replaces B's ready index
   with A's old bytes. The document marker still equals the current DB-derived
   digest, but the ready-folder identity is from the previous export.

The original counterexample passed by asserting this **failure state**. It
characterized the proposed primitives; the current regression extends it with
the accepted repair and refusal of further writes described below. The upload
thread is joined before exit: it does not require a
detached upload, a new stale event, a lost announcement, or a stale final marker
write from A. B may finish while A's synchronous request remains outside its
control. A later recomputation could repair the index, but the last ordinary event
can already have succeeded; the marker cannot certify that its files survived.

This violates E0 §6.5's requirement that claim/lock loss settle or fence in-flight
external IO **before another writer can proceed**. Renewing a lease, checking the
DB before/after each operation, or joining IO before a handler returns does not
keep the PostgreSQL lock held after its session is terminated. An HTTP timeout
also does not establish that a remote request cannot commit. The same ordering
can affect a file replacement or removal; delete/forget/restore have deliberately
not been wired or exercised by this change.

**Accepted reviewer disposition (Claude, design owner, 2026-11-13).** Prevent
where cheap, repair by digest, and do not add coordination machinery. Check the
same lock session before every mutation, bound provider connect/read timeouts and
multipart units, and stop issuing requests on lock/claim loss. One already-issued
request can still land late; timeout alone cannot retract it. Accept that rare
transient staleness and define the marker as “current inputs were published once”.
Attach the expected SHA-256 to every corpus object and have verifying reconcile
rewrite mismatches, absent objects and missing metadata, including behind a matching
marker. This preserves the small protocol instead of introducing durable fences,
receipts or journals. Periodic verification is wired with activation in slice 4;
D74's erasure barrier/drain remains binding and separate. The reproducer becomes
an implementation regression: the late write really lands, further requests from
the dead publisher are refused, and verifying reconcile repairs the newer bytes.
The accepted contract is reconciled in D155 and E0 §§5–7. No ingestion identity or
paid-work reuse boundary changes (Rule 4).

Reproduce with `REMEMBERSTACK_DATABASE_URL` pointing to a disposable PostgreSQL
test database (the imported fixture resets its schema):

```bash
.github/ci/start-minio.sh
REMEMBERSTACK_TEST_MINIO_ENDPOINT_URL=http://127.0.0.1:59000 \
  uv run pytest src/tests/adapters/test_minio_corpus_lock_loss.py -q
```

## Provider facts checked on 2026-11-13

Google documents an S3 interoperability path using its XML endpoint and HMAC
credentials, including V4 signing. This supports reusing the S3 adapter rather
than designing a native GCS adapter. [Google interoperability](https://cloud.google.com/storage/docs/interoperability).
Its XML listing API documents `list-type=2`, continuation tokens and prefixes;
those are the operations the paged tree listing needs.
[Google list objects](https://cloud.google.com/storage/docs/xml-api/get-bucket-list).

Large copies can use S3-compatible multipart upload in GCS. Multipart completion
replaces the object; GCS does not support preconditions on that upload path.
The design therefore serializes writers rather than depending on multipart CAS.
[Google multipart uploads](https://cloud.google.com/storage/docs/multipart-uploads).

AWS documents strong read/write/delete/list consistency.
[AWS S3 consistency](https://aws.amazon.com/s3/consistency/).
GCS documents strong object/list consistency and individual-object atomicity,
while warning that caches and access revocation have different behavior. The
design's multi-file consistency and mount-purge acknowledgement are engine
contracts, not an inference that S3 offers directory transactions.
[Google consistency](https://cloud.google.com/storage/docs/consistency).

The mountable.io S3 endpoint, bucket=filesystem-id mapping and exact
`mountable mount --ticket-stdin --json DIR` command are owner-supplied contracts.
Public mountable documentation could not be retrieved in this session; no
successful interoperability or session-revocation test is claimed. Provider
qualification belongs to the delivery tests, with MinIO mandatory in this repo.

### Mountable upload-listing compatibility (PR #603)

Source inspection on 2026-11-13 of
[`writeitai/fsit@a645c163`](https://github.com/writeitai/fsit/tree/a645c163e4746503dc0d83ad5a6d8827f397c060/internal/gateway)
found one incompatible request in slice 1: `s3.go`'s `bucket()` returns
`NotImplemented`/501 for `ListMultipartUploads`. Requiring it during raw/artifact
purge or verification would block every managed hard forget. `s3_multipart.go`
serves create/upload-part/complete/abort for a known upload id and sweeps abandoned
uploads after its seven-day `uploadLifetime` (on an hourly sweep). Staged parts
are not published files. The adapter continues aborting its own failed uploads.

Treat only upload-listing `NotImplemented`/501 as provider-managed cleanup and
return explicit `multipart_uploads="provider_managed"` evidence, with a warning
for existing callers that discard the return. This verifies published-object
absence, not immediate erasure of unfinished parts. Providers serving upload
listing keep explicit abort and verification. Swallowing authorization, transport,
other service or abort failures would hide real erasure failures; all still fail.
No provider-name switch, configuration flag or cached capability is needed.

The same source audit checked the other S3 requests added by the PR:

| Adapter request | Mountable handler contract |
|---|---|
| `ListObjectsV2`, including `continuation-token` and automatic `encoding-type=url` | `s3_list.go` accepts both and returns an opaque next token |
| `Content-MD5` on small PUT and upload-part | Both call `s3body.go`'s `newS3Body`, which validates the base64 MD5 and rejects mismatched bytes |
| `If-None-Match: *` on immutable PUT | `s3.go`'s `s3Conditions` accepts this exact precondition |
| Multipart create, upload-part, complete and abort | Object-level routes in `s3.go`/`s3_multipart.go` serve them; 8 MiB parts meet the 5 MiB minimum |

These calls need no compatibility change. Source inspection does not qualify a
deployed endpoint. Slice 6 must exercise these requests, record the provider's
unfinished-part retention/cleanup guarantee and distinguish it from checked
absence in purge evidence.

## Reuse and scope

P3 reads committed bytes and never calls a model. Nullable placement changes only
the placement output generation/root-call schema; it does not alter the skeleton,
block grid, route, Markdown, chunker, embedding input or extraction input.
Publication markers, suffixes and generated metadata must stay outside
all D56/Rule-4 identities. Existing reusable summaries remain reusable; acceptance
of `placement=null` must not be misclassified as degradation and retried forever.

The amendment reconciles the binding E0, retrieval, media, layer, forget, overview,
schema, benchmark-attestation and requirement contracts. It does not implement them, design a hosted
control-plane route, change K's standalone repositories, or add a migration,
compatibility alias, alternate view or native GCS adapter.
