# Ingest attribution: the typed principal that created a version

**Status:** binding for the narrow attribution slice implemented in
`p9_21_0042`. Decision: **D101**.
**Date:** 2026-08-31.
**Scope:** recording *who* created a document version. Participant
assertions, structured event time, and any assertion-grain forget target
are **out of scope** and remain unbuilt.
**Cloud counterpart (non-binding):** the managed cloud's proposal
`design/proposals/engine-native-asserted-provenance.md` in
`writeitai/ultimate-memory-cloud`, which motivated this slice.

## 1. Problem

The engine records what a document says and where it came from, but not
who put it there. `POST /ingest` has no principal, and
`document_versions` has no attribution column, so "who ingested this?"
has no memory-side answer. Callers that *do* know — a managed control
plane authenticates every upload — have nowhere to put it.

D50 makes content-level authorization and per-user scoping library
non-goals. That is a statement about **authorization**, not about
**attribution**: knowing who ingested a document is a provenance fact,
and recording it changes no read path.

## 2. Decision

### 2.1 A typed principal at version grain

`ingest_principals(principal_id, deployment_id, kind, external_ref,
first_seen_at, last_seen_at)`, unique per `(deployment_id, kind,
external_ref)`; `document_versions.ingested_by_principal_id` references it
through a composite FK on `(deployment_id, principal_id)`.

`kind` is a closed enum — `user | api_credential | service` — because the
three are different referents. A credential is a machine identity that a
person once minted; **reporting it as that person is false attribution**,
so the kind is stored and never inferred away. `external_ref` is opaque to
the engine: it is the caller's stable id, and the engine attaches no
meaning to its shape beyond requiring **printable ASCII** — the header
transport cannot carry anything else, so the constraint turns an encoding
crash into an explicit 422. Callers use opaque ids, so it costs nothing
real.

### 2.2 Attribution is creation-scoped and immutable

Only a **newly created** version records a principal. Under D55, bytes
identical to the lineage's latest version are a content-hash no-op that
returns the existing version with `created=False`; a later submitter of
the same bytes therefore changes nothing here. That attempt belongs to the
caller's own audit trail, not to memory. There is deliberately no
co-uploader list and no re-attribution path.

### 2.3 Trusted perimeters only

The composing profile surfaces this as `trusted_principal_source`
(env **`REMEMBERSTACK_SELFHOST_TRUSTED_PRINCIPAL_SOURCE`** — the self-host
settings class carries the `REMEMBERSTACK_SELFHOST_` prefix), default off.
Enable it **only** where the deployment's network perimeter has already
authenticated the actor it names. When the API auth perimeter is configured,
the request must additionally carry full `write` scope before these headers
are believed; a narrow browser `ingest` credential can add a document but
cannot name its immutable principal. Without API auth, enabling the setting
trusts the network perimeter by itself. On an ordinarily reachable deployment
it makes attribution meaningless rather than merely permissive.

Attribution is honoured only when the composing profile declares
`trusted_principal_source=True` and, when present, the API credential carries
`write` scope. A narrow credential identifies its own caller but has no
authority to assert an arbitrary principal. Untrusted attribution is **ignored,
not rejected**: nothing forged is recorded either way, so refusing buys no
safety while adding a failure mode where a merely misconfigured deployment
rejects real documents.
**Metadata must never break ingest.** Default is off, so the self-host
posture is unchanged.

This bounds the trust rather than eliminating it: a full-write caller within a
trusted perimeter is still asserting the principal. Narrow credentials never
turn their own subject into attribution implicitly; doing so would conflate
the credential holder with the immutable creator claim.

### 2.4 Transport: headers, never the query string

The pair travels as `X-Ingest-Principal-Kind` / `X-Ingest-Principal-Ref`.
`external_ref` is erasable PII, and a URL is copied verbatim into access
logs, reverse proxies and traces — copies a later principal deletion
cannot reach. Headers are not logged by the access-log line.

### 2.5 Deletion semantics

The FK is `ON DELETE SET NULL`: deleting a principal nulls attribution and
**never** destroys the document version. `version_principal()` returns
`None` for an unattributed version and for an erased principal — the same
honest answer.

*Known gap, stated rather than implied:* this is row deletion, **not** a
D74-grade forget. There is no portable manifest, barrier, residual
verification or restore replay for principals. A person-grain hard-forget
target is required before this is offered as an erasure guarantee, and is
the next slice.

### 2.6 Compatibility

