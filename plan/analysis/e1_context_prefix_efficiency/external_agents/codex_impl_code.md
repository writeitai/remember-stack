# Codex — D80 implementation review (post-must-fix)

**Verdict: Request changes**

Post-must-fix re-review of branch `code/d80-embedding-input-impl` against
`e1_embedding_input_policy.md` §3–§7 and `orchestration_design.md` § embed_chunk
rules 1–6. Prior blockers in pure policy, generation-keyed P1, recovery match,
E2 allowlist, and legacy strip **mostly landed**. The branch still ships with
**red integration tests**, a **total-outage → poison-skip** contract violation,
**no query-side active-generation filter**, and **stale D66 docs**. Those are
blocking.

---

## Must-fix findings

### M1 — Broader integration suite still asserts retired prefix-LLM behavior

The focused unit/migration suite (below) is structurally green. The live
chain/API proofs that actually exercise E1→E2→search against a database are
still written for the old product:

| Test | Broken assertion | Actual D80 behavior |
|---|---|---|
| `src/tests/workers/test_e2_chain.py:413–416` | expects a prompt containing `"state where this passage sits"` | E1 no longer emits that instruction; E2 bundle uses `LOCATION:` typed line |
| `src/tests/surfaces/test_retrieval_api.py:785–787` | `context_prefix == "Sits in the staffing note."` | Deterministic header (e.g. title/section/role), dual-written to `location_header`/`context_prefix` |
| Same retrieval test’s “mismatched prefix” branch (`:795–808`) | `UPDATE chunks SET context_prefix = 'mismatched prefix'` then expects hydration drop | Confirm path prefers `location_header or context_prefix` (`query_engine.py:1557–1558`); dual-write leaves `location_header` intact, so the mutation is a no-op |

Also still carrying dead canned `ContextPrefix` fixtures:
`test_retrieval_api.py:98`, `test_e1_chain.py:109`, `test_e3_chain.py:195`,
`test_lifecycle_reconciliation.py:134`.

**Fix:** rewrite assertions to D80 facts — zero location-LLM prompts, P1 text
body-only, PG stamp / API `context_prefix` is the deterministic header (or
null), hydration drop tests must mutate the field the confirm path actually
reads (`location_header` and/or section_role / missing P1).

### M2 — Poison split treats total outage as success-with-all-skips

Orchestration rule 4: split only on failure that is **not** a total outage;
size-1 poison is a typed skip for that chunk.

Implementation (`workers/e1.py:360–394`):

```text
except Exception:
    if len(batch) == 1:
        poison_skips.append(batch[0]); return
    # else halve and recurse
```

Every exception is treated as candidate poison. A network/provider outage on a
64-chunk batch halves to 64 size-1 failures, stamps `embedding_ref=skip:poison_chunk`
for **every** chunk (`:315–323`), passes the readiness barrier (`:325–339`),
and enqueues E2 with **zero searchable vectors**.

That is the opposite of “typed fail for a poison chunk, not a document
dead-letter of finished siblings” under a total outage: finished siblings
never existed, and the document is falsely embed-ready.

**Fix:** classify outages (transport / 5xx / rate-limit / adapter “provider
unavailable”) and **re-raise** so the work row retries; only apply size-1
typed skip for chunk-attributable failures (or after an explicit
non-outage signal from the provider port). Do not close readiness on a
full-set poison skip without a non-outage proof.

### M3 — Query never scopes to the active `(policy_generation, embedder_generation)`

Design §4.5 / §5.2 / §7: P1 rows are generation-keyed; search/filter uses the
**active** pair pointer; cutover flips the pointer only when required rows
exist.

What landed:

- Lance upsert key is the triple (`adapters/selfhost/lance.py:89–95`) ✅
- `match_chunk_embeddings` / generation-scoped `chunk_vectors` ✅
- Dual-generation coexistence proven in unit test ✅

What did **not** land:

- No deployment/query-scope **active pointer** store
- `QueryEngine._nominate_chunk_ids` calls `search_chunks` /
  `search_chunks_lexical` **without** generations
  (`surfaces/query_engine.py:713–719`)
- Port defaults keep filters optional (`ports/p1_index.py:108–121`)
- `chunk_texts` is unscoped (`lance.py:362–385`); with two rows per
  `chunk_id`, `limit=len(chunk_ids)` can drop ids or return an arbitrary
  generation’s projection

