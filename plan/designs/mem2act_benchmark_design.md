# Mem2ActBench — binding design (Track B)

> **Status:** binding setup for WP-8.7b. Pure/synthetic tests allowed. Real
> provider/RS runs require owner preflight + `--execute`.

## 1. Protocol

```text
protocol                 RS-Mem2Act-v1
upstream                 Cantaloupe-M/Mem2ActBench
upstream commit          b00726940b5abbe9bd324bdd7a2cb272f5c62a29
dataset subdir           Mem2ActBench/
qa items (released)      400
qa items (resolved)      323   # committed publication set
primary metric           tool_name_exact AND required_args_match
secondary                arg_key_recall, level-stratified accuracy
reader model             pinned per run (default openrouter/openai free choice)
judge                    none (deterministic)
```

## 2. Task

For each QA item:

1. Load gold `query`, `tool_call`, `target_tool_schema`, `complexity_metadata`.
2. Resolve `session_id` via committed `session_map.json` (built by subset match).
3. **empty:** reader prompt = schema + query only.  
4. **rememberstack:** ingest session turns once per session; retrieve top-k
   strings for the query; reader prompt = schema + query + learnings.  
5. **full_context:** reader prompt = schema + query + full session transcript.  
6. Parse one JSON tool call `{name, arguments}`.  
7. Score.

## 3. Scoring (deterministic)

- **tool_name_ok:** case-sensitive exact match on `tool_call.name`.  
- **args_ok:** every gold argument key present; values compared after
  normalization (strip, lower for strings, bool/int coercion). Extra predicted
  keys allowed.  
- **item_ok:** tool_name_ok ∧ args_ok.  
Missing/invalid JSON → fail.

Report overall item accuracy and per-level (L1–L4) accuracy. Failures stay in
the denominator.

## 4. Arms

| Arm | Implementation |
|---|---|
| `empty` | No ingest; no retrieval |
| `rememberstack` | Public `MemoryClient.ingest` per turn/doc; `claims_hybrid_rrf` retrieve; format learnings like STATE adapter |
| `full_context` | Transcript in prompt; no RS required |

First campaign: **empty vs rememberstack** only.

## 5. Tiers

| Tier | Items | Notes |
|---|---:|---|
| smoke | 12 | Stratified across levels present |
| development | 40 | Stratified |
| publication | 323 | All resolved |

Manifests: `benchmarks/mem2act/manifests/`.

## 6. Parallelism

- Items are independent after session ingest caches.  
- Parallelize by item with a worker pool (reader-bound).  
- RS: one deployment; sessions ingested once and reused across QA that share
  `session_id`.  
- Optional Hetzner for Compose; reader can be local/API.

## 7. Dataset handling

Do **not** vendor multi‑MB jsonl into git. Operator supplies
`--dataset-root` pointing at a checkout of the pinned commit. Commit only
manifests + session_map + adapter code.

## 8. Acceptance (setup)

- Manifests validate; session_map covers every manifest id.  
- Scorer pure tests pass.  
- `prepare` writes run fingerprint without provider calls.  
- Owner-authorized smoke: 12 items × {empty, rememberstack} with explicit cap.

## 9. Out of scope

- Official paper reimplementation of all seven baselines on day one.  
- STATE-Bench / MemoryArena.  
- Azure GPT-5.4.  
- Claiming full 400 until unresolved 77 are linked.
