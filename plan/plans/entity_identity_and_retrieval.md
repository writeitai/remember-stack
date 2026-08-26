# Sequencing — entity identity and retrieval (D95–D97)

**Status:** sequencing only (not design). Binding how:
[`entity_identity_and_retrieval_design.md`](../designs/entity_identity_and_retrieval_design.md).
**Why:**
[`entity_identity_and_retrieval_analysis.md`](../analysis/entity_identity_and_retrieval_analysis.md).
**Plan reviews (r1):**
[`REVIEW_codex-sol_entity_identity_retrieval_plan_2026-08-26.md`](../../design/reviews/REVIEW_codex-sol_entity_identity_retrieval_plan_2026-08-26.md),
[`REVIEW_agy_entity_identity_retrieval_plan_2026-08-26.md`](../../design/reviews/REVIEW_agy_entity_identity_retrieval_plan_2026-08-26.md).
This revision folds the **agreed correctness landmines** and the
operator’s **no backward-compatibility** cutover. It does **not**
reopen D95–D97.

This is a reform of shipped Phase-2 machinery, not a new numbered phase
of the original spine. Work packages are pointers with contracts
(`roadmap.md` §6). An executing agent reads only the listed sections.
If a WP seems to require deviating from the design, stop — amend the
design first.

## Cutover posture (operator, 2026-08-26)

**No backward compatibility.** Do not keep old writers, dual readers,
nullable `entities.type` for mixed binaries, `resolve(type?)` for old
clients, or a drain of old `E3_NORMALIZER_VERSION` work as a first-class
package. One hard cut: migrate, bump component versions, rebuild P2,
ship new code. Old queued normalize work of the previous generation is
abandoned or failed closed, not dual-run. Dogfood/LoCoMo stores may be
wiped or rebuilt; old type values are discarded as identity (design §9),
not migrated into hats.

That **does not** mean “ignore deploy order inside a WP.” Name-only
`INSERT` still dies on `type NOT NULL` until that migration has run.
Put schema change and writer change in the **same WP**, migration first
in that PR.

It also **does not** drop the r1 correctness findings: common-name list
before new T0, eval/`judge_pair` before T0 activation, profile/T3
safety before T0 activation, rewrite every type consumer in the type-cut
WP.

## Order rationale

1. Bare-noun refusal + aliases + **static common-name list** first, or
   T0 and a second-mint path will mint `game` / glue the first `John`.
2. **One type-cut WP:** schema drop + name-only extract/mint + P2/query
   consumers. Splitting “stop writing type” from “drop NOT NULL” is a
   deadlock even with no BC.
3. **Eval harness + `judge_pair`** must see same-lemma non-matches
   **before** new T0 is activated, or D95 cannot be measured.
4. **Profile worker + T3 name+profile** before new T0, or T3 still
   embeds the spelling and re-glues Johns. (r1: profile is a new
   worker, not a tweak; `REFRESH_PROFILE` is an unused enum.)
5. **Then** activate T0-as-candidate / second mint.
6. Default retrieval recipes last (or coded in parallel, shipped after
   the type-cut and T0). Neighborhood already walks empty predicates;
   I.7 is recipe/API defaults, not inventing the hop.

Do **not** optimize for LoCoMo. Do **not** treat dropping types as a
substitute for T0. Do **not** add expand/contract or mixed-version
drain because “reviewers mentioned it” — operator waived BC.

| WP | Goal | Reads | Depends | Deliverable | Acceptance |
|---|---|---|---|---|---|
| WP-I.1 | Bare-noun refusal; source + canonical aliases (idempotent); **static `common_name_lemmas` + min-length** in `ResolverConfig`; guard **writer** exists (used in I.5) | design §3.1, §4.3–4.5 | — | E3 prompt; alias upsert; `ResolverConfig` common-name list | `game` not minted; `FIFA 23` may mint; `App`/`Application` share an id on replay; T0 **must not** auto-accept configured common names even with one hit |
| WP-I.2 | **Hard type cut (same PR, migration first):** drop `entities.type` NOT NULL/FK/column (or stop using it); drop signatures / D86 type path; name-only `EntityRef` and mint; bump `E3_NORMALIZER_VERSION`; rewrite type consumers (P2 DDL/Parquet/export, `GraphNode`, `memory_v1.entities_current`, `resolve(type?)`, `typed_absence`, bootstrap type seed as unused, tests). Rebuild P2. Abandon old normalize generation. | design §4–5, §9; D96 | WP-I.1 | Alembic + E3 + resolver mint + P2/query/bootstrap | mint succeeds with no type; `works_for(Alice, Me)` persists; P2 snapshot has no type property; `resolve` has no type argument; unknown predicates still D5 |
| WP-I.3 | **`judge_pair` no longer auto-true on lemma equality;** golden schema not keyed by `entity_type`; **one** P/R curve; land design §8 fixtures (including same-name non-match and empty-profile John) | design §3.4, §8; D22 | WP-I.2 (untyped golden rows) | `eval/resolution.py` + fixtures | same-lemma non-match is a visible T0/T3/T4 error when regressed; suite does not crash without types |
| WP-I.4 | **New** profile refresher (`ProfileRefresherHandler` or compose onto existing observation-flush); T4 prompt profile + salient observations; T3 embeds name+profile (not name-only); debounce on evidence change; D74 forget of profile with the entity | design §3.3; D74 | WP-I.2 | worker + `_T4_PROMPT` + T3 upsert + forget tests | “is a bank” / “lives in Prague” appear in T4; two same-name vectors differ once profiles differ; missing profile is fail-safe (no name-only certainty) |
| WP-I.5 | **Activate D95 T0:** auto-accept only §3.1 (distinctive, one hit, not common-name, not guard, profile unopposed); same lemma may mint second id; `resolution_exclusions` on T4 no-match; populate `generic_identifier_guard` when a lemma spans ≥2 ids | design §3.1–3.2; D95 | WP-I.1, WP-I.3, WP-I.4 | `resolver.py` T0/mint/exclusions | father/son → two ids; SAP shorthand → one id; empty-profile **John** does **not** auto-merge the second John; lemma lock serializes races without forbidding two rows |
| WP-I.6 | D97 default path: resolve → lookup observations+relations → `neighborhood` empty predicates → fact-text search; optional dynamic predicate (any stored name, including `other:`); no type filter | design §7; D97 | WP-I.2, WP-I.5 | recipes / D87 defaults | hop returns `other:*` neighbors; observations via lookup not graph nodes; no new query-path LLM; “list banks” matches observation/profile text |
| WP-I.7 | Same-PR website pages for each user-visible WP above (D66) | D66 | with the WP it documents | `website/src/app/docs/**` | docs describe shipped behavior only |

**Parallelism:** I.3 and I.4 may be **developed** in parallel after I.2;
both must **merge before** I.5. I.6 implementation can start against
fixtures after I.2; it **ships** after I.5. I.1 is independent of schema
except tests that still mint with types until I.2.

**Inside WP-I.2 (same PR, not extra compatibility):** Alembic upgrade
runs before app code that omits `type`. One release. No dual writer.

**Exit:** design §11 tests green on the §8 golden slice; one resolver
curve (not per-type); T0 false-merge on common names is a failing test
if I.5 regresses.

**Non-goals:** reintroducing hats; expand/contract typed columns;
mixed-generation E3 drain; LoCoMo-only prompts; `mention_id` on
evidence; a `bank` type; keeping `resolve(type?)`.
