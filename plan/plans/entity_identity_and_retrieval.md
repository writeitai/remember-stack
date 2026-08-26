# Sequencing — entity identity and retrieval (D95–D97)

**Status:** sequencing only (not design). Binding how:
[`entity_identity_and_retrieval_design.md`](../designs/entity_identity_and_retrieval_design.md).
**Why:**
[`entity_identity_and_retrieval_analysis.md`](../analysis/entity_identity_and_retrieval_analysis.md).

This is a reform of shipped Phase-2 machinery, not a new numbered phase
of the original spine. Work packages are pointers with contracts
(`roadmap.md` §6). An executing agent reads only the listed sections.
If a WP seems to require deviating from the design, stop — amend the
design first.

**Order rationale (why this sequence, not another):**

1. Stop minting junk and recording aliases **before** touching T0, or
   the new second-mint path will mint `game` twice.
2. Strip types from extract/schema **before** T0 reform, or T4 still
   sees a forced Product/Person guess as if it were evidence.
3. Kill exact-name-as-verdict **before** wiring profile, or profile
   never runs for father/son.
4. Profile from observations + `judge_pair` **before** threshold games.
5. Retrieval recipes that omit predicates can ship as soon as
   neighborhoods are clean; the primitive already walks all `RELATES`.

Do **not** optimize WP order for LoCoMo categories. Do **not** treat
dropping types as a substitute for WP-I.3.

| WP | Goal | Reads | Depends | Deliverable | Acceptance |
|---|---|---|---|---|---|
| WP-I.1 | Refuse bare head nouns; write source aliases on mint/match; populate `generic_identifier_guard` | design §4.3–4.5; analysis §3.1; Graphiti extract rule as cited in analysis §4 | — | E3 prompt + resolver alias/guard writers | `game` not minted; `App`/`Application` can share an id; guard row appears for promiscuous lemmas; D66 docs if extract behavior is user-visible |
| WP-I.2 | Name-only `EntityRef`; stop writing `emitted_type`; remove `_signature_allows` / D86 type path; drop type from mint | design §4–5; D96 | WP-I.1 | `relations.py`, E3, resolver mint, D86 call sites | resolve with name only; no type FK on insert; unknown predicates still D5 |
| WP-I.3 | T0 auto-accept only under design §3.1; same lemma may mint a second id; `resolution_exclusions` on T4 no-match | design §3.1–3.2; D95; registries cascade | WP-I.2 | `resolver.py` T0/mint | father/son golden row: two ids; distinctive unique SAP-shorthand: one id; lemma lock still serializes races |
| WP-I.4 | Profile refresher from observations; T4 gets blurb + salient facts; T3 embeds name+profile; city/bank facts update profile not id | design §3.3 | WP-I.3 (otherwise profile is theatre) | profile refresher + `_T4_PROMPT` + T3 upsert | “is a bank” / “lives in Prague” appear in T4; two same-name vectors differ; “list banks” can match profile/observation text |
| WP-I.5 | `judge_pair` lemma equality is not automatic match; land the §8 golden slice in D22 harness | design §3.4, §8 | WP-I.3 | eval harness | false-merge vs false-split reported per tier including T0; same-name non-match is visible |
| WP-I.6 | Drop `entities.type` / signatures / unused `entity_types` use; `works_for` unconstrained by kinds | design §5, §9; D96 | WP-I.2 | Alembic + `core_manifest.py` + schema doc | `works_for(Alice, Me)` persists; graph nodes untyped; P2 rebuild |
| WP-I.7 | Default recipes/assured context: lookup facts + `neighborhood` with empty predicates + fact-text search; do not require a predicate argument | design §7; D97; retrieval_design neighborhood | WP-I.1 (clean graph) | retrieval recipes / D87 operations defaults | hop by id returns `other:*` neighbors; observations via lookup not as graph nodes; no new query-path LLM |
| WP-I.8 | Same-PR website pages for any user-visible extract/resolve/retrieval change in the WP that ships it | D66; website IA | with the WP it documents | `website/src/app/docs/**` | docs describe shipped behavior only |

**Exit:** design §11 tests green; §8 golden slice recorded in eval_runs;
resolver thresholds measured as **one** curve (not per-type).

**Non-goals for this train:** reintroducing hats; LoCoMo-only prompt
tuning; adding `mention_id` to evidence; a separate “bank” type.
