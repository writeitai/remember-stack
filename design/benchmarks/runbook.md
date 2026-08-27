# LoCoMo benchmark runbook

How to run the benchmark, and the operational thinking behind every step.
Written 2026-07-31 from the first completed publication run. A reader with a
shell, the infra credentials (see the private infra repo), and no prior
context should be able to reproduce a full run from this page.

## 1. The mental model

Three facts drive every operational decision:

1. **Scores are only comparable under one protocol pin.** A run is prepared
   once (`prepare` writes `run.json` + `state.json` with the protocol name,
   fingerprint, model/prompt/schema hashes and budgets); every later stage
   re-validates against that pin. Never compare numbers across protocols,
   and treat any published third-party LoCoMo number as its own protocol —
   mem0's paper, full-context baselines, and vendor marketing all use
   different answer models, retrieval budgets, and judges.
2. **Per-sample isolation is mandatory.** The answer stage refuses to run
   unless the serving deployment contains exactly the sample's documents
   with a fully completed pipeline, live-graph readiness, and a fresh P3
   projection. Consequence:
   one conversation per store at a time, with a wipe between conversations —
   and therefore conversations are *embarrassingly parallel across hosts*.
3. **The engine pipeline is asynchronous and can stall.** Extraction and
   relation-normalization run as queued workers; a run is only answerable
   after a **true drain** (zero pending/running rows *and* zero dead-letter
   rows). Most operational failures are drains that look finished but are
   not.

## 2. Tiers, protocols, budgets

- Tiers (`--tier`): `smoke` = 8 questions on conv-26; `development` = 200;
  `publication` = 1540 across all 10 conversations. Question counts per
  conversation: 26:152, 30:81, 41:152, 42:199, 43:178, 44:123, 47:150,
  48:191, 49:156, 50:158.
- Protocol (`--protocol`, prepare-time only): `full-v14`. Both the answer
  agent and judge use `openai/gpt-5.6-luna`; reasoning effort is pinned to
  `none` for both. It is the sole executable protocol and is not comparable with
  historical v1–v13 runs.
- The answer agent can use the complete public read plane: the four assured
  operations (`testimony_context`, `fact_context`, `answer_context`, and
  `resolve_entity`), direct primitives, open SQL, typed live-graph helpers,
  saved queries, and the P3 mount. Public Cypher is absent. It is allowed 8 tool calls / 9 total agent calls per
  question and must return the shortest phrase that fully answers the question.
  The reader step auto-retries invalid (non-JSON) completions up to 2 extra
  attempts — this fired 83 times in 1540 questions, so it is load-bearing.

## 3. Environment a run needs

Exported in the shell that invokes the CLI (values live in the host's
`/opt/remember-stack/.env`; see the private infra repo for provenance):

```
REMEMBERSTACK_OPENROUTER_API_KEY          # all LLM + embedding traffic
REMEMBERSTACK_API_URL=http://127.0.0.1:18000
REMEMBERSTACK_API_TIMEOUT_SECONDS=60      # V14 transport budget
```

`run_shard.sh` sets every non-secret V14 ingest binding itself: Luna for the
generative seats, Qwen3-Embedding-8B for vector seats, and Nebius as the pinned
embedding host. It overrides ambient self-host defaults, and ingest compares
the complete `GET /deployment` binding map before uploading any document.

## 4. Running a single conversation (smoke or one shard)

The maintained path is the sharding kit, which encodes every lesson below:

```
export LOCOMO_PROTOCOL=full-v14
export LOCOMO_MAX_EVALUATOR_COST_USD=60
bash benchmarks/locomo/sharding/run_shard.sh conv-26 .benchmark-runs/my-run /opt/locomo/locomo10.json
```

Per sample it: acquires the host lock, verifies the previous live sample's
complete-plane GCS receipt and archive identities, then wipes the explicitly
bound Compose project. It starts the empty stack with worker scaling
(extract-claims ×8, normalize-relations ×6, adjudicate-observations ×4,
embed-claim ×2), attests the protocol-frozen environment inside every app
container, ingests, waits for a **true drain** (6 h budget, aborts on dead-letter),
verifies live graph, publishes P3, answers, judges, and creates a new verified GCS backup
before the next sample may begin. See
`benchmarks/locomo/sharding/README.md` for the binding backup/restore contract.
`summarize --run <dir>` prints the scorecard.

Manual equivalents, when you need them:

- Drain check: `select stage, status, count(*) from processing_state
  where status in ('pending','running','dead_letter') group by 1,2;`
- P3 projection: `docker compose --profile operations run --rm projections`
  (the operation is P3-only under D98).
- Deployment identity is self-provisioned from `.env`
  (`REMEMBERSTACK_SELFHOST_DEPLOYMENT_ID`), so a wiped stack comes back
  with the same deployment id — nothing to re-create.

## 5. Failure modes and their recoveries (all observed, all recoverable)

