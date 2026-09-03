# K Plane — Compiled and Authored Knowledge (Design)

> **Binding D107 amendment (2026-09-03).** The K prose writer's claim bundle
> carries `asserted_at` and the D41 fields with the two-clock framing, and
> the deterministic fact sheet prints a date under `valid from`/`valid until`
> only for `world_time` rows, under `said on` for `said_on` rows, and `—`
> otherwise; observation history sorts by the fact window. Contract:
> `temporal_clocks_design.md` §5.1, §6.

> **Binding D98 amendment (2026-08-27).** D98 supersedes D11 and removes the
> unproved global community analytics product. The shipped K layout has entity
> pages, source digests, root indexes, and purpose-scope pages; it has no
> community/topic pages, `community` routing rule, `community_changed` event,
> or `entity_graph_metrics` dependency. Entity-subtree, predicate, document-set,
> and manual routing remain. Pre-D98 community wording below is superseded where
> not rewritten in place.

How the system turns the evidence spine into the high-level, browsable knowledge layer agents
read *first* — per-purpose curated summaries, entity profiles, and authored documents — while
keeping every page mechanically traceable to the evidence it rests on. Binding design for
decisions **D45–D47, refined by D73** (which also accept objections **O2** and **O4**); builds on D1 (split
source of truth), D12 (debounced aggregate triggers), D24 (blast-radius
review), D33 (decision ledgers for non-deterministic stages), D42 (document origin), D43
(observations). This one document covers the whole plane; D73 withdraws the previously proposed
K3 belief tier because core principles are authored K2 content. Schema:
`postgres_schema_design.md` §11. Numbers here are starting points to measure, not committed
constants (CLAUDE.md).

> **Reading this cold (CLAUDE.md Rule 1).** Plane E (evidence) stores what sources said as
> **claims** (immutable natural-language assertions), normalized into **relations**
> (entity→entity facts) and **observations** (single-entity value facts), all anchored on
> canonical **entities** with bi-temporal validity windows. Plane K (this doc) is the layer
> above: **markdown pages in a git repo**, written for reading — an agent looks here first and
> drills into evidence only when needed. Two words carry precise meanings throughout:
> **compiled** = a page written by an LLM *from* the evidence and regenerated when that
> evidence changes; **authored** = a page written by a human or agent as first-class content
> (a plan, a target design, a decision log) — never regenerated, but *alerted* when evidence
> it relied on changes. A page's **citations** are the recorded evidence IDs it rests on; a
> page's **routing rule** is the stored, mechanically-evaluable definition of what evidence
> belongs to it. Together they are the page's *manifest* — the thing that makes staleness,
> deletion, and audit computable instead of guessed.

## 1. The core idea: intelligence chooses; machinery routes

The design splits plane K's work along one line: **an LLM decides what pages exist and what
each page is *about*; SQL decides which pages a new piece of evidence affects.** This works
because of something plane E already guarantees — by the time evidence lands, it has been
through entity resolution and relation normalization, so every
new claim/relation/observation arrives *pre-labeled* with the keys rules match on (canonical
entity IDs, governed predicates, document metadata). The expensive semantic
understanding happened upstream, once; K routing reuses it for free.

The alternative — free agent sessions browsing the repo each cycle to *discover* what to
update — leaves the two load-bearing steps (routing new evidence to pages; deciding which
pages exist) as unrecorded, per-cycle LLM improvisation, and then needs merge-conflict retry
and hot-file serialization machinery to survive concurrent sessions. It also makes the
system's core promises undecidable: "is this page stale?", "which pages must recompile when
this document is deleted?", and "is this page's coverage complete?" have no computable answer
when the compile's read set was never recorded. D45 rejects that mechanism. (This is not a new
discipline — it is D33's discipline, already applied to every other non-deterministic stage:
extraction has its decision ledger, adjudication its transcript, resolution its append-only
decisions. Plane K was the last LLM stage whose decisions evaporated when the session ended.)

What is **not** deterministic — deliberately — is the content. Writers have full creative
latitude (and may be full agent sessions — a stock harness with default tools over the
mounted memory + CLI, §7). Determinism lives only in
*triggering* (what is stale), *routing* (which page gets what evidence), and *bookkeeping*
(what fed what).

## 2. One mechanism, many scopes (D47/D73 — accepts O2)

Plane K runs **one compilation mechanism**. K1 and K2 name its shipped scope layout, not
separate machinery:

| Tier | What it is under this design |
|---|---|
| **K1 — general knowledge** | the **default scope**: entity pages, source digests, the root index |
| **K2 — purpose scopes** | additional scopes (people profiles, business planning, as-is/to-be migration tracking, a personal operating doctrine, …) — each a git subtree + registry rows (`scopes`, `scope_interests`, D16), sharing the one entity space; each may mix compiled support material with authored principles and decisions (§8) |

A scope is: a subtree of the repo, its registry rows, its pages (compiled and authored), and
one **shared model page** (§7) that anchors its vocabulary. "Scopes multiply, truth doesn't"
(D16) holds: scopes own compiled markdown and authored documents, never facts.

### A framework, shipped with a default configuration

The mechanism above is deliberately a **framework**: nothing in the machinery knows what "K1"
or "K2" *mean*. Two distinct strata, with a sharp line between them:

- **The framework contract — fixed, not per-deployment configurable:** the two page kinds and
  their ownership rules (D46); routing rules with binding citations (D45); the trigger surface
  and its acyclicity invariant (§5); one git repo + the Postgres control plane, with the
  driver as the repo's only automated committer.
- **The knowledge layout — pure configuration, reshaped freely per deployment:** which scopes
  exist, their trees, rule assignments, and authored pages. All of it is registry rows, git
  content, and plan decisions, never code. **K1 (default scope) plus K2 (purpose scopes) is the
  shipped default configuration** — a reference layout, not a requirement of the machinery;
  a deployment may rename, drop, or invent scopes.

D15 established "ontology is content, not machinery"; the same statement holds one plane up:
**knowledge structure is configuration, not machinery.** This applies equally to our own
deployments and to any user of the open-source library — they inherit the contract, they own
the layout. Guarantees travel with page ownership and citations, not tier names.

### Relationship to the original K1–K3 conception

