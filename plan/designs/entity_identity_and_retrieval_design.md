# Entity identity and retrieval — design (binding)

**Status:** accepted as operator-directed 2026-08-26 (D95–D97). Dual
implementation review still applies before code lands; this document is
the *how*, not a phased MVP.
**Date:** 2026-08-26
**Decision log:** D95, D96, D97
**Analysis:**
[`entity_identity_and_retrieval_analysis.md`](../analysis/entity_identity_and_retrieval_analysis.md)
**Amends:** [`registries_design.md`](registries_design.md) §2 profiles,
§3 cascade (T0 as verdict), §4 “How an entity gets its type”;
[`e2_e3_claims_relations_design.md`](e2_e3_claims_relations_design.md)
E3 EntityRef / signature-before-resolve;
[`retrieval_design.md`](retrieval_design.md) default neighborhood;
[`postgres_schema_design.md`](postgres_schema_design.md) `entities.type`
NOT NULL and `mentions.emitted_type` as required extract law.
**Does not amend:** D2 (claims vs relations), D3–D4 (supersession), D5
(`other:` escape remains), D43 (observations stay untyped), D48
(hydration), D60/D61 (library boundary).

Write for a cold reader (CLAUDE.md Rule 1). Sequencing lives in
[`entity_identity_and_retrieval.md`](../plans/entity_identity_and_retrieval.md)
(Rule 2).

---

## 1. The problem in one page

The system must attach every fact to the **right real-world thing** and
then **retrieve** those facts without asking the caller to speak
ontology.

Three failures the previous cascade cannot handle at once:

1. **Metonymy.** “SAP” as vendor shorthand and “SAP” as the suite in
   the next sentence are often **one retrieval identity**. Splitting
   them on type makes every lookup union two nodes.
2. **Homonymy.** A man and his father share a name. Both are people.
   Type cannot split them. The current T0 exact-lemma match glues them
   at confidence 1.0 before any judge or profile can speak.
3. **Dual role.** “Someone works for me” is one person, not a Person
   entity plus a Company twin. Domain/range `works_for: Person →
   Organization` either drops the fact or mints a second id.

The reason any of this exists is retrieval: resolve names in a question
to **ids**, load facts about those ids, hop a small neighborhood
**without requiring a predicate**, match **fact text**. Wrong ids make
every hop wrong. Required types and custom verbs make every request a
query planner.

---

## 2. Decisions this design implements

**D95 — Identity is the referent.** One `entity_id` per real-world
thing. A name generates **candidates**. Exact lemma is not a merge
verdict when the name is common, two hits exist, a profile fights the
new claim, or the judge says different. The same spelling may be two
ids. A short profile is evidence the judge sees, never the lookup key.
Relatedness is a relation, not a field on the entity.

**D96 — Extract names; type is not identity.** Mentions are a diary of
names (string, claim, span). A class is not required to emit or resolve
a name. Optional **hats** (many types on one id, additive) may exist
for filters and optional signature checks. They never unique with the
name. Domain/range must not force a second id for dual roles.
`works_for` may point at a person. Signature checks fail **open** when
an end has no hat. D86 still applies when a hat *is* emitted and is not
in the registry.

**D97 — Default retrieval does not speak ontology.** The ordinary
question path is: resolve to ids → load observations and relations for
those ids → one-hop `neighborhood` with an **empty** predicate list →
match fact text (relation `fact_label`, observation `statement`,
claims). A governed predicate is an optional narrowing filter.
Observations are not graph neighbors; they are loaded as facts about
the id.

---

## 3. Identity cascade (amends D17 T0-as-verdict)

Keep T0–T4, block-loose / decide-tight, registry-self-contained (D20).
Change what T0 *means*.

### 3.1 T0 — strong candidate, not always a verdict

Exact match on `aliases.normalized_lemma` **lists** the matching active
entity (or entities — more than one exact hit is now possible).

Auto-accept that hit **only when all of these hold**:

