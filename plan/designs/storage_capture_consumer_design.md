# Engine-owned retained-storage captures

**Status: accepted on coordinated merge of this PR; not implemented.**
This is the complete consumer contract, not an implemented producer,
qualified adapter or permission to change commercial terms.
[Analysis and source boundary](../analysis/storage_capture_consumer_20261111.md).
The broader growth-permission proposal remains unchosen
([scope boundary](#8-what-this-decision-does-not-adopt)).

## 1. Problem, decision and ownership

A provider can prove which stored entries existed at an instant; it cannot
decide which are the engine's canonical sources, failed copies, knowledge or
projections. A processing success cannot describe all retained bytes either:
a card, unchanged upload, failed conversion or privacy operation can retain
resources without a successful paid processing receipt.

The engine owns one read-only capture consumer. It joins a native engine view
and qualified store evidence for one deployment at one requested UTC instant.
It classifies every retained reference and observed resource, checks the
complete sets in both directions, and publishes immutable evidence. Native
reference/allocation meaning and completeness remain in this open-source
library. An operator receives content-free quantities and evidence identities,
and decides funding and permitted commercial mapping through D61. No port can
create a document, decide E/K/P truth or fabricate a native commit.

The same contract covers the two maintained substrate families. The self-host
adapter supplies qualified local/store evidence; the managed reference adapter
supplies authenticated provider evidence. Their required capabilities are
identical. A configured store without a qualified consistent-view capability
returns unsupported; an ordinary directory listing, S3 listing, latest counter
or old collector is never a fallback. This limitation concerns the storage
evidence capability, not ordinary unmetered memory correctness or reads.

Capture remains available for parked, deletion/reconciliation and privacy
work. It does not require a customer storage top-up merely to observe existing
resources. The operator provisions and accounts for collection/evidence work.
No price, minimum, new customer quota, debt permission or free interval is
introduced.

## 2. One typed input and one original view

An engine request has exactly:

- contract: rememberstack.storage_capture_request.v1;
- deployment_id, an actual engine UUID32;
- cut_at_unix_us, an unsigned decimal-string UTC microsecond instant;
- store_binding_digest, schema_profile_digest, classification_digest and
  native_capture_profile_digest;
- operator_mapping_digest, naming the operator's independently accepted
  component mapping, never caller-authored byte totals or prices.

The capture identity is SHA256 of the contract's ASCII bytes, one NUL byte and
canonical request JSON. Same identity/different input conflicts. Bindings
identify the native PostgreSQL store and every configured canonical, artifact,
corpus, forget and K store,
with role, exact store identity and admitted representation/profile. No
customer prefix or filesystem display name repairs an incomplete binding.
An absent component is zero only when configuration and the closed writer
profile prove that the component cannot exist.

The operator invokes the one library operation
StorageCaptureCatalog.capture_once(request). Scheduling and invocation
authority belong to the deployment
operator; no daemon, new task queue or release coordinator is added. The
operator can batch independent deployments at the same instant; one
deployment's missing evidence never authorises another's bytes.

Before the earliest possible cut, acquire the qualified native engine
mutation/schema/maintenance guard. Its original transaction checks an absolute
native deadline before acquiring locks and remains alive through the full
engine clock-uncertainty interval. The profile pins
the required SHARE/DDL/maintenance exclusions, supported index access methods
and extension writers. An advisory convention or MVCC view alone cannot freeze
physical allocation. Unsupported/background writers refuse qualification.

The guard uses one fixed owner-installed native operation with minimal EXECUTE
authority for the scoped collector LOGIN, not an owning-role password or
arbitrary SQL/table names in the API. It derives the deployment and closed
relation set from native state and the installed profile. Preserve actual
LOGIN attribution. Neither a customer key nor a caller-supplied role/clock
enlarges that authority. The original transaction's finite lock, statement and
transaction bounds must fit the original boundary budget; no reconnect or
late re-lock continuation is permitted.

While that guard holds, establish one original read-only repeatable engine
view and record its backend/transaction/snapshot identity and actual native
acquisition-before/acquisition-after clock bracket. Pin extraction relations
against DDL for that view. Sampling time when enumeration starts is not its
acquisition time. Read and spool every qualified PostgreSQL allocated-byte
value, including pg_relation_size/pg_total_relation_size reconciliation, while
the short guard still excludes writers across the cut. These immutable physical
records live outside the measured customer set before that guard releases.
Repeatable read and an AccessShare extraction pin do not freeze physical file
sizes against subsequent DML; later enumeration must not resample those sizes
and label them as the earlier cut.
The first consistent snapshot of that repeatable-read transaction must be
acquired under the guard. Any earlier role/configuration query ends its own
transaction before this view begins; locking after an earlier SELECT would
not refresh a frozen view. The native guard also excludes the actual engine
migration/partition/maintenance entry points; table locks alone are not a
promise against unaccounted schema or extension changes.

Arm the one provider invocation early enough for its own qualified boundary.
The two systems independently establish their witnesses around the same T.
Release the engine's short guard after its own continuity/bracket and definite
release evidence; do not wait for the provider's long materialisation or
final manifest. The provider never calls back into the engine. Stream complete
reference/classification/member pages through the
same original engine view. No later reader pool or exported-handle handoff
recreates the view. The view has the profile's finite native lifetime and
measured retention/I/O envelope. Loss, expiry or a missed cut gives no completed
historic capture. Never hold the guard while enumerating millions of entries,
waiting for model work, delivering evidence, obtaining funds or receiving ACK.
Materialise the two sides independently after their short releases. Complete
the engine result only when the finished provider witness also proves the
required overlapping bracket, release, scope and complete pages. Provider
failure makes this attempted engine capture unavailable; it does not justify
retaining the engine guard or reconstructing an older cut. No extra early
provider callback/API is needed.

## 3. Provider boundary and complete matching

The managed adapter consumes fsit D63's one native capture protocol for the
exact four distinct raw/artifacts/corpus/forget filesystem IDs in one
organisation and cell. Its request remains fsit-capture-v1 with the existing
organisation UUID32, literal cell ID, sorted canonical filesystem IDs and
cut_at_unix_us. Each filesystem is sixteen bytes encoded as 26 lowercase
unpadded base32 characters; decode/re-encode exactly. Path and alias framing
uses those decoded bytes, never a synthetic UUID alias.

The agreed item boundary has exactly filesystem_id, path_id, version_id,
entry_kind, logical_bytes, inline_bytes, alias_group and storage_flags.
Entry kinds are regular, directory and symlink; a hard link retains its
resolved supported kind plus an alias group. Non-links use explicit null
alias_group. Size/inline integers are unsigned decimal strings. The five
independent boolean flags are metadata_gzip, inline_content, chunk_backed,
chunk_manifest and remote_backed. Mixed flags remain explicit. Unsupported or
contradictory modes, device/FIFO/socket/irregular representations, malformed
link ownership or unknown integer values refuse rather than being normalised.
Version identity binds both original stored byte rows when a link is resolved.

The provider owns the decoder and durable page/manifest wire implementation;
the engine adapter validates that one versioned protocol, not a second
decoder or substitute API. The complete manifest binds request/scope,
qualified provider/filer/schema profile, actual writer/process lifecycle,
both original views, clock/guard/release evidence, all four explicit scoped
sets, counts and ordered page digests, completion and expiry. One empty set is
not a missing set. Page ordinals are contiguous and replay returns the exact
same immutable result. The authenticated transport/current scoped grant
proves origin/authority; a digest alone does not.

The engine derives path identities for its known keys under that exact
capture/filesystem and matches references to provider items in both
directions. Duplicate members, absent canonical entries, inconsistent
versions/sizes, unproved aliases or unknown entries cannot become zero.
Distinct stored copies remain distinct even when content hashes match.
A qualified alias collapses only one demonstrated stored origin, not provider
resource counters or unrelated logical references. Names, contents, symlink
targets and owning-role query results never leave the provider boundary.
Several logical references may name one stored version: retain those reference
members while counting that actual stored origin only once.

Resource facts remain separate: filesystem retained/pending/sealed counters;
chunk groups by pending/uploading/sealed and deletion mark; multipart groups
by open/completing/committing/aborting with upload/registered-part counts and
bytes. Sealed is a subset of retained, not an extra charge; uploading bytes
remain a reservation until sealing records actual size. Groups preserve zero,
state and deletion distinctions. Cross-scope, negative, overflowing or unknown
states refuse. These dimensions overlap and are never summed into billable
logical bytes. Their witness records the ORIGINAL resource-view acquisition
bracket separately from namespace acquisition and extraction duration. They
are not asserted to be all physical resources at the requested cut.

The self-host adapter must supply equivalent complete, authenticated immutable
store evidence under its qualified native snapshot/journal profile. A mutable
local walk or a generic S3 listing after guard release does not meet it.
Qualifying that concrete substrate mechanism is mandatory; this interface does
not claim that current LocalFS/S3 adapters already provide it.

## 4. Component meaning and commercial mapping

For PostgreSQL, the engine closes the schema and writer inventory for its exact source.
Relations resolve by catalog ancestry and approved partition templates/bounds,
indexes by pg_index and TOAST by reltoastrelid. Count each qualified physical
fork/file once and reconcile the physical-table pg_total_relation_size sum.
Partitioned parents/indexes have no payload of their own. Sequences, WAL,
system/catalog/extension side resources and operational evidence remain
separate. Do not replace allocated bytes with row-length estimates or
subtract guessed dead/free pages.

Purpose classification distinguishes canonical raw/referenced artifacts,
truth-defining provenance, K repository origin/history, P3 durable origin,
privacy-control intent, staging/failed attempts, paid-work checkpoints,
audit/operations, scratch/rebuilds, caches and serving replicas. Hot/cold
placement is a different field. The closed source inventory, not a name
prefix, owns these classes. Unknown/mixed purpose remains explicit and cannot
be assigned a fictional physical row fraction.

The operator mapping may select only qualified engine-owned purposes for its
four component totals. It cannot classify memory truth, silently include
excluded operator resources or suppress their cost. A mixed physical root
without an accepted exact mapping yields no chargeable component result.
The captured allocation/purpose remains evidence; it is not zero.

Serving a durable P3 origin in place does not create another physical copy.
An additional staging/served tree is a distinct operator replica. Distinct
raw/artifact and P3 objects stay distinct resources even when their payloads
match; the operator's accepted mapping determines permitted inclusion without
invented content-hash deduplication. K uses qualified actual repository object/
pack ownership and retained byte evidence, not one charge per checkout.
Forget control is separately durable and is not a customer funding authority.

Only a complete capture with a current matching mapping may produce the four
operator component amounts and their checked sum. Missing qualification,
unknown representations or incomplete classification produces no valid billing
snapshot. Observations may still be retained as explicitly unqualified
diagnostics. The operator's existing missing-data policy owns consequences.

## 5. Exact evidence set, publication and ACK

The immutable engine manifest binds the request, native witness, provider
manifest digest, component records, purpose/resource observations and exact
native membership pages. Members identify actual classified references and
committed terminal processing outcomes visible in the same engine view by
their stable opaque IDs and payload digests. Outstanding/uncertain operations
remain explicit observations; a processing failure does not prove their
stored resources disappeared. Classification does not require a paid receipt.
Enumerate every actually present committed terminal row in the captured view,
without filtering by processing-delivery ACK or a sequence range. Existing
privacy/deletion authority is not delayed to preserve financial membership.

Only actually visible committed terminal members can establish storage
eligibility; an allocated processing sequence, a maximum sequence, a count or
an earlier HTTP/task ACK cannot. Processing receipt contracts and processing
success/failure economics remain unchanged. The storage channel does not
silently consume or reinterpret the processing outbox's ACK state.

The closed content-free record shapes are:

| Record | Required fields |
| --- | --- |
| Reference member | kind=reference, opaque_id, payload_digest, component, purpose, store_binding_id, resource_identity, version_identity, logical_bytes, alias_group |
| Terminal member | kind=processing_outcome, opaque_id, payload_digest, measurement_id, outcome, receipt_digest |
| Resource observation | kind=resource_observation, opaque_id, payload_digest, store_binding_id, purpose, resource_evidence_digest |
| Component record | component, measured_bytes, classification_digest, operator_mapping_digest, evidence_digest, resource_set_digest |
| Native witness | contract, capture_id, deployment_id, profile_digest, source_digest, schema_digest, backend_identity, transaction_identity, view_identity, view_acquired_before_unix_us, view_acquired_after_unix_us, guard_acquired_before_unix_us, guard_acquired_after_unix_us, guard_checked_after_unix_us, release_before_unix_us, release_after_unix_us, clock_profile_digest, clock_error_us, allocation_records_digest |
| Page | contract, capture_id, page_ordinal, members, member_count, page_digest |
| Manifest | contract, capture_id, request, native_witness_digest, provider_manifest_digest, component_records, reference_count, terminal_count, resource_observation_count, page_count, page_digests, set_digest, manifest_digest |
| ACK | contract, capture_id, manifest_digest, set_digest |

Components are postgres_allocated, canonical_object, k_repository and p3_origin,
each exactly once and ordered by literal component name. Every measured_bytes
is an unsigned decimal integer string, including explicit zero for a qualified
empty component. A reference's purpose is one source-classified value:
canonical_raw, referenced_artifact, truth_provenance, knowledge_origin,
projection_origin, privacy_control, staging, failed_attempt, paid_checkpoint,
audit_operations, adapter_metadata, scratch_rebuild, cache, serving_replica or
mixed_unresolved.
An unresolved value cannot produce a qualified component amount.
Resource observations retain excluded/staged exposure without turning it into
customer bytes. Receipt digests bind existing native v3 terminal receipts;
terminal outcome is succeeded or failed, and only actually committed succeeded
members are eligible for the operator's successful-growth allocation.
Member payload/receipt digests cover content-free native metadata, never raw
source, claim, prompt or filename bytes. They are not exported content hashes.
The receipt digest binds the existing immutable canonical v3 receipt body,
without delivery/ACK state or reformatting its identifiers into this new
protocol's representation. No processing receipt/key is changed.

A reference member names a store-backed reference. Its logical_bytes is that
reference's complete stored-version length, checked against the qualified
native/store evidence, not an estimate of a PostgreSQL row or its disk pages.
Several references can repeat the same length; never sum those reference
lengths into a component. The PostgreSQL component uses only the allocated
physical values spooled under the short guard. Canonical-object and P3-origin
components use the logical stored lengths of the mapped actual origins, each
once after qualified alias/origin matching. K uses the native profile's actual
retained compressed object/pack-file lengths, each stored file once, not an
uncompressed object total or repeated checkout size. The mapping selects
permitted purposes; it cannot silently switch these byte bases. Provider
retained/pending/sealed or multipart resource counters are never replacements
for any of those amounts. Reference logical_bytes is matching evidence, not an
additional fifth component. The checked total is only the four measured_bytes.

The native witness contract is rememberstack.storage_native_witness.v1.
Its identities name the original sessions/view, not newly sampled replacements.
Its clock profile binds the native/UTC uncertainty and continuity proof.
Guard acquisition's upper bound is no later than T-error; same-transaction
guard continuity is checked after T+error, and definite release follows that
check within the original deadline. View acquisition retains its own actual
bracket. A delayed command or a later enumeration timestamp cannot satisfy
those inequalities for an earlier view.

Opaque member/resource/version identities are typed native UUID32 or
profile-domain SHA256 identities, never paths or content hashes. The profile
fixes which kind uses which identity representation. Members are unique by
(kind, opaque_id), ordered by literal kind then canonical opaque identity, and
paged without omission. The native capture profile pins one positive
page_member_limit for deterministic consecutive blocks of that sorted set;
page ordinals start at zero, only the final page may be shorter, and no page
is empty. An empty member set has zero pages and an explicit empty digest list.
This is a collection-memory bound, not a customer resource cap. A changed
payload for one member conflicts. Counts
are unsigned decimal strings, equal the actual complete member sets, and
explicitly include zero. The manifest has four component records and an
ordered page-digest list even for an empty logical store. A missing list is
not an empty one.

Canonical JSON is compact UTF-8 with no insignificant whitespace and
lexicographically sorted ASCII keys. Contract/identity/enum string values use
their declared ASCII vocabularies; booleans and required nulls are JSON values.
Unsigned decimal strings match 0|[1-9][0-9]*, without signs or leading zeroes.
Engine UUIDs use UUID32 and SHA256 digests use lowercase hexadecimal.
Each record digest uses its contract's ASCII bytes, NUL and canonical bytes
excluding its own digest field. Engine request/page/manifest/ACK contracts are
rememberstack.storage_capture_request.v1, rememberstack.storage_capture_page.v1,
rememberstack.storage_capture.v1 and rememberstack.storage_capture_ack.v1.
Reference, terminal-member and resource-observation payload digests use
rememberstack.storage_reference.v1, rememberstack.storage_terminal_member.v1
and rememberstack.storage_resource_observation.v1 respectively. Their canonical
preimage is the complete corresponding closed member record above, retaining
kind and excluding only payload_digest; the domain is not an extra member field.
Pages bind capture ID, ordinal, exact ordered members and page digest.
The manifest binds ordered page digests and explicit component/member counts;
its set digest binds that same ordered exact membership. Duplicates, gaps,
changed order/content or missing context refuse.
The set digest is SHA256 over ASCII rememberstack.storage_capture_set.v1,
NUL and the ordered raw 32-byte page digests. Native/provider witness schemas
and profile identities are independently pinned; an arbitrary untyped map is
not an accepted witness.

The component digests have these exact preimages. Each is SHA256 over its
listed ASCII contract, NUL and the canonical JSON document described below;
the document contains that contract and no digest of itself.

| Digest | Contract and complete canonical document |
| --- | --- |
| classification_digest | rememberstack.storage_classification.v1; contract, source_digest, schema_profile_digest, rules. Each rule has native_class_id and purpose, ordered by native_class_id. Every installed source/writer class appears exactly once; native_class_id is the pinned native profile's opaque class identity. |
| operator_mapping_digest | rememberstack.storage_mapping.v1; contract, classification_digest, store_binding_digest, rules. Each rule has native_class_id, store_binding_id and component (explicit null means excluded), ordered by native_class_id then store_binding_id. The independently accepted complete mapping covers every admitted class/store pair and preserves the exclusions and byte bases above; it has no prices or caller byte amounts. |
| resource_set_digest | rememberstack.storage_resource_set.v1; contract, capture_id, component, resources. Each resource has store_binding_id, resource_identity, version_identity, resource_evidence_digest and measured_bytes; order by the first three fields. Include each qualified stored origin/fork exactly once after proven alias/ownership resolution, with unsigned byte strings; conflicting or mixed ownership refuses qualification. |
| evidence_digest | rememberstack.storage_component_evidence.v1; contract, capture_id, component, native_witness_digest, provider_manifest_digest, classification_digest, operator_mapping_digest, resource_set_digest and measured_bytes. |

Resource evidence points to the exact native allocated-byte record or the
qualified immutable store item/version under its pinned witness schema, never
a new live query. A proven absent component has an explicit empty resource
list. Actual zero-byte origins/forks remain resource members with zero amounts;
they are not dropped merely because their component sums to zero. The
consumer checks that its unique resource amounts sum to that component and
that all four components match the manifest. Profile documents are immutable,
versioned canonical JSON under their declared closed schemas; unknown schemas,
missing rules or changed accepted documents refuse rather than being treated
as arbitrary JSON policy. Their exact canonical bytes, not a display name or
an operator's in-memory interpretation, determine the digests.

ACK contains exactly contract, capture_id, manifest_digest and set_digest.
Persist completion in separately classified operational storage before
delivery. Lost ACK replays identical bytes; it never drops unseen members or
acknowledges a numeric prefix. A different exact set conflicts. Financial
settlement and its native idempotency stay with the operator. No source text,
filenames, prompts, claim contents, object content hashes or credentials are
sent with the content-free receipt.

## 6. Failure, privacy and economics

Capture never repairs a failed historical cut with present bytes. Take a newly
named forward cut; the operator applies its existing missing-data rule. Reads,
hard-forget intent, deletion and ordinary forward fixes do not depend on a
successful capture ACK. Short native locks release on timeout/process loss;
there is no release recovery coordinator or historical rollout settlement.
An unproved release/view lifetime gives no completed receipt.

Collection is O(native roots plus references, entries and accounted resources).
Pages bound memory, not total work. For N encoded items averaging m bytes, one
capture spool is approximately N*m before indexes/WAL/backups; three retained
copies are approximately 3*N*m. No new retention promise follows from this
formula. FSIT artifacts expire 72 hours after the cut under D63; engine/operator
retention and acknowledgement must meet the accepted privacy/financial policy.
Spool, view retention, I/O, pauses, retries and excluded resources have
attributable operator cost and belong in the existing qualified floor.

No new price, customer cap, waiver, minimum or cross-meter cost allocation
solves a failed coverage vector. Report the exact uncovered vector for an
explicit operator decision. The measured fresh-schema catalog is not a
populated maintenance, minimum footprint or margin proof.

## 7. Implementation-facing acceptance

Before advertising capture capability, qualify actual source/schema/adapter
profiles on populated storage: all roots/indexes/TOAST/forks and admitted
maintenance; same-session guard/view loss; clock steps/deadlines; provider
scope/key/link/version/alias mismatches; complete empty and multi-page sets;
staging/failed/card/no-op/forget paths; exact committed-set holes; duplicate/
lost ACK and persistence failure; current authority/expiry; deletion during
extraction; and fixed ordinary LOGIN authority without owner credentials.
Retain two-version/provider-call D56 tests; no content or paid reuse key changes.

Managed deployment requires the matching accepted UMC D55/D102 measurement and
eligibility amendment before implementing/enabling that adapter. Its current
three-credit rate, usable-time quantity, exclusions, held-only/no-debt behavior,
processing economics and 48-hour safety policy survive. UMC must replace the
retired-journal/counter-sum/processing-watermark path atomically; this document
does not authorise a compatibility collector or a parallel financial executor.

## 8. What this decision does not adopt

The broader proposal's ResourceEnvelope/ResourceProfile permissions, all-writer
pre-growth financial authority and storage intent/disposition protocol remain
unchosen. This consumer observes and classifies resources; it does not make a
read-only observation conditional on implementing that separate permission
system. Existing admission/funding invariants are not weakened. If a workload
needs a changed admission rule to satisfy its current-terms floor, that exact
operator change requires its own accepted design.

Rejected shortcuts are provider counter sums, incomplete/live listings,
cloud-side semantic classification, processing watermarks, row-byte guesses,
and counting equal hashes as one stored copy. None proves the same scope,
instant, purpose, membership or supplier exposure.
