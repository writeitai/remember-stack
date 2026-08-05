# Batch E — the saved-query registry

A saved query is something other people's work comes to depend on, so the
shape of this batch follows from one rule: a name means one thing over time.

## Editing adds a version

Identities and versions are separate tables and versions are append-only. An
edit does not change what an earlier caller ran — it adds a version — because
silently redefining a name is the failure this exists to prevent. §5's bounds
are enforced with `quota_exceeded`: 1,000 identities per deployment, 50
versions per identity, 64 KiB of SQL, and per-principal draft limits, because
drafts are cheap to make in a loop and an agent will.

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

## Not built in this slice

- The saving validator that actually EXECUTES the four fixtures. The report
  shape and the gate that requires it exist; the thing that runs positive,
  empty, tombstone and cap cases against a deployment does not.
- Discovery exposure: drafts are excluded from default discovery by §5, and
  nothing yet publishes the active set.
- `run_saved_query` as a surface entry point — that belongs with Batch F.
