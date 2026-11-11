# Engine storage mutation and capture contract

**Status: non-binding proposal; no implementation permission.**
[Analysis, object writers and source boundary](../analysis/storage_classification_and_admission.md).
[Observed schema/fork inventory](../analysis/storage_inventory/README.md).
**Adoption trigger:** agree the full contracts below, qualify populated
PostgreSQL/adapter measurement and provider continuity, and reconcile the
binding engine designs listed in §8. A managed operator must separately accept
the D55/D102 billing amendment and prove its current-terms cost floor. This
proposal chooses no price, waiver, minimum charge or new customer limit.

## 1. Decision candidate and authority boundary

Use one engine-owned storage mutation record and immutable disposition stream,
independent of processed-input measurements. Obtain finite resource permission
before growth. At a cut, capture the exact classified references, qualified
physical relation allocations and disposition set while short mutations are
closed. An adapter reports substrate evidence; the engine determines E/K/P
meaning and completeness. A processing success or highest sequence is not a
storage receipt.

One deployment is one trust domain. All semantic records, replay, admission
checks and capture validation stay in the complete OSS engine. The operator's
wallet/rate and permission to spend are external resource policy. A narrow
`StorageResourcePort` returns permission for a described resource envelope and
acknowledges exact content-free evidence. It cannot author a document, classify
truth, replace local native constraints or claim a commit occurred. Self-host
uses local operator-provisioned resource authority; the reference adapter may
bind the same envelope to prepaid funds. Neither requires a web UI, shared
control plane or proprietary correctness implementation.

This is a substrate permission/capture extension under D60/D61, not an
abstraction over PostgreSQL, E0, E/K/P or paid reuse. Both maintained adapter
profiles must implement the same contract. An adapter without the required
capabilities cannot claim storage admission/capture support; no old collector
or unguarded path is kept as a fallback. Local/reference implementation and
qualification are adoption dependencies, not code shipped by this document.

## 2. One intent before resource growth

The engine durably assigns `storage_mutation_id` and an immutable intent digest
for an operation. Replays locate that same record through the engine's existing
operation/claim identity; a new HTTP retry is not permission to allocate again.
Same identity/different intent conflicts. The out-of-band ID never enters
source bytes, connector `(source_kind, source_ref)`, dates, titles, chunk/header
inputs, content hashes, Selection/Claimify keys or embedding policy/model keys.
No D56 reuse boundary or generation changes.

The typed intent contains an engine-assigned `authority_request_id`, deployment
identity, operation kind, exact logical owner/reference identities, source/schema/classification/adapter profile,
parent authority when applicable, and a conservative checked-integer resource
envelope. The envelope covers retained allocation, staging/attempt copies and
metadata/cleanup growth, with explicit validity and finite attempt/operation
bounds supplied by the qualified existing admission profile. A byte bound is
not a bound on file count, multipart churn, database page/index growth or
cleanup time. If any required bound/enforcement is unknown, refuse new growth;
do not substitute logical input length or invent a customer limit.

The operator port authorises that exact intent/envelope before the engine
starts the first costly write or issues an upload capability. The engine
appends the returned immutable authority binding before executing. The request
ID exists before admission; the provider-returned authority ID is not an input
to its own pre-admission intent digest. Permission
proves resource availability, not an E/K/P mutation. Authority cannot be spent
by another intent, reused after exhaustion or silently enlarged. A request
requiring more creates a new explicitly bounded child intent/authority request
for the remaining unstarted writes before that growth; it does not mutate the
original intent or an old financial hold. Its parent link is resource metadata,
never a new document/content/reuse identity. Metadata used to make this record,
its indexes, audit and capture artifacts are separately provisioned operator
resources with their own measured bound, not an infinite free bootstrap path.

Intent, authority binding, dispositions and exact ACK progress live in one
engine-owned operational schema in the existing PostgreSQL database, separate
from the measured customer relation set and included in the operator cost
profile. No external service stores memory truth. Where canonical references
change in PostgreSQL, the native transaction appends the corresponding
disposition evidence with that change. Object/Git writes are not a distributed
transaction: the intent stays outstanding until the actual provider effect
and the committed native reference agree. Capture artifacts use the same
separately classified bounded operational spool.

