# One mutable fact window: reassessing the temporal program

**Status:** non-binding analysis, 2026-09-07.
**Question:** can ordinary fact adjudication solve temporal identity and date
corrections without permanent fact categories, two world-time windows, or a
separate temporal correction engine?

## Problem and evidence inspected

The intended separation already exists: claims preserve what sources said;
facts record the engine's current interpretation. Source publication time,
world time, and the time the engine recorded a belief answer different questions.
Keeping those clocks separate does not require two world-time windows per fact.

The reviewed draft is engine PR #384 at `7a64e34d`, based on main `c0f5c010`.
Its diff contains 86 files and approximately 24,942 additions. It remains an
incomplete implementation: the observation store applier is not yet the running
worker, and the worker/barrier migration is not accepted. The user explicitly
asked to reconsider unnecessary complexity before any merge.

Two independent, read-only analyses examined (1) writes, identity, retries and
schema and (2) retrieval, output semantics and caches. Both favored one mutable
fact window and removing hard category/date identity gates. The write audit
favored preserving a reduced prepare/infer/revalidate/apply pattern over holding
database locks through remote inference. The retrieval audit proposed making
profiles date-qualified historical summaries, removing their need to change
merely because a date passes. These recommendations are resolved below rather
than treated as already binding.

Exact local sources inspected:

- `src/rememberstack/core/fact_temporal.py`, `seed_fact`, `occurrence_union`,
  `correct_window`: the draft creates a second evidence union, seeds an
  open-ended verdict for an occurrence, and restricts later corrections.
- `src/rememberstack/model/temporal_write.py`, operation validation: ordinary
  evidence/identity writes cannot change verdict dates.
- `src/rememberstack/core/observation_temporal.py`,
  `observation_permits_evidence`, and `core/relation_temporal.py`: kinds,
  datedness and overlap mechanically constrain semantic identity.
- `src/rememberstack/spine/temporal_journal.py` and migrations
  `p9_28_0049`–`p9_30_0051`: endpoint ownership, operation dependencies,
  compensation, conversion and forget checkpoints implement that authority.
- On main, `spine/fact_catalog.py`, `upsert_relation`, `_SELECT_RELATION`,
  `_LATEST_CLOSED_UNTIL`: same triples merge before adjudication; date selection
  uses current-only and prior-closure assumptions.
- On main, `spine/supersession.py`, `_adjudicate_pair`: source order and a
  source-date/clock fallback influence world-time closure.
- On main, `spine/observation_adjudication.py`, `_coerced_reason`,
  `_evidence_compatible`, `_resplit_later_evidence`: date and exact-text gates
  can override identity; later publication is used to redistribute evidence.
- `model/assured_operations.py`, `FactTime`; `adapters/postgres_p1.py`, fact
  time predicates; `surfaces/query_engine.py`, final fact confirmation:
  current/at/overlap/history already operate on one world interval.
- `model/envelope.py`, `Validity`, `KFreshness`: one world interval, system
  timestamps, and existing cache freshness disclosure already exist.
- `spine/profile_refresher.py`, profile construction and candidate SQL:
  deterministic joined salient facts; finite ends are excluded while future
  starts are not checked. The SQL explains its no-timer assumption.
- `core/knowledge_fact_sheet.py`: historical facts can be labelled "ended",
  conflating a completed world interval with disbelief.
- `plan/designs/temporal_clocks_design.md` (D107),
  `temporal_write_and_lifecycle_design.md` (D110),
  `observation_temporal_application_design.md` (D113), and their SQL appendices.

References to draft implementation above describe the preserved checkpoint,
not a requirement to retain those files in the replacement PR.

## Why the original distinction is unnecessary

A tournament win on 10 May 2022 happened then. The engine can continue believing
that historical fact in 2026. Its world window describes 10 May 2022; its system
timestamps describe when the engine held that belief. Making a second window
run from the tournament date forever confuses these meanings.

Similarly, "Alice was CEO from 2019 to 2022" remains a believed historical fact
after 2022. A current CEO query and a biography ask different questions of the
same store. Existing explicit time modes are sufficient to express that
difference; persistent `state` and `occurrence` categories are not necessary.

Categories can be useful language in an adjudication prompt. They become harmful
when the database or code uses them to prohibit an otherwise valid match.
Two tournaments can happen on one day, and two reports with different dates can
describe one tournament whose date was corrected. Undated testimony can identify
an event by name, place, opponent and context. None of these is decided by an
interval-overlap test or a fixed three-way classification.

## Options considered

### Keep the framework and rename its windows

This preserves tested transaction code, but also retains forbidden ordinary
updates, seed authority, endpoint ownership and compensation. Renaming does not
address the user's concern. Removing every obsolete invariant piecemeal would
leave many storage and replay concepts without an independent purpose.

### Keep two world windows as convenience metadata

An evidence date union is easy to compute when inspecting claims. Persisting it
creates another value consumers must interpret and another derived copy to
refresh and forget. It is especially misleading when conflicting dates make
the union span a period that no source asserted. Keep source dates on evidence;
do not store a second fact window.

