# Review — BEAM official nugget scorer + P2 projection hang diagnosis

- **Reviewer:** claude-fable
- **Date:** 2026-08-07
- **Scope:** commit `2a94ffd2` *feat(benchmarks): BEAM official nugget LLM-judge scorer (#226)* (already on `main`)
- **Artifacts reviewed:**
  - `benchmarks/rs_harness_beam/official_score.py` (484 lines)
  - `benchmarks/rs_harness_beam/cli.py` (157 lines)
  - `benchmarks/rs_harness_beam/fixtures/` (BEAM 100K/1 rubrics, unified judge prompt)
  - `src/tests/benchmarks/test_beam_official_score.py` (75 lines)
  - `design/operations/p2-projection-hang-beam-smoke.md` (66 lines)

## Method

Everything below was checked against files on disk, not inferred. Specifically:

- Ran the gates: `ruff check` **pass**, `ruff format --check` **pass**, `pyright --pythonversion 3.13` **0 errors**, `pytest src/tests/benchmarks/test_beam_official_score.py` **6 passed**. The change is green on every gate CI runs (`.github/workflows/ci.yml:60-64`).
- Executed `_kendall_tau_b` against a reference τ-b implementation on tie-bearing inputs (see F-3).
- Fetched upstream `src/evaluation/compute_metrics.py` and `src/evaluation/run_evaluation.py` from `mohammadtavakoli78/BEAM` to ground the faithfulness section rather than reasoning from the paper alone.
- Cross-read the in-repo OpenRouter adapter (`src/rememberstack/adapters/openrouter.py`) and the P2 projection path (`src/rememberstack/spine/projection.py`, `src/rememberstack/workers/p2.py`) to check the new code against established house patterns and to verify the hang doc's claims against real symbols.

---

## 1. Verdict

### **Accept with changes**

The metric math is a genuinely faithful port of upstream BEAM — I verified the nugget mean, the `split("\n")` event extraction, the `(τ_b + 1) / 2` normalization, and `final_score = tau_norm * f1` all match `compute_metrics.py`. That is the hard part and it is right. The placeholder-containment scorer is correctly retained and correctly labelled as non-authoritative (`cli.py:34`). Tests, lint, and pyright are green.

**There are no P0 findings, and I want to be explicit about that rather than manufacture one.** The one candidate (F-2, the `max_tokens=800` reasoning-model trap) is contradicted by evidence: the live smoke run completed and produced `overall_mean ≈ 0.5625`. It is fragile, not broken.

What holds this back from a clean Accept is that this is a **scorer that spends real money and currently has all-or-nothing failure semantics**: a single transient network blip, a single rate-limit, or a single rubric-text mismatch discards every judge call already paid for in that run and writes nothing to disk (F-1). Upstream is more robust here than our port. That plus the error-message hygiene violation (F-4) should land before the next paid run.

**One headline correction matters beyond the code:** `overall_mean` has **no counterpart in upstream BEAM** (F-6). It is an RS-invented aggregate. The `0.5625` figure should not be reported or compared as "our BEAM score" until that is either removed or explicitly relabelled.

---

## 2. Findings

| ID | Sev | Title | Location |
|----|-----|-------|----------|
| F-1 | **P1** | All-or-nothing run: one transient failure discards every paid judge call | `official_score.py:198-209`, `376-476` |
| F-2 | **P1** | `max_tokens=800` against a reasoning-model default; repo standard is 32k | `official_score.py:44,185` |
| F-3 | **P1** | `_kendall_tau_b` mis-handles pairs tied in both lists (deviates from upstream's SciPy τ-b) | `official_score.py:276-303` |
| F-4 | **P1** | Error strings echo model output and provider error bodies, against an explicit in-repo rule | `official_score.py:141,204,209` |
| F-5 | **P1** | Snapshot can strand in `building` — the stated invariant only covers exceptions, not hangs/kills | `workers/p2.py:268-277` |
| F-6 | **P1** | `overall_mean` is not a BEAM metric; micro-averaging silently reweights abilities | `official_score.py:454-470` |
| F-7 | **P2** | Banned-API `noqa` + duplicated OpenRouter client instead of reusing `OpenRouterSettings` | `cli.py:81-83`, `official_score.py:156-209` |
| F-8 | **P2** | `--api-key` on argv exposes the key to `ps` and shell history | `cli.py:152-156` |
| F-9 | **P2** | `parse_judge_json` fallback raises an unwrapped `JSONDecodeError` | `official_score.py:138-141` |
| F-10 | **P2** | `_clamp_score` docstring contradicts behaviour; off-scale judge scores pass through | `official_score.py:144-153` |
| F-11 | **P2** | Event-ordering alignment is O(n×m) sequential paid calls, silently | `official_score.py:254-273` |
| F-12 | **P2** | `_NUGGET_ABILITIES` is dead code | `official_score.py:47-60` |
| F-13 | **P2** | Untrusted system-under-test output is interpolated raw into the judge prompt | `official_score.py:221-228` |
| F-14 | **P2** | Nugget judging is paid for on `event_ordering` items, then dropped from `primary_score` | `official_score.py:418-421` |
| F-15 | **P2** | Test suite covers only the tie-free τ-b path and no HTTP/judge behaviour | `test_beam_official_score.py:58-60` |
| F-16 | **P2** | No runner produces the `run_dir` contract the scorer consumes | `benchmarks/rs_harness_beam/` |
| F-17 | **P2** | `llm_equivalence` substring check misreads hedged replies | `official_score.py:242-251` |
| F-18 | **P2** | `score-official` is undocumented in the benchmark runbook | `design/benchmarks/runbook.md` |

---

### P1 findings

#### F-1 — All-or-nothing run: one transient failure discards every paid judge call

`official_score.py:198-209` catches only `urllib.error.HTTPError`. A read timeout raises `TimeoutError`, and a connection reset or DNS failure raises `urllib.error.URLError` — **neither is an `HTTPError`**, so neither is converted to `OfficialScoreError`. The CLI's handler (`cli.py:98-100`) catches `OfficialScoreError` only, so these surface as a raw traceback.

Worse than the traceback is the loss. `score_run_dir_official` writes the report only *after* the full item loop completes (`official_score.py:473-474`). Every nugget judged before the failure — real, billed OpenRouter calls — is discarded. On a 20-item fixture with 1–6 nuggets each, item 18 failing throws away ~50 paid calls and leaves nothing on disk.

An HTTP 429 or 503 produces the same total loss, just via a cleaner error message. There is no retry, no backoff, and no incremental persistence anywhere in the module.

This is also the one place the port is **less robust than upstream**: `run_evaluation.py` wraps each ability in `try/except` with `traceback.format_exc()` and continues, saving results per ability. Ours aborts the whole run.

**Fix:** retry `429`/`5xx`/`URLError`/`TimeoutError` with bounded exponential backoff; write the report incrementally (or checkpoint judged items to a sidecar keyed by `question_id` + rubric hash so a rerun resumes); catch `URLError`/`TimeoutError` and convert to `OfficialScoreError` so the CLI degrades to a clean exit code.

#### F-2 — `max_tokens=800` against a reasoning-model default

`official_score.py:185` hardcodes `"max_tokens": 800`, and `:44` defaults the judge to `openai/gpt-5.6-luna`. This repo already documents that model as a reasoning model — it is the worked example in the adapter's own effort-map docstring (`adapters/openrouter.py:110-117`) — and the adapter deliberately sets `_DEFAULT_MAX_COMPLETION_TOKENS = 32_000` (`:37`) with the rationale spelled out at `:87-89`:

> *"The 32k default gives reasoning models deliberate generation headroom."*

`max_tokens` on OpenRouter is a **combined reasoning-and-content** budget. At 800, reasoning tokens can consume the whole allowance, returning empty `content`; `parse_judge_json` then raises `judge returned non-JSON: ''` and, per F-1, kills the run. The scorer also sends no `reasoning` parameter, so it inherits whatever the provider defaults to.

I am **not** claiming this is currently failing — the live smoke run completed, which is direct evidence it does not always trigger. But it is one `REMEMBERSTACK_OPENROUTER_REASONING_EFFORT_MAP` change or one verbose `reason` field away from truncating, and the repo already learned this lesson once (see the `_completion_diagnosis` docstring at `adapters/openrouter.py:409-417`, written because *"a recurring production failure had no diagnosable cause"*).

**Fix:** raise the budget to the house default and pin effort explicitly; distinguish empty content from malformed JSON in the error path, reusing `_completion_content` / `_completion_diagnosis` semantics.

#### F-3 — `_kendall_tau_b` mis-handles pairs tied in both lists

`official_score.py:288-290` skips any pair where `dx == 0 and dy == 0`. A pair tied in *both* rankings must be counted in **both** tie corrections (`n1` **and** `n2`); skipping it undercounts both, inflating the denominator `sqrt((n0-n1)(n0-n2))` and deflating |τ|.

Measured against a reference implementation:

| x | y | this impl | correct τ-b |
|---|---|-----------|-------------|
| `[1,1,2]` | `[1,1,2]` | 0.6667 | **1.0** |
| `[1,1,2,2]` | `[1,1,2,2]` | 0.6667 | **1.0** |
| `[1,2,2]` | `[3,4,4]` | 0.6667 | **1.0** |
| `[1,2,3]` | `[1,2,3]` | 1.0 | 1.0 |

Two identical rankings scoring 0.67 is unambiguously wrong for a function whose docstring claims "tie correction".

**Reachability — stated honestly:** I traced `to_rank` (`:324-326`) and could **not** construct a live input that hits the buggy branch. `union` is deduplicated, and an item absent from one list always has a real rank in the other, so `dx == 0 && dy == 0` cannot arise through `event_ordering_score` today. So this is **latent, not currently corrupting scores.**

It is still P1 because: (a) it is a deviation from upstream, which uses SciPy's `kendalltau(variant="b")` — correct by construction; (b) the helper is module-level, exported, and unit-tested as a general τ-b, so reuse is invited; (c) the natural refactor of assigning missing items a shared tie rank in *both* lists makes it reachable immediately, and it would fail silently — deflated scores, no error.

The adjacent `n < 2 → return 0.0` (`:277-279`) is likewise defensible-but-odd: it flows to `tau_norm = 0.5` rather than a neutral or undefined marker. Also unreachable with the current fixture (EO rubrics have 3 and 5 nuggets), worth a comment.

**Fix:** count both-tied pairs in `ties_x` and `ties_y`, and extend the test to the tie cases in the table above.

#### F-4 — Error strings echo model output and provider error bodies

`adapters/openrouter.py:409-417` states a deliberate, reasoned rule for this codebase:

> *"This is deliberately metadata-only. Model output can restate customer material, and these strings reach `processing_state.last_error` and the logs, so no completion text, prompt, provider error message, or credential is ever included -- only flags, lengths, and enumerated reasons."*

The new module breaks that rule in three places:

- `official_score.py:141` — `f"judge returned non-JSON: {text[:200]!r}"` embeds **model output**.
- `official_score.py:204` — `f"OpenRouter judge HTTP {error.code}: {detail}"` embeds up to 500 chars of the **provider error body**, which the adapter docstring specifically calls out as able to echo the prompt.
- `official_score.py:209` — `f"unexpected OpenRouter body: {body!r}"` dumps the **entire response body**.

The judge prompt contains the system-under-test's answer, which in any non-synthetic run is derived from ingested user material. These strings reach stderr and operator logs.

Today's BEAM fixture is public synthetic data, which is why this is P1 and not higher. But this scorer is built to be pointed at real run dirs, and the rule exists precisely so this is not decided case-by-case.

**Fix:** report metadata only — status code, content length, `finish_reason`, a JSON-parse flag. Reuse `_completion_diagnosis`.

#### F-5 — Snapshot can strand in `building`

`workers/p2.py:269-272` asserts an invariant:

> *"NO failure may strand a snapshot as eternally 'building'"*

That guarantee is implemented as an `except Exception` block (`:268-277`), so it holds **only for Python exceptions**. The two failure modes actually observed on the BEAM smoke — an indefinite block inside `watermark()`, and the operator killing the container — never reach `except`. The row stays `building` forever.

The hang doc's own workaround #2 (a manual `UPDATE projection_snapshots SET status='failed'`) is direct evidence the invariant is broken in practice. I found no reaper or stale-snapshot sweep anywhere in `src/rememberstack`.

This is a real correctness gap in the product path, surfaced by the benchmark rather than caused by it — and it is the finding here with the longest reach beyond benchmarking.

**Fix:** see §5 — a statement timeout converts the hang into an exception the existing handler already covers, which closes most of this. A startup sweep for stale `building` rows closes the SIGKILL case.

#### F-6 — `overall_mean` is not a BEAM metric

`official_score.py:454-458` computes `overall_mean` as a **micro-average over items**; `by_ability` (`:450-453`) is a per-ability mean. `cli.py:101-108` prints `overall_mean` as the headline.

Upstream `run_evaluation.py` accumulates results as `{ability: [results]}` and **never computes a single scalar**. The BEAM paper reports a per-ability table. So `overall_mean` is an RS invention presented under the `"scorer": "beam_official_nugget_llm_judge"` and `"protocol": "RS-Harness-BEAM-v1"` labels, which invites exactly the comparison it cannot support.

The mechanism also has a silent trap. The fixture has a uniform 2 items per ability across 10 abilities, so micro == macro **only by coincidence**. Score a subset — or add a third probe to one ability — and the headline silently reweights toward the abilities with more items, with no warning and no change to the field name.

I could not verify the item count behind the reported `0.5625` without the run dir. **Check the `n` field in `score_report_official_rs.json`:** if `n != 20`, the abilities were already unevenly weighted in that number.

**Fix:** report the macro-average over abilities as the headline (matching how BEAM results are actually presented), keep the micro-average only if separately named (`overall_micro_mean`), and emit per-ability `n` so imbalance is visible.

### P2 findings

**F-7 — Banned-API `noqa` and a duplicated OpenRouter client.** `cli.py:81-83` carries `# noqa: TID251 — CLI boundary only`. This is the **only** TID251 suppression in the repository, and the rule it suppresses has an explicit rationale at `pyproject.toml:122-124`: *"All configuration goes through pydantic-settings... Env vars are read in exactly one place — a BaseSettings class — never ad hoc."* Meanwhile `OpenRouterSettings` (`adapters/openrouter.py:76-83`) already reads `REMEMBERSTACK_OPENROUTER_API_KEY` via `env_prefix`, with `timeout_s`, `base_url`, `max_completion_tokens`, and effort mapping — every knob F-2 wants. `OpenRouterJudge` (`official_score.py:156-209`) hand-rolls a second client on `urllib` where the house client uses `httpx`. Reusing `OpenRouterSettings` removes the `noqa`, the duplicate client, and most of F-1/F-2 at once. *(For accuracy: `OpenRouterSettings.api_key` is a plain `str`, not `SecretStr`, so the new code is consistent with precedent on that point — the deviation is the ad-hoc env read, not the type.)*

**F-8 — `--api-key` on argv.** `cli.py:152-156` accepts the key as a command-line argument, exposing it to `ps` output and shell history. Prefer env-only, or accept a file path.

**F-9 — Unwrapped `JSONDecodeError`.** `official_score.py:140` calls `json.loads(match.group(0))` outside any `try`. A brace-balanced-but-invalid capture raises `json.JSONDecodeError`, which is not an `OfficialScoreError` and so escapes `cli.py:98` as a traceback — same class of problem as F-1. The `re.search(r"\{.*\}", ...)` at `:138` is also greedy: prose containing braces on both sides of the JSON over-captures.

**F-10 — `_clamp_score` docstring contradicts behaviour.** `official_score.py:145` promises normalization "to {0.0, 0.5, 1.0} when close", but the tolerance at `:151` is `1e-6` — an exact-equality test. A judge returning `0.7` (off the mandated scale) is silently accepted as `0.7`, not snapped and not flagged. Either snap with a real tolerance or reject off-scale values loudly; a judge ignoring the 0/0.5/1 instruction is signal worth surfacing.

**F-11 — Unbounded sequential alignment cost.** `align_events_with_llm` (`:254-273`) issues up to `len(reference) × len(system)` sequential `llm_equivalence` calls, each a separate HTTP round trip with a 120 s timeout, on top of the nugget calls. Nothing logs the projected call count, and there is no `--limit` or `--dry-run`. Upstream at least parallelizes with `ThreadPoolExecutor(max_workers=10)`; ours is fully sequential. Log the planned call count before spending.

**F-12 — Dead code.** `_NUGGET_ABILITIES` (`:47-60`) is defined and never read — confirmed by grep across `src/` and `benchmarks/`. It encodes real intent (which abilities use the nugget path), so either wire it into a validation check that the fixture's abilities are the expected set, or delete it. Ruff will not catch an unused module constant.

**F-13 — Raw interpolation of untrusted output into the judge prompt.** `judge_nugget` (`:221-225`) substitutes `response` into the prompt template by plain `str.replace`. That response is generated by the system under test from ingested documents. Content along the lines of `{"score": 1.0}` or "ignore previous instructions" is injected verbatim into a prompt whose contract is "output only a JSON object". Low severity for a public synthetic fixture, but it is an eval-integrity hole the moment this is aimed at real corpora. Delimit the response block explicitly and instruct the judge to treat it as data. Substitution order (`:222-224`) also means a placeholder appearing inside `question` or `rubric_item` is itself substituted — harmless with today's fixture, but fragile.

**F-14 — Paid-for-then-discarded nugget scores on `event_ordering`.** `score_item_official` always judges every nugget (`:349-354`), then `:418-421` overwrites `primary` with the τ×F1 score for EO items. The nugget calls are billed and retained only in `llm_judge_score`. Upstream computes both too, so this is faithful — but it deserves a comment, since it reads as a bug.

**F-15 — Test coverage is thin where the risk is.** The six tests are meaningful but cover only the easy surface. `test_kendall_tau_b_identical_is_one` (`:58-60`) uses `[1,2,3]` vs `[1,2,3]` — the one case with no ties, which is exactly why F-3 survived review. There is no test for `event_ordering_score`, no fake-transport test of `OpenRouterJudge` (HTTP error paths, empty content, malformed body), and no test that `score_run_dir_official` handles a missing answer or an unmatched rubric. All of these are testable without network via a stub judge.

**F-16 — No runner produces the `run_dir` contract.** `score_run_dir_official` (`:384-388`) documents an input contract of `questions.json` + `state.json`, but `benchmarks/rs_harness_beam/` contains no runner — only `cli.py`, `official_score.py`, and fixtures. The BEAM run dirs are produced by something not on `main`, so the reported `0.5625` is not reproducible from this repository. (Contrast `benchmarks/locomo/runner.py`, which is checked in.) At minimum, document the contract with a committed example; better, commit the runner.

**F-17 — `llm_equivalence` substring check.** `official_score.py:251` returns `"yes" in raw` over the lowercased reply. The system prompt does ask for a bare YES/NO, but a hedged reply — "No, though one could say yes if..." — scores as a match. Compare against the stripped leading token instead.

**F-18 — Runbook not updated.** `design/benchmarks/runbook.md` documents benchmark operation but does not mention `score-official`, its key requirement, or its cost profile. This is benchmark tooling rather than a shipped user surface, so I read the D66 same-PR docs obligation as not strictly triggered — but the runbook is where an operator would look, and `/docs/project-status` makes no BEAM claim needing correction (verified).

---

## 3. Faithfulness vs upstream BEAM

Checked against `mohammadtavakoli78/BEAM` `src/evaluation/compute_metrics.py` and `run_evaluation.py`.

### Verified faithful

| Aspect | Upstream | This port |
|--------|----------|-----------|
| Nugget aggregation | `sum(scores) / len(rubric)` | `mean_nugget_score` (`:235-239`) — identical |
| Judge scale | 0.0 / 0.5 / 1.0 per rubric item | Same, via the committed unified prompt |
| Judge prompt | Unified rubric-criterion prompt | Fixture is a faithful copy with `<question>` retained |
| EO event extraction | `llm_response.split("\n")` | `response.splitlines()` (`:358`) — same approach |
| EO alignment | `align_with_llm()`, 1-to-1, first match wins | `align_events_with_llm` (`:254-273`) — same greedy semantics |
| τ normalization | `tau_b_norm = (tau_b + 1) / 2` | `:329` — identical |
| EO final score | `final_score = tau_b_norm * f1` | `:335` — identical |
| F1 | `2PR/(P+R)` from tp/fp/fn | `:316-320` — identical |
| Ability set | 10 abilities | Fixture has exactly those 10; `_NUGGET_ABILITIES` is the 9 non-EO ones — consistent |

The core metric is a correct port. That is worth saying plainly.

### Gaps and deviations

1. **`overall_mean` has no upstream counterpart** (F-6). Upstream produces `{ability: [results]}` and no scalar. This is the most consequential gap because it is the number being quoted.

2. **τ-b is reimplemented rather than delegated** (F-3). Upstream calls SciPy's `kendalltau(variant="b")`, correct by construction. The hand-rolled version is wrong on both-tied pairs. SciPy is presumably avoided as a benchmark dependency, which is reasonable — but then the reimplementation must be tested against the cases SciPy handles.

3. **Blank-line handling deviates, in our favour.** Upstream uses a bare `split("\n")`; `:358` filters empty lines and strips whitespace. Upstream would count blank lines as system events, inflating false positives and depressing precision. Ours is arguably the better metric — but it is a deviation that **raises our score relative to upstream**, so it must not be presented as a like-for-like number. The `if not system_list` fallback at `:359-360` has no upstream equivalent either.

4. **Failure semantics deviate, against us** (F-1). Upstream catches per-ability and continues, persisting partial results; ours aborts the whole run and writes nothing.

5. **Concurrency deviates** (F-11). Upstream uses `ThreadPoolExecutor(max_workers=10)`; ours is sequential. Wall-clock only, no correctness impact.

6. **Judge model differs by necessity.** Upstream uses `gpt_llm` from `src.llm`; we use `openai/gpt-5.6-luna` via OpenRouter. Unavoidable, but it means absolute numbers are not comparable to the published table — judge identity materially affects nugget scoring. `judge_model` is recorded in the report (`:466`), which is the right call; the comparability caveat should be stated in the runbook.

7. **Rubric matching is exact-string** (`match_rubric`, `:113`). Upstream pairs by position within its own data layout, so it has no equivalent failure. Any whitespace, curly-quote, or unicode drift between our `questions.json` and the fixture aborts the entire run mid-way (F-1). Normalizing whitespace and quotes before comparison would remove a whole class of expensive late failures.

**Bottom line:** per-ability BEAM scores from this scorer are defensible for internal tracking with the judge-model caveat stated. The single `overall_mean` figure is not a BEAM metric and should not be presented as one.

---

## 4. Security and operational issues

**Secrets.** F-8 (`--api-key` on argv → `ps`, shell history) and F-7 (`os.environ` read bypassing the sole-BaseSettings rule). No key is logged today — `:209` dumps the response body, not the request headers — but the key is held as a plain `str` on `OpenRouterJudge._api_key` (`:171`) with no `__repr__` guard, so any future object dump exposes it.

**Data exposure.** F-4 is the substantive one: model output, provider error bodies, and full response bodies flow into error strings, against a documented and reasoned in-repo rule. Separately, `score_report_official_{arm}.json` (`:473`) persists full question text, full response text, and judge reasoning to the run dir. That is *correct* for an auditable eval — the scores are meaningless without it — but the run dir then contains everything the system said about the corpus. `.gitignore` covers `.benchmark-runs/`; it does not cover an arbitrary `--run` path. Worth a line in the runbook.

**Timeouts.** 120 s per call (`:164`) matches the adapter default — fine. But there is no *total* budget. Worst case for one EO item is `(nuggets + |ref|×|sys|)` sequential calls; with the 5-nugget EO probe and a 5-line answer that is 30 calls at up to 120 s each, ~60 min for a single item, all silent. Add a wall-clock budget and progress logging.

**Cost.** Unbounded and unreported. Total calls = `Σ nuggets` (28 for the committed fixture) + EO alignment (up to `|ref|×|sys|` per EO item, unbounded in `|sys|` since it derives from model output — a chatty 40-line answer against the 5-event rubric is 200 calls from one item). There is no `--dry-run`, no `--limit`, no pre-flight estimate, and no post-run token accounting. Combined with F-1's discard-everything failure mode, a run can burn budget and produce no artifact. **This is the pairing I would fix first: bounded cost and durable partial results.**

**Reproducibility.** F-16: no committed runner means the headline number cannot be regenerated from `main`.

---

## 5. Is the P2 hang diagnosis sufficient?

**Yes — the diagnosis is sound, and I verified every structural claim against code.** This is a good document: it separates symptom, root cause, and evidence; it records the negative finding (*"This is not 'still building graph offline with no logs'"*), which is exactly the kind of thing that saves the next investigator hours; and it gives operators runnable workarounds.

Verified:

- `GraphExport.watermark()` exists at `spine/projection.py:75`, and `_SELECT_WATERMARK` (`:673-679`) is verbatim the SQL the doc names.
- `jit = off`, `join_collapse_limit = 1`, `from_collapse_limit = 1` are set in `graph_export` (`:151-153`) as described.
- Call order in `workers/p2.py` is `unresolved_survivors()` (`:294`) → `watermark()` (`:308`) → `rows()` (`:316`), which confirms the doc's "no parquet files written" — the block happens before any row streaming, and after the survivor gate has already passed.

**One thing the doc misses, and it sharpens the fix.** `graph_export` builds an indexed `TEMP TABLE graph_survivor` (`:155-156`, `:417-428`) with the stated purpose that *"the indexed temp survivor table keeps every edge join linear"* (`:141`). The export queries use it — `_EXPORT_SQL` joins `graph_survivor` directly (`:504`). **`_SELECT_WATERMARK` does not.** It reaches through `memory_v1.graph_edges_visible_history`, which re-derives survivorship via `entities_current` from scratch.

So the precise root cause is tighter than "the view is heavy": *the watermark is the one query on the export connection that bypasses the survivor map the connection was specifically set up to provide.* Every other read on that connection is linear; this one re-expands the full provenance chain. That also explains why `unresolved_survivors()` at `:294` completes fine while `:308` hangs.

The doc's follow-up list already gestures at this ("export temp survivor map"), but states it as one option among three rather than as the identified root cause.

### Fix priority

**1. Statement timeout on the export connection — do this first.** No `statement_timeout` is set anywhere in the projection path (verified across `src/rememberstack`), even though the repo already uses the pattern elsewhere (`query_space/memory_v1_manifest.json:1428-1429` sets 60 s default / 60 s hard for open queries). Adding `SET LOCAL statement_timeout` in `graph_export` (alongside the existing `SET LOCAL`s at `:151-154`) converts an indefinite hang into a `QueryCanceled` exception — which the handler at `workers/p2.py:268-277` **already** catches and records as `failed`. One line largely closes F-5, turns a silent stall into a diagnosable failure, and is safe to ship independently of any query rewrite.

**2. Rewrite the watermark against the survivor map.** Derive `max(ingested_at)` from the base relation rows joined to the temp `graph_survivor` table, matching what `_EXPORT_SQL` already does. This preserves the D7 property the docstring requires (`:76-80`) — read on the export connection, inside the same REPEATABLE READ cut, so it still cannot advertise a relation the cut does not contain — while making the query linear. This is the actual fix; the timeout only makes failure visible. Guard it with a test asserting the watermark equals the max over exported rows.

**3. Progress logging per export step.** The doc asks for this and it is right: `unresolved_survivors → watermark → per-table rows` should each log start and duration, so the next stall is localized from logs rather than from `pg_stat_activity`.

**4. Stale-`building` sweep.** Even with a timeout, SIGKILL still strands rows. A startup sweep marking `building` snapshots older than a threshold as `failed` completes the invariant at `workers/p2.py:269`.

**5. Only then consider simplifying `entities_current`.** The doc lists this, and it is the deepest fix, but it touches an invariant view that other consumers depend on. The cost figures also deserve a caveat the doc does not give: an `EXPLAIN` *estimate* of ~10⁷ on 774 entities says the planner has lost the row-count trail through the recursive CTE, not necessarily that execution is 10⁷ units. The `count(*)` timeout at 15–60 s is the stronger evidence. Worth stating, so the next reader does not over-trust the estimate.

### Doc nits

- The 2026-08-07 evidence is from "lab stack" — record the Postgres version and whether `ANALYZE` had run on the smoke DB. A missing `ANALYZE` on freshly-loaded tables is a common cause of exactly this planner failure, and ruling it in or out is cheap.
- Workaround #1's `pg_terminate_backend` on a `query ILIKE` match is broad; `SIGKILL`-equivalent termination of any backend matching a substring will also hit a legitimate concurrent query. Suggest `pg_cancel_backend` first, and add `AND state_change < now() - interval '5 min'`.
- Consider linking the doc from `plan/analysis/` if a design change to `entities_current` follows, so the rationale is not stranded in an ops note.

---

## 6. Suggested follow-ups

**Before the next paid scoring run**

1. Retry with backoff + incremental/checkpointed report writing; catch `URLError`/`TimeoutError` (F-1).
2. Raise `max_tokens` to the house default and pin reasoning effort (F-2).
3. Strip model output and provider bodies from error strings (F-4).
4. Fix `_kendall_tau_b` tie handling and add the tie tests from the F-3 table (F-3, F-15).
5. Rename or replace `overall_mean` with a macro-average, and emit per-ability `n` (F-6). Re-state the `0.5625` figure accordingly.

**Before pointing this at a non-synthetic corpus**

6. Reuse `OpenRouterSettings` / the existing adapter; drop the TID251 `noqa` and the duplicate urllib client (F-7).
7. Delimit the response block in the judge prompt and instruct the judge to treat it as data (F-13).
8. Env-only API key (F-8).

**P2 hang**

9. `SET LOCAL statement_timeout` in `graph_export` — smallest change, largest diagnostic win.
10. Watermark from the temp survivor map, with an equivalence test.
11. Per-step progress logging; stale-`building` sweep (F-5).

**Reproducibility and docs**

12. Commit the BEAM runner, or document and fix the `run_dir` contract with a committed example (F-16).
13. Runbook section for `score-official`: cost profile, key handling, judge-model comparability caveat, and the "not the BEAM headline number" warning (F-18).
14. Pre-flight call-count estimate and `--dry-run` / `--limit` (F-11).
15. Fake-transport tests for `OpenRouterJudge` and `event_ordering_score` (F-15).
16. Wire or delete `_NUGGET_ABILITIES` (F-12); reconcile the `_clamp_score` docstring (F-10); guard the `parse_judge_json` fallback (F-9); tighten `llm_equivalence` (F-17); comment the intentional EO nugget/τ duplication (F-14).

---

## Appendix — verification commands

```bash
# Gates (all green on 2a94ffd2)
uv run ruff check src/ benchmarks/
uv run ruff format --check src/ benchmarks/
uv run pyright src/ benchmarks/ --pythonversion 3.13
uv run pytest src/tests/benchmarks/test_beam_official_score.py -q

# F-3 reproduction
python3 -c "
import sys; sys.path.insert(0,'.')
from benchmarks.rs_harness_beam.official_score import _kendall_tau_b
print(_kendall_tau_b([1.,1.,2.],[1.,1.,2.]))  # 0.6667, correct tau-b is 1.0
"

# F-12 dead constant
grep -rn '_NUGGET_ABILITIES' src/ benchmarks/   # single hit: the definition

# F-7 sole banned-API suppression in the repo
grep -rn 'TID251' src/ benchmarks/

# P2: watermark bypasses the survivor map the connection builds
grep -n 'graph_survivor\|_SELECT_WATERMARK' src/rememberstack/spine/projection.py
grep -rn 'statement_timeout' src/rememberstack/spine/ src/rememberstack/workers/   # no hits
```

Upstream sources consulted:
`https://github.com/mohammadtavakoli78/BEAM` — `src/evaluation/compute_metrics.py`, `src/evaluation/run_evaluation.py`.
