# Scope & Verification

- **Repository**: `/Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication`
- **Reviewed Head SHA**: `b575c5567ab87f7225b43fe271ba9b36019ce08f`
- **Stacked Base SHA (D119)**: `d808164b443c7b27ea0c1aaebc0e0b3c67512702`
- **Worktree State**: Clean and untouched (read-only inspection; no edits, no DB resets, no paid models, no remote mutations).
- **Prior Reviews**:
  - `ecedaabe`: [`d120_d121_agy_review_ecedaabe.md`](../../plan/implementation_evals/d120_d121_agy_review_ecedaabe.md)
  - `d84af5a4`: [`d120_d121_agy_review_d84af5a4.md`](../../plan/implementation_evals/d120_d121_agy_review_d84af5a4.md)
  - `5273eee8`: [`d120_d121_agy_review_5273eee8.md`](../../plan/implementation_evals/d120_d121_agy_review_5273eee8.md)

---

### Commands Actually Run

```bash
git rev-parse HEAD
# Output: b575c5567ab87f7225b43fe271ba9b36019ce08f

git status --short
# Output: (clean)

# CI inventory & layer architecture contracts
python .github/ci/check_test_inventory.py
# Output: test inventory OK: unit=108 integration=60 discovered=168

uv run lint-imports
# Output: Contracts: 5 kept, 0 broken. (295 files, 783 dependencies analyzed)

# Formatting & type checks
uv run ruff format --check src/ benchmarks/
# Output: 503 files already formatted

uv run ruff check src/ benchmarks/
# Output: All checks passed!

uv run pyright src/ benchmarks/
# Output: 0 errors, 0 warnings, 0 informations

# Core concise adjudication & protocol tests
uv run pytest \
  src/tests/core/test_concise_adjudication.py \
  src/tests/benchmarks/test_locomo_protocol.py \
  src/tests/benchmarks/test_locomo_runner.py::test_single_run_summary_json_is_unchanged
# Output: 64 passed in 5.78s

# Full LoCoMo runner, protocol, store backup & worker version tests
uv run pytest \
  src/tests/benchmarks/test_locomo_protocol.py \
  src/tests/benchmarks/test_locomo_runner.py \
  src/tests/benchmarks/test_locomo_store_backup.py \
  src/tests/workers/test_d119_versions.py
# Output: 151 passed, 2 skipped in 7.67s

# Semantic validity & entity guard tests
uv run pytest \
  src/tests/workers/test_claim_valid_time.py \
  src/tests/workers/test_e3_bare_head_noun.py
# Output: 26 passed in 2.91s

# PostgreSQL tests against lane database on port 55441
REMEMBERSTACK_DATABASE_URL="postgresql+psycopg://rememberstack:rememberstack_test@127.0.0.1:55441/ugm_d120_test" \
  uv run pytest \
    src/tests/spine/test_concise_adjudication_pg.py \
    src/tests/workers/test_e2_chain.py
# Output: 11 passed (7 concise pg + 4 e2 chain) in 20.0s

# Additional PG regression sanity checks on port 55441
REMEMBERSTACK_DATABASE_URL="postgresql+psycopg://rememberstack:rememberstack_test@127.0.0.1:55441/ugm_d120_test" \
  uv run pytest \
    src/tests/spine/test_fact_application_writer.py \
    src/tests/workers/test_claimify_loss_ledger_pg.py \
    src/tests/surfaces/test_retrieval_api.py
# Output: 33 passed in 141.72s
```

---

### Audit of Prior Review Nits (`ecedaabe`)

