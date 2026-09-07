# Stored originals and conversion without a route

**Status:** non-binding analysis supporting D117 and `plan/designs/e0_files_design.md`
§3 and §6. **Evidence inspected:** 2026-09-07, engine main `8fad369d` and the
unpublished parking implementation inherited from `c49b9985`.

## Problem and corrected evidence

An accepted file may have no configured converter. Previously E0 wrote its bytes
and version, then `ConvertHandler.handle` marked the version failed and threw a
nonretryable error. The caller's upload succeeded but processing entered the DLQ.
Reuploading identical bytes does not request reprocessing; operator replay can
reopen a dead-letter row after configuration is fixed.

The first attempted fix refused these uploads with HTTP 415. That conflated
storage with processing: an agent can use an original even without extracted
text. The user explicitly chose durable storage and `no_route` parking.

The subsequent assertion that raw-only versions already appeared on mounts was
wrong. `spine/projection.py::_SELECT_CORPUS_DOCUMENTS` joined through
`documents.current_version_id`; `document_catalog.py::record_section_tree`
advances that pointer only after structure succeeds. A LEFT JOIN on representations
and the `(not converted)` fallback in `workers/p3.py::_document_stub` do not
make a never-current version reachable. Independently tracing the currency write
and projection read confirmed the gap.

There is a second gap: `SelfHostProfile.publish_mounts` supplies neither raw nor
artifact roots. `LocalMountPublisher._view` creates empty directories when roots
are absent. A stub is discoverable metadata, not proof of a usable raw mount.

## Alternatives and chosen behavior

| Alternative | Assessment |
| --- | --- |
| Reject at ingest | Prevents storage for agent use; rejects the requested product behavior. |
| Keep dead-lettering | Turns a known configuration limitation into operational failures. |
| Store without a work row | Needs a second backlog discovery mechanism; user chose explicit parked work. |
| Park by far-future timestamp | No actual resume time exists; scheduling updates could release it accidentally. |
| Promote raw versions to current | Changes processed currency and risks replacing working evidence with incomplete content. |
| New raw-only projection and mount tree | Duplicates snapshot publication, navigation and forget cleanup. Adoption trigger: raw history needs a separate browse contract beyond latest surviving original. |
| **Park conversion; extend existing P3 with separate stored identity** | Preserves storage, keeps work resumable and exposes originals without changing processed currency. |

The stored selection is the newest durable, nondeleted version by `version_no`.
Processed fields remain tied to current currency. A ready v1 plus unsupported v2
must display v1's summary/artifacts separately from v2's original. A lineage with
no processed version receives a normal stable `documents/<doc_id>/` stub and an
explicit absence of processed content. Deleting v2 falls back to the newest
surviving stored version. Tombstoned lineages disappear.

Managed measurement creates `content_objects` before writing the raw object.
`managed_ingest_measurements` rows with disposition `new_version` require accepted
admission before P3 can advertise that version's raw pointer. Admission rejection
must not be mistaken for durable raw availability. This change does not broaden
the managed text classifier or cloud billing contracts.

## Scheduling and recovery

E0 is the shared ingress boundary. It supplies the route set to the transactional
catalog, which compares the **stored content object's MIME**, not just the latest
upload declaration: content hashes are deduplicated and their MIME is first-write-
wins. Unsupported work starts pending with `defer_reason=no_route`. Claims exclude
that reason independently of time. No processing attempt or conversion usage is
charged for merely waiting; raw storage is still retained.

Explicit `remember ops resume-no-route --deployment …` consults the actual configured
route set and releases only matching live versions. It cannot assume all parked
MIMEs became supported because one route was added. Worker configuration can still
differ during a rolling restart, so missing routing at dispatch must park again
without declaring failure or consuming the attempt allowance. Genuine converter
errors still follow ordinary failure/retry rules.

## Connector-cycle completeness

Review traced `lifecycle.py::_SELECT_READY_CYCLES` and
`workers/reconcile.py::SyncCycleFinalizer`: source absence can close unsupported
facts only after observed processing has completed. A live `no_route` observation
is incomplete evidence, so it retains the existing conservative cycle barrier
(`completed_at` set, `finalized_at` null). Treating parking as success or lossy
finalization would either infer absence from unread content or need a second
recovery protocol when the converter arrives. Explicitly deleting that version
or lineage makes its parked row irrelevant to the barrier. Other work-state
barriers remain unchanged. Resuming and finishing the pipeline allows ordinary
finalization; source-tombstone cascades continue independently. The cost is that
one retained unsupported observation can delay absence-based closure for its
whole sync cycle indefinitely. This is deliberate preservation of evidence,
not a promise that unsupported content has been processed.

## Mount publication, security, cost and failure

P3 already rebuilds explicitly (`SelfHostProfile.run_projection`), independently
of completion of conversion. Availability begins after a successful P3 build and
mount publication; ingest does not promise synchronous filesystem visibility.
Use configured existing raw/artifact provider mount roots so emitted keys lead
to bytes. Read-only enforcement remains operator-owned. D116 withdraws the off-path
restriction; backend auditing follows D51/D116, and plain local filesystem reads
are not recorded. This implementation wires raw pointers and existing mounts;
D116 direct browse entries and D115 source_open remain separate implementation work. This PR does not provision SeaweedFS, alter production topology,
or assert that placeholder directories are real bucket mounts.

The projection adds indexed per-lineage latest-version lookups and conversion-state
reads, but no LLM calls, duplicate raw copies, new store or per-upload rebuild.
Parked ledger rows persist for retained unsupported versions; this is intentional
backlog, subject to existing deletion/forget, not a timed retry loop. Snapshot
failure retains the previous published tree. Existing tombstones, D74 serving
barriers and snapshot purge inventory apply because all new navigation stays in
P3. Previously downloaded raw data cannot be revoked by rebuilding a projection.

## Verification contract and sources

Prove first raw-only upload, ready v1 plus parked v2, deletion fallback, lineage
removal, managed-unaccepted exclusion, deployment isolation, and exact bytes read
from a configured raw root using the published stub. Prove selective resume,
no-route worker configuration mismatch, and stored-MIME deduplication against a
real database. Run migrations and the broader worker/spine suites serially on an
isolated database, because these fixtures reset schema.

Local sources: `spine/document_catalog.py::record_upload, record_section_tree`;
`spine/managed_metering.py` admission dispatch; `spine/projection.py` corpus export;
`spine/lifecycle.py` deletion; `spine/forget.py` tombstones and snapshot cleanup;
`spine/work_ledger.py` claim/resume; `workers/e0.py`, `workers/base.py`; `workers/p3.py`;
`adapters/selfhost/mounts.py`; `profiles/selfhost.py::run_projection, publish_mounts`;
`plan/designs/e0_files_design.md` §2–§6, D40/D51/D55/D65/D74.
