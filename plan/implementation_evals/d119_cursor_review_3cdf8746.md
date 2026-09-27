**Approve** the D119 implementation at `3cdf874640b7a1d25ac70178b1bcd0e134c60c8d` against accepted scope. This is not merge permission: the sequential 55440 run and GitHub CI are still in flight.

Verified `HEAD` = `origin/feat/multi-span-claim-extraction` = `3cdf874640b7a1d25ac70178b1bcd0e134c60c8d` (`fix(contracts): regenerate origin-evidence schema and protocol pins`). Worktree is clean. PR https://github.com/writeitai/remember-stack/pull/400 is draft on that OID. I did not modify files, branches, or port 55440.

## Scope

The diff implements accepted D119 without the excluded machinery:

- Engine-built `S*` labels; the model cites labels, not offsets (`source_passages.py`, Claimify schema `source_refs`).
- Origin must be a target passage overlapping a Selection keep; neighbors are support-only.
- Complete bounded occurrence list (cap 8) on `chunk_claims.evidence_spans`; `claims.char_start` / `char_end` stay origin-only.
- Same-section prev/next context; summaries are orientation-only and cannot ground.
- Anchor checks are provenance; `entailment_self_verdict` stays advisory (plain-language prompt work is PR402).
- D56 reuse keeps claim IDs and remaps every span through fixed **target / previous / next** slots, including `None` absences. Repeated text uses relative window offsets, not `find()`.
- Neighbor identity is in `extraction_input_hash` (empty string per missing side). Header timestamps still invalidate, as disclosed.
- SQL helper rejects empty/object/missing/null/fractional/negative/reversed shapes; `REVOKE ALL … FROM PUBLIC`; view owner + query-role `SELECT` split preserved.
- `p9_31_0052` actually refuses populated `claims` / `chunk_claims` and does not add the column.
- API `evidence_spans` is the **origin** occurrence (`cc.chunk_id = claim.chunk_id`) on all five hydration SQLs; `_HYDRATE_SOURCES` still uses origin `c.chunk_id` → representation. Current positions are `memory_v1.claim_occurrences_live`.
- Forget deletes `chunk_claims` rows (span metadata with them). No new fact category, date window, checker call, fragment graph, store conversion, or extract-model change (`openai/gpt-5.6-luna` unchanged). Extractor generation rolled to `d119-multi-span-1`. Compose gate is empty `p9_30_0051` → head `p9_31_0052`.

## Blockers

None in the reviewed code. Merge still depends on the unfinished 55440 suite and CI.

## Material findings

None that break the accepted contract with a concrete failure I can show from this head.

Parent’s earlier production bugs look fixed in this SHA: subquery CHECK, view column order, `[{}]` NULL-safe helper, PUBLIC `EXECUTE`, real Alembic refusal, origin-occurrence envelope coordinates, 500-test `version_no`, Compose 0051→0052.

## Nits (not merge-blocking)

1. **Write-path origin fallback vs reuse raise.** Fresh insert synthesizes a one-span list when `ClaimRecord.evidence_spans` is empty:

```298:301:src/rememberstack/spine/claim_catalog.py
                        evidence_spans=claim.evidence_spans
                        or origin_span_from_record(
                            char_start=claim.char_start, char_end=claim.char_end
                        ),
```

Reuse already errors on empty spans (`claim_catalog.py` 151–154). Production `_grounded_claim` always sets `resolved.spans`. A caller that omitted `evidence_spans=()` would persist origin-only and call it complete. Fail closed like reuse if you touch this again.

2. **Neighbor key mismatch.** E1 hashes with `section_id`; E2 windows/bundle use `section_path`. A disagreement would re-extract (text mismatch / unique-window miss), not silently remap to the wrong side. Aligning the two keys would be tighter.

3. **Dead substring re-anchor.** `resolve_reused_occurrence_provenance` / `find_span_intervals` are unused on the E2 reuse path (remap + `resolve_spans_occurrence_provenance`). Harmless leftover.

4. **Shape CHECK allows extra JSON keys** (e.g. `{"char_start":0,"char_end":4,"x":1}`). Required missing/null/fractional/range cases are rejected.

5. **Draft PR body is stale** (still lists unfinished 500/envelope/rebase work). Do not treat it as the delivery report. I did not edit GitHub.

## Tests I actually ran

Did **not** call the full 500, and did **not** use 55440.

Independent this review, `REMEMBERSTACK_DATABASE_URL` unset:

- `test_source_passages.py` + `test_occurrence_provenance.py` + `test_d119_reuse.py` + `test_claimify_loss_ledger.py` + `test_claim_valid_time.py` — **81 passed**
- `test_locomo_protocol.py` + `test_openapi_export.py` + `test_single_run_summary_json_is_unchanged` — **59 passed**

Not re-run here (parent evidence / still running):

- Parent 27 core/PG + 49 E2/regression + real Alembic refusal on an earlier snapshot
- Parent unit/protocol/OpenAPI 74 + serialized fixture 1 at this head
- A3-version preflight of `test_500_versions_reuse_handler_remap_and_lineage_facts` (reported pass; I did not repeat)
- Live 55440: `test_d119_versions.py` (envelope + 500), `test_d119_multi_span.py`, `test_d119_migration.py`, query-space manifest + open-query prose — pytest PID 58194, log still only a quiet `-q` dot; one test had started, 500 not finished
- GitHub CI: Quality/CLA/path filters green; Unit, contract smoke, integration, Compose, client-engine **in progress**

500-test shape on this head matches what parent asked for: E0–E3 `_VersionRig`, two origin-eligible labels for “She joined…” / “Alice Novak is…”, all occurrence rows checked against remapped text, `LifecycleCatalog.recount` then `evidence_count == 1`, notes rewrite forces new extract calls, per-test `reset_database` (not TRUNCATE deployments). Envelope test asserts API spans belong to origin `document.md` while `claim_occurrences_live` matches the shifted version.

## Limitations

No paid model, no LoCoMo quality claim, no live-store conversion. Semantic entailment is still model-advisory; exact spans do not prove the combined assertion. Neighbor-edit invalidation is proven at hash/window level, not by a second 500-scale neighbor-only handler fixture — that is enough for this contract.

**Verdict: approve the code at `3cdf874`. Do not merge until 55440 and CI finish green.**
