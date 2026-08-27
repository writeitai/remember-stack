# Entity identity, type, and retrieval — analysis

**Date:** 2026-08-26
**Status:** analysis (the *why*). Binding how is
[`entity_identity_and_retrieval_design.md`](../designs/entity_identity_and_retrieval_design.md).
Decisions: D95–D97.
**Not LoCoMo-first.** A live LoCoMo store supplied symptoms. The operator’s
constraint is real-world identity (ERP, homonyms, person-as-employer). If
identity and retrieval are right, benchmarks follow.

This note is for a future agent or human who was **not** in the conversation.
It records the problem, the as-built failure, the operator constraints, the
peer-engine evidence, and why the design says what it says.

---

## 1. How this started

A live LoCoMo v14 conversation (`conv-47`, James/John) was dumped from
Postgres: 31 documents, 229 chunks, 1427 claims, 85 entities, 85 aliases
(one per entity — no extra surface forms), 905 observations, 70 relations.
Junk entities included `game`, `App`, `System`, `Card`, plus cousins
(`Application`, `Gameplay`), protocol chrome (`Adapter`), and design-id
leakage (`D17:32`). Thirty-four of 85 entities were typed `Product`.

The operator’s reaction was not “tune LoCoMo.” It was: **are those four
nouns one thing or four?** And then the load-bearing question:

> An entity has a name and a type. Does duplicating on type make sense
> when SAP is software, org, and concept in successive sentences? In ERP
> people use company and product names interchangeably; lookup should
> often treat them as the **same retrieval identity**. Two people can
> share a name (a man and his father); type is Person for both, so type
> cannot split them. A description (“lives in Prague”) could. The current
> system cannot handle that. **Entity identity is the key to system
> performance.**

A later constraint, equally important:

> Someone could be working **for me**. I should not have to be tracked
> twice — as Person and as Company — while still talking just about me.

The north star, stated late and then used to rank everything:

> The whole reason we do this is so retrieval works like magic and does
> not fall over on each request. Take full advantage of the system (and
> its simplicity).

Do not treat this analysis as architecture. The design and D95–D97 bind.

---

## 2. Two problems wearing one trench coat

### 2.1 Metonymy — one name, closely related uses (SAP)

“SAP announced a patch” (the company acts) and “we run SAP” (the software
is used) share a spelling. In daily ERP talk they are often **the same
lookup**. Splitting them means every search must union two nodes.

When the product has its **own** name (S/4HANA, SuccessFactors), that is
a **second referent** with a second name, connected by a relation
(`created`, `part_of`, `uses`) — not because a type differed.

### 2.2 Homonymy — one name, genuinely different people (father/son)

Jan Novák and his father Jan Novák are two people. Fusing them poisons
observations, profiles, forget, and every later answer. Type cannot
split them (both Person). Description/context can, **as evidence a judge
sees**, never as the lookup key. Keying identity on “the Jiri in Prague”
forks the person when they move.

### 2.3 Dual role — one person, several hats (works-for-me)

“Someone works for me,” where I am one human, is not a hallucination.
The legal entity, if it exists and has its **own name** (Acme s.r.o.),
is a second referent. If it does not, it is still **one id**. Forcing a
Company twin, or an Organization hat the person does not claim, is the
same identity bug as three SAPs.

These three are not one lever. Type-as-key makes (2.1) and (2.3) worse
and does nothing for (2.2). Exact-name-as-verdict makes (2.2) impossible
and happens to glue (2.1), which is the desired SAP-shorthand behavior
only when the spelling really is one referent.

---

## 3. As-built (what the code actually does)

Pinned checkout used during the conversation:
`/Users/jpuc/code/moje/remember-stack` at `d9e44a37` (later `main` may
have moved; the *mechanisms* below were observed in
`workers/e3.py`, `spine/resolver.py`, `model/relations.py`,
`core/core_manifest.py`, `surfaces/graph_queries.py`, migrations
`p0_02_0003` / `p0_02_0004`).

### 3.1 Tables (high level)

```
claim  ──E3──►  EntityRef {name, type}
                    │
                    ▼
              CascadeResolver.resolve()
                    │
     ┌──────────────┼──────────────────┐
     ▼              ▼                  ▼
mentions       resolution_decisions   entities (+ aliases)
(naming event) (mention → entity_id)  (the identity)
     │              │
     │              └── entity_id ──► relations / observations
     │
     └── mentions have NO entity_id
         evidence tables have NO mention_id and NO type
```

