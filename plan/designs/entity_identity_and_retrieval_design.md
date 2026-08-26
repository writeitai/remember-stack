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
**Operator direction (same day):** no entity type classes; profile text
(and the observations that feed it) replace type filters. **No
backward-compatible dual writer** (plan cutover).

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

**D96 — No entity types; extract names; profile is observation prose.**
Mentions are a diary of names (string, claim, span). E3 does not emit
a class. There is no `entities.type`, no hats table, no domain/range
on kinds, no D86 type gate. Dual-role facts (`works_for` pointing at
a person) are allowed because both ends are ids. What used to be
“Company” / “bank” / “lives in Italy” lives in **observations** and in
the **profile**, which is a cached prose (and salient-observation)
projection of those facts — not a classification.

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
- no stored profile (or salient observations) that **contradict** the
  new claim (T4 if a profile exists and the claim is not obviously
  about the same life)

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

The profile is **not** a type, a label, or a second identity. It is a
**cached projection of the entity’s important observations** (and
salient relations), written as short prose plus the statements it was
built from.

- **Truth stays in observations** (D43): “KB is a bank licensed by
  ČNB”; “based in Italy”; “lives in Prague.” Those rows are the
  join table. There is no parallel `entity_types` / hats vocabulary.
- **`profile_summary`** is the refresher’s blurb over that join
  (debounced on evidence change), e.g. “Czech commercial bank; seat
  Prague; ČNB licence.” Until any observation exists, T4 sees claim
  text + candidate name only.
- **Salient observations** (starting point: a small evidence-ranked
  set for the candidate) are passed to T4 with the blurb so “is a
  bank” / “lives in Prague” is visible even if the blurb is stale or
  thin. This *is* the profile mechanism, not a later add-on.
- “List banks” is fact/profile **text** retrieval over those
  statements, not a type filter. Boolean lists are only as sharp as
  the observations. That is accepted.

Production `_T4_PROMPT` includes:

```text
MENTION: {name}
CLAIM CONTEXT: {claim_text}
CANDIDATE: {canonical_name}
CANDIDATE PROFILE: {profile_summary or "(none)"}
CANDIDATE FACTS: {salient observation statements or "(none)"}
Same real-world entity?
```

T4 answers **same / not-same** only. It does not emit `related_to`.
Father/son, if the claim states kinship, is a **relation** written
after both ids exist, not a merge.

T3 compares the mention embedding to the candidate’s **profile
embedding** (name + summary + salient facts), not the name-only
vector stamped at mint. Two people with the same given name must not
share a vector because they share a spelling.

City, job, employer, “is a bank” **update observations and then the
profile**. They do not change `entity_id` and do not mint a type.

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

Do not write `emitted_type`. The column, if left in the schema, stays
NULL and unused. Mentions are names, not classifications.

Mentions are not candidates. Do not SELECT mentions to resolve a name.
Do not add `mention_id` to evidence tables.

### 4.2 EntityRef

E3 structured output for an endpoint is a **name** only (canonical
nominative form). No `type` field. The prompt has no registry-types
block. D86’s unknown-type path is vacated (D96): there is nothing
illegal to retry. Unknown **predicates** still follow D5 (`other:` or
drop).

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

## 5. No entity types (amends D18 typing and domain/range)

There is **no** class on the entity, **no** hats table, **no** extract
type, **no** domain/range over kinds.

“Company”, “bank”, “court”, “based in Italy” are **facts** (observations
or relation `fact_label`s). They appear in the profile because the
profile is a projection of those facts (§3.3). They are not a second
ontology.

### 5.1 Dual role

“Someone works for me” is two resolved ids and `works_for`. The object
does not have to be an Organization. A separately **named** company
is a second entity plus a relation. Signature tables (`predicate_signatures`,
`_signature_allows`) are removed. Predicates remain a **name vocabulary**
(D5): governed list plus `other:`.

Travel, residence, “is a bank”: observation and/or a generic edge with
a fact sentence. Do not invent a core verb per noun, and do not invent
a type per sector.

### 5.2 What remains of D15 / D18 / D86

- **D5 predicates** stay (governed + `other:`). Retrieval filters on
  them are optional and, when used, may name any stored predicate
  including `other:traveled` (D97).
- **D18 entity types and domain/range** are withdrawn as identity and
  as a write gate. Extension packs that only added types are unused;
  packs may still add **predicates**.
- **D86** (unknown entity type retry-then-drop) is vacated: extract
  does not emit types. Unknown predicates still map to `other:` or
  drop.