- `IngestedVersion` is unchanged. The internal principal UUID is not the
  answer to "who uploaded this", and on the no-op path it could not be
  reported truthfully, so it is not in the receipt. An older client with
  `extra="forbid"` still parses responses.
- The `ingested_by` keyword is omitted entirely when no attribution is
  present, so an existing structural `IngestPort` implementing the old
  signature keeps working.
- The parameters are optional; a caller that sends nothing behaves exactly
  as before.

### 2.7 Migration

**One transaction, and no index.** An earlier revision built a partial
index on `ingested_by_principal_id` `CONCURRENTLY` in an autocommit block
to spare a large table a write lock. Alembic commits before entering that
block, so an interruption stranded committed objects with the revision
unstamped and the rerun died on `type … already exists`; the advertised
fallback (pre-build it by hand) was impossible, because the column does
not exist until this migration runs.

The resolution is that **the index is not needed**. Nothing reads by
principal: `version_principal()` anchors on the version primary key and
joins principals by theirs. The index would be pure write cost for a query
this slice does not have. The bounded "documents by principal" operation
is a later slice and can add it concurrently in its own revision, once the
column exists.

What remains — enum, table, nullable column, FK — is small and atomic: it
commits whole or rolls back whole.

The FK is added **`NOT VALID`**. A validated `ADD CONSTRAINT` sequentially
scans `document_versions` while holding `ShareRowExclusiveLock`, so the
write outage grows with the table (measured: a concurrent `UPDATE` timed
out). `NOT VALID` is **scan-free** (`seq_scan=0`), removing that
table-size dependence.

Be precise about what it is not: `NOT VALID` is **not lock-free**.
PostgreSQL 19 still takes `ShareRowExclusiveLock` for the DDL, held until
this atomic transaction commits, and a concurrent writer waits on it. The
property gained is *bounded and brief* rather than *proportional to row
count*. Nothing is skipped by it either:
it forgoes checking only *pre-existing* rows, and the column is added by
this same migration, so every existing row is `NULL` and cannot violate
anything. The constraint is fully enforced for every subsequent insert and
update either way. An operator who wants the planner's `convalidated` flag
can run `VALIDATE CONSTRAINT` online at any time — it takes only
`ShareUpdateExclusiveLock` and does not block writers. No separate
lookup index on `ingest_principals` either: the UNIQUE constraint provides
one, and a duplicate would be a second physical copy of PII.

`downgrade()` drops attribution and therefore **destroys** it; it is a
schema rollback, not a data-preserving one.

## 3. Alternatives rejected

| Alternative | Why not |
| --- | --- |
| No principal (status quo) | Leaves the flagship provenance question unanswerable |
| Opaque string, no kind | Cannot distinguish a person from a credential, so downstream surfaces would either guess or over-claim |
| Infer `user` from a credential's owner | False attribution — the failure this design exists to prevent |
| Principal in the query string | Copies erasable PII into logs/proxies/traces |
| Accept attribution from any caller | The bearer authenticates a deployment, not a caller; the claim would be forgeable |
| **Refuse** untrusted attribution (403) | Buys no safety over ignoring it — nothing forged is recorded either way — and lets a misconfigured deployment reject real documents |
| Add the principal id to `IngestedVersion` | Breaks `extra="forbid"` clients and cannot be truthful on the D55 no-op |
| Full assertion/participant model now | Much larger; needs a resolver contract and an assertion-grain forget target first |

## 4. Consequences

- One new closed enum, one table, one nullable column, one optional
  header pair, one composition flag.
- No read path, authorization behaviour, retrieval result, or extraction
  output changes.
- Erasure is **weaker than D74** until the follow-up slice lands (§2.5).
- Attribution within a trusted perimeter is caller-asserted (§2.3).
- Operators must apply `p9_21_0042` and start the new runtime **before**
  any client forwards attribution: an older engine accepts the upload,
  creates an unattributed version, and D55 then prevents a retry from
  repairing it.

## 5. Verification

DB-free surface proofs: header pairing (both-or-neither), closed-vocabulary
rejection, credential-is-not-a-user, untrusted attribution ignored while the
document is still ingested, non-ASCII reference rejected as a clear 422
rather than crashing, reference absent from the request URL, legacy port
compatibility, receipt shape unchanged.

PostgreSQL catalog proofs: record and read back; **no re-attribution on the
D55 no-op**; deleting a principal keeps the document version.

Outstanding test debt, recorded rather than hidden: HTTP→E0→catalog
end-to-end, the observed-lineage worker path, concurrent same-lineage
different-actor writes, populated upgrade plus downgrade/re-upgrade, and a
log/metric redaction regression.
