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
| Stable handle | Existing `doc_id`; an immutable, uniqueness-checked short suffix; publication lookup through existing document search | A global mounted id-to-path file grows with the entire corpus and becomes another shared rewrite on every move |
| Maintenance | A per-document ledger stage and serialized P3 writes; repair uses the same renderer | Full snapshots recopy originals and media unrelated to each edit; multiple independent writers need conflict handling for shared indexes |
| Read consistency | Atomic objects, explicit folder-level in-progress marker, indexes written after files | S3 has no portable multi-object directory transaction; snapshot isolation would restore the recopy cost or add a pointer filesystem |
| Hard forget | Reset all P3 contents and controlled serving caches under the existing barrier, then repair | Remembering only the last friendly path misses interrupted moves and independently restored old paths; storing all historic titles in immutable manifests is a privacy liability |
| Provider API | S3 operations, streamed across stores when necessary | Native GCS and mandatory server-side copy add provider-specific behavior with no required benefit |
| Fan-out | Deterministic radix shards for navigation children; bounded index tables | Unbounded topic directories and full member lists cease to be readable at millions of documents |

The serialized publisher is a deliberate small correctness mechanism: the
pipeline stays concurrent, but one deployment's P3 changes cannot overwrite each
other's shared indexes. It does not scan or recopy unrelated documents on each
event. Shared files are bounded child tables and counts, queried through indexed
projection metadata. Bulk repair is explicitly an O(corpus bytes) operation.
Throughput/lock wait and large-media transfer costs must be measured; there is no
additional scheduler or directory-specific lock protocol in this contract.

Hard-forget reset costs another copy of surviving originals. That cost is accepted
for the rare privacy operation, in exchange for a purge that remains provable even
when PostgreSQL, a bucket, or a serving cache is restored independently. Every
engine-controlled mount must stop serving and discard cached bytes before purge
acknowledgement; a database API barrier alone cannot stop an already mounted reader.
External client downloads are not library-controlled active stores. Lost worker
leases alone do not prove that an object request stopped: drain must include
in-flight external writes before reset/verification can complete.

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

## Reuse and scope

P3 reads committed bytes and never calls a model. Nullable placement changes only
the placement output generation/root-call schema; it does not alter the skeleton,
block grid, route, Markdown, chunker, embedding input or extraction input.
Projection revision numbers, suffixes and generated metadata must stay outside
all D56/Rule-4 identities. Existing reusable summaries remain reusable; acceptance
of `placement=null` must not be misclassified as degradation and retried forever.

The amendment reconciles the binding E0, retrieval, media, layer, forget, overview,
schema and requirement contracts. It does not implement them, design a hosted
control-plane route, change K's standalone repositories, or add a migration,
compatibility alias, alternate view or native GCS adapter.