A reader arriving from older documents (requirements v1/v2, decisions D1/D12 as originally
written, early discussions) should treat K3 as **withdrawn by D73**, not as latent machinery.
The original conception treated K1/K2/K3 as three layers — implicitly three pipelines, a
separate belief design (`k3_beliefs_design.md`, never written), and concurrent agent sessions
editing a shared repo. D47 first collapsed those into one mechanism; D73 completed the
simplification after the motivating use case showed that core principles are authored K2
content, while evidence-qualified facts already belong to E3. The current design is **one**
machine (planner / writers / driver — D45), two page kinds (D46), one trigger surface (§5),
K1 general knowledge, and any number of K2 purpose scopes.

K1's progressive-disclosure summaries and K2's pluggable coexisting scopes retain their
requirements-level guarantees through mechanical staleness, binding citations, and
authored-page alerts. Several things in this design have **no counterpart** in the original
conception at all — authored pages and curation sidecars, the promotion loop (§9), page watches
and dispatch subscriptions (§5), and two-band pages (§5). The migration, personal-memory, and
agent-operated-company scenarios forced them into existence. One line to carry away: *K1 and
K2 are scope configuration; compiled versus authored is the load-bearing semantic boundary.*

## 3. Three roles and one ownership rule (D45)

| Role | What it is | What it owns | What it may never do |
|---|---|---|---|
| **Planner** (LLM) | maintains the *structure*: which pages exist, the tree, each page's routing rules | page existence, splits/merges/moves, rule changes — all as append-only `knowledge_plan_decisions` | write page content |
| **Writer** (LLM, per page — Codex/OpenCode) | compiles **one page per invocation** from its inputs (§6); may be agentic (§7) | the body of *compiled* pages | touch any other file; leave inputs uncited |
| **Driver** (deterministic worker) | computes staleness (SQL), schedules writers in dependency order, validates outputs, syncs Postgres, dispatches subscriptions (§5), commits | the git *commit* — it is the repo's **only automated committer** | generate content; override curation |

**Authors** — humans or operating agents (in the named deployments, almost always agents) —
own the fourth surface: **authored pages and curation sidecars** (§4), committed through
normal git flow. The driver pulls before each cycle. Because the compile system's file set and
the authors' file set are disjoint by the ownership contract, merge conflicts between the
system and itself are structurally impossible, and conflicts among authors happen only in
their own files, under ordinary git rules. The prior design's in-session
conflict-retry and the hot-file rolling-window worker are **removed** (the root `index.md` is
simply the last target in the dependency order, compiled once per cycle — D12 refined).

## 4. Two page kinds and the ownership contract (D46)

Every K artifact is one of two kinds. Both carry citations; they differ in who writes the body
and what happens when cited evidence changes:

| | **Compiled page** | **Authored page** |
|---|---|---|
| body written by | its writer (LLM) | a human or an authoring agent |
| derived from evidence? | yes — regenerated from its rules' evidence | no — it *is* first-class content (a design, a decision, a target state) |
| when cited evidence changes | page goes **stale → recompiled** | page gets a **review flag** ("a decision here rests on changed evidence") — never auto-rewritten |
| human input via | the **curation sidecar** | direct editing (it's theirs) |
| examples | entity profile, topic summary, as-is system description, recurring-pattern summary | to-be architecture, mapping decisions, project plans, position papers, personal principles |

**Curation sidecars.** Human judgment about a *compiled* page lives in a per-page, git-tracked
sidecar (`<page>.curation.md`): pins ("keep this framing"), exclusions ("never cite claim X"),
corrections ("this conclusion is disputed — present both sides"), free guidance. The sidecar
is a first-class compile input (it is hashed into `inputs_hash`, §5 — editing it triggers a
recompile), and the enforceable subset is enforced mechanically (excluded evidence IDs are
filtered from the writer's bundle and rejected from its citations).

**The quarantine rule.** Compiled bodies are machine-owned. If anyone — a human or an
out-of-band agent — edits one directly, the driver detects it (`content_hash` mismatch), does
**not** overwrite and does **not** silently absorb it: the diff is quarantined into a
*proposed sidecar entry* and the page is excluded from recompilation until the proposal is
accepted or rejected (or the page is adopted — see below). An author's work is never
destroyed; it is moved to where it survives regeneration.

**Authored pages still participate in the manifest system.** An authored page's frontmatter
declares its citations (`cites:` — the evidence IDs the author relied on) and optional
**watch rules** (`watch:` — whose consequence is a *flag*, not a recompile: "tell me when
anything new lands about module X"). A watch target may be an evidence key or **another page**
(`watch: page:to-be/ordering-flow`, §5), and in agent-operated deployments a watch can bind to
a **dispatch subscription** (§5) so the owning agent's workflow is invoked instead of a queued
flag. The driver syncs frontmatter to Postgres. This is what makes authored content safe at
scale: decisions are automatically alerted when the ground they stand on moves (§9).

### How a page gets its kind — and how it changes

The system never *classifies* a page's kind — kind is fixed by **which door the page entered
through**, and enforced by ownership:

- **Planner-created pages are compiled, always.** The planner creates a page *because* evidence
  needs a home (orphan facts, a splitting page, a new entity or source grouping) — so its content is by
  construction derivable from the spine, and it gets routing rules and a writer. The planner
  cannot create authored pages; there is nobody to write them.
- **Committed pages are authored, always.** An authored page comes into existence when a person
  (or an authoring agent) writes a file and commits it through normal git flow; on the next
  cycle's pull the driver finds a file that is not one of its own artifacts, registers it
  `authored`, and syncs its frontmatter.

The judgment that *does* exist belongs to the author choosing a door: **could every sentence on
this page cite evidence already in the spine?** If yes, don't write it — request it as a
compiled page and it stays current forever. If it contains **commitments the world has no
evidence for yet** — a target design, a decision, a stance, a plan — it cannot be compiled
(there is nothing to compile it *from*) and must be authored. Mixed needs resolve by
**composition, never hybrid pages**: human input into a compiled page goes through its sidecar;
an authored page wanting an evidence-derived section *links to* a compiled page rather than
inlining a copy that would rot.

Kind can change — always as a recorded plan decision (`convert_kind`), never silently:

- **Adoption (compiled → authored).** The natural path is the quarantine flow: an author edited
  a compiled body, and one triage outcome is "the author takes this page over" — flip to
  `authored`, stop recompiling, keep its routing rules as watch rules (staleness becomes review
  flags, §5).
- **Handover (authored → compiled).** The author judges the page fully evidence-backed: the
  planner attaches rules, a writer takes over, and the author's residual judgment moves into
  the sidecar. Because this discards an author's ownership of a body, it is the one plan
  action that **never auto-applies** regardless of blast radius — it stays `proposed` until
  the author (human or agent) confirms.

A complementary route exists for content, not just pages: finalized authored material can be
**ingested as a source** (D42 stamps it system-originated), so its statements enter plane E as
evidence — compiled pages then absorb it and the authored draft retires. §9 shows this
*promotion loop* end to end.

**Consequence for D1 (refined by D46).** The git repo remains plane K's source of truth, but
its *irreducible* core — what backups genuinely protect — narrows to **human-authored
content** (authored pages + sidecars). Compiled pages are *semantically regenerable*: re-running
a compile over the same recorded inputs yields a page that is not byte-identical (LLM
non-determinism) but says the same thing, traceable to the same evidence. That is exactly what
objection O4 asked for.

## 5. Routing rules, citations, and what "stale" means (D45)

### Routing rules — mechanical, chosen by the planner

A routing rule is a stored row (`knowledge_page_rules`): a **kind** plus parameters, where
each kind has one fixed, deterministic SQL evaluation over the spine. A page may hold several
rules (their union). The closed kind set:

| Kind | Parameters | Matches |
|---|---|---|
| `entity` | entity_id; optional predicate filter; which fact layers (relations / observations / claims-via-mentions) | everything about one entity |
| `entity_subtree` | root entity_id | the entity plus its `part_of` closure (e.g. a subsystem and its modules), then as `entity` per member |
| `predicate_beat` | predicate; optional subject/object | e.g. `works_for → acme`: who works at Acme |
| `doc_set` | document metadata filter (source, mime, `origin` D42, time range) | evidence from a document family (e.g. board minutes) |
| `scope_interests` | scope_id | delegates to the registry's `scope_interests` rows (entity types, predicates, metadata, keywords) |
| `manual` | explicit entity/evidence ID list | the editorial escape hatch (§ below) |

**Why not description-based (semantic) rules.** A description rule ("this page covers Acme's
pricing strategy") needs an LLM or embedding comparison *per new evidence item, per page* —
a classification pass whose cost scales with volume × page count and whose answers are
non-reproducible. That is the pre-extraction value gate's mistake in new clothes (D25). The
division of labor instead: the planner (an LLM) *chooses* "there should be an Acme page keyed
on `entity: acme`"; SQL *evaluates* "these 12 new rows match `entity: acme`". Zero LLM calls
on the routing path — the same rule the query path already obeys (D9).

**Routing granularity vs. editorial granularity.** Mechanical keys route at the granularity
plane E produces: entity, predicate, and document set. Finer subdivision ("Acme
pricing" vs "Acme hiring" as separate pages) is *editorial*, not routing: the rule delivers
everything-Acme to one page, whose writer organizes it into sections. The planner splits at
the routing level only where a mechanical key exists to split on (a predicate set, a doc-set,
a subtree member); where none exists, a split uses `manual` rules with explicit assignments
(typically adopted from the writer's own split suggestion). Intelligence decides the split;
the *record* of the split is mechanical.

**The inverted key index.** Every rule's match keys are materialized to `knowledge_rule_keys`
(`(key_kind, key_value) → rule`). Routing a batch of new evidence is then one indexed lookup —
the same block-first philosophy as supersession (D4): exact keys narrow, expensive work runs
only on the narrowed set. `entity_subtree` rules have *derived* membership, so
the driver re-materializes their keys when a `part_of` relation touches the
subtree — an ordinary evidence event the driver already sees.

**Orphan evidence — the planner's inbox.** Evidence matching *no* rule in a scope is counted
per entity ("Bob has 14 unhoused facts"). Aggregated orphans, page-size overflows,
writer suggestions, and reflection findings (§7) are the planner's triggers; its
outputs are append-only `knowledge_plan_decisions` (create/split/merge/move/retire/adjust-rule)
with a rationale. Low-blast-radius decisions auto-apply; restructures above a threshold queue
for review by an **accountable reviewer outside the proposing context** — a human or a
designated reviewer agent (§7) — the D24 pattern applied to structure.

### Citations — the binding output contract

Every compile ends with the writer returning, besides the markdown: its **citations** (the
evidence IDs the page rests on, with roles `supports | contradicts | cites`), a short
**page summary** (2–3 sentences, stored in Postgres — parents consume child summaries without
re-reading files), and optional **suggestions** (planner inputs, never direct action). The
driver validates citations (IDs must exist; excluded IDs must be absent), replaces the page's
`knowledge_artifact_evidence` rows, and records the compile in `knowledge_compilations`
(inputs hash, candidate/cited/uncited counts, versions, commit). Rule-matched evidence the
writer chose *not* to cite is thereby counted — the K-plane analogue of D33's Selection-drop
ledger ("why isn't fact X on this page?" has an answer).

### Staleness — mechanical, three causes

A page was compiled from a snapshot of its rules' answer. It is **stale** when that snapshot
no longer matches reality:

1. **New evidence matches a rule** but was never in the page's candidate set.
2. **Cited (or candidate) evidence changed state** — a relation's validity window was capped
   or invalidated, an observation superseded, a contradiction opened.
3. **Cited evidence was deleted** (source document removed — §10).

Formally, the driver computes per page an
`inputs_hash = hash(sorted candidate FACT state — relation/observation IDs + their validity/
currency fingerprints + counts; claim-grain candidates keyed by (lineage, chunk_content_hash),
never raw claim IDs (D54 — a re-extraction that changes no fact state stales nothing)
+ curation sidecar hash + sorted child page-summary hashes + shared-model-page summary hash
+ writer prompt/model version + rule configuration)`, and the page is stale **iff** it differs
from the hash recorded at last compile. This is D12's idempotency discipline (content hash +
version) applied to K: re-running a cycle is a no-op; a prompt-version bump recompiles exactly
everything; "is anything stale?" is one deterministic computation. Stale ≠ wrong — it means
"compiled from inputs that are no longer current"; what the new text *says* is entirely the
writer's judgment. Stale also ≠ instant recompile: plane K stays debounced (D12) — stale pages
accumulate and compile on the window. For **authored** pages, causes 1–3 produce the review
flag instead (D46). *(These are the evidence-side causes only; the complete trigger taxonomy —
sidecar edits, version bumps, DAG propagation, the manual override, and the authored-side
channels — is consolidated in "Triggering end to end" at the end of this section.)*

Every compiled page carries a machine-written provenance footer (compiled-at, evidence as-of,
citation count) — the per-page freshness metadata that mixed-freshness reasoning
(`questions.md` #23) needs.

### What compilation consumes — the adjudicated layers are the skeleton, claims are hydrated selectively

Compiling from raw claims would re-pay, at every compile, exactly the work E3 already did —
redundancy collapse (200 claims asserting one employment → one relation), validity adjudication,
contradiction grouping — and claims cannot answer "is this still true" (current belief is
relation/observation semantics; requirements bar claims from it). So a compiled page's primary
inputs are the **adjudicated layers**: **relations** — the distinct facts, whose windows give
current-vs-ended, whose `evidence_count` gives salience ordering, and whose
`contradiction_group` marks what to surface as tension — and **observations** — the value facts
and their capped history, which is precisely the timeline material ("headcount 500 → 600 over
2024"). Claims enter in exactly two roles:

1. **The residue** — kept claims that normalized into *neither* layer: attributed statements
   ("Alice said the migration would slip"), n-ary and qualified assertions. Without
   claims-via-mentions in the rule, a person page would silently miss half its value.
2. **Color for the leading facts** — the fact label says `works_for(alice, acme)`; its best
   evidence claim says *"hired as VP of Engineering to rebuild the platform team."* The writer
   hydrates a claim or two for the facts that lead the page.

This is also the hub-entity budget rule (§11, residual 3) made concrete: relations +
observations are **bounded** (distinct facts, not corpus-proportional) and always included in
full; claims are the unbounded layer and are **capped** — the residue plus top-K evidence per
leading fact, evidence-count-ranked, with the cut recorded in the compile transcript (no silent
caps).

### The two-band page — deterministic fact sheet + LLM prose

Part of an entity page needs no LLM at all: a table of current relations and an observation
timeline is a *deterministic render* — and asking a writer to re-type facts into prose is
exactly where hallucination risk lives and tokens burn. A compiled page therefore has **two
bands**:

```markdown
# Acme
_LLM band — the synthesis a machine can't do: what Acme is, what changed
lately, what is contested, what is load-bearing. Sections group and
narrativize (People; Financials — surfacing the FY2023 $5M-vs-$7M conflict
side by side; Trajectory — over the observation timeline), each statement
citing the facts it interprets._

---
## Fact sheet (generated)
| fact                                 | valid since | evidence |
| Alice Novak works for Acme (VP Eng)  | 2024-03     | 12 docs  |
| …                                    |             |          |
_deterministic driver render: current relations, observation history, open
contradiction groups — exact at compile time, zero LLM._
_compiled 2026-07-05 · evidence as of 2026-07-05T06:00Z_
```

- The **fact-sheet band** is rendered by the *driver* from the same candidate set the writer
  received: deterministic, always literally correct, zero hallucination surface, zero token
  cost.
- The **LLM band** is where the writer earns its place — salience, trends, tensions, narrative.
  Its citations shrink to what the prose actually *interprets*, which sharpens the faithfulness
  audit (§7).
- **Degradation mode:** the planner may designate a page **fact-sheet-only**
  (`kind='fact_sheet'`) — zero writer cost for low-importance entities, upgraded to full prose
  when evidence volume or demonstrated demand justifies it (an ordinary plan decision).

### The K plane as a trigger surface — watches, subscriptions, dispatch

The routing layer (rules + the inverted key index) is the system's **attention mechanism**,
and attention is worth more than recompiles. A routing rule's *consequence* depends on what
owns it:

| Rule owner | Consequence | What happens |
|---|---|---|
| a **compiled page** | recompile | the page goes stale; its writer regenerates it (§6) |
| an **authored page** | flag | an `authored_review` item routes to the page's author (§4) |
| a **subscription** | **dispatch** | a registered agentic workflow is invoked |

Two mechanisms complete the surface:

- **Page-level watches.** A watch target may be an evidence key *or another page*
  (`watch: page:to-be/ordering-flow`): subscribe to a page's recompiles instead of
  re-declaring its rules. It is the same edge the compile DAG already uses (parents consume
  children), with a flag/dispatch consequence — and the right ergonomics for a **paired
  workbench**: a gap-analysis page watches the compiled to-be page it judges, and stays
  correctly subscribed even as the planner adjusts that page's rules underneath.
- **Subscriptions.** A per-deployment registry binds match criteria (a routing rule of its
  own, and/or watched pages) to a **workflow endpoint**: *"anything about competitor X or our
  unit economics → run the replanning workflow."* Dispatch is **debounced per subscription**
  and delivered with the D12 worker discipline (Cloud Tasks, retries, DLQ, idempotent
  consumers). The payload carries the **delta, never a bare ping** — matched evidence IDs, the
  citation/validity changes (the compile transcript computes them anyway), and the affected
  page refs — so the subscriber wakes knowing *what* moved, not just that something did.

**The closed loop, without circularity.** The motivating consumer is an agent-operated
deployment's **planning module** (an autonomous company's planner): it subscribes to what its
plans depend on; when relevant evidence lands it is dispatched, reads the memory (compiled
pages first — that is what they are for), revises its plan files, and commits. The driver
syncs those back as **authored pages** with fresh citations and watches — plans are authored
content par excellence: commitments no evidence attests yet, premised on evidence that must
alert them when it moves. If a ratified plan is later ingested as a source (the §9 promotion
loop), **D42's origin stamp** marks it system-generated, so the company's own plans can never
masquerade as independent external evidence.

**The boundary.** The memory system's job ends at *reliable attention and served context*: it
recognizes relevant arrivals, notifies the right subscriber with the right delta, and serves
the compiled context the subscriber reasons against. It runs no planning logic, owns no
subscriber workflows, and evaluates no plans — subscribers are operating agents outside the
system boundary, which is what keeps the system reusable across deployments.

*(This subsection is the design of the "E→K signal/interrupt channel" that D42 named and
scoped out pending an agent-operations deployment — which is now a named target. D42's origin
capture is unchanged; the other items D42 listed — operational-state scopes,
decision↔evidence-snapshot links — remain non-goals here.)*

### Triggering end to end — compiled vs authored, compared

This subsection consolidates the complete trigger story in one place, because the deepest
asymmetry in the whole design lives here: **for a compiled page, a trigger produces an
*action* (the system regenerates the body it owns); for an authored page, a trigger produces
*information* (the system notifies an owner it has no authority over).** Same routing front
end, different rights on the far side. An implementer should be able to build the driver's
trigger handling from this subsection alone.

**The shared front end (three steps, both kinds).**

1. **Events.** Plane-E workers *push*; plane K never polls. As adjudication and writes
   complete, workers emit `knowledge_refresh_queue` rows: `evidence_changed` (new
   relations/observations/claims; windows capped by supersession; invalidations;
   contradiction groups opened — changed IDs in the payload), and `tombstone`
   (deletions, §10).
2. **Debounce.** Events accumulate; the driver runs on the D12 window ("N items or T
   minutes"), never per event. Each cycle *also* begins with a git pull — the **second
   trigger source**: human/agent commits (sidecar edits, authored-page updates, new authored
   pages) enter the system here, and quarantines (§4) are detected here.
3. **Route (all SQL, zero LLM).** Changed evidence already carries its labels — plane E
   resolved entities, predicates, and document metadata before anything reached
   this queue — so routing is two indexed lookups: the **rule-key index**
   (`knowledge_rule_keys`), which catches *new* evidence matching a declared interest, and the
   **citation reverse-lookup** (`knowledge_artifact_evidence`), which catches state changes to
   evidence a page already *used* (this second path matters: a page must learn its ground
   moved even if the planner has since changed its rules). Derived-membership rules
   (`entity_subtree`) get their key sets re-materialized when their inputs change.
   Routing's output is a set of *(owner, matched delta)* pairs; from here the two kinds
   diverge.

**Compiled pages — the full trigger taxonomy: eight ways to change one hash, plus one
override.** Every update path for a compiled page reduces to the single mechanical test of
§5 ("Staleness"): *did the recomputed `inputs_hash` change?* The taxonomy below generalizes
the three evidence-side causes listed there — it is exhaustive, and each row is just a
different way to move the same hash:

| # | Trigger | Enters via | What changed in `inputs_hash` |
|---|---|---|---|
| 1 | new evidence matches the page's rules | rule-key lookup | candidate ID set grew |
| 2 | cited/candidate evidence changed state (window capped, invalidated, contradiction opened) | citation reverse-lookup | a validity fingerprint |
| 3 | cited evidence deleted | tombstone event | candidate ID set shrank |
| 4 | a child page's summary changed | DAG propagation (after children compile) | a child-summary hash |
| 5 | the scope's shared model page changed | DAG propagation (scope-wide) | the model-page hash |
| 6 | curation sidecar edited | git pull | the sidecar hash |
| 7 | writer prompt/model version bump | version config | the version component |
| 8 | routing rule adjusted | plan decision | the rule configuration |
| 9 | manual | queue row (`manual`) | — none; an explicit **override** of the hash test, for operational use |

Consequence — always the same, and only one: `status='stale'`, wait for the cycle, recompile
in DAG order (shared model page first, then leaves → parents → root index, once), the driver
re-renders the deterministic fact-sheet band, the writer rewrites the prose band, citations
are replaced, the hash is updated. Properties an implementer must preserve:

- **Stale ≠ instant.** Staleness is a state, recompilation is a batch; the debounce window is
  the cost model. Marking stale is cheap and immediate; compiling is deliberate.
- **Failure is safe.** A failed writer job leaves the previous, internally-consistent version
  serving (still marked stale); retried next cycle, dead-lettered per D12. There is no
  partial-page state, ever.
- **Reads never trigger.** No query, browse, or hydration has side effects on plane K. All
  triggering originates from writes (plane E events, git commits, plan decisions) — this is
  what keeps the retrieval path zero-LLM and side-effect-free (D9).
**Authored pages — four channels in, notification out.** An authored page has no
`inputs_hash` and no staleness: the system cannot know whether its *content* is outdated,
because the content is judgment. What the system knows is whether the page's **declared
ground** moved. Four channels, each resolving to a notification:

1. **Citations** (`cites:` frontmatter) — *"what I stood on."* Cited evidence changing state
   raises an `authored_review` flag carrying the delta: a commitment on this page rests on
   evidence that changed.
2. **Watch rules** (`watch:` evidence keys) — *"what I care about going forward."* Catches
   **new** evidence the page never cited: the gap analysis wants to hear about a new interface
   on module X even though it never referenced it. Citations look backward at premises;
   watches look forward at interests — a page usually needs both.
3. **Page watches** (`watch: page:<path>`) — *"the compiled record I judge."* When the watched
   compiled page recompiles, the flag carries **that compile's citation delta** (added /
   removed / invalidated — the transcript computes it anyway), so the author sees exactly what
   moved under the record, not merely that it moved.
4. **Tombstone / hard-forget** (§10) — cited evidence being erased produces a **redaction
   flag** with a duty attached: the author must act, because the system never rewrites an
   author's words, even to forget.

Then the **delivery fork**: each flag either *queues* for the author, or — where a dispatch
subscription is bound — the driver *invokes* the owning agent's workflow (debounced per
subscription, delta-carrying payload). The receiving workflow reads the delta plus the
compiled context, revises the authored page, and commits; the driver's next pull syncs the
updated frontmatter, and the flag resolves. That is the entire "colleagues keep the gap
analysis current" loop with agents as the colleagues.

**The asymmetries, side by side** — these four lines are the design's trigger semantics in
miniature:

| | Compiled | Authored |
|---|---|---|
| system's right | owns the body → **regenerates** | informs the owner → **notifies** |
| coverage comes from | **computed** (the rule's candidate set is exhaustive by construction) | **declared** (only as good as `cites:` + `watch:`) |
| on deletion | heals itself (recompiles without the evidence) | acquires a duty (redaction flag) |
| failure mode | stale-but-consistent page | unread flag |

**Acyclicity — why trigger loops cannot form.** Triggers flow one way: plane E → compiled
pages → authored pages. A compiled page can never depend on an authored page's content
(writers compile from E-plane evidence and child *compiled* summaries only), and the only
path from authored content back to plane E is **explicit ingestion** (the §9 promotion loop,
D42-stamped). So runaway ping-pong — a recompile flagging an author whose edit re-triggers
the recompile — is structurally impossible, not merely debounced away. Implementers should
treat "compiled never consumes authored" as an invariant, not a convention.

**Two mechanical guards this analysis adds:**

- **The declaration lint.** An authored page with zero citations *and* zero watches is
  invisible to every channel above — it will go silently stale, which is the exact disease
  this plane exists to cure. The driver therefore raises a standing review item on any such
  page at frontmatter-sync time: *"this page has declared no ground — it can never be
  alerted."* Cheap, mechanical, catches the failure at creation.
- **Reader-facing flag visibility.** Open flags reach the *author* (queue or dispatch), but a
  *reader* — an agent loading the to-be before acting on it — must also be able to see "this
  page has N unresolved evidence-change flags," or agents will plan against commitments the
  system already knows are shaky. The body is untouchable (D46) but the flag state is
  queryable; it must surface on at least one read path — the P3 `_index.md`, the retrieval
  API's page metadata, and/or a driver-owned status sidecar. Which surface(s) is an open
  choice (§11 spike 9); *that* it surfaces is design.

## 6. The compile cycle

Triggered by the D12 debounce window ("N changed evidence items or T minutes"). One cycle:

1. **Pull** the repo (pick up human commits: authored pages, sidecars). Sync authored
   frontmatter (`cites:`/`watch:`) to Postgres; quarantine any direct edits to compiled bodies.
2. **Route**: consume queued evidence events → `knowledge_rule_keys` lookups →
   re-materialize derived rule keys where needed → stale set (compiled) + review flags
   (authored) + debounced dispatch batches (subscriptions, §5) + orphan aggregates.
3. **Plan** (only when structural triggers fire): planner emits `knowledge_plan_decisions`;
   auto-apply the low-blast-radius band, queue the rest for review.
4. **Compile** stale pages in dependency order — the scope's shared model page first if stale,
   then children before parents (parents consume child summaries), the root index last, once.
   Writers run in parallel across disjoint pages (Cloud Run jobs, D12 retry/DLQ semantics); per
   page, the driver renders the deterministic fact-sheet band and the writer produces the LLM
   band (§5) — fact-sheet-only pages skip the writer entirely.
5. **Validate & commit**: citations resolve, exclusions honored, internal links resolve to
   existing artifacts; one commit for the cycle; two-phase against Postgres (record compilations
   `pending` → push → mark committed; reconcile HEAD on startup).

A failed writer job leaves its page at the previous version — stale but consistent, retried
next cycle, dead-lettered after the D12 retry budget. There is no partial-page state.

**Walkthrough.** A memo lands: *"Bob joined Acme as CFO; Alice departed."* Plane E (unchanged)
extracts claims, resolves Bob/Alice/Acme, inserts `(bob, works_for, acme)`, and supersession
caps `(alice, works_for, acme)`. Routing, all SQL: the new relation carries keys `bob` and
`acme` → the Acme page's `entity` rule matches (**stale, cause 1**); the capped Alice relation
is cited by both the Acme page and Alice's profile (**both stale, cause 2**); Bob matches no
rule → orphan count (enough Bob facts and the planner proposes a Bob page). Next window:
three writers recompile three pages from current evidence; the team page keyed
`works_for → acme` also caught cause 1; the topic index and root recompile last because child
summaries changed.

## 7. Quality at scale — coherence, completeness, and where the intelligence lives

Per-page compilation raises a fair objection: locally fine pages, globally incoherent scope
(inconsistent terminology, duplicated coverage, missed cross-cutting insight). Four mechanisms
answer it:

- **The shared model page.** Each scope maintains one page (compiled or authored) holding its
  conceptual model: vocabulary, the domain's shape, naming conventions (for a migration scope:
  the system landscape and glossary). It is a declared input of *every* writer in the scope
  (in `inputs_hash` — it compiles first, everyone consumes it). One vocabulary, one model,
  hundreds of pages. It should be small and stable: when it materially changes, dependent
  pages legitimately recompile — that is correct semantics, priced consciously.
- **Parents synthesize.** A parent page compiles after its children and sees their summaries —
  cross-child insight ("three modules all depend on the same legacy table") lives at the level
  that can see across, and can pull cross-child evidence directly.
- **Writers may be agents — the rule is a completeness floor, not a ceiling.** Nothing
  restricts a writer to its pre-hydrated bundle: for high-stakes scopes the writer explores
  the memory freely (a stock harness session — see "The writer runtime" below). The rule
  guarantees the *floor* — every matching evidence item verifiably reached the compile
  (candidate set recorded, uncited items counted) — and citations record everything used,
  floor or beyond. The contract is only: one owner per page, recorded inputs.
- **The reflection pass.** A periodic LLM job reads across the compiled tree plus health
  metrics (orphan volume, staleness distribution, page sizes, uncited-candidate rates,
  navigation dead-ends) and proposes structural changes — repo-wide noticing, landing as
  recorded `knowledge_plan_decisions` instead of anonymous edits. It should run as a
  **different agent/model than the planner** — fresh eyes challenging the tree, not the
  proposer grading its own work.

### The writer runtime — a stock harness, default tools, validated at the boundary

The writer runtime is deliberately simple: **a stock Codex/OpenCode session with its default
tools** — shell, file reads, file writes — and **no custom tool integration, no instrumented
retrieval layer**. This works because consumption is already harness-shaped (D51): the
memory's read surfaces are the read-only mounts (P3 tree, artifacts, K checkout) and the
memory CLI, both reachable from a default shell; the consumption skill teaches them; and
every reachable object carries its evidence ID (stub frontmatter, CLI results — a D50
requirement), so citing is *copying an ID it already has*, never reconstructing one.

**One runtime, a policy knob.** The driver always prepares the same per-page working
directory — `bundle/` (the pre-hydrated rule-matched candidate evidence, IDs inline), the
curation sidecar, child page summaries, the scope's shared model page, the previous page
version, and instructions rendered from the registry (the D15 pattern) — and always launches
one session in it. "Bundle mode" vs "agent mode" is an instruction + model-tier choice per
page, not two runtimes: a leaf entity page gets *"compile from `bundle/` only"* and a small
model; a high-stakes scope page gets *"explore the memory freely"* and a frontier model.
(Fact-sheet-only pages skip the writer entirely — §5.)

**The contract is enforced at the boundary, not inside the session:**

- **Write surface.** The driver consumes only the declared outputs — the page body, the
  citations with roles, the page summary, optional suggestions — as files in the working
  directory; anything else the session wrote is discarded. "One page per writer" is enforced
  by *what the driver commits*, not by restricting the agent's editor.
- **No internet.** The sandbox has no network beyond the memory surfaces: a page must never
  smuggle un-ingested evidence past the spine (the §9 promotion loop is the door for new
  content, D42-stamped).
- **Read-only memory.** The mounts and CLI are read-only (D9 — reads never trigger).
- **Budget and timeout per invocation**, metered to `cost_ledger` like every worker (D12).

**Why un-instrumented reads are safe — validation lives on outputs, where it already was.**
The completeness floor is computed by the driver independently of the writer's behavior
(candidate set recorded, uncited candidates counted); citations are validated mechanically
(IDs must exist, excluded IDs rejected); faithfulness is a sampled audit by a different model
family (D53). An instrumented tool layer would add a log of what was *seen* — but a writer
can mis-cite even with logged reads, so the audit is required either way; the instrumentation
buys almost nothing and costs a custom tool stack to build, version, and keep in lockstep
with the harness.

**The session transcript is the residual audit log.** Harness sessions produce transcripts
for free; the driver archives each writer session's transcript (GCS) and stamps
`session_transcript_uri` on the compile row (`knowledge_compilations`, schema §11). "What did
the writer actually look at beyond the bundle?" stays answerable after the fact — the D33
spirit, at zero instrumentation cost.

**Review without a human in the loop.** The design's gates — the blast-radius review band,
`authored_review` flags, quarantine triage, handover confirmation — require an **accountable
decision point outside the automatic path**, not specifically a person. In agent-operated
deployments (the norm for the named targets), the review-band consumer is a designated
**reviewer agent**, authored pages are owned by the operating agents that wrote them, and the
user is *notified, never consulted*. This is safe to run because structure is revertible by
construction (append-only plan decisions + git history + snapshots), so a wrong verdict costs
a revert, not a loss. The residual risk — agents reviewing agents, the same family as D42's
self-confirmation concern — is accepted and priced: every decision carries its trigger and
rationale, every compile its inputs and citation deltas, so a human can always **audit after
the fact**. The human's realistic role in these deployments is auditor, not operator.

The **semantic linter** survives, demoted from load-bearing to quality assurance: it no longer
detects staleness (that is mechanical now); it checks prose — cross-page contradictions,
broken narrative, tone drift — and files findings as review items or recompile requests.

**Evaluation (O6, D22 pattern).** Plane K gets its own eval surface: *writer completeness*
(planted-fact canaries — a claim matching a page's rule must appear or be counted uncited),
*citation faithfulness* (sampled audit that the page's statements are supported by its
citations — the D32-layer-4 pattern applied to K), and *staleness latency* (evidence-change →
recompile lag against the configured cadence).

## 8. Core principles and stances are authored K2 content (D73)

A personal or organizational operating principle — "prefer simple codebases", "preserve an
audit trail", "optimize for reversibility" — is a chosen normative commitment. Corpus
redundancy, evidence counts, and contradiction state may inform it, but cannot establish who
endorses it. Therefore principles live as **authored pages in a K2 purpose scope**, not as a
machine-compiled belief tier.

A typical personal-memory scope might contain an authored shared model page, a tiny
`principles/` subtree, and compiled `project-patterns/` pages. The compiled pages summarize
recurring experience across projects and may produce inert suggestions. Promotion into the
principles set is explicit: an accountable human or authoring agent creates, changes, or
retires the authored page through ordinary git flow. The driver never promotes a suggestion
or rewrites the principle body.

Principles still participate fully in the K contract:

- `cites:` records the decisions and experiences the principle rests on;
- `watch:` and page watches connect it to relevant projects and compiled summaries;
- changed evidence raises a review flag or dispatches its owner, never changes its words;
- the scope's shared model page defines the vocabulary and relationship among principles;
- no numeric stance score is inferred without a separately measured, explicitly authorized
  design.

Evidence-qualified current facts remain E3 relations/observations. Ordinary compiled K1/K2
pages synthesize them with freshness disclosure; they do not become a second belief authority.
The former K3 proposal is removed, not deferred. Selectivity and distillation alone do not earn
a new tier, and a future distinct mechanism requires a new concrete use case and decision.

## 9. Worked example — a migration scope (as-is / to-be), and the promotion loop

The data-migration deployment (registries §1) tracks the **as-is** state of an enterprise
system landscape and designs the **to-be** state. One scope, two subtrees — and the split
between compiled and authored does **not** fall on the as-is/to-be line. The line is
*attested vs. being-created* (§4): future-state facts that sources attest are evidence like
any other; only content no source yet attests must be authored.

- The systems extension pack (registries §4) makes the landscape *entities*:
  `System`/`Module ⊂ Product`, `BusinessProcess ⊂ Concept`, predicates `uses` (core since D64,
  inherited by the pack's subtypes), `depends_on`,
  `part_of`. Workshop notes, emails, and specs from different people all resolve onto the same
  module entities — that is plane E doing its job.
- **`as-is/` is compiled.** The ordering-subsystem page holds an `entity_subtree` rule on the
  subsystem entity; module pages hold `entity` rules; the scope's shared model page holds the
  landscape overview and glossary. Every workshop note that mentions module X mechanically
  reaches module X's page (the completeness floor — for a migration, "no interface silently
  missed" *is* the quality bar), and every statement hydrates to claims to source documents
  (the audit bar).
- **`to-be/` is mostly compiled too.** The future state is *attested*: workshop minutes,
  decision registers, and ratified design documents assert it. "Orders flow through the new
  ESB from Q3" is a claim like any other, carried by the Work pack's `Decision` entities
  (registries §4 — a decision is a fact that holds until reversed) and future-dated validity
  windows (D41 intervals are just windows; nothing requires them to be past). A later workshop
  revising a decision is **ordinary supersession** — "what was the standing decision on X as
  of March?" is an as-of query — and the to-be pages recompile as decisions move. This is why
  the most-important-to-track content belongs on the compiled side: *tracking is what compiled
  pages do.* The authored-only alternative fails visibly: fifteen workshops in, an authored
  target-architecture doc drowns in review flags while someone manually merges every change.
- **Only the drafting front is authored.** The target design *being written* — commitments no
  source attests yet — is an authored page citing the as-is evidence it stands on, with watch
  rules on the entities it maps.
- **The promotion loop** closes the two: **draft** (authored) → **ratify** → **ingest the
  ratified document as a source** (D42 stamps it system-originated, so it never inflates
  external evidence counts) → its statements become claims / `Decision` entities / future-dated
  relations → the compiled `to-be/` pages **absorb it automatically** → the draft page is
  retired or handed over (`convert_kind`, §4). Authored is the *workbench*; compiled is the
  *record*.
- **The ground shifts**: a late workshop note yields the claim "module X *also* writes table
  B." Plane E records it; routing marks the module X as-is page and the affected compiled
  to-be pages stale (recompiled next window) — and any still-authored draft citing the old
  fact gets a **review flag** routed to its author (in these deployments, the operating
  agent — or its dispatch subscription invokes the authoring workflow directly, §5): *a
  commitment on this page rests on evidence that changed.* Nothing is silently rewritten and
  nothing goes silently stale. A colleague's evolving **gap analysis** is the same pattern —
  an authored page watching the compiled as-is and to-be pages it judges, its owner dispatched
  with the delta when they move.
- **Structure without a human.** The scope's tree (an `as-is/` subtree mirroring the system
  landscape; a `to-be/` decision-log + target-architecture layout) is planner-maintained
  state, seeded and periodically challenged by the reflection/reviewer agent (§7) — recorded
  plan decisions end to end; the human appears only in the audit trail.

## 10. Deletion and hard-forget

The deletion cascade (requirements; E0 §2) reaches plane K mechanically through citations:

- **Delete a document** → its testimony currency ends and current support changes (the E-plane
  cascade retains audit history) → reverse lookup through `knowledge_artifact_evidence` before
  scrub → affected **compiled** pages recompile without the removed evidence; affected **authored**
  pages get review flags; pages whose rules now match nothing become planner retire proposals. No
  tombstone guesswork.
- **Hard forget (GDPR)** additionally requires erasing the *text* the evidence produced. New
  compiled bodies regenerate clean, but **git history retains prior page versions** — so the
  K repo's hard-forget mechanism is a history-erasure step (rewrite/squash of the affected
  paths' history, e.g. `git filter-repo`), scoped by the citation index to exactly the pages
  that ever cited the forgotten source. D74 makes those artifact IDs part of the portable
  content-free manifest, so a restored K remote is erased again before serving; backup schedules
  and physical expiry belong to the operator/cloud under D60. Authored pages and compiled-page
  curation sidecars must be owner-redacted before the forget manifest is accepted — the system
  reports exact blocking paths and never rewrites an author's words. See
  `hard_forget_design.md` for the ordering and restore gate.

## 11. Consequences, residuals, and spikes

**What this buys** (mapped to standing requirements): "refreshed incrementally, never
globally" becomes exact (the stale set *is* the refresh set); authored principles remain
explicitly owned while citations and watches keep their ground visible; the deletion cascade reaches K mechanically; per-page
freshness metadata exists; the K half of the "serial git bottleneck" risk is removed
structurally (one committer, disjoint writes, DAG order).

**Residuals, honestly named:**
1. **Planner quality is the new load-bearing judgment.** A bad tree is a bad tree — but it is
   inspectable, append-only state (reviewable, revertible), not emergent session behavior.
   Blast-radius gating (D24 pattern) bounds the damage of any single decision.
2. **Rule-kind coverage.** The closed kind set must express how real scopes define their
   pages; `manual` is the escape hatch and new kinds are additive registry-style changes. If a
   scope's pages routinely need semantic membership, that is a signal to improve plane E's
   keys (an ontology extension), not to add description-matching to routing.
3. **Hub pages.** A mega-entity's candidate set (the user in an assistant deployment; the
   company in the agency) can exceed a writer's context: the rule needs ranking/budgeting
   (evidence-count-ordered top-N with an explicit "and N more" ledger) — the same hub problem
   observations handle, solved the same cheap-first way.
4. **Shared-model-page churn** recompiles its scope; keep it small, stable, and mostly
   authored.

**Spikes (measure before locking numbers):**
1. Rule-kind coverage on a real scope (drive the planner over an actual corpus slice; count
   `manual`-rule frequency — high frequency = missing kind or missing ontology key).
2. Planner blast-radius bands (auto-apply vs review thresholds; like D24's
   `expected_impact` middle band).
3. Writer completeness + citation faithfulness eval (canaries; sampled audits) — joins the
   E2/E3 harness (`questions.md` #14) as one eval surface.
4. Compile-cycle economics at scale (dirty-pages distribution per debounce window; hub-page
   budgets; shared-model-page recompile blast radius).
5. ~~Git-history erasure + restore non-resurrection mechanics for hard-forget~~ **DESIGNED by
   D74** — affected paths are erased from every reachable ref and restored remotes replay the
   portable manifest before serving; WP-7.5 supplies the executable canary. Provider backup
   rotation remains an operator/cloud concern under D60.
6. **Future-state extraction** for migration-style scopes (§9): decision-language →
   `Decision` entities + future-dated D41 windows, and how planned flows normalize
   (future-dated `uses`/`depends_on` relations vs Decision-mediated) — measure on a corpus
   slice; gates how much of a `to-be/` subtree can be compiled.
7. **Dispatch semantics** (§5): per-subscription debounce windows, payload size caps,
   at-least-once delivery + consumer idempotency, dead-letter policy for failing subscriber
   workflows — measure with the first agent-operated scope.
8. ~~**Reader-facing flag surface**~~ **RESOLVED (D49)** — two surfaces: the retrieval
   **response envelope** carries each consumed K page's `compiled_at` + staleness + open-flag
   count, and P3's generated `_index.md` mirrors the same per page for the browse path. The
   status-sidecar option is dropped (a second mutable state to keep honest, for no third
   consumption mode). See `retrieval_design.md` §5.

## References

Decisions: **D45–D47, D73, D98** (this design), D1, D12, D16, D24, D33, D42, D43 (`decisions.md`).
Objections resolved: O2, O4 (`plan/analysis/objections.md`). Review that motivated it:
`plan/analysis/design_review_2026_07.md` (F1). Schema: `postgres_schema_design.md` §11.
Adjacent designs: `overall_design.md` §5, `registries_design.md` (scopes, extension packs),
`e0_files_design.md` §2 (deletion), `p2_graph_design.md` §6 (global analytics removed).
