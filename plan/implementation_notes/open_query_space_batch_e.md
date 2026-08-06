# Batch E — the saved-query registry

A saved query is something other people's work comes to depend on, so the
shape of this batch follows from one rule: a name means one thing over time.

## Editing adds a version

Identities and versions are separate tables and versions are append-only. An
edit does not change what an earlier caller ran — it adds a version — because
silently redefining a name is the failure this exists to prevent. §5's bounds
are enforced with `quota_exceeded`: 1,000 identities per deployment, 50
versions per identity, 64 KiB of SQL, and per-principal draft limits, because
drafts are cheap to make in a loop and an agent will. The draft-byte ceiling
counts the SQL about to be written, not only what is already stored.

Registry mutations that take a `query_id` also filter by `deployment_id`. The
connection remains the physical tenancy boundary; the column filter is defense
in depth inside one database, not a second tenant authority.

## A version runs only against the surface it was checked against

Every version pins the `surface_manifest_hash` it validated against.
`publish_surface_hash` moves every active version to `pending_revalidation` in
the CALLER'S transaction, so the suspension and the publication of a new hash
are one act: there is no instant at which a version is executable while
claiming a validation nobody performed. That state is deliberately
non-executable.

`revalidate` compare-and-swaps on the hash. A validator reads the hash when it
starts and offers it back; if the surface moved again meanwhile, its result
describes a surface nobody is running and cannot activate anything. Without
that, a slow validator could quietly re-activate a version against a surface it
never saw. Restoration to `active` requires minor-compatibility AND every
fixture passing; anything else is `broken`, which is a state someone has to
look at rather than one a version drifts out of.

## Authoring and approving are different acts

An agent drafts; an operator activates; the approver is recorded and may not be
the author. A registry where the author can approve their own work has
approvals that attest to nothing.

Activation also requires a validation report in which every §5 fixture —
positive, empty, tombstone, cap — ran and passed. "It validated" is not
something a later reader can check, so the report names each fixture, and a
validator that ran three of them does not get to call that a pass.

## The saving validator executes the four fixtures

`validate_saved_sql` is the saving validator §5 requires. It takes
operator-owned fixtures (bound parameters, and an optional row cap for the cap
case) and runs each class through the existing `QuerySandboxExecutor` —
`explain_sql` once for diagnostics, then `query_sql` for every fixture.
Parameters stay bound; nothing is rendered into the SQL text. The judgments are
small and named:

- **positive** — completed without error
- **empty** / **tombstone** — completed with no rows (the operator supplies
  parameters that must not invent content, or must not surface deleted content)
- **cap** — completed and never returned more rows than the operator's
  `max_rows`

A missing fixture class fails that class. The report is what activation reads.

## Discovery excludes drafts by default

`list_saved_queries` returns registry metadata only. With no `status` filter it
lists active versions of non-disabled identities — agents may draft freely;
only an operator-activated version is discoverable by default. Passing
`status="draft"` is an explicit request, not the default. Disabled identities
leave the listing immediately. `describe_saved_query` returns one immutable
version (parameters, validation state, hashes) and may describe a draft when
asked by name so an operator can inspect before activating.

`describe_query_space(include_examples=True)` surfaces the seventeen shipped
`examples.*` names from the checked-in example bodies. Customer registry rows
are listed through `list_saved_queries`, not mixed into that flag.

## Every refusal says which refusal it is

`resolve` distinguishes not-found, disabled, awaiting-revalidation and
incompatible. A caller who gets nothing back from a query that was silently not
run cannot tell that from a query that returned no rows.

## The shipped examples

All seventeen `examples.*` names §3.1 maps ship with a body, and every body
parses through the same grammar an ad-hoc statement does — an example that
could not be run would be worse than shipping none. They are plain SQL over the
`memory_v1` views on purpose: an example needing a trick the surface does not
otherwise support would teach the wrong thing. They carry `shipped_example`
assurance, which is not a platform guarantee of their MEANING; copying one and
changing its filters produces a customer-authored query, which is the point.

## Migration head

The registry migration is `p9_06_0027` with `down_revision = p9_05_0026`
(Batch D). Migration-head assertions in the revision graph test, the noop
upgrade test, and the compose release gate advance to `p9_06_0027`.

## Not built in this slice

- `run_saved_query` as a surface entry point — that belongs with Batch F.
- Batch F adapters, consumption-skill/docs rewrite, benchmark machinery, and
  deprecation telemetry.
- A marketplace, dependency system, automatic installer, saved-query chains,
  dynamic top-level MCP tools, or speculative RPC layers.