### 5.3 Documented non-goals

- M2M hats / Graphiti labels on the entity (optional kind filters).
  Kind browse (“list companies”) is observation/profile text until a
  later **facet** design exists. That facet would be derived from
  observations, not a return of `entities.type`.
- Hats on facts as a second vocabulary beside predicates.
- `(name, type)` uniqueness.

---

## 6. Predicates, relations, observations

Relations stay `(subject_entity_id, predicate, object_entity_id)` plus
bi-temporal metadata and `fact_label`. Observations stay untyped
statements on one `subject_entity_id` (D43). Evidence stays claim →
fact. **No schema change** to those fact tables for types or mentions.

Governed predicates remain a convenience vocabulary for **optional**
filters (`works_for`, `knows`, `reports_to`, …). The set of **legal**
filters is **dynamic**: any predicate that exists in the store,
including `other:`. Named recipes may still bind a common verb. Default
retrieval does not require a predicate (D97). Do not promote
`other:traveled` into core just because it is common.

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
5. A predicate is an **optional** argument when the question is
   clearly that filter (“who reports to X”). Any stored predicate is
   legal, including `other:`. Do not require `type` on `resolve`.

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
| “KB is a bank”; “based in Italy” | one id; facts on observations; profile repeats them; “list banks” hits that text, not a type |

`judge_pair` must be able to score the same-name **non**-matches.

---

## 9. Implementation-facing contracts

| Site | Contract |
|---|---|
| `EntityRef` | `name` only |
| `CascadeResolver.resolve` | T0 auto-accept only under §3.1; else cascade or mint; same lemma may mint; no type argument |
| `_T4_PROMPT` | `CANDIDATE PROFILE` + salient observation statements |
| T3 upsert | embed name+profile (+ salient facts) when they exist |
| Profile refresher | rewrite `profile_summary` from the entity’s observations/relations; does not write types |
| `_INSERT_MENTION` | no `emitted_type` |
| `_INSERT_ENTITY` | no `type` column |
| `_signature_allows` | removed |
| `generic_identifier_guard` | written by resolve/cluster, not only deleted by forget |
| `GraphQueries.neighborhood` | empty predicates = all `RELATES` (keep) |
| `judge_pair` | lemma equality is not automatic match |
| E3 prompt | names + governed predicates; no REGISTRY TYPES; bare-noun refusal |
| `resolve` primitive | drop `type?` |

Schema: drop `entities.type` NOT NULL (column dropped or unused). Drop
or stop writing `mentions.emitted_type`. `predicate_signatures` unused.
`entity_types` unused for identity (may remain as dead registry until
migration removes it). Migrations amend `postgres_schema_design.md` in
the same implementation PR. P2 rebuild (D7). Existing type values are
**not** migrated into hats; they are discarded as identity. If a fact
“X is a bank” exists, it is already an observation.

**Cutover:** no mixed-version writer contract. Schema drop and name-only
mint ship together (migration first in that PR). Old normalize
generations are abandoned, not dual-run. Sequencing:
[`entity_identity_and_retrieval.md`](../plans/entity_identity_and_retrieval.md).

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
| Optional M2M hats / Graphiti labels | Kind filter without paying D18; still a parallel ontology; “bank” is a fact |
| Hats on facts | Duplicate of predicates + observation text |
| Keep types for “list companies” | Enumerative lists come from observation/profile text; facets if ever added are derived from those facts |
| Description as identity key | Move city → new person; profile is evidence, not the key |

---

## 11. Test battery (acceptance; numbers are starting points)

- T0: distinctive unique lemma auto-merges; common given name with
  conflicting profile does not; second mint of same lemma after T4
  no-match.
- T4 sees profile **and** salient observations; two same-name vectors
  differ once profiles differ; “lives in Prague” can split homonyms.
- `judge_pair` false-merge/false-split on §8 rows.
- E3: `game` not minted; `FIFA 23` may mint; `EntityRef` is name-only.
- `works_for(Alice, Me)` persists with no types on either end.
- Profile refresher: bank + Italy observations appear in
  `profile_summary`; “list banks” can match that text.
- Neighborhood with no predicates returns `other:traveled` neighbors;
  observations for the same id load via lookup, not as graph nodes.
- Guard: a lemma linking many entities is down-weighted.
- Forget still purges profile, observations, and guard rows with the
  entity (D74).

No LLM on the query path is added (D9).
