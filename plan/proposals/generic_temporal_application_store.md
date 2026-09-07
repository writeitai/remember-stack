# Generic temporal application storage

Status: unchosen alternative, 2026-09-07. Analysis:
[observation temporal applications](../analysis/observation_temporal_applications.md).

A generic admission/application table could represent both relation and
observation assertions, prepared answers, target sets and lifecycle effects.
It would centralize some CAS and replay plumbing. It also requires plane-tagged
foreign keys, different assertion identities, relation multi-target versus
observation single-target/current-support authority, and changes to accepted
D110/D112 relation storage and every receipt consumer.

It is not selected for the observation writer: D90 already supplies its work
units and barriers, and a small observation-specific application extension can
reuse the shared temporal journal and the same preparation protocol. Duplicating
relation version fan-out or using processing payloads as application authority
is likewise unnecessary.

Adoption trigger: demonstrated duplication across completed plane implementations
that a generic schema removes without weakening typed foreign keys, independent
generation pins, complete target certificates, support relocation, forget closure
or replay. The comparison must include migration and compatibility costs and
receive a binding amendment before replacing accepted stores.


## Automatic reassociation after support erasure

A distinct unchosen extension would automatically reassociate the same erased
application when new independent identity evidence appears. It needs explicit
triggers, per-input attempt identity, admission ordering, CAS publication,
replay and repeated-forget authority. Ordinary window correction does not own
identity, and retrying a completed receipt cannot supply this capability.
Adoption requires evidence that new assertions or a distinct adjudicator-generation
ingestion cannot meet the recovery need, followed by a binding complete contract.