- exactly one active exact hit in the deployment
- the lemma is **not** on `generic_identifier_guard` and is not a
  configured common-name / too-short token (golden-set-measured list;
  starting point: given names, tokens shorter than three letters,
  lemmas already flagged promiscuous)
- no stored profile that **contradicts** the new claim (T4 if a profile
  exists and the claim is not obviously about the same life)
- if hats exist on the candidate and the mention suggested a hat, they
  need not match — hat disagreement **escalates**, it does not split
  by itself

Otherwise escalate to T1–T4 (or mint if nothing blocked). Confidence
1.0 exact-merge is reserved for the auto-accept band above.

### 3.2 Same lemma, two ids

If T4 says the mention is **not** the candidate, **mint** a new
`entity_id` with the same canonical spelling and a new alias row. The
lemma advisory lock still serializes the race; it does not forbid a
second row. `resolution_exclusions` records “these two are not the
same” so T4 is not re-asked forever.

This is how father/son and two employees in different cities exist.
It is **not** how SAP-the-shorthand becomes two nodes: distinctive
brand lemmas auto-accept at T0 when a single hit exists.

### 3.3 Profile is T4 evidence (amends registries §2 as implemented)

`entities.profile_summary` is a short blurb maintained by the designed
profile refresher (debounced on evidence change). Until a profile
exists, T4 sees claim text + candidate name only.

Production `_T4_PROMPT` includes:

```text
MENTION: {name}
CLAIM CONTEXT: {claim_text}
CANDIDATE: {canonical_name}
CANDIDATE PROFILE: {profile_summary or "(none)"}
Same real-world entity?
```

Hats, if present, may appear as a hint, never as the question. T4
answers **same / not-same** only. It does not emit `related_to`.
Father/son, if the claim states kinship, is a **relation** written
after both ids exist, not a merge.

T3 compares the mention embedding to the candidate’s **profile
embedding** (name + summary), not the name-only vector stamped at
mint. Two people with the same given name must not share a vector
because they share a spelling.

City, job, and employer changes **update the profile**. They do not
change `entity_id`.

### 3.4 `judge_pair`

The golden-pair harness must **not** return true solely because lemmas
are equal. Same-name non-matches are first-class eval cases. Without
that, D22 cannot see the failure this design exists to fix.

### 3.5 Relatedness

Father/son, SAP SE ↔ S/4HANA, employer/employee: **relations** with a
predicate and valid-time. Not `related[]` on the entity. Retrieval
walks them (D97).

---

## 4. Extract and mentions (amends E3 EntityRef)

### 4.1 What a mention is

The `mentions` table stays: immutable transcript of “we tried to link
this name in this claim.” Columns that remain load-bearing:
`surface_form`, `normalized_lemma`, `canonical_name_form`, `claim_id`,
`chunk_id`, `doc_id`, spans when known.

`emitted_type` is **not required**. If written, it is optional
low-trust (“this sentence’s guess”) and is **never** copied onto the
entity as law. Prefer NULL until a consumer other than first-mint
exists.

Mentions are not candidates. Do not SELECT mentions to resolve a name.
Do not add `mention_id` to evidence tables.

### 4.2 EntityRef

E3 structured output for an endpoint is a **name** (canonical
nominative form). A hat/type field is optional. The prompt does not
require a class. Illegal hats, **when present**, still hit D86
(retry then drop that assertion; never coerce to `Concept`; never
auto-register).

### 4.3 Eligibility — do not mint filler nouns

The normalizer must not emit a name that is only a bare head noun
(`game`, `app`, `system`, `card`, `photo`, `module`, `the system`)
unless the claim qualifies it as a specific referent (FIFA 23,
James’s Unity strategy game). Prefer dropping the relation or
observation over inventing a referent. Protocol/boilerplate lines
(adapter banners, “X is a participant”) are the same class.

This is extract eligibility, not a new mentions subsystem.

### 4.4 Aliases

On mint **and** on match, write the surface form actually seen in the
claim as `provenance=source` in addition to `llm_canonical`. `App` and
`Application` can be one id. Do not alias `game` onto FIFA 23 because
they co-occur.

### 4.5 Promiscuous lemmas