A **claim** is the durable sentence. A **mention** is “this claim used
this name, classified as this type.” A **resolution decision** is “that
mention is this `entity_id`.” An **entity** is the canonical referent.
A **relation** is a fact between two ids. An **observation** is a fact
about one id. **Evidence** is “this claim supports or contradicts this
fact.”

Mentions are **not** a pre-filter and **not** a candidate list.
Candidates come from `aliases` (T0/T1/T2) and the entity vector index
(T3). Mentions are written **as** we resolve. Junk mentions (`game`)
are extract proposing junk names, then faithfully logged and often
minted.

### 3.2 T0 is the silent merge

`_T0_EXACT` matches `aliases.normalized_lemma` only, confidence 1.0,
stop. Type is ignored. Same-name father and son never reach T4.
`judge_pair` (the golden-pair harness) also returns true if lemmas
match, so eval cannot see same-name non-matches.

Production `_t4` already passes mention type and candidate type. The
judge is not the homonym bottleneck. T0 is.

### 3.3 Type is first-mint-wins, not a vote

Binding text (`registries_design.md` §4) says canonical type is a
majority/highest-confidence vote. Code: `_INSERT_ENTITY` copies
`EntityRef.type` once. Nothing later updates `entities.type`.
`mentions.type_confidence` exists and is not written.

`EntityRef` is `name` + `type`, both required. E3’s prompt forces a
class from the registry. D86 retries then drops illegal strings. That
is why `game` is typed `Product`: the model must pick something.

### 3.4 D18 is not an independent check

`works_for` is Person → Organization only (`core_manifest.py`).
`reports_to` is Person → Person. `located_in` is Organization | Place
| Event → Place (a **person** living in or traveling to a place is
illegal as a relation). `uses` wants a Product object.

Pre-resolve `_signature_allows` uses the **same JSON** that proposed
the edge. The model grades its own homework. Post-resolve uses stored
`entities.type` (first mint). Daisy typed Person, Adapter typed
Product: stale class, not truth.

“Person cannot `works_for` a Person” was briefly offered as a reason
to keep types. It is the dual-role bug: drop the fact, or mint me
twice.

### 3.5 `other:traveled` is expected

Sixteen governed predicates plus `other:<snake>` (D5). There is no
`visited`. `located_in` forbids Person as subject. So “James traveled
to Italy” is either:

- an **observation** (one subject — the person; Italy is words in the
  statement; **not** a graph neighbor), or
- a **relation** `(person, other:traveled, Italy)` — both names become
  mentions and entities; a hop can land on Italy.

The strict set did not *miss* travel. It **pushed travel out** of the
guest list. Frequent `other:` is the bouncer reporting that everyday
sentences do not fit. Retrieval that must guess `other:traveled` vs
`visited` vs an observation falls over. Retrieval that walks any edge
and matches fact text does not care.

### 3.6 Neighborhood already walks without a predicate

`GraphQueries.neighborhood(entity_id, hops, predicates=())` omits the
predicate filter when the list is empty. P2 stores every relation as
`RELATES`; the verb is a property. You hop by **id + hop count**.
Passing `related_to` would *only* hit edges literally named
`related_to`. Registry `parent_predicate=related_to` is bookkeeping,
not the hop. **Observations are not on that walk.**

`profile_summary` is designed as T4 candidate context and is unused in
the production prompt. T3 at mint embeds the surface name, so two
people with the same name have the same vector. `generic_identifier_guard`
exists (D21); the resolver does not populate it.

---

## 4. Peer engines (what we actually opened)

Disposable checkouts under UMC `.research/entity-memory-engines/`
(2026-08-26): Mem0 `39bc023`, Hindsight `c2486a2`, Graphiti `993e081`,
Cognee `690c0ec`. Independent Fable and Codex reviews live in the UMC
analysis corpus; they corrected one survey error (production T4 already
sees both types; T0 is the silent merge).

