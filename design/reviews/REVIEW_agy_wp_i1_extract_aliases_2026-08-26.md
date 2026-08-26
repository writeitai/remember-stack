# Adversarial Implementation Review: PR 308 (WP-I.1 Extract Aliases & Bare-Noun Refusal)

**Reviewer identity:** Antigravity (`agy`)  
**Date:** 2026-08-26  
**PR:** [writeitai/remember-stack#308](https://github.com/writeitai/remember-stack/pull/308)  
**Branch:** `origin/feat/wp-i1-extract-aliases` vs `origin/main`  
**Commit:** `b28106e58c1bcd607f3fdf2f2e07979fa337d87e` (`feat(e3): refuse bare head nouns; record source and canonical aliases`)  
**Review target:** Implementation across:
- [`.github/ci/unit-paths.txt`](file:///Users/jpuc/code/moje/remember-stack/.github/ci/unit-paths.txt)
- [`src/rememberstack/model/relations.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/relations.py)
- [`src/rememberstack/spine/entity_eligibility.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/entity_eligibility.py)
- [`src/rememberstack/spine/resolver.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py)
- [`src/rememberstack/workers/e3.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/e3.py)
- [`src/tests/spine/test_entity_eligibility.py`](file:///Users/jpuc/code/moje/remember-stack/src/tests/spine/test_entity_eligibility.py)
- [`src/tests/spine/test_resolver.py`](file:///Users/jpuc/code/moje/remember-stack/src/tests/spine/test_resolver.py)
- [`src/tests/workers/test_e3_bare_head_noun.py`](file:///Users/jpuc/code/moje/remember-stack/src/tests/workers/test_e3_bare_head_noun.py)
- [`website/src/app/docs/ingestion/pipeline/page.mdx`](file:///Users/jpuc/code/moje/remember-stack/website/src/app/docs/ingestion/pipeline/page.mdx)

**Output path:** `/var/folders/wt/plp93ggs40586mdsvzzqy4c40000gp/T/grok-goal-6edec73546f9/implementer/REVIEW_agy_wp_i1_extract_aliases_2026-08-26.md`  
**Verdict:** **Approve**

---

## Executive Summary & Verdict

PR 308 delivers the complete contract for **WP-I.1** as specified in [`plan/plans/entity_identity_and_retrieval.md`](file:///Users/jpuc/code/moje/remember-stack/plan/plans/entity_identity_and_retrieval.md) and [`plan/designs/entity_identity_and_retrieval_design.md`](file:///Users/jpuc/code/moje/remember-stack/plan/designs/entity_identity_and_retrieval_design.md) (§3.1, §4.2–4.5).

Specifically, PR 308 introduces:
1. **Deterministic bare-head-noun refusal:** [`is_bare_head_noun`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/entity_eligibility.py#L28) filters out unqualified generic nouns (`game`, `app`, `system`, `card`, `photo`, `module`, `the system`) in E3 before `resolve` is ever invoked, while preserving qualified referents like `FIFA 23`, `James's Unity strategy game`, and canonical `Application`.
2. **Dual-provenance alias recording:** [`EntityRef`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/relations.py#L13) now carries an optional `surface` field. On both mint and match replay, [`CascadeResolver._record`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L506) idempotently upserts `llm_canonical` (using `EntityRef.name`) and `source` (using `EntityRef.mention_surface()`) aliases attached to the same `entity_id`.
3. **Generic identifier guard writer:** `CascadeResolver.refresh_generic_identifier_guard` updates `generic_identifier_guard` on mint/replay, counting distinct entities sharing the lemma and setting `is_downweighted = true` when `count >= 2`.
4. **Clean boundary adherence:** The PR strictly bounds its scope to WP-I.1. It does **not** drop entity types from schema or models (deferred to WP-I.2), does **not** convert T0 into a candidate-only listing (deferred to WP-I.5), and adds no dead code or runtime flags.

All 1,011 unit tests in `.github/ci/unit-paths.txt` pass cleanly.

**Verdict: Approve.**

---

## Acceptance Criteria Verification

| Acceptance Criterion (WP-I.1) | Status | Evidence |
|---|---|---|
| `game` not minted; `FIFA 23` may mint | **Met** | [`is_bare_head_noun`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/entity_eligibility.py#L28) drops `game` and accepts `FIFA 23`; integrated into [`NormalizeRelationsHandler._normalize_claim`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/e3.py#L453,L533); verified in [`test_e3_bare_head_noun.py`](file:///Users/jpuc/code/moje/remember-stack/src/tests/workers/test_e3_bare_head_noun.py). |
| Claim text `App` records source alias `App` and canonical `Application` | **Met** | `EntityRef(name="Application", surface="App")` records `surface_form="App"` in `mentions`, and upserts `("source", "App")` and `("llm_canonical", "Application")` on the same `entity_id` in `aliases`; verified in [`test_resolver.py`](file:///Users/jpuc/code/moje/remember-stack/src/tests/spine/test_resolver.py#L366). |
| `EntityRef` has optional surface; types remain until I.2 | **Met** | [`EntityRef`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/relations.py#L13) defines `surface: str | None = None` and retains required `type: _NonEmpty`; schema retains `entities.type` and `mentions.emitted_type`. |
| Guard WRITER exists (not T0 auto-merge) | **Met** | [`refresh_generic_identifier_guard`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L607) upserts to `generic_identifier_guard` counting distinct entity IDs; T0 resolution logic is not altered to candidate-only (kept for WP-I.5). |

---

## Detailed Evaluation of Mandatory Checks

### Check 1: Tests Drive Shipped E3 and Resolver Code
- **Assessment:** **Pass.**
- **Details:** The test additions in [`test_e3_bare_head_noun.py`](file:///Users/jpuc/code/moje/remember-stack/src/tests/workers/test_e3_bare_head_noun.py), [`test_entity_eligibility.py`](file:///Users/jpuc/code/moje/remember-stack/src/tests/spine/test_entity_eligibility.py), and [`test_resolver.py`](file:///Users/jpuc/code/moje/remember-stack/src/tests/spine/test_resolver.py) directly execute production entrypoints:
  - `NormalizeRelationsHandler._normalize_claim` is tested with simulated LLM payloads to verify that bare head nouns on relation endpoints (`subject` and `object`) and observation subjects are dropped before invoking `resolver.resolve()`.
  - `CascadeResolver.resolve()` is driven through real PostgreSQL integration fixtures to verify alias upserts, mention fields, and guard row synchronization.
  - `is_bare_head_noun` is directly asserted for both bare nouns and qualified forms.

### Check 2: Bare-Noun List Must Not Drop Canonical `Application`
- **Assessment:** **Pass.**
- **Details:** In [`_BARE_HEAD_NOUNS`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/entity_eligibility.py#L9), the list contains:
  `{"adapter", "app", "card", "game", "item", "module", "photo", "system", "the app", "the module", "the system", "thing", "tool"}`.
  `"application"` is not in `_BARE_HEAD_NOUNS`. When an entity is extracted with canonical `name="Application"` and `surface="App"`, `is_bare_head_noun(name="Application")` returns `False`, allowing the entity to proceed through resolution and minting.
  This is explicitly validated in [`test_qualified_referents_are_kept`](file:///Users/jpuc/code/moje/remember-stack/src/tests/spine/test_entity_eligibility.py#L12) and [`test_normalize_passes_source_surface_to_resolve`](file:///Users/jpuc/code/moje/remember-stack/src/tests/workers/test_e3_bare_head_noun.py#L119).

### Check 3: Alias Upsert is Idempotent on Mint and T0 Replay
- **Assessment:** **Pass.**
- **Details:** [`_UPSERT_ALIAS`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L821) uses PostgreSQL's `ON CONFLICT (deployment_id, entity_id, normalized_lemma, provenance) DO UPDATE SET last_seen = now(), alias_text = EXCLUDED.alias_text`.
  - On initial mint, `_record` executes `_UPSERT_ALIAS` for `llm_canonical` (`reference.name`) and `source` (`surface`).
  - On replay (T0 exact match), `_record` runs the exact same `_UPSERT_ALIAS` statements, refreshing `last_seen` without violating constraints or duplicating rows.
  - Distinct provenances (`source` vs `llm_canonical`) on the same `entity_id` correctly produce two separate rows when the strings differ, and two separate rows (one per provenance) when they match.
  - This is verified in [`test_source_and_canonical_aliases_on_mint_and_replay`](file:///Users/jpuc/code/moje/remember-stack/src/tests/spine/test_resolver.py#L366).

### Check 4: No Premature Scope Creep (T0 Candidates in I.5, Type Drops in I.2)
- **Assessment:** **Pass.**
- **Details:** 
  - **No T0-as-candidates change:** [`CascadeResolver.resolve`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/resolver.py#L110) retains the existing T0 exact-lemma matching logic returning `method="T0", confidence=1.0`. Transitioning T0 into candidate listing only is deferred to WP-I.5 as required by the plan sequencing.
  - **No type column drop:** `EntityRef.type`, `entities.type`, and `mentions.emitted_type` remain intact. No Alembic schema migrations are bundled into this PR (WP-I.2 will perform the single hard type cut).

### Check 5: Code Cleanliness, Inventory, and Documentation
- **Assessment:** **Pass.**
- **Details:**
  - `E3_NORMALIZER_VERSION` is properly bumped to `"e3-normalize-2026.08b:temp0-1:unknown-type-gate-1:claim-fanout-1:bare-noun-1"` in [`workers/e3.py`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/workers/e3.py#L57).
  - `.github/ci/unit-paths.txt` is updated with `src/tests/spine/test_entity_eligibility.py` and `src/tests/workers/test_e3_bare_head_noun.py`. Test inventory check (`check_test_inventory.py`) passes (71 unit, 55 integration).
  - Public documentation is updated in [`website/src/app/docs/ingestion/pipeline/page.mdx`](file:///Users/jpuc/code/moje/remember-stack/website/src/app/docs/ingestion/pipeline/page.mdx#L155) documenting bare-noun drop rules and dual-alias recording.
  - No production flag for exact-T0 is introduced.

---

## Findings & Notes

### P0 / P1 (Blockers / Critical)
*None.*

### P2 / Minor Observations & Nits
1. **Set Membership Redundancy in `entity_eligibility.py` (Nit):**
   In [`_BARE_HEAD_NOUNS`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/entity_eligibility.py#L9), `"the app"`, `"the module"`, and `"the system"` are explicitly included alongside `"app"`, `"module"`, and `"system"`.
   In [`is_bare_head_noun`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/spine/entity_eligibility.py#L37), `if lemma.startswith("the ") and lemma[4:] in _BARE_HEAD_NOUNS:` already matches any `"the <noun>"` where `<noun>` is in `_BARE_HEAD_NOUNS`.
   The explicit `"the ..."` entries in the set are redundant with the prefix check, though completely harmless.
2. **Whitespace-Only Fallback on `mention_surface()`:**
   [`EntityRef.mention_surface()`](file:///Users/jpuc/code/moje/remember-stack/src/rememberstack/model/relations.py#L27) uses `if self.surface is None or not self.surface.strip(): return self.name`. This cleanly handles both omitted `surface` and empty/whitespace string edge cases.

---

## Conclusion

PR 308 meets all acceptance criteria for WP-I.1 with clean separation of concerns, comprehensive test coverage, and exact alignment with D95–D97 design requirements. Ready to merge.