The resolver **populates** `generic_identifier_guard` when a lemma
points at too many distinct entities (D21; starting threshold measured
on the golden set). Guarded lemmas stop driving T0 auto-accept and are
down-weighted in T1/T2 blocking.

---

## 5. Hats, not a required class (amends D18 typing-from-extract)

### 5.1 One id, optional many hats

Replace “exactly one NOT NULL `entities.type` written at mint” with:

- Identity does not include type.
- An entity **may** carry zero or more hats from `entity_types`
  (core + enabled extensions). Storage is a many-to-many
  `(deployment_id, entity_id, type)` table. Adding a hat does not
  mint a second id. A later mention may **add** a hat; it must not
  **drop** an existing specific hat silently (do not copy Graphiti’s
  “already specific → discard incoming”).
- Extract does not have to assign a hat. Hats may be derived later
  from observations (optional), review, or an explicit extract guess.

`(name, hat)` is **not** unique. That is the GraphRAG fork.

Display may pick a primary hat for UI; it is not identity.

### 5.2 Domain/range without a second me

`works_for` (synonyms `works_at`, `employed_by`, `employee_of`)
**allows object type Person or Organization**. “Someone works for me”
is one id. A separately **named** company is a second entity plus a
relation.

Pre-resolve signature checks on LLM-emitted classes are **removed**.
They were the model grading its own homework.

Post-resolve: if **both** ends have hats and **no** signature matches
at any ancestor, drop the candidate (re-derivable from the claim). If
either end has **no** hat, **allow** (fail open). Do not drop a true
edge because first-mint typed Daisy as Person.

`located_in` continues to describe places; a person’s travel or
residence that does not fit a governed signature is an **observation**
or `other:` / generic edge with a fact sentence — not a second entity
type. Do not invent a new core verb per `traveled`.

### 5.3 What we keep of D18 / D86 / D5

- The eight core types remain a **vocabulary of hats**, not a mint
  requirement.
- Sixteen governed predicates remain the **filter vocabulary** plus
  `other:<snake>` (D5). Retrieval does not require the caller to name
  them (D97).
- D86 remains for **emitted** illegal hats.
- `Concept` is still not a junk drawer.

### 5.4 Documented non-goal

Deleting the hats table and all signatures is a viable simpler
system (Hindsight-shaped). It is **not** this design. Adoption
trigger: the golden set in §8 shows hats never change a correct
same/not-same or a correct keep/drop of a relation that fail-open
would not have handled. Until then, hats stay optional.

---

## 6. Predicates, relations, observations

Relations stay `(subject_entity_id, predicate, object_entity_id)` plus
bi-temporal metadata and `fact_label`. Observations stay untyped
statements on one `subject_entity_id` (D43). Evidence stays claim →
fact. **No schema change** to those fact tables for hats or mentions.

Keep a **short** governed list for filters people actually use
(`works_for`, `knows`, `reports_to`, `part_of`, `uses`, …). Everything
else: observation (if you do not need a hop target) or a generic edge
with a fact sentence (if you do). Do not promote `other:traveled` into
core just because it is common; load it as fact text and, if both ends
are entities, walk it as `RELATES`.

Unlabeled `related_to` with **no** fact text is rejected: one hop
becomes a hairball and “who works here” becomes an LLM filter over
noise.

---

## 7. Retrieval (D97)

Ordinary question path (zero extra LLM beyond whatever `resolve` already
does on the hot path — T0–T3 only, D17):

1. `resolve` the names in the question to ranked `entity_id`s. Ambiguity
   returns ranked candidates, never a silent guess (existing S51).
2. `lookup` relations **and** observations for those ids (no predicate
   required).
3. `graph.neighborhood(entity_id, hops)` with **empty** `predicates`
   (already the code default: walk every `RELATES` edge). Cap hops
   (starting point: 1 for ordinary questions, existing default 2 is
   allowed when the recipe asks). Observations are **not** neighbors;
   they came from step 2.
4. `search` fact text (relation labels, observation statements, claims)
   constrained to those ids / neighborhood ids when ids were supplied.
