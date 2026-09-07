# Unknown-start states and relation exclusion

Status: non-binding analysis, 2026-09-07. This records an implementation-discovered
conflict in D107/D110. The binding resolution is D111 in
`../designs/temporal_clocks_design.md` §4.2.1.

## Problem and evidence

Consider two claims normalized to the same relation triple and state kind:
“Alex is CEO” without dates and “Alex was CEO during 2019.” D107 §4.2 prohibits
evidence attachment between dated and undated windows. Its §4.4 requires
coexistence when no supported succession boundary or contradiction exists.
Nevertheless §4.2's relation exclusion treats an ordinary unknown state window
as an unbounded range, overlapping every dated window of that triple. There is
no legal stored outcome for an ordinary, non-conflicting mixed pair in both
ingestion orders. Inventing succession from identical values would not fix the
semantic problem.

Exact inspected contracts: `../designs/temporal_clocks_design.md` §4.1
(unknown seed endpoints), §4.2 (mixed evidence prohibited; unknown bounds under
exclusion), §4.4 (world-time caps and coexistence), §7.1 (current membership);
`../designs/temporal_write_and_lifecycle_design.md` §4 (corrections draw endpoint
candidates only from already-linked identity evidence) and §6.1 (erasure is
distinct from ordinary missing timing). The implementation at `267a9d8e` plus
the ordered-application working changes exposed the conflict; an implementation
failure is evidence of the gap, not authority to weaken the constraint.

PostgreSQL's [range documentation](https://www.postgresql.org/docs/15/rangetypes.html#RANGETYPES-INFINITE)
confirms that missing endpoints produce unbounded ranges. Its
[constraint documentation](https://www.postgresql.org/docs/15/sql-createtable.html)
confirms that the default exclusion check is immediate. Retrieved 2026-09-07.
Deferring the check changes its timing, not whether coexistence is valid.

## Alternatives and independent challenge

| Alternative | Assessment |
| --- | --- |
| Force evidence attachment | Loses the D107 distinction between a dated spell and an undated assertion whose identity is unresolved. |
| Create a contradiction group, erase a boundary, or invalidate belief | Introduces unsupported semantics merely to escape a constraint. |
| Keep the input retrying | Does not resolve the invariant and stalls an ordinary input indefinitely. |
| Exempt only states with both endpoints NULL | Incomplete: an ending occurrence can lawfully cap an unknown-start state to `(NULL, T)`. A later dated assertion before T produces the same mixed-pair conflict. |
| Exempt all states with either endpoint unknown | Unnecessarily removes protection from known-start open-ended states. |
| Exempt states whose start is unknown | Resolves both unknown-start shapes; retains known-start finite and open-ended protection; uses existing endpoint data. |
| Introduce explicit per-fact uncertain-overlap permission | Can distinguish more cases, but adds authority, writers, replay, erasure and consumer contracts for a class already identifiable by missing start. |
| Permit evidence after model-proven mixed identity | Potentially useful separate semantics, but inconclusive judgments still need lawful coexistence. |

An independent read-only analysis initially preferred the both-NULL exception.
A second pass challenged it with the legal ending-occurrence cap. Both analyses
then converged on known-start-only exclusion. `CanonicalBounds.is_known` in
`src/rememberstack/core/temporal.py` is start-based; changing that helper would
not itself settle the state identity contract or unknown-start semantics.

## Recommended full consequences

The exclusion requires a known verdict start, in addition to its existing state,
non-invalidated, no-contradiction and no-erased-boundary conditions. Unknown-start
rows retain their kind, immutable seed, actual endpoints and bases. The
admission/identity/block/revision protocol and durable application receipt
prevent duplicate application; SQL does not pretend to settle uncertain identity.

Acquiring a known start is a transition into exclusion eligibility. Under the
complete D110 locks the writer must check the entire proposed interval against
all eligible same-triple neighbors. Refuse an overlapping correction and retain
the previous endpoints with a recorded reason. A cap that preserves an unknown
start does not enter the exclusion. Erasure keeps its separate provenance and
uncertain-membership rules.

Current membership remains D107 §7.1: a missing start adds no lower-bound test;
a known end still expires the row. Reads disclose the actual missing start and
unresolved identity overlap. Aggregates count fact identities, including both
coexisting identities when eligible; that count is not proof of distinct dated
spells or unique real-world episodes. Profiles cannot turn this uncertainty into
a fabricated timeline. No new schema field, scheduler or public permission is
needed. These contracts apply to conversion, replay and all writers, not only
new ingestion.

The compatible exact-state shortcut is a related implementation hazard: a
single deterministic evidence target must survive inference about additional
candidates. D107 already requires that outcome. Multiple compatible exact
targets require their own explicit identity selection contract; this analysis
does not authorize arbitrary selection among historical slices.

Required database evidence includes both ingestion orders, unknown-start finite
ends, multiple historical slices, known-start exclusion rejection, valid and
refused start acquisition, deterministic receipt replay, and erasure behavior.
The amendment is not an implementation or release certificate.
