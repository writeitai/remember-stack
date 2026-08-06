# Batch E — the saved-query registry

A saved query is something other people's work comes to depend on, so the
shape of this batch follows from one rule: a name means one thing over time.

## Editing adds a version

Identities and versions are separate tables and versions are append-only. An
edit does not change what an earlier caller ran — it adds a version — because
silently redefining a name is the failure this exists to prevent. PostgreSQL
enforces content immutability with a BEFORE UPDATE trigger: `sql`,
`query_hash`, schemas, interpretation, defaults, author, and identity keys
cannot change after insert. Lifecycle fields (`status`, validation report,
approver, assurance, supersession, pinned manifest hash) may transition.

§5's bounds are enforced with `quota_exceeded`: 1,000 identities per
deployment, 50 versions per identity, 64 KiB of SQL, and per-principal draft
limits. The draft-byte ceiling counts encoded SQL **plus draft registry
metadata** (description, interpretation, parameter/result schemas, default
limits, validation report), including the pending write. Concurrent drafts
serialize on the per-deployment `saved_query_registry_state` row (`FOR UPDATE`)
so two writers cannot race past the ceiling.

Registry mutations that take a `query_id` also filter by `deployment_id`. The
connection remains the physical tenancy boundary; the column filter is defense
in depth inside one database, not a second tenant authority. `resolve` joins
versions with `(deployment_id, query_id)`.

## A version runs only against the surface it was checked against

Every version pins the `surface_manifest_hash` it validated against.
`publish_surface_hash` writes the deployment's authoritative hash into
`saved_query_registry_state` and moves every active version to
`pending_revalidation` in the CALLER'S transaction — suspension and publication
are one act.

`revalidate` compare-and-swaps against the **database** current hash: it locks
the registry-state row and conditions the version UPDATE on
`status = pending_revalidation` AND the state hash still equaling the
validator's start hash. A B validation cannot reactivate after C was published
while the version was already pending. Restoration to `active` requires
minor-compatibility AND every fixture passing via real
`QuerySandboxExecutor` execution of stored SQL (no caller-supplied pass flag);
anything else is `broken`. The new validation report is bound to the
authoritative start hash and stored, and the transition is audited.

`SavedQueryRegistry` draft/validate/activate (and resolve's surface check) load
the DB-authoritative hash from `saved_query_registry_state`. The state row is
initialized only when absent; if it exists and differs from the constructor
hash, the call fails closed with `saved_query_revalidation_pending`. Validation
evidence is never relabeled: `validate_saved_sql` fails any EXPLAIN/fixture
result whose `surface_manifest_hash` does not match the authoritative hash.

## Authoring and approving are different acts

An agent drafts; only a deployment operator or explicit policy may activate.
Activation authority is a small injected predicate (`can_activate`); the
registry default-denies. The stored `author_principal` is loaded for
self-approval refusal — callers do not supply an author claim. Activating a
new version deprecates any prior active version of the same identity
atomically. Customer drafts set `assurance = customer_authored`; activation
sets `customer_reviewed` (or keeps `shipped_example`). NULL is not used to
imply platform fact assurance.

## Validation evidence is bound and authoritative

`validate_version` is the only path that writes a validation report. It loads
the stored immutable SQL, runs the four §5 fixtures through
`QuerySandboxExecutor` with bound parameters (plus safe EXPLAIN diagnostics),
and persists a report bound to `query_hash` + `manifest_hash`. Activation
rejects unbound, misbound, or partial reports. Status transitions to `active`
are allowed only from `draft` or `pending_revalidation` — never from
`broken`, `deprecated`, or `disabled` via stale evidence.

## Saving validates contracts before write

`draft` accepts a declared `memory_vN` (only `memory_v1` is supported),
parameter schema, declared result schema, and default limits. A short typed
validator accepts JSON Schema scalar and array-of-scalar shapes and checks
defaults against the authoritative §4.3 hard caps. Those fields are persisted
and returned by `describe_saved_query`.

## Discovery excludes drafts by default; resolve chooses active

`list_saved_queries` returns registry metadata only. With no `status` filter it
lists **active** versions of non-disabled identities. Drafting v2 advances
`latest_version` (newest authored) but does **not** hide an active v1:
`resolve` and default discovery select the active version. Omitting `version`
on `describe_saved_query` prefers active, else the newest authored version.
`describe_query_space(include_examples=True)` surfaces the seventeen shipped
`examples.*` names.

## Every refusal says which refusal it is

`resolve` distinguishes not-found, disabled, awaiting-revalidation and
incompatible. A caller who gets nothing back from a query that was silently not
run cannot tell that from a query that returned no rows.

## Governance audit survives purge

`saved_query_audit` retains non-content evidence for activate, disable, purge,
publish, revalidate, and validate: deployment/query/version IDs, query hash
where applicable, actor, action, old/new hashes, timestamp. Hard delete purges
customer SQL text and cascades version rows; audit rows remain.

## The shipped examples

All seventeen `examples.*` names follow the exact §2 binding mappings
(inclusive two-parameter `claims_as_of` overlap, `claim_occurrences_live` for
`claims_about`, `identity_events_visible` for `identity_as_of`,
`facts_visible_history` time-bucket for `entity_timeline`, evidence join for
`explain`, INNER JOINs only for `multi_hop_context` — no LEFT JOIN orphan
branch). Every body parses through the grammar and executes within caps under
a no-op search port for semantic/lexical SRFs. Checked-in
`EXAMPLE_FIXTURE_PARAMETERS` supply operator-owned positive/empty/tombstone/cap
parameters for each example; focused proofs run all four classes through
`validate_saved_sql` on the empty deployment. Drafting with
`origin=shipped_example` requires the injected activation authority so an
agent cannot self-assert shipped-example assurance.

## Migration head

The registry migration is `p9_06_0027` with `down_revision = p9_05_0026`
(Batch D). It creates identities, versions, registry-state, audit, and the
content-immutability trigger. Migration-head assertions advance to
`p9_06_0027`. Catalog expects 66 public tables and the measured constraint
counts for that head.

## Not built in this slice

- `run_saved_query` as a surface entry point — that belongs with Batch F.
- Batch F adapters, consumption-skill/docs rewrite, benchmark machinery, and
  deprecation telemetry.
- A marketplace, dependency system, automatic installer, saved-query chains,
  dynamic top-level MCP tools, or speculative RPC layers.
- PostgreSQL RLS or a second tenancy/routing authority.