Each short growth action checks the current authority and the existing
claim/forget/native guards, then records its exact effect under the intent.
Do not hold a database/capture lock while waiting for model work or remote
permission. Existing worker replay and the ordinary work row remain the retry
mechanism; there is no new queue, daemon, release coordinator or rollback path.
A lost answer leaves the action uncertain until the existing adapter's exact
identity can prove its effect. Uncertainty never becomes a successful no-op.

All writers participate: raw ingest, direct-upload issuance/registration,
cards, metadata/reference edits, conversion/structure outputs, E1/E2/E3 and
search writes, K publication/history, P3 builds/publication, retries/backfills,
maintenance/rebuild copies, and privacy-control writes. Ingestion still passes
through E0, and truth edits still use their existing append/review semantics.
A read that records audit/usage metadata uses its pre-provisioned operator
metadata envelope; enabling the contract must not make ordinary reads depend
on an unrelated customer storage top-up.

## 3. Direct uploads, reuse and safety writes

A direct/multipart capability is issued only after authority is recorded and
binds one staging identity, destination, maximum admitted resources and finite
validity. Already-issued URLs can keep producing resources after an engine
request ends: closing the engine cut does not revoke them. Provider enforcement
must bound the actual outstanding capabilities/parts/replacements under that
authority. The deployed `ingest_stored` path starts after upload and therefore
does not itself satisfy this pre-upload condition.

Registration serialises with discard for that key and proves one of: the
exact object became the retained raw reference; existing canonical data stayed
unchanged and this extra object was deleted; or staging remains unresolved.
Identical content at two physical keys is not one resource until deletion or
a qualified alias witness proves it. A refusal that preserves an already
registered key is not permission to delete it. Provider “not found” without a
complete qualified namespace/resource witness cannot release unknown parts.

Card conversion, unchanged input and reused outputs follow this same storage
path even when processing emits no new billable receipt. Reusing existing
canonical references may prove zero canonical growth, while a new metadata
observation, attempted duplicate upload or retained checkpoint still has a
resource effect. Storage retry bookkeeping must not force model re-execution.
A failed processing outcome releases only its processing obligation; canonical
or staged storage retains independent evidence and authority.

D74 forget intent, erasure, sanitized rebuild and restore re-honor remain
correctness requirements when a customer is parked. They use an independently
provisioned, bounded operator safety envelope; customer wallet refusal cannot
suppress or roll back accepted privacy intent. Before admission is supported,
the adapter/profile must prove that envelope covers the existing permitted
safety workload and its failures, or refuse ordinary growth whose eventual
safe erasure it cannot support. This is a technical acceptance condition, not
a new free entitlement or a claim that current supplier costs are covered.
The manifest root remains separately durable from the data restore set.

## 4. Immutable storage dispositions, not one success bit

Each intent has an append-only sequence of disposition revisions. Each record
binds the mutation, revision, previous record digest, exact effect digest and
resource authority. A revision has two separate dimensions:

| Dimension | Closed vocabulary and meaning |
| --- | --- |
| Canonical effect | `not_committed`, `unchanged`, or `committed`. A committed effect names the exact native/catalog reference transaction; unchanged proves the retained reference set did not grow. Neither is inferred from processing status. |
| Resource disposition | `outstanding`, `cleanup_pending`, or `resolved`. It identifies retained canonical resources and all still-possible staged/failed/rebuild resources. Resolved means every effect is accounted for; retained canonical data can remain present and funded. It does not mean zero bytes. |

The immutable intent record is included even before the first disposition;
therefore a crash immediately after admission cannot disappear from a cut.
A canonical commit can coexist with `cleanup_pending`. A refusal can leave
`not_committed/outstanding`. Cleanup appends a new observation and changes no
old record. A committed truth mutation cannot later be recast as never having
happened; supersession/deletion is another ordinary engine mutation. Native
transaction/reference evidence and the adapter's exact object/version witness
must agree before `committed` is recorded. A per-attempt lease or a task ACK is
not that proof.

Object/reference records bind exact store identity and opaque object version,
logical size, role, owner and lifecycle. A shared canonical object is referenced
once by its actual stored identity, with all logical references retained.
Distinct copies stay distinct even if bytes hash equally. A provider hard-link
alias can collapse one stored origin only after the qualified adapter proves
ownership and alias semantics; it does not merge resource-accounting counters.
Unknown size/kind/version/alias is unresolved evidence, never zero.