| Engine | Identity | Type | Relations | Retrieval implication |
|---|---|---|---|---|
| **Mem0** | normalized text | syntax class, not ontology | not a typed graph | cannot split homonyms |
| **Hindsight** | `(bank, lower(name))` unique; **no ontology type** | optional labels on **facts** | no entity–predicate–entity vocabulary. Memory units are English facts. Links between facts are temporal/semantic/causal. Co-occurrence is untyped | search fact text, expand via shared entities. Reader never names `works_for` |
| **Graphiti** | UUID | `labels[]` on one node | Neo4j walk type always `RELATES_TO`; each edge has a `name` and a **fact sentence**. Optional FACT_TYPES | hybrid over name+fact, then hop `RELATES_TO*` without guessing the verb |
| **Cognee** | name-only identity fields; same-name in one extract graph **forks** | type is a separate `is_a` node | free-form `snake_case` Wikipedia-like links | vectors + graph; no closed predicate registry |

Graphiti as shipped is **weaker** than “wear every hat”: after merge it
promotes a generic label and **drops** a later conflicting specific
(Person already there → incoming Organization discarded). That hides
“also an employer.” A RememberStack hats table that **adds** labels is
not a Graphiti copy; it is more permissive, which dual-role needs.

None of them put relatedness on the entity row. None of them make
`(name, type)` the unique key except GraphRAG-shaped forks (Cognee in
one extract graph; older GraphRAG `groupby(["title","type"])`), which
RememberStack already rejected.

---

## 5. Why the design says what it says

**Identity is load-bearing; type is not.** Wrong `entity_id` → every
observation, relation, hop, and forget is attached to the wrong world
object. Wrong type, if type is not the key and not a hard gate, is a
bad filter. The operator’s ERP and homonym experience matches this.

**Exact name cannot be a verdict.** Otherwise father/son is impossible
before any description can help. Profile-in-T4 without breaking T0 is
theatre. Distinctive rare names can still auto-merge when nothing
fights them (that is how SAP shorthand stays one id without a type
union).

**Mentions stay; mention type does not.** The table is the right diary.
Requiring a class creates an error space (`game` as Product, SAP as
whatever the first sentence said). `emitted_type` is the same E3 field
copied onto the row; there is no second classifier. Linking does not
read it.

**D18 must not mint a second me.** Dual-role facts are true. Mechanical
Person→Organization is ontology taste (`reports_to` already allows
Person→Person). Widening signatures forever is the wrong fix; failing
open when hats are missing, and allowing a person as `works_for`
object, is the identity-preserving rule.

**Retrieval must not speak ontology.** Default: resolve names to ids,
load observations **and** relations, hop any edge (already implemented
if you omit `predicates`), match fact text. Governed predicates are a
narrowing filter (“who reports to X”), not the way every question
starts. Do not collapse every edge to unlabeled `related_to` with no
fact sentence — that makes one hop a hairball. Graphiti kept the fact
sentence; Hindsight kept the fact as the unit of recall.

**No entity types (operator chose C, same day).** Hats on the id, hats
on facts, and required classes all lost. “Bank” / “based in Italy” are
observations; the profile is a cached projection of those facts for
T4/T3 and for “list banks” as text. Facets, if ever, derive from
observations — they do not bring `entities.type` back. Identity work
is still T0 + profile (D95); dropping types is not a substitute for
that.

**T0 never auto-merges (same-day revision).** Exact lemma only lists
candidate ids. A “distinctive vs common-name list” shortcut still
glues the second `Jan` unless a huge stoplist exists, and turning that
shortcut on for a *large* corpus is backwards (more collisions). Scale
path: T3 on mention+claim vs **profile** (repeats of James, no LLM).
T4 when profile is empty, fights, or several Johns exist. No
thousand-name census. The Case A/B walk and the default-off exact-hit
idea are §5.1.

**Do not optimize extract for LoCoMo categories.** Bare-noun refusal
helps every corpus. Father/son and person-as-employer are the tests.

### 5.1 T0 never decides — distinctive names, common names, and a default-off exact-hit flag

Operator follow-up (same day, after D95 bound T0 as candidates): **T0
never auto-merges is the default; write it down.** Then: could the old
exact-lemma auto-accept still exist as a **manual** switch, off unless
someone turns it on after a large memory already has many entities?

This section is the *why* of that conversation. Binding remains D95
(T0 lists, never merges). The flag is an **unchosen proposal**
([`optional-exact-t0-accept.md`](../../design/proposals/optional-exact-t0-accept.md)),
not WP-I.5 work.

