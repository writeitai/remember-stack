# Sequencing — entity identity and retrieval (D95–D97)

**Status:** sequencing only (not design). Binding how:
[`entity_identity_and_retrieval_design.md`](../designs/entity_identity_and_retrieval_design.md).
**Why:**
[`entity_identity_and_retrieval_analysis.md`](../analysis/entity_identity_and_retrieval_analysis.md).
**Plan reviews:**
r1
[`Codex`](../../design/reviews/REVIEW_codex-sol_entity_identity_retrieval_plan_2026-08-26.md) /
[`agy`](../../design/reviews/REVIEW_agy_entity_identity_retrieval_plan_2026-08-26.md);
r2 (hard-cut)
[`Codex`](../../design/reviews/REVIEW_codex-sol_entity_identity_retrieval_plan_r2_2026-08-26.md) /
[`agy`](../../design/reviews/REVIEW_agy_entity_identity_retrieval_plan_r2_2026-08-26.md).
This revision folds r2 **named contracts** (I.2 consumer list, source
surface, D74 shared-entity profile, distinct-id T0, eval gate). It does
**not** reopen D95–D97 or restore expand/contract.

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
   I.6 is recipe/API defaults, not inventing the hop. I.7 is D66 docs.

Do **not** optimize for LoCoMo. Do **not** treat dropping types as a
substitute for T0. Do **not** add expand/contract or mixed-version
drain because “reviewers mentioned it” — operator waived BC.

| WP | Goal | Reads | Depends | Deliverable | Acceptance |
|---|---|---|---|---|---|
| WP-I.1 | Bare-noun refusal; **source surface + canonical name** on the extract payload (see design §4.2); idempotent source + `llm_canonical` aliases; **static `common_name_lemmas` + min-length** in `ResolverConfig`; guard **writer** exists (used in I.5) | design §3.1, §4.2–4.5 | — | E3 prompt + `EntityRef` surface field; alias upsert; `ResolverConfig` common-name list | `game` not minted; `FIFA 23` may mint; claim text `App` records source alias `App` and canonical `Application`; T0 **must not** auto-accept configured common names even with one **entity** hit |
| WP-I.2 | **Hard type cut (same PR, migration first).** Drop `entities.type` (NOT NULL, FK, column) and stop writing `mentions.emitted_type`; drop `predicate_signatures` / D86 type path; name-only type on `EntityRef` (surface remains); bump `E3_NORMALIZER_VERSION`. Rewrite **every** type consumer in this PR: `workers/p2.py` DDL/Parquet; `spine/projection.py` Entity export; P3 Tier-1 path `entities/<type>/<id>` → `entities/<entity_id>` (`e0_files_design.md`, `workers/p3.py`); P1 entity search SQL (`adapters/postgres_p1.py` / `ports/p1_index.py`); `memory_v1.entities_current` (`p9_01_0022`); `GraphNode` / envelope; `query_engine` `resolve(type?)` and `typed_absence`; `http_api.py` / `sdk.py`; `assured_operations.py`; `deployment_bootstrap.py` / `core_manifest.py` type seed unused; `eval/resolution.py` type strata; catalog/migration tests. Rebuild P2 **and** P3. Abandon old normalize generation. | design §4–5, §9; D96 | WP-I.1 | Alembic + listed files | mint succeeds with no type; `works_for(Alice, Me)` persists; P2/P3/P1/`memory_v1` have no type; `resolve` has no type argument; unknown predicates still D5; migration test pins new revision |
| WP-I.3 | **`judge_pair` no longer auto-true on lemma equality;** golden schema not keyed by `entity_type`; **one global P/R curve plus per-tier diagnostics** (do not delete the deciding tier); land design §8 fixtures (same-name non-match, empty-profile John) | design §3.4, §8; D22 | WP-I.2 | `eval/resolution.py` + fixtures | same-lemma non-match is a visible T0 **and** T3/T4 error when those tiers regress; suite does not crash without types |
| WP-I.4 | **New** profile refresher (compose onto observation-flush or `ProfileRefresherHandler`); T4 prompt profile + salient observations; T3 embeds name+profile (not name-only); debounce on evidence change; **D74:** forget of document A on a **shared** entity invalidates/rebuilds profile (forgotten distinctive phrase gone from summary, salient inputs, vector, search); empty profile is fail-safe | design §3.3; D74 | WP-I.2 | worker + `_T4_PROMPT` + T3 upsert + forget tests | “is a bank” / “lives in Prague” appear in T4; two same-name vectors differ once profiles differ; shared-survivor forget test green |
| WP-I.5 | **Activate D95 T0** only after a **recorded passing I.3+I.4 eval run**. Auto-accept only §3.1; hits = **distinct active `entity_id`s** (not alias rows); same lemma may mint second id; `resolution_exclusions` canonical low/high UUID pairs on T4 no-match; populate `generic_identifier_guard` when a lemma spans ≥2 **entities** | design §3.1–3.2; D95 | WP-I.1, WP-I.3, WP-I.4 | `resolver.py` T0/mint/exclusions | father/son → two ids; SAP shorthand → one id; empty-profile **John** does **not** auto-merge; one entity with two provenances still counts as **one** hit; lemma lock serializes races without forbidding two rows |
| WP-I.6 | D97 default path: `resolve` → lookup observations+relations → `neighborhood` empty predicates → ID-constrained fact-text search (`assured_operations.py`, `operation_executor.py`, `query_engine.py`); optional dynamic predicate (any stored name, including `other:`); no type filter | design §7; D97 | WP-I.2, WP-I.5 | those files + recipes | hop returns `other:*` neighbors; observations via lookup not graph nodes; no new query-path LLM; “list banks” matches observation/profile text; ambiguity / missing P2 / caps are explicit |
| WP-I.7 | Same-PR website pages for each user-visible WP above (D66) | D66 | with the WP it documents | `website/src/app/docs/**` | docs describe shipped behavior only |

**Parallelism:** I.3 and I.4 may be **developed** in parallel after I.2;
both must **merge before** I.5. I.6 implementation can start against
fixtures after I.2; it **ships** after I.5. I.1 is independent of schema
except tests that still mint with types until I.2.

**Inside WP-I.2 (same PR, not extra compatibility):** Alembic upgrade
runs before app code that omits `type`. One release. No dual writer.

**Exit:** design §11 tests green on the §8 golden slice; one **global**
resolver curve **and** per-tier diagnostics; T0 false-merge on common
names is a failing test if I.5 regresses; I.5 does not merge without a
passing post-I.4 eval record.

**Non-goals:** reintroducing hats; expand/contract typed columns;
mixed-generation E3 drain; LoCoMo-only prompts; `mention_id` on
evidence; a `bank` type; keeping `resolve(type?)`.