5. A governed predicate is an **optional** argument when the question is
   clearly that filter (“who reports to X”).

Assured operations (`fact_context`, `answer_context`, D87) follow the
same default: do not require a predicate list to return an entity’s
facts and one-hop relations.

Clean neighborhoods are part of this contract. Filler-noun entities
and duplicate-me Company twins make hops wrong even when the walk is
untyped.

---

## 8. Golden set (D22)

Same-name and dual-role cases are first-class. Measure **false merge**
vs **false split** per tier, including T0.

Minimum rows:

| Case | Expected |
|---|---|
| “we installed SAP” / “SAP announced…” | one id |
| SAP SE vs S/4HANA, both named | two ids + relation |
| Java language vs Java island | two ids |
| Father and son, same name, different lives | two ids + kinship relation if claimed |
| One person moved city | one id; profile updates |
| Two employees, same name, different sites, extra evidence | two ids |
| “Someone works for me” (I am a person) | one id for me; relation allowed |
| Bare `the system` / `game` | no entity |
| James traveled to Italy as a sentence | observation and/or generic edge; Italy is an entity **only** if nominated as a name, not because travel must be `located_in` |

`judge_pair` must be able to score the same-name **non**-matches.

---

## 9. Implementation-facing contracts

| Site | Contract |
|---|---|
| `EntityRef` | `name` required; type/hat optional |
| `CascadeResolver.resolve` | T0 auto-accept only under §3.1; else cascade or mint; same lemma may mint |
| `_T4_PROMPT` | includes `CANDIDATE PROFILE` |
| T3 upsert | embed name+profile when profile exists |
| `_INSERT_MENTION` | do not require `emitted_type` |
| `_INSERT_ENTITY` | do not require a type column; hats are a separate table |
| `_signature_allows` | not called on emitted classes before resolve; post-resolve fail-open if a hat is missing |
| `works_for` signatures | object Person \| Organization |
| `generic_identifier_guard` | written by resolve/cluster, not only deleted by forget |
| `GraphQueries.neighborhood` | empty predicates = all `RELATES` (keep) |
| `judge_pair` | lemma equality is not automatic match |
| E3 prompt | names + governed predicates; no mandatory REGISTRY TYPES; bare-noun refusal |

Schema: `entities.type` is no longer NOT NULL identity. Hats live in
`entity_hats(deployment_id, entity_id, type)` with FK to `entity_types`.
Existing rows migrate: current `entities.type` becomes one hat.
`mentions.emitted_type` nullable. Migrations implement
`postgres_schema_design.md` as amended in the same implementation PR.

Rebuild P2 after hat/signature changes (D7). Dropped D18 edges remain
re-derivable from claims.

---

## 10. Alternatives (complete-system, not phases)

| Alternative | Why it lost |
|---|---|
| Keep T0 exact as verdict | Homonyms impossible |
| `(name, type)` unique | Forks SAP |
| Required extract class | Error space; first-mint law |
| D18 pre-resolve on emitted types | Not independent |
| `works_for` Organization-only | Dual-role second id |
| Delete mentions | Lose naming transcript |
| Unlabeled edges, no fact text | Hairball |
| Hats that drop conflicting specifics | Hides dual-role |
| Description as identity key | Move city → new person |

---

## 11. Test battery (acceptance; numbers are starting points)

- T0: distinctive unique lemma auto-merges; common given name with
  conflicting profile does not; second mint of same lemma after T4
  no-match.
- T4 sees profile; two same-name vectors differ once profiles differ.
- `judge_pair` false-merge/false-split on §8 rows.
- E3: `game` not minted; `FIFA 23` may mint; EntityRef without type
  resolves.
- `works_for(Alice, Me)` when Me is a person persists.
- Neighborhood with no predicates returns `other:traveled` neighbors;
  observations for the same id load via lookup, not as graph nodes.
- Guard: a lemma linking many entities is down-weighted.
- Forget still purges hats and guard rows with the entity (D74).

No LLM on the query path is added (D9).