#### Two cases that look different and are not

**Case A — the lemma looks distinctive.** The store currently has one
id whose cleaned spelling is `sap`, `x æ a-12`, or a rare surname. The
next mention with that spelling *feels* like a repeat. Old T0 accepted
at confidence 1.0. That is safe **only until** a second real-world
thing uses the same string. Uniqueness is a property of **this table
right now**, not of the name. The first collision of a previously
unique lemma is the exact moment auto-accept is fatal, and it is
silent: no T3, no T4, no profile, father/son glued.

**Case B — the lemma is common or already collides.** `Jan`, `John`,
`James`, two employees, a man and his father. Type cannot split them
(both people). Exact lemma cannot split them. Old T0 never lets
profile or a judge speak.

A “distinctive lemma auto-accepts; common names escalate” shortcut is
Case A plus a stoplist for Case B. It does not scale:

- The stoplist is thousands of given names, locale-dependent (`Jan` is
  ordinary in Czech, unusual in some English stores), and still misses
  the second `James` until he exists.
- Case A becoming Case B is the interesting event. The stoplist never
  contains a name the first time a collision appears.
- Maintaining the list is a census of the world. The resolver would
  rather not.

**T0 never deciding** handles both cases with one rule. Zero
candidates → mint. One or more → those ids are candidates; T3 may
accept a **repeat of a known profiled person**; T4 when the profile is
empty, fights, or several Johns exist; mint another id if T4 says
different. No census. Father/son can reach T4. A thousand mentions of
the same James are embeddings, not judges.

#### Why “enable exact-T0 after a large corpus” is backwards

The intuition: once many entities exist, most new mentions are repeats
of names we already have, so exact-hit is a cheap cache.

What actually happens as the table grows:

- **More collisions, not fewer** (birthday paradox). A small diary
  might have one Jan. A large CRM has several. Enabling auto-accept
  *because* the store is large is enabling it at the peak collision
  rate.
- **Repeats of a known person are already cheap.** That is T3
  (mention+claim vs profile), not T0. The cost T0 would save vs T3 is
  one embedding lookup. The cost of one false merge is every later hop
  and forget attached to the wrong id.
- **Empty-profile cold start is worse on a large store, not better.**
  The second Jan still has a thin or empty profile the first time he
  appears. T0 would glue him to the first Jan without looking.
- A default-off flag that exists in production **will be flipped**
  under T4 cost pressure. The first silent glue has no eval alarm
  unless `judge_pair` already treats lemma equality as a non-match
  (D95 / WP-I.3) — and even then the store is already wrong.

So: keep exact-lemma auto-accept **out of the default cascade**. Do
not auto-enable it at an entity-count threshold. If a later operator
still wants a switch, the adoption trigger is **not** corpus size.
The honest triggers (closed unique namespace; identifier-shaped
strings such as LEI/email, which are a *different* T0) live on the
proposal. WP-I.5 does not ship the flag.

---

## 6. Operator constraints (do not drop)

1. Do not treat analysis as settled architecture (this file is why;
   D95–D97 bind).
2. Do not optimize for LoCoMo. Real-world identity first.
3. Entity mechanism is the key to performance.
4. Type must not fork SAP-the-shorthand or require a Company twin of
   a person.
5. Homonyms need profile/context, not type.
6. Relatedness is a relation, not a field on the entity.
7. Retrieval should not fall over guessing predicates.
8. T0 never auto-merges; cheap repeats are T3+profile.
9. Do not enable exact-lemma T0 because the store is large.

---

## 7. Alternatives rejected (and why)