During re-embed / embedder swap, unscoped ANN/BM25 can mix generations (wrong
metric/dimension risk if dimensions ever differ; always wrong identity during
cutover). This is the remaining half of prior must-fix #1.

**Fix:** resolve active `(policy_generation, embedder_generation)` at query
scope (settings/deployment stamp is enough for library-boundary single-tenant)
and pass it on every chunk search + text projection. Refuse unscoped search
when multiple generations exist for a deployment, or always require the
pointer.

### M4 — D66 same-PR docs still describe the retired product

`website/src/app/docs/reference/api/page.mdx:183–185`:

> Chunk results disclose their **generated** `context_prefix` separately from
> **verbatim** `chunk_text`

Both claims are false under D80: header is deterministic (when present);
P1/`chunk_text` is normalized body-only, not the raw `document.md` slice.
Standing D66 obligation is unmet.

### M5 — Claimed “poison split” coverage is missing; recovery is only half-tested

`test_d80_recovery_and_hydration.py` docstring claims “recovery, poison split,
E2 validation, legacy hydration.” Actual tests:

| Covered | Missing |
|---|---|
| `match_chunk_embeddings` triple+hash | Handler-level: P1 present, PG missing → no provider re-call |
| Dual-generation row coexistence | Handler-level: PG stamp present, P1 missing → re-embed + repair |
| E2 closed kind/provenance | Poison split halves then size-1 skip; siblings committed |
| Legacy `_strip_legacy_prefix` | Total-outage re-raise (M2) |
| §4.3 multi-chunk without section title | Readiness fail when vectors missing (non-skip) |

Without handler fault-injection, M2 and partial-batch recovery can regress
silently.

---

## Should-fix findings

### S1 — Connector packaging never reaches E1 from the spine

`ChunkSource` now has `source_shape`, `channel_ref`, `thread_ref`,
`author_ref`, `message_ts` (`model/chunks.py:52–57`). `_location_facts` maps
them (`e1.py:517–531`). **But** `_SELECT_CHUNK_SOURCE`
(`chunk_catalog.py:162–172`) only selects
`d.title, d.source_kind, …` — no packaging columns. No migration adds them to
`documents` / `document_versions` / source-map spans either.

Production path therefore always gets defaults (`source_shape="document"`,
refs `None`). Message-atom compact headers and `source_shape` P1 scalars are
reachable **only** from unit tests that construct `LocationFacts` directly.
Design §3.2 permits missing connector metadata, so this is not a pure-policy
bug — but prior must-fix #3 asked for a real map path; the map is a dead end.

### S2 — E2 Claimify instruction text still names “CONTEXT PREFIX”

`workers/e2.py:160` still tells the model additions may come from “stored
CONTEXT PREFIX”. Bundle members are now `LOCATION:` typed pairs only. Stale
instruction invites the model to invent prefix-shaped `added_context` that the
grounding gate then drops (noise, not silent incorrect membership). Align the
prompt with §3.3.

### S3 — Missing `provenance` is admitted into the grounding union

`_location_grounding_pairs` (`e2.py:780–783`):

```python
if provenance is not None and str(provenance) not in _LOCATION_ELEMENT_PROVENANCE:
    continue
```

`provenance is None` passes. Design’s closed record requires provenance;
membership rule is allowlisted provenance. Reject missing provenance the same
way as `model_derived`.

### S4 — `normalize_body` still collapses newlines

`embedding_input_policy.py:104–106` collapses **all** whitespace to single
spaces. That string is both embedding body and P1 BM25/evidence text. Lists,
code, and multi-paragraph structure become one line. §4.1 allows policy-owned
normalization, but the choice is still undocumented in the version artifact
description. Prefer preserving newlines (collapse only horizontal runs) or
pin the collapse explicitly in `EMBEDDING_INPUT_POLICY_VERSION` docs.

### S5 — Invented `"untitled"` coordinate heuristic

`_has_useful_coordinates` treats title `"untitled"` (any case) as absent
(`embedding_input_policy.py:278`). Not in §4.3. Either delete or freeze in the
policy artifact text.

### S6 — Migration dead code