| Symptom | Cause | Recovery |
| --- | --- | --- |
| `answer` refuses: deployment lacks required pipeline/P1/live-graph/P3 capability readiness | Drain not actually complete, live graph catalog/helper health failed, or P3 published before the last ingest event | Finish the true drain, repair live graph catalog if reported, rebuild P3, then answer. P3 must follow ingest. |
| Rows stuck in `dead_letter` | A chunk's extraction (or a relation stage) exhausted 3 attempts — usually glm non-JSON (#174) | `docker compose exec -T api python -m rememberstack.surfaces.cli ops replay <processing_id> --deployment <id> --attempts 3`, then wait for the drain again. In practice one replay round clears it; bound retries (the wrappers use 3 rounds) so a truly poisoned chunk stops the run loudly instead of looping. |
| A worker reports a model different from `state.json` | A separate post-launch `docker compose up --scale` read stale ambient `.env` values and created a mixed-model fleet | Stop the invalid run. Relaunch through `run_shard.sh` only; set its `LOCOMO_*_WORKERS` variables if different replica counts are needed. The runner attests every app container before ingest and during every drain poll. |
| Extract or normalize remains slow | Replica counts are too low for the current chunk/claim fan-out | Set `LOCOMO_EXTRACT_CLAIM_WORKERS`, `LOCOMO_NORMALIZE_RELATION_WORKERS`, `LOCOMO_ADJUDICATE_OBSERVATION_WORKERS`, or `LOCOMO_EMBED_CLAIM_WORKERS` on the original `run_shard.sh` invocation. Never scale the benchmark with a second Compose command. |
| run_shard refuses: "partial checkpoint; resume stages manually" | A previous attempt died mid-sample, leaving partial ingest/answer records in the run dir | If the stack matches the checkpoint, run the incomplete stage directly; `ingest` first proves the exact public live-lineage/visible-version join. If it does not match and the sample has no answer/judge records, rerun that sample in a new run directory and merge it with the old run. If any answer/judge record exists, restart every sample assigned to that run directory; merging a replacement sample would correctly fail as overlap. Never edit or force a checkpoint forward. |
| Item failures recorded in run state | Per-item failures are terminal in that run | Missing items (never attempted, e.g. after a stage-level refusal) can be answered in the same run dir. A terminal failed item requires a fresh deployment and restarting every sample assigned to that run directory; a replacement sample cannot be merged over existing records. |
| Preflight/answer/judge cost cap hit | Caps are run-cumulative, not per-invocation | Pass a positive finite run-absolute cap (`--max-evaluator-cost-usd`), sized from §7. |

Operational hygiene that made overnight runs survivable: every long chain
runs **on the host under `nohup`** (session-side background tasks get
killed), progress goes to a log file you can `tail`, and any watcher that
kills processes must use bracket patterns (`pkill -f 'script[.]sh'`) —
plain patterns match the ssh session's own command line and kill it.

## 6. Full publication run: shard it

Sequential cost is ~2 h/conversation ≈ 20 h. Isolation makes conversations
independent, so the intended shape is N hosts, each running a disjoint
sample subset under the same protocol, merged at the end
(`summarize --run A --run B ...` validates identity and disjointness and
recomputes the official score from item records).

- Provision clones from the benchmark-host snapshot (see infra repo;
  **always pass `ssh_keys` at server creation** — a clone created without
  one gets an expired root password that blocks even pubkey login).
- On each clone: checkout the target revision, `docker compose build`
  (run_shard does not build), then run the kit under `nohup`.
- Balance shards by conversation size (`make_shards.py`), or by question
  counts above.
- Wall-clock: a one-conversation shard finishes in ~2.5 h (ingest+drain
  ~1–1.5 h, answer ~40 min for ~150 questions at ~12 s/q, judge ~10 min).

Every shard must run the same exact repository revision. The merger rejects
different revisions, and operators must not manually combine their summary
numbers: such runs measured different systems.

## 7. Historical sizing estimate

These figures came from the pre-v12 GLM-5.2 extraction path and are only useful
for rough capacity planning. V14 uses the current `main` bindings and must record
its own actual cost and duration; the provider account cap remains the hard
monetary boundary.

| Item | Cost | Time |
| --- | --- | --- |
| Ingest + extract one conversation | ~$0.70–1.00 | 60–110 min (with scaled workers) |
| Answer one conversation (~150 q) | ~$0.60 | ~40 min |
| Judge one conversation | ~$0.05 | ~10 min |
| Full publication run (10 conv) | ~$7 ingest + ~$5.30 evaluate | ~20 h sequential; ~2.5–5 h sharded |

## 8. What to preserve from every run

The score is the least valuable output. Keep:

- The run dir(s) — `state.json` holds every answer trace including which
  claims were retrieved per question.
- The run log, which records stage progress and failures.

Each completed sample's PostgreSQL authority/P1/live-graph catalog, P3, run
state, and mount state is
uploaded to its immutable GCS prefix and verified before the next isolated
sample may wipe the local store.