| Alternative | Why it lost |
|---|---|
| `(name, type)` as identity key | Forks SAP; GraphRAG/Cognee-in-one-graph failure |
| Query-time union of all types named X | Makes the user speak ontology; retrieval already has hops |
| Identity = description | Moves city → new person; shared city → collision |
| Entity-level `related[]` | Relatedness is a dated fact with a predicate |
| Keep T0 exact as verdict; add profile | Profile never runs for same lemma |
| Distinctive lemma + common-name stoplist | Second `Jan` still glues; thousands of locale-dependent names; uniqueness is a table property, not a name property |
| Exact-T0 auto-accept, default-off, enable when the corpus is large | Birthday paradox: more collisions at scale, not fewer. T3+profile is the cheap repeat path. Flag kept as a proposal with a *different* trigger; not WP-I.5 |
| Drop types **instead of** fixing T0 | Homonyms still collapse — D95 remains required; D96 is additional |
| Optional M2M hats | Parallel ontology; “bank” is already an observation; default retrieval does not use kind |
| Hats on facts | Duplicate of predicates + observation text |
| Required class on every mention | Error space; first-mint poison |
| D18 pre-resolve on emitted types | Model grading its own homework |
| Unlabeled `related_to` only, no fact text | Hairball neighborhood |
| Delete the mentions table | Lose the naming transcript |
| Graphiti copy including drop-conflicting-label | Hides dual-role hats |
| LoCoMo-first extract prompts | Benchmarks follow identity, not the reverse |

---

## 8. Exact-tip retrieval-budget finding (2026-08-27)

The first WP-I.6 implementation gave `default_fact_context` one absolute
25-second PostgreSQL deadline and reapplied the remaining time as
`statement_timeout` during fact confirmation and graph expansion. Exact-tip
review found two holes before those guarded statements:

1. P1 readiness checks and semantic fact/profile nomination opened ordinary
   connections and ran without the operation deadline.
2. Fact-authority connections called `engine.connect()` before configuring the
   remaining statement budget. A saturated general pool could therefore wait
   outside the advertised 25 seconds.

The graph path already demonstrated the useful containment shape: a private
engine with no overflow plus application admission whose semaphore wait is
clamped to the caller's absolute deadline. Separate P1 and authority
semaphores over the same SQLAlchemy pool would not solve the problem: together
they could admit more work than that pool owns and reintroduce an unbounded
checkout behind either semaphore.

The final shape therefore uses one dedicated interactive-retrieval engine and
one shared bounded admission object for both P1 reads and fact authority.
`default_fact_context` passes the same monotonic deadline through P1 channel
checks, fact nomination, optional entity-profile rescue, anchor/neighbor
checks, and repeatable-read fact/evidence confirmation. Each admitted P1 and
authority transaction applies `statement_timeout` and `transaction_timeout`
from the remaining budget. Pool saturation becomes the existing typed
`boundary`; it never widens retrieval or falls back to an unguarded general
pool.

Rejected repairs:

- increasing SQLAlchemy `pool_timeout`: still independent of the operation
  deadline and makes overload slower;
- setting timeouts only after ordinary `engine.connect()`: bounds statements,
  not checkout;
- separate bounded P1 and fact pools over one engine: admissions can exceed
  physical capacity;
- sharing the worker/write engine: background load can consume the very slots
  intended to keep interactive reads bounded.

---

## 9. Exact-tip public-contract findings (2026-08-27)

The closure review of WP-I.6 found two public-identity consequences that the
implementation had not carried through mechanically.

First, D97 changes `fact_context` selection semantics and bounds: an anchored
current/point-in-time read now expands a bounded live-graph neighborhood,
accepts `hops` and `predicate`, and reserves one entity slot for neighbors.
`answer_context` inherits that changed fact child. Publishing both as version
1 would make pre-D97 and post-D97 traces claim the same contract identity even
though the same input can return different facts. The existing operation
version rule therefore requires `fact_context@2` and `answer_context@2`;
`resolve_entity@1` and `testimony_context@1` are unchanged.

Second, those descriptor changes alter the complete answer-tool catalog and
the `surface_manifest_hash`. A LoCoMo run retaining the `full-v14` name would
look comparable to the pre-D97 live-graph protocol even though its ordinary
fact recipe differs. The benchmark identity must roll to `full-v15`; the
dataset, model seats, call budgets, and 21-tool catalog size stay unchanged.
This is protocol bookkeeping, not authorization for a paid run.

The same review found an availability-contract hole: if the primary relation
or observation P1 channel is unpublished, `P1SearchUnavailableError` escaped
the assured operation as an HTTP 500. That state is expected during a policy
cut and must remain fail-closed, but it is not an untyped server defect. The
default recipe therefore maps primary-channel unavailability to the same typed
fact-context boundary used for bounded database/graph unavailability. The
optional profile channel remains an additive recall path and may still be
skipped when it alone is unpublished.