`p8_01_0021_d80_embedding_input.py:30–34` still does
`name = column_sql.split()[0]` / `del name`. Harmless; remove.

### S7 — Embedder generation identity is still bare model id

`embedder_generation = self._settings.embedding_model` (`e1.py:183`). Design
wants model + dimension + metric + provider params in the generation identity
(§4.5 / §8). Cost/correctness risk on silent provider param drift. Record as
follow-on if not in this PR’s generation string.

### S8 — Zero-call attestation on same-version policy bump

Cross-version carry-forward exists (`carry_forward_sources` + hash match).
Same-version policy-generation change with unchanged `embedding_text_hash` +
embedder still takes the provider path (cost only). Design §4.5 allows
zero-call attestation into a new policy row.

### S9 — Prepare stamps only after success/skip

Design §6.2 allows pure prepare stamps before provider calls. Impl stamps on
`_commit_batch` / `_stamp_skips` only. Acceptable under “may”; means a crash
mid-batch recomputes renders (cheap) and relies on P1 hash match for vector
reuse (now present). Optional tighten: durable prepare before embed.

### S10 — `test_e1_chain` does not prove P1 text is body-only

Updated acceptance checks stamps and zero LLM prompts
(`test_e1_chain.py:219–231`) but never asserts Lance `text == normalize(body)`
while embedded provider text may be `header + body`. That was the single most
important retrieval property the old prefix-in-P1 test accidentally proved.
Add it.

---

## Residual risks / deferred work

1. **Dual-generation cutover ops** — storage allows two rows; no active pointer,
   retirement path, or query gate (M3). Unsafe to run a real re-embed migration
   against live query traffic.
2. **Connector metadata contract (§3.2)** — not in PG; message_atom product
   path unblocked only after connector + spine columns + SELECT (S1).
3. **Recipe filter support for new scalars** — P1 now can store
   `source_kind` / `source_shape`; no evidence recipes declare operators on
   them yet (§5.1: a scalar no recipe filters on does not satisfy retrieval).
4. **Claims do not inherit message scalars** — correctly not implemented
   (§5.5); join-based recipes remain future retrieval work.
5. **Fault-injection spike §10.4** — still no automated kill-between-P1-and-PG
   proof at handler level (M5).
6. **H_MAX≈48** — compact message headers still routinely drop `Time:` /
   `Author:` as whole fields; correct bounding, weak location signal until
   constants are measured and versioned (§10.1–10.3).
7. **D74 purge of new scalars/facts** — not re-audited in this pass; confirm
   forget deletes generation-keyed Lance rows (all generations) and
   `location_facts_json`.

---

## Design fidelity notes

### Re-verify prior must-fixes

| # | Prior must-fix | Status |
|---|---|---|
| 1 | P1→PG recovery + generation cutover | **Partial.** Composite P1 key + `embedding_text_hash` + `match_chunk_embeddings` recovery + per-batch P1→PG order + missing-P1 re-embed path are real. Active query pointer / cutover filter **not** done (M3). |
| 2 | Poison split / typed skip / readiness | **Partial.** Halve-to-1, `skip:empty_body` / `skip:poison_chunk` stamps, readiness fail on missing non-skip vectors — yes. Total-outage misclassification — **no** (M2). |
| 3 | `source_shape` path + E2 closed enum / provenance + no `section_role` | **Partial.** E2 allowlist + provenance filter + no `section_role` in union — **yes**. Production `source_shape`/refs always default — **no** (S1). |
| 4 | Legacy hydration | **Fixed** for the common case: strip when PG has no policy stamp (`query_engine.py:1554–1565`, `_strip_legacy_prefix`). Lance legacy columns init to `'legacy'` on upgrade. |
| 5 | Red tests fixed | **Partial.** Focused D80/migration modules fixed. **e2_chain + retrieval_api (and related fixtures) still red** (M1). |

### Orchestration embed_chunk rules 1–6