Permission expiry stops new writes. It does not erase existing bytes, release
an unresolved resource liability or turn retained staging into operator-free
space. The operator adapter must keep the applicable resource obligation until
exact disposition and funding transfer/cleanup are acknowledged. Unknown
outcomes and provider cleanup lag remain explicit exposure. The full bounds
and money transfer are operator-owned acceptance work; they are not supplied
by processing-sequence order or by this engine record.

## 5. One short consistent cut

A requested cut names deployment, store set, instant T and qualified clock
error, schema/classification/adapter versions and a derived capture identity.
The same identity/different inputs conflicts. Begin early enough that the
engine's relevant short mutations are drained before the earliest possible T.
Do not wait for a large upload or a model job to finish: admitted in-flight
resources already have their own class and obligation.

The engine holds its native mutation/schema/maintenance boundary continuously
through the latest possible T and through the matching provider witness. It
freezes canonical/reference/disposition transactions and qualified physical
allocation, including attached indexes and TOAST, without calling a remote
writer that needs those same locks. One original read-only native
guard transaction takes the qualified SHARE/schema/maintenance locks. Its
lock/statement/transaction deadlines fit inside the provider's remaining
boundary budget; an expired command cannot acquire a late guard or reconnect
under the old identity. A separate original repeatable-read/read-only engine
catalog transaction establishes its view while that guard holds. It is the
only engine extraction reader, not a later reader pool or snapshot-handle
handoff. Release on timeout, process loss, changed schema or unknown writer
gives no completed engine capture.
Migration/partition creation and any extension/background writer must be
included in the qualified boundary. An advisory convention or MVCC snapshot
alone does not prove this physical-allocation guarantee.

The candidate physical algorithm keeps the existing *allocated* relation
quantity: enumerate actual qualified roots/descendants; attach indexes through
`pg_index`, TOAST through `reltoastrelid`; count each physical fork/file once;
check the equivalent per-table `pg_total_relation_size` sum. Partitioned parents
and partitioned indexes have no payload of their own. Resolve descendants by
catalog ancestry and approved bounds/templates, never a prefix. Sequences,
views, system/catalog/WAL and extension side resources are explicitly separate.
Do not subtract dead tuples/free pages or call `pg_column_size` a replacement
quantity. Each purpose/mixed relation still needs the operator's explicit
D55-compatible mapping. All admitted access methods, fork/segment layouts,
reindex/vacuum and extension behavior need populated qualification.

Sample physical allocation while the short guard holds across T, and durably
spool its bounded relation records and continuity evidence outside the measured
footprint. Release that guard after the bracket and definite provider release;
do not hold it while enumerating millions of logical references. The already
established engine view supplies the exact reference/intent/disposition set
through pages after release. It has a finite native lifetime and measured
memory/I/O/snapshot-retention envelope; view loss or materialisation timeout
means no completed historic receipt. The two different transaction roles are
necessary: committing the SHARE guard must not destroy the extraction view,
and the long reader must not retain that short write-blocking lock. This
captures allocation at T; it does not reconstruct old physical sizes later.