### Use one mutable window and ordinary adjudication

Selected recommendation. A decision can attach evidence, distinguish identities,
revise dates in either direction, close a predecessor, or record conflicting
testimony. Its existing rationale, confidence and evidence references explain
the result. A window is replaced as a complete value, so explicit null endpoints
can clear a wrong date or reopen a mistaken end without an inverse-operation
framework. Omission means preserve, not clear.

This still costs model calls, candidate reads, transactions and retry bookkeeping.
It does not make concurrency or forgetting disappear. It removes duplicated
temporal authority rather than removing necessary correctness properties.

## What remains necessary

**Identity before attachment.** Main's same-triple upsert must not collapse two
events before the model sees them. Exact text can nominate candidates but cannot
prove independent assertions refer to one occurrence. Only an actual application
retry can skip identity based on its already recorded result. Remove the universal
same-triple interval exclusion: distinct events can share coarse dates and triples.

**Grounded mutable dates.** Date updates need cited, admissible source claims and
a rationale, not equality to one mechanically selected endpoint. Context may
justify a date inferred from several statements. A claim-to-many-assertions
normalization must not copy one date to every assertion without justification.

**Atomic application.** Fact values, evidence assignments, decision history and
completion must agree after a crash. Reuse domain adjudication transcripts and
the existing ledger. If inference runs outside locks, revalidate its candidate
and evidence snapshot before applying; otherwise stale output can overwrite new
truth. Keep this a narrow application contract rather than a temporal journal
with a second dependency graph.

**Historical identity.** Learning A in 2019, A in 2024, then B in 2022 may require
separate A tenures. It may also describe an erroneous report. The accepted
semantic decision must explicitly choose the split and supporting assignments.
Automatically moving every later-published claim invents that decision.

**Forget.** Removing a source must remove retained source-derived payloads and
unsupported dates from snapshots, transcripts, preparations, caches and indexes.
Fewer new stores make this smaller; omitting the deletion proof still leaks data.
Source withdrawal is a different operation and can retain historical evidence.

## Retrieval and cache consequences

General historical questions must use `history`; a bounded date question uses
`overlap`; current-state questions use `current` or `at`. The default is currently
`current`, so prompts, operation descriptions and callers need explicit changes.
Deleting the second window alone does not fix historical recall.

Unknown is not the same as explicitly open. One window needs enough endpoint
meaning/precision to preserve that distinction. This is date metadata, not a
fact category or date-dispute state. Relevant undated facts should remain visible
as possible candidates, without counting as established matches for every year.
Ranked/top-k retrieval cannot establish an exact complete count; existing
truncation information and exact-query paths remain relevant.

Profiles currently join selected fact text into an entity summary used for
resolution. Recommended product choice: make that text date-qualified and select
without a moving "current only" filter. "CEO of Acme, 2019–2022" remains accurate
as the clock advances. Date changes still trigger the existing profile repair.
Date-qualified snapshots remove the need for a new temporal cache scheduler for
this content. They must not quietly claim that every selected relationship holds
now. K pages can use dated content and explicit snapshot wording; authoritative
current questions still go through fact-time filtering.

If current-only prose is a requirement, expiry is real: scheduling and stale-read
protection are required together. This is the genuine alternative, not a reason
to introduce certificates for every date-qualified historical summary.

## Implementation scope and review

The unreleased framework has no production compatibility obligation merely
because it was written. Removing it from the draft while preserving Git history
is safer than pretending the new contract is a field rename. Already merged
canonical SQL, extraction vocabulary and benchmark-provider work must survive.
No migration may downgrade an installed experimental draft schema silently.

The delivery plan must separate accepting this change of design from claiming the
replacement runtime works. Tests must demonstrate corrected dates in both
directions, contextual identity, distinct same-day events, historical retrieval,
unknown-date disclosure, atomic retry, stale inference rejection and forgetting.
Independent Antigravity and Grok reviews are required by the user. Neither review
authorizes a merge or release; the user retains that decision.


## Independent external review clarification

Antigravity and Grok reviewed committed redesign `5e2fd916`. Both confirmed the
main-based cleanup and favored one mutable window. Grok identified a concrete
semantic gap: claim CHECKs reject partial windows, and `core/temporal.py` fills a
missing bounded claim end from its start. Applying either rule to a partial fact
would invent a date. The design therefore needs its own explicit window-shape
table, keeps unknown precision as the existing `unknown` enum value, and prohibits
claim canonicalization on partial or already-canonical fact rows. This clarifies
the one-window representation; it adds no second window or fact category.

Antigravity requested explicit query-match output and a strict consumer/conversion
release gate. A query-specific `temporal_match` field distinguishes confirmed and
possible candidates; it is not stored date-dispute state. Both the wire schema
and stable application uniqueness are concrete implementation-contract gates.
Grok also requested explicit D43 measurement semantics and supersession labels
at the old rule/DDL sites. Known reporting periods are finite world windows;
reaching their end does not invalidate belief in the historical figure.