All three concrete findings identified in [`d120_d121_agy_review_ecedaabe.md`](../../plan/implementation_evals/d120_d121_agy_review_ecedaabe.md#L127-L161) were confirmed fixed:

1. **Normalizer zero-or-more**: Fixed in [`src/rememberstack/workers/e3.py:83`](../../src/rememberstack/workers/e3.py#L83) (`"Turn the CLAIM into zero or more assertions: each assertion is one relation or one observation."`), removing the contradiction with the DECISION RULES.
2. **Reserved T/W schema names**: Fixed in [`src/rememberstack/model/concise_adjudication.py:26`](../../src/rememberstack/model/concise_adjudication.py#L26) (`"It must not reuse a supplied F, C, A, E, S, T, or W name."`).
3. **Stale generation docs & variant comments**: Fixed in [`benchmarks/locomo/README.md:3-6`](../../benchmarks/locomo/README.md#L3-L6) and [`benchmarks/locomo/protocol.py:126`](../../benchmarks/locomo/protocol.py#L126) (updated to Full-v30, `assertion-clarity-3`, and `concise-handles-5`).

---

### Upstream D119 Stack Base Delta (`3cdf8746..d808164b`)

The delta between approved runtime `3cdf8746` and `d808164b` was inspected:
- It contains only the rename from Full-v28 to Full-v29, frozen fingerprint refresh (`71fb5551...`), test updates, and durable review artifacts ([`d119_agy_review_3cdf8746.md`](../../plan/implementation_evals/d119_agy_review_3cdf8746.md), [`d119_cursor_review_3cdf8746.md`](../../plan/implementation_evals/d119_cursor_review_3cdf8746.md), [`d119_implementation_20260914.md`](../../plan/implementation_evals/d119_implementation_20260914.md)).
- All 151 benchmark unit/runner tests pass against `d808164b`.
- There are no protocol divergences or breaking changes introduced in the D119 base.

---

### Detailed Review of PR402 Delta (`d808164b..b575c556`)

#### 1. Instructions & Plain Language Clarity
- **E2 Selection & Claimify ([`e2.py:163-286`](../../src/rememberstack/workers/e2.py#L163-L286))**:
  - The parent's rewrite translates formerly dense specifications into plain English: "a reader should understand who or what each claim refers to without seeing the surrounding conversation."
  - Coherent multi-sentence statements are kept intact (illustrated with Joanna's third screenplay and its three themes) while independently dated or independently attributed statements remain separated.
  - Origin vs. supporting citations are clearly stated: the first entry in `source_refs` must be an origin-eligible target passage overlapping a Selection keep; subsequent entries supply permitted context.
  - Section summaries are unambiguously identified as orientation aids only, never source evidence.
- **E3 Normalizer ([`e3.py:82-140`](../../src/rememberstack/workers/e3.py#L82-L140))**:
  - Defines the core vocabulary clearly: `"A claim is what the source said. An assertion is one relation or observation taken from it. A later stored fact will interpret testimony about that assertion. People and events are entities, not the proposition."`
  - Explains the scoped `uses_claim_window` rule concretely: applies only to the specific assertion for which the raw source window is valid testimony (e.g. 2019 hiring vs 1990 founding).
- **Fact Adjudication ([`fact_adjudication.py:38-114`](../../src/rememberstack/spine/fact_adjudication.py#L38-L114))**:
  - Direct prose without duplicate sections. World dates are defined at first appearance.
  - Plain guidance on date corrections vs new facts: the writer cannot rewrite an existing statement, but date corrections for date-neutral statements keep the same fact and replace its chosen window with cited evidence (`"won Tournament A"` changing chosen dates from 5 Nov to 6 Nov).

#### 2. Assertion Meaning Preservation
- Bidirectional distinction between entity sharing and proposition sharing is articulated consistently across E2, E3, and Fact Adjudication:
  - *"Winning, participating and enjoying that tournament are different propositions. A win implies participation, but storing only participation loses the result. Conversely, participation or enjoyment is not positive evidence of a win. Preserve each assertion's meaning; repeated reports of the same win belong to the winning fact."*
- Attributed stances are kept distinct from facts: `"Nate said he won" must never become an unqualified "Nate won"` (E2) and `"Nate claimed to win" does not establish "Nate won"` (Fact Adjudication).

#### 3. Source vs. World Dates
- Clear distinction maintained across all levels:
  - `source_said_at`: when the source spoke or published (never a fallback world date).
  - `source_world_*`: raw source dates with inclusive ends.
  - `chosen_*`: stored canonical UTC bounds with exclusive ends (`[3 Nov, 6 Nov)` for day precision).
- Succession updates: explicitly instructed to cap predecessors at the successor's *world* start, never publication time or `now`.
- Temporal resolution in E2: relative expressions are resolved against the header anchor and written directly into `claim_text` as ISO values while cited source passages preserve verbatim source wording.

#### 4. Witness Semantics & Unhydrated Witnesses
- In [`project_concise_inputs`](../../src/rememberstack/core/concise_adjudication.py#L272-L289), unhydrated window witnesses (present in stored fact's `window_claim_ids` but omitted from the bounded snapshot's claims payload) are assigned deterministic `W` handles (`W1`, `W2`, ...) under `window_claims_not_supplied`.
- The prompt explicitly warns: `"W-names disclose window witnesses whose text is not supplied; you may not cite them."`
- The translator [`_translate_window`](../../src/rememberstack/core/concise_adjudication.py#L503-L509) strictly requires supporting claims to be `C` handles from `mapping.claims`; citing a `W` handle immediately raises `ValueError("handle W1 is not a claim name")`.
- Verified in PostgreSQL integration: `test_unhydrated_window_witness_is_named_but_not_citable` passes.

#### 5. Handle Translation to Existing Writer
- Model-facing handles (`F`, `C`, `A`, `E`, `S`, `T`, `W`) are bijective and derived deterministically per snapshot.
- Model produces [`PromptFactDecision`](../../src/rememberstack/model/concise_adjudication.py#L86).
- [`translate_prompt_decision`](../../src/rememberstack/core/concise_adjudication.py#L512) converts it directly into [`FactApplicationDecision`](../../src/rememberstack/model/fact_application.py#L65).
- Reserved prefixes `F/C/A/E/S/T/W` are forbidden for declared new facts.
- Stale attempt protection: guarded by compare-and-swap on `attempt_id` and input fingerprint, not handle spelling.

#### 6. Architecture Integrity: Locking & Categories
- **No Models Under Locks**: [`FactAdjudicator.prepare`](../../src/rememberstack/spine/fact_adjudication.py#L205) locks candidate rows, snapshots inputs, commits the attempt record, and releases the DB connection. In [`FactAdjudicator.drain`](../../src/rememberstack/spine/fact_adjudication.py#L158-L180), model generation and response translation execute completely outside transactions and locks. [`FactApplicationCatalog.apply_attempt`](../../src/rememberstack/spine/fact_applications.py#L326) then applies the decision atomically.
- **No Classifiers / Secondary Checkers**: Decisions are single-step and schema-constrained without secondary verify calls.
- **No New Categories or Windows**: Preserves existing single chosen-window model without extra event/state taxonomies.

#### 7. Size Measurements Verification
The offline byte comparison table in [`d120_d121_implementation_20260914.md:150-155`](../../plan/implementation_evals/d120_d121_implementation_20260914.md#L150-L155) was verified by running `_prompt_schema_size_report` on the exact test fixtures:

| Fixture | What it is | full snapshot | compact input | old prompt+schema | new prompt+schema |
| :--- | :--- | ---: | ---: | ---: | ---: |
| small mostly-unique | 1 fact, distinct wording | 2054 | 1332 | 9345 | 10631 |
| varied reconstructed | 6 facts, shared source span, 1 repeat | 10166 | 4955 | 17457 | 14254 |
| bounded 20 varied | 20 facts, 40 claims, 40 applications | 57142 | 28760 | 64433 | 38059 |
| best-case repeated | 20 copies of 1 long sentence | 66267 | 17080 | 73558 | 26379 |

- Schema alone: old UUID schema 4462 bytes; concise handle schema 4710 bytes.
- Prompt template: 4597 bytes.
- Docs accurately characterize these as UTF-8 bytes and avoid making unsubstantiated tokenizer, billed-cost, or benchmark-score claims.
- Historical evaluation sections (`5273eee8`, `ef17559a`) are properly labeled as prior checkpoints.

---

### Protocol & Sharding Consistency

- **LoCoMo Protocol**: Bumped to `RS-LoCoMo-Full-v30`, adapter version `locomo-full-adapter-2026.09-concise-adjudication-v30`.
- **Extractor Generation**: `...:assertion-clarity-3` ([`e1.py:67`](../../src/rememberstack/workers/e1.py#L67), [`protocol.py:77`](../../benchmarks/locomo/protocol.py#L77)).
- **Normalizer Generation**: `...:assertion-clarity-3` ([`fact_adjudication.py:35`](../../src/rememberstack/spine/fact_adjudication.py#L35), [`protocol.py:82`](../../benchmarks/locomo/protocol.py#L82)).
- **Adjudicator Generations**: `relation-adjudicator-2026.09d:concise-handles-5` and `obs-adjudicator-2026.09d:concise-handles-5` ([`fact_adjudication.py:33-34`](../../src/rememberstack/spine/fact_adjudication.py#L33-L34), [`protocol.py:85`](../../benchmarks/locomo/protocol.py#L85)).
- **Fingerprint**: Pinned serialized run summary matches in [`test_locomo_runner.py:1934`](../../src/tests/benchmarks/test_locomo_runner.py#L1934) (`7c0f0501895d1c219667e807d003e0ccb154bef5accc97dbbcbdded957a4ab72`).
- **Sharding & CLI Defaults**: [`run_shard.sh:28,62`](../../benchmarks/locomo/sharding/run_shard.sh#L28) and [`sharding/README.md:181`](../../benchmarks/locomo/sharding/README.md#L181) default to `full-v30`.

---

### Findings

#### Blockers
*None.*

#### Material Findings
*None.*

#### Nits
1. **Minor example phrasing vestige in [`e2.py:295`](../../src/rememberstack/workers/e2.py#L295)**:
   - *Detail*: In the Claimify prompt Example 1, lines 294–295 read:
     `Note the quote form: the resolved date replaces "yesterday" even inside the attributed speech; source_span keeps the verbatim wording.`
     In the multi-span Claimify output schema, claims emit `source_refs` (which cite source passages) rather than a single `source_span` field (which is emitted by Selection candidates). The instruction at line 256 already correctly states: `"the verbatim wording is preserved by the cited source passages; claim_text stands alone"`.
   - *Recommendation*: In parent's final pre-merge touch, line 295 can be adjusted to:
     `attributed speech; the cited source passages keep the verbatim wording.`
     *(Zero functional or schema impact, as Claimify model output does not have a `source_span` field).*

---

### Verdict

**APPROVE** (Scoped to PR402 at `b575c5567ab87f7225b43fe271ba9b36019ce08f`).
All unit, format, type, contract, and PostgreSQL integration tests pass cleanly. The instructions achieve high plain-language clarity, fully preserve assertion identity and date distinctions, and translate safely to the existing writer without changing locking or database structures. Ready for parent final adjustments and merge.