For the proposed fsit adapter, [fsit #565 at c50b4cd9](https://github.com/writeitai/fsit/blob/c50b4cd9/design/designs/scoped-storage-capture.md)
is an **unaccepted interface dependency**. Its fixed native batch closes the
metadata gate, drains/marks it and holds one original read-only owning-role
`filemeta` SHARE guard across T, then keeps only its two existing extraction
views for bounded materialisation. Engine processing, money and consumer ACK
never extend that provider fence. The engine holds its own boundary over the
same clock bracket; the provider command never waits on an engine callback.
One provider request/witness is matched to one engine capture, not to a later
present-time filesystem list.

The provider request digest binds protocol, organisation, cell, sorted exact
four filesystem UUIDs and integer UTC microseconds. Its complete pages include
roots/directories and opaque path/version/alias identities, entry kind, logical
size and storage-format facts. The engine derives expected path IDs from its
known keys under that same capture/filesystem scope and matches both ways.
Every returned entry is either a matched classified resource, declared adapter
metadata or explicit unresolved/operator resource; no unknown entry is omitted.
Only aliases emitted/owned by an admitted adapter may be canonical. Original
stored metadata/link bytes are bound by the provider version digest; a
successful decoder must be qualified on the actual filer image, not an older
protobuf re-encoding that drops fields.

Provider chunk/deletion/reservation and multipart dimensions have their **own
observation bracket** and can overlap. They are not summed into the logical
inventory or falsely labelled exact physical resources at T. Missing pages,
wrong scope, version/kind/alias mismatch, unknown call, lost guard continuity,
clock step, expired lease or incomplete local evidence produces no receipt.
Provider capture completion alone is insufficient for engine completion.

## 6. Exact captured set and acknowledgement

The engine materialises the complete classified reference/catalog view and
the following exact intent/disposition set visible in that same view: every
intent with outstanding/cleanup resources, every intent without a disposition,
each immutable authority binding of those intents, and each disposition
revision not acknowledged in that view. Every unacknowledged binding is also
included even if its intent's resources have resolved. Already
acknowledged resolved revisions need not be emitted again; retained canonical
resources still appear in the complete reference catalog. Each included
revision binds its predecessor, so missing history cannot be repaired by
assuming a numeric prefix. A post-T ACK or new revision cannot retroactively
change this manifest's membership. Stable pages use contiguous ordinals, explicit counts and
item/page/set digests. Each disposition member is
`(storage_mutation_id, disposition_revision, payload_digest)`; intents and
authority bindings have their own explicit member kinds/identities/digests.
An intent without an authority or disposition remains an explicit intent
member, not a zero-size omission. Scope/source/schema and
classification versions, native clock/continuity evidence, physical component
records and the provider manifest digest are bound by the final manifest.
Explicit empty sets are distinct from absent sets.

The proposed envelopes have these required fields, with no optional unknown
fields or processing-contract reuse:

| Envelope | Required fields |
| --- | --- |
| Intent | `contract`, `deployment_id`, `storage_mutation_id`, `operation_kind`, `opaque_owner_ids`, `source_profile`, `schema_profile`, `classification_profile`, `adapter_profile`, `authority_request_id`, `resource_envelope`, `intent_digest` |
| Authority binding | `contract`, `deployment_id`, `storage_mutation_id`, `authority_request_id`, `intent_digest`, `authority_id`, `resource_envelope_digest`, `permission_digest`, `authority_digest` |
| Disposition | `contract`, `deployment_id`, `storage_mutation_id`, `revision`, `previous_digest`, `intent_digest`, `canonical_effect`, `canonical_evidence_digest`, `resource_disposition`, `resource_evidence_digest`, `authority_id`, `payload_digest` |
| Page | `contract`, `capture_id`, `page_ordinal`, `members`, `page_digest` |
| Manifest | `contract`, `capture_id`, `deployment_id`, `cut_at_unix_us`, `clock_error_us`, `profile_digests`, `native_witness_digest`, `provider_manifest_digest`, `component_records`, `intent_count`, `authority_count`, `disposition_count`, `page_digests`, `set_digest`, `manifest_digest` |
| ACK | `contract`, `capture_id`, `manifest_digest`, `set_digest` |

Contracts respectively use `rememberstack.storage_intent.v1`,
`rememberstack.storage_authority.v1`, `rememberstack.storage_disposition.v1`, `rememberstack.storage_page.v1`,
`rememberstack.storage_capture.v1` and `rememberstack.storage_ack.v1`.
Operation kinds are `ingest`, `reference_change`, `metadata_change`,
`process_stage`, `knowledge_publish`, `projection_build`, `maintenance`,
`forget`, `delete` and `adapter_metadata`; profile-specific detail is in the
immutable intent. Revisions start at one and advance under the intent's native
row lock; revision one has null `previous_digest`. There is no global sequence
whose allocation could stand in for commit.

Canonical encoding uses UTF-8, sorted fixed ASCII field names, UUID32 lowercase
hex, checked unsigned 64-bit integer decimal strings, explicit null where
specified, and no insignificant whitespace. Digest fields use lowercase
SHA-256 hex. Each record's digest is SHA-256 of its exact contract ASCII bytes,
NUL, and canonical envelope bytes excluding that envelope's own digest field.
The set digest uses ASCII `rememberstack.storage_set.v1`, NUL and the ordered
raw 32-byte page digests. Pages are ordered by member kind, mutation UUID raw
bytes and revision (zero only for non-revisioned intent/authority members),
then authority request UUID where relevant. Member kinds are the literal ASCII
strings `authority`, `disposition` and `intent`. A
page digest binds its capture ID and ordinal in its envelope. Counts, duplicate
membership, contiguous page/revision coverage and every predecessor digest
must validate relative to the exact prior ACK/predecessor records in the
captured view; a digest is not completeness by itself. The typed payloads of
`resource_envelope`, native evidence and each adapter profile must be accepted
with their qualification vectors before binding adoption; no untyped mapping
is executable permission. This is not a protocol a running adapter advertises.

The collector persists completed evidence outside the measured footprint
after the bounded views finish; the short guard has already released. It
delivers content-free scope, quantities, evidence
identities and exact membership, with no source bytes/text, filenames/paths,
prompts, claims, content hashes or credentials. Digests authenticate neither
caller nor provider; the existing scoped workload/adapter authority does that.
The operator ACK names the exact manifest/set digest and identities. Lost ACK
replays those immutable bytes; changed membership conflicts. Only that exact
acknowledged set is eligible for the operator's storage allocation/settlement.
No highest processing sequence, row count, task completion or “all older than”
shortcut substitutes for set completeness.

Do not retain the capture boundary while waiting for delivery or money. Failed
historic cuts remain missing; forward-only recovery takes a newly named cut
and applies the operator's accepted missing-data policy. It never relabels
current bytes as a missed past day. A native profile/source change invalidates
its qualification until the new profile is accepted; it is not a release
recovery framework.

## 7. Consequences, alternatives and economic limits

The benefit is one resource truth path for every operation, including no-op,
card, retry and privacy work, while paid reuse remains unchanged. The cost is
an engine-owned intent/disposition/reference catalog, typed adapter work,
short bounded write pauses, complete evidence materialisation and tests.
Pages avoid loading millions of records into RAM; they do not make enumeration,
metadata/WAL/backups or temporary exposure free. Capture concurrency/lifetime
and current admissible workload require a measured profile. The fresh-schema
allocation in the analysis is not a guaranteed customer quantity.

Rejected alternatives: processing-sequence order misses uncommitted holes and
card/no-op writes; a four-counter sum mixes storage purposes and staging; live
row estimates change the physical quantity; content-hash deduplication hides
real copies; a cloud-side classifier would own memory truth; a separate global
recovery service adds no measurement proof. A new customer quota, price or
waiver is not chosen to make these gaps disappear.

The managed current-terms floor must independently cover PostgreSQL/objects,
permitted in-flight/churn/failed/rebuild/forget resources, provider deletion
tails, backups/traffic/rounding, instrumentation/capture and periods without
customer-usable coverage. Resource fields alone do not bound all of those.
If the existing admissible profile cannot prove coverage and enforcement,
new growth stays refused and the exact failing vector goes to a separate
reviewed operator decision. No automatic minimum charge, cross-meter allocation
or free-storage treatment follows from this proposal.

## 8. Binding adoption and validation

Adoption must reconcile `e0_files_design.md`, `postgres_schema_design.md`,
`postgres_p1_search_projection_design.md`, `orchestration_design.md`,
`hard_forget_design.md`, the K/packaging provider contracts and their decision
entries, rather than append a contradictory standalone rule. In particular
replace the old managed two-hold/processing-sequence coupling with the accepted
operator amendment; leave processing success/failure economics explicit.
D60/D61, D74 and D56 survive. Public docs continue to describe only shipped
behavior; this proposal adds no advertised route or command.

Required complete vectors: every writer and adapter; card/no-op/reference edit;
pre-upload and late/replaced multipart parts; immutable same-ID conflicts;
crash before/after object and native commits; losing/duplicate writes; exact
set holes/reordered ACK; canonical commit with cleanup pending; unknown alias,
entry, index, TOAST, extension or schema; populated bloat/all forks/reindex/
vacuum/concurrent partition DDL; independent provider resource brackets;
missed/stepped clock, process/guard loss, timeout and restart; mixed paid/
safety resources and restore re-honor; and source/adapter changes. Tests must
exercise millions-of-document inventory through pages, not a static tiny
catalog assumption.

Ingestion implementation retains the two dated versions/provider-call tests
in `test_reuse_lifecycle.py` and `test_d140_text_origin_pg.py`, with stable
lineage/names/dates, untouched sections on both sides of a multi-chunk edit,
packing and D122-window cases. Known reuse gaps remain named expected failures,
never claimed fixed. The local migration-only schema experiment is the sole
runtime evidence added here; no mutation/capture/funding/adapter acceptance
vector above has been run by this proposal.
