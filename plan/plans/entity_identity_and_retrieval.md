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
2. Stop requiring a class **before** T0 reform, or T4 still sees a
   forced Product/Person guess as if it were evidence.
3. Kill exact-name-as-verdict **before** wiring profile, or profile
   never runs for father/son.
4. Profile + `judge_pair` **before** threshold games and before hats
   become interesting.
5. Retrieval recipes that omit predicates can ship as soon as
   neighborhoods are clean; the primitive already walks all `RELATES`.
6. Schema hats / `works_for` Person object can travel with (2)–(3);
   they must not wait on LoCoMo.

Do **not** optimize WP order for LoCoMo categories. Do **not** drop
hats as a substitute for WP-I.3.

| WP | Goal | Reads | Depends | Deliverable | Acceptance |
|---|---|---|---|---|---|
| WP-I.1 | Refuse bare head nouns; write source aliases on mint/match; populate `generic_identifier_guard` | design §4.3–4.5; analysis §3.1; Graphiti extract rule as cited in analysis §4 | — | E3 prompt + resolver alias/guard writers | `game` not minted; `App`/`Application` can share an id; guard row appears for promiscuous lemmas; D66 docs if extract behavior is user-visible |
| WP-I.2 | Name-only `EntityRef`; `emitted_type` not required; no D18 pre-resolve on emitted classes; D86 only when a hat is present | design §4.1–4.2, §5.2; D96; D86 | WP-I.1 (prompt already names-first) | `relations.py`, E3 prompt, `_signature_allows` call sites | resolve without type; illegal *emitted* hat still retry-then-drop; no pre-resolve signature on guessed classes |
| WP-I.3 | T0 auto-accept only under design §3.1; same lemma may mint a second id; `resolution_exclusions` on T4 no-match | design §3.1–3.2; D95; registries cascade | WP-I.2 | `resolver.py` T0/mint | father/son golden row: two ids; distinctive unique SAP-shorthand: one id; lemma lock still serializes races |
| WP-I.4 | Write `profile_summary`; pass it to T4; T3 embeds name+profile; city change updates profile not id | design §3.3; registries §2 | WP-I.3 (otherwise profile is theatre) | profile refresher + `_T4_PROMPT` + T3 upsert | two same-name people have different vectors once profiles differ; T4 prompt contains `CANDIDATE PROFILE` |
| WP-I.5 | `judge_pair` lemma equality is not automatic match; land the §8 golden slice in D22 harness | design §3.4, §8 | WP-I.3 | eval harness | false-merge vs false-split reported per tier including T0; same-name non-match is visible |
| WP-I.6 | Hats table; migrate `entities.type` to one hat; `works_for` object Person\|Organization; post-resolve fail-open if a hat is missing | design §5, §9; D96 | WP-I.2 | Alembic + `core_manifest.py` + schema doc amendment | `works_for(Alice, Me)` persists when Me is Person; existing entities keep a hat; P2 rebuild |
| WP-I.7 | Default recipes/assured context: lookup facts + `neighborhood` with empty predicates + fact-text search; do not require a predicate argument | design §7; D97; retrieval_design neighborhood | WP-I.1 (clean graph) | retrieval recipes / D87 operations defaults | hop by id returns `other:*` neighbors; observations via lookup not as graph nodes; no new query-path LLM |
| WP-I.8 | Same-PR website pages for any user-visible extract/resolve/retrieval change in the WP that ships it | D66; website IA | with the WP it documents | `website/src/app/docs/**` | docs describe shipped behavior only |

**Exit:** design §11 tests green; §8 golden slice recorded in eval_runs;
D22 floors do not regress on the old per-type curves where those still
apply to hats.

**Non-goals for this train:** deleting the hats table; LoCoMo-only prompt
tuning; adding `mention_id` to evidence; Graphiti drop-conflicting-label.