| Rule | Verdict | Evidence |
|---|---|---|
| 1 Claiming row / pure prepare in job | ✅ | Single `EMBED_CHUNK` handler; prepare is in-process |
| 2 Batching ≤ capability; no cross doc/rep/lane/gen | ✅ | `embed_batch_size` default 64; one representation per claim |
| 3 `call_key = embed_chunks:{first}:{count}` | ✅ | `e1.py:358–359` (`min` UUID as first id) |
| 4 Poison split | ⚠️ | Split exists; total-outage handling wrong (M2) |
| 5 P1 then PG; retry without provider if triple+hash match | ✅ | `_commit_batch` order; `match_chunk_embeddings` recovery |
| 6 Readiness under active pair or typed skip | ⚠️ | Checks vectors ∪ closed skips; all-poison-skip false ready (M2) |

### Policy §3–§7 highlights

| Contract | Verdict |
|---|---|
| No location LLM on default path | ✅ |
| Total pure `render_embedding_input` + version `e1-embed-input-v1:char` | ✅ |
| §4.3 ordered modes; step 4 without bare `"document"` shape | ✅ (`embedding_input_policy.py:259–265`) |
| No numeric `node_path` header fallback | ✅ |
| Whole-field header bound under `H_MAX` | ✅ (`_join_header_fields`) |
| Empty body → typed skip | ✅ stamped `skip:empty_body` |
| P1 text = normalized body only | ✅ write path; **under-tested** (S10) |
| Header separate on PG + API field | ✅ dual-write `location_header`/`context_prefix` |
| LocationElement closed kinds; no summary | ✅ builder + E2 consumer |
| Free-form header out of grounding union | ✅ |
| P1 key `(chunk_id, policy_generation, embedder_generation)` | ✅ |
| Universal P1 scalars projected on write | ✅ `source_kind`, `source_shape`, generations (recipe filters still open) |
| Claims do not inherit message scalars | ✅ |
| E2 extractor generation bumped for bundle/union change | ✅ `e2-extract-2026.08a:d80-location-elements-1:…` |

---

## Test results

**Command requested:**

```bash
uv run pytest \
  src/tests/core/test_embedding_input_policy.py \
  src/tests/core/test_d80_recovery_and_hydration.py \
  src/tests/workers/test_d79_summary_consumption.py \
  src/tests/spine/test_migrations.py::test_revision_graph_is_one_linear_structural_chain \
  src/tests/adapters/test_lance_retrieval.py -q
```

**This review agent has no shell execution tool**, so pytest was **not** run
here. Static collection of those modules shows:

- No import of removed `_prefix_prompt`
- `p8_01_0021` is in the expected revision chain
- Policy / recovery / d79 / lance tests are self-contained unit tests

**Expected focused result:** green (assuming local env matches CI).

**Expected broader result (must-fix M1):** red when DB fixtures run:

- `test_e2_chain.py::test_claims_land_grounded_…` (retired location-LLM prompt)
- `test_retrieval_api.py::test_lexical_claim_and_live_chunk_search_…` (canned
  prefix + dual-write mismatch branch)

Re-run the focused command and the two integration modules above before
merge.

---

## Executive summary

1. **Verdict: Request changes** — durability and pure-policy cores are largely
   real; merge blockers remain.
2. Prior pure-policy bugs (mid-field truncation, numeric `Section path:`, step-4
   over-breadth, free-form union membership) are **fixed**.
3. Prior operational holes (generation-keyed P1, per-batch P1→PG, hash recovery,
   empty/poison skip stamps, readiness incomplete raise) are **mostly fixed**.
4. **M1:** e2_chain + retrieval_api still assert prefix-LLM product behavior —
   branch is still red against a real database.
5. **M2:** any provider exception poison-skips size-1 tails and can mark an
   entire document embed-ready with zero vectors (total outage).
6. **M3:** dual-generation storage without query active-pair filter — cutover
   is not safe.
7. **M4:** D66 API docs still say “generated” prefix and “verbatim” chunk text.
8. **M5:** no true poison-split / handler crash-recovery tests despite the
   module name.
9. Connector `source_shape`/message refs are model fields only; spine never
   loads them (S1).
10. E2 prompt text still says CONTEXT PREFIX (S2); missing provenance still
    enters the union (S3).
11. Approve only after M1–M5 (integration rewrite, outage vs poison, active
    generation on search, docs, fault-injection tests). S1–S10 may ship as
    tracked follow-ups if explicitly sequenced outside this PR.
12. With M1–M5 closed, the D80 design’s hard path (deterministic input,
    generation-safe vectors, typed E2 location) is in good shape.
