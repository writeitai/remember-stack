# Adversarial Implementation Review (Round 3): PR 308 (WP-I.1 Extract Aliases & Bare-Noun Refusal)

**Reviewer identity:** Antigravity (`agy`)  
**Date:** 2026-08-26  
**PR:** [writeitai/remember-stack#308](https://github.com/writeitai/remember-stack/pull/308)  
**Branch:** `origin/feat/wp-i1-extract-aliases` vs `origin/main`  
**Commits evaluated:**
- `991d7648` (`feat(e3): refuse bare head nouns; record source and canonical aliases`)
- `5d80db3a` (`fix(er): ground source aliases in the claim span`)
- `fde1bd60` (`fix(er): write source aliases only for claim-grounded spans`)  
**Review target:** Complete implementation across:
- `.github/ci/unit-paths.txt`
- `src/rememberstack/model/relations.py`
- `src/rememberstack/spine/entity_eligibility.py`
- `src/rememberstack/spine/resolver.py`
- `src/rememberstack/workers/e3.py`
- `src/tests/spine/test_entity_eligibility.py`
- `src/tests/spine/test_resolver.py`
- `src/tests/workers/test_e3_bare_head_noun.py`
- `website/src/app/docs/ingestion/pipeline/page.mdx`

**Output path:** `/var/folders/wt/plp93ggs40586mdsvzzqy4c40000gp/T/grok-goal-6edec73546f9/implementer/REVIEW_agy_wp_i1_extract_aliases_r3_2026-08-26.md`  
**Verdict:** **Approve**

---

## Executive Summary & Verdict

In Round 2, Codex highlighted a subtle boundary defect in the fallback alias write path: when an emitted `EntityRef.surface` was not present in the claim, the resolver fell back to `reference.name` and unconditionally wrote `("source", reference.name)` without verifying whether the canonical name actually occurred as a span in `claim.claim_text`. When neither the emitted surface nor the canonical name was present in the claim, an ungrounded string was still stamped with `provenance=source`.

Commit `fde1bd60` (`fix(er): write source aliases only for claim-grounded spans`) cleanly and rigorously resolves this finding:
1. `CascadeResolver._record` now checks whether `emitted` appears in `claim.claim_text`; if not, it checks whether `reference.name` appears in `claim.claim_text`. If neither appears in the claim text, `source_text` is set to `None`.
2. When `source_text is None`, no `provenance="source"` alias is written to the database. The canonical alias (`provenance="llm_canonical"`) is still recorded for `reference.name`.
3. `test_ungrounded_surface_does_not_write_source_alias` in `src/tests/spine/test_resolver.py` was updated to assert that no `source` provenance row is created at all when neither surface nor canonical name is present in the claim.

All unit tests in `.github/ci/unit-paths.txt` pass cleanly (`1012 passed, 5 skipped`). Pyright static type checking passes with 0 errors/0 warnings, Ruff passes all lint checks, and `git diff --check` is clean.

**Verdict: Approve.**

---

## P0 / P1 Assessment & Round-by-Round Audit

| Priority | Item | Status | Verification & Resolution |
|---|---|---|---|
| **P1 (r1 #1)** | **Source provenance requires claim grounding** | **Resolved** | `surface_appears_in_claim` uses word-bounded regex (`(?<!\w)needle(?!\w)`) with `re.IGNORECASE | re.UNICODE`. Enforced in `CascadeResolver._record`. |
| **P1 (r1 #2)** | **Acceptance tests driving shipped E3 + CascadeResolver** | **Resolved** | `_normalize_through_shipped_resolver` composes `NormalizeRelationsHandler` and `CascadeResolver` against DB; tests verify bare nouns dropped, qualified names minted, and aliases recorded correctly. |
| **P1 (r2 #1)** | **Omit `source` alias entirely when neither surface nor canonical name is in claim** | **Resolved** | When `surface_appears_in_claim` is `False` for both `mention_surface()` and `reference.name`, `source_text` is `None` and `_upsert_alias(..., provenance="source")` is skipped entirely. Covered by `test_ungrounded_surface_does_not_write_source_alias`. |

---

## Acceptance Criteria Verification (WP-I.1)

| Criterion | Implementation Status | Evidence |
|---|---|---|
| **Bare-noun refusal** | **Met** | `is_bare_head_noun` in `src/rememberstack/spine/entity_eligibility.py` identifies unqualified generic nouns (`game`, `app`, `system`, `the system`, `photo`, `module`, etc.). Checked deterministically in `NormalizeRelationsHandler._normalize_claim` before resolution. Verified in `test_prompt_forbids_bare_head_nouns`, `test_normalize_drops_game_relation_without_resolve`, and `test_e3_drops_game_before_shipped_resolver_mints`. |
| **Qualified referent preservation** | **Met** | Qualified forms (`FIFA 23`, `Application`, `James's Unity strategy game`) pass eligibility checks and resolve normally. Verified in `test_qualified_referents_are_kept` and `test_normalize_resolves_fifa_23`. |
| **Alias extraction and provenance** | **Met** | `EntityRef` includes optional `surface: str | None = None`. Grounded claim surfaces produce both `("llm_canonical", <name>)` and `("source", <surface>)` on the same `entity_id`. Verified in `test_source_and_canonical_aliases_on_mint_and_replay`. |
| **Ungrounded surface rejection** | **Met** | When neither surface nor canonical name is present in the claim text, no `source` alias is upserted. Verified in `test_ungrounded_surface_does_not_write_source_alias`. |
| **Generic identifier guard writer** | **Met** | `refresh_generic_identifier_guard` counts distinct entity IDs sharing a lemma and updates `generic_identifier_guard` with `is_downweighted = (distinct_entity_count >= distinct_floor)`. Verified in `test_generic_identifier_guard_downweights_shared_lemma`. |
| **Preservation of entity types (WP-I.2 boundary)** | **Met** | `EntityRef.type`, `entities.type`, and `mentions.emitted_type` remain intact. No premature schema cuts or Alembic migrations before WP-I.2. |
| **T0 exact match preserved (WP-I.5 boundary)** | **Met** | T0 resolution mechanics unchanged; candidate-only refactoring remains deferred to WP-I.5. |

---

## Detailed Code Audit of Round-3 Commit (`fde1bd60`)

### 1. Fallback Grounding and Source Alias Omission (`src/rememberstack/spine/resolver.py:523-577`)

```python
mention_id = uuid4()
emitted = reference.mention_surface()
if surface_appears_in_claim(surface=emitted, claim_text=claim.claim_text):
    source_text = emitted
elif surface_appears_in_claim(
    surface=reference.name, claim_text=claim.claim_text
):
    source_text = reference.name
else:
    source_text = None
surface = source_text if source_text is not None else reference.name
surface_lemma = normalized_lemma(surface=surface)
connection.execute(
    _INSERT_MENTION,
    {
        "mention_id": mention_id,
        "deployment_id": deployment_id,
        "surface_form": surface,
        "lemma": surface_lemma,
        "canonical_name_form": reference.name,
        "emitted_type": reference.type,
        "claim_id": claim.claim_id,
        "chunk_id": claim.chunk_id,
        "doc_id": claim.doc_id,
    },
)
self._upsert_alias(
    connection=connection,
    deployment_id=deployment_id,
    entity_id=entity_id,
    alias_text=reference.name,
    lemma=lemma,
    provenance="llm_canonical",
)
if source_text is not None:
    self._upsert_alias(
        connection=connection,
        deployment_id=deployment_id,
        entity_id=entity_id,
        alias_text=source_text,
        lemma=normalized_lemma(surface=source_text),
        provenance="source",
    )
self.refresh_generic_identifier_guard(
    connection=connection, deployment_id=deployment_id, lemma=lemma
)
if source_text is not None:
    source_lemma = normalized_lemma(surface=source_text)
    if source_lemma != lemma:
        self.refresh_generic_identifier_guard(
            connection=connection,
            deployment_id=deployment_id,
            lemma=source_lemma,
        )
```

- **Exact grounding guarantee:** If the model proposes `surface="App"` on a claim that mentions neither `App` nor `Application`, `source_text` is `None`.
- **Clean DB state:** Only the `llm_canonical` alias is stored. No ungrounded `provenance="source"` row can enter the database, preventing T0 exact matching on hallucinated tokens.
- **Guard maintenance:** Generic identifier guard is refreshed for the canonical `lemma` and, if a distinct grounded `source_text` exists, for `source_lemma`.

### 2. Negative Test Verification (`src/tests/spine/test_resolver.py:495-532`)

```python
def test_ungrounded_surface_does_not_write_source_alias(
    database_engine: Engine,
) -> None:
    """A hallucinated App span is not stored as provenance=source."""
    provider = FakeModelProvider(generate_router=_first_token_router)
    resolver = _resolver(engine=database_engine, provider=provider)
    minted = resolver.resolve(
        deployment_id=_DEPLOYMENT_ID,
        reference=EntityRef(name="Application", surface="App", type="Product"),
        claim=_claim(),
    )
    assert minted.created
    with database_engine.connect() as connection:
        aliases = (
            connection.execute(
                text(
                    "SELECT alias_text, provenance FROM aliases"
                    " WHERE entity_id = :entity_id"
                ),
                {"entity_id": minted.entity_id},
            )
            .mappings()
            .all()
        )
        mentions = (
            connection.execute(
                text("SELECT DISTINCT surface_form, canonical_name_form FROM mentions")
            )
            .mappings()
            .all()
        )
    provenances = {(row["provenance"], row["alias_text"]) for row in aliases}
    assert ("llm_canonical", "Application") in provenances
    assert ("source", "App") not in provenances
    assert ("source", "Application") not in provenances
    assert not any(provenance == "source" for provenance, _ in provenances)
    assert all(row["surface_form"] == "Application" for row in mentions)
```

- Rigorously validates that no `provenance="source"` alias is created when neither `App` nor `Application` appears in the claim text.

---

## Tooling & Verification Results

1. **Unit Test Suite:**
   ```
   uv run pytest $(cat .github/ci/unit-paths.txt)
   ================ 1012 passed, 5 skipped, 1 warning in 77.90s ================
   ```
2. **Type Checker:**
   ```
   uv run pyright
   0 errors, 0 warnings, 0 informations
   ```
3. **Linter:**
   ```
   uv run ruff check src
   All checks passed!
   ```
4. **Git Diff Hygiene:**
   ```
   git diff --check origin/main...origin/feat/wp-i1-extract-aliases
   (Clean - 0 issues)
   ```

---

## Findings

- **P0 Findings:** 0
- **P1 Findings:** 0
- **P2 Findings / Nits:** 0

---

## Conclusion

PR 308 (WP-I.1) is complete, robust, cleanly bounded, and fully satisfies all criteria and review feedback across all rounds. Ready to merge.
