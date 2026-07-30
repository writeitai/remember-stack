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
   with a fully completed pipeline and fresh P2/P3 projections. Consequence:
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
- Protocols (`--protocol`, prepare-time only): `full-v5` (gpt-4o-mini
  answer agent — measured unusable, 1–2/8 smoke, loops and invalid output)
  and `full-v5-strong` (`openai/gpt-5.6-luna` agent, reasoning effort
  pinned to `none` in the protocol itself). Judge is luna in both. Use
  `full-v5-strong` for anything you intend to read.
- The answer agent sees only what the tools return (k=10 verbatim claims
  per query; never raw transcript), must answer in six words or fewer, and
  is allowed 8 tool calls / 9 total agent calls per question. The reader
  step auto-retries invalid (non-JSON) completions up to 2 extra attempts —
  this fired 83 times in 1540 questions, so it is load-bearing.

## 3. Environment a run needs

Exported in the shell that invokes the CLI (values live in the host's
`/opt/remember-stack/.env`; see the private infra repo for provenance):

```
REMEMBERSTACK_OPENROUTER_API_KEY          # all LLM + embedding traffic
REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER
REMEMBERSTACK_API_URL=http://127.0.0.1:18000
REMEMBERSTACK_API_TIMEOUT_SECONDS=150     # claims_verbatim embeds queries; 30s default times out
```

Engine-side (in `.env`, read by the docker stack): the extraction model
(`REMEMBERSTACK_E2_EXTRACT_MODEL`, currently `z-ai/glm-5.2`) and
`REMEMBERSTACK_OPENROUTER_REASONING_EFFORT_MAP` pinning glm models to
effort `none` — glm at auto effort intermittently emits reasoning prose
instead of JSON (issue #174), which is also the root cause of most
dead-letter rows.

## 4. Running a single conversation (smoke or one shard)

The maintained path is the sharding kit, which encodes every lesson below:

```
export LOCOMO_PROTOCOL=full-v5-strong
export LOCOMO_MAX_EVALUATOR_COST_USD=60
bash benchmarks/locomo/sharding/run_shard.sh conv-26 .benchmark-runs/my-run /opt/locomo/locomo10.json
```

Per sample it: forensically dumps the previous store into
`<run>/forensics/`, wipes the stack (`docker compose down -v`), starts it
with worker scaling (`--scale worker-extract-claims=3
--scale worker-normalize-relations=6 --scale worker-embed-claim=2`),
ingests, waits for a **true drain** (6 h budget, aborts on dead-letter),
publishes projections, answers, judges. `summarize --run <dir>` prints the
scorecard.

Manual equivalents, when you need them:

- Drain check: `select stage, status, count(*) from processing_state
  where status in ('pending','running','dead_letter') group by 1,2;`
- Projections: `docker compose --profile operations run --rm projections`.
- Deployment identity is self-provisioned from `.env`
  (`REMEMBERSTACK_SELFHOST_DEPLOYMENT_ID`), so a wiped stack comes back
  with the same deployment id — nothing to re-create.

## 5. Failure modes and their recoveries (all observed, all recoverable)

| Symptom | Cause | Recovery |
| --- | --- | --- |
| `answer` refuses: "deployment did not report the exact completed pipeline and fresh P2/P3 projections" | Drain not actually complete (pending rows, or dead-letter rows which pending/running counts miss), or projections published *before* the last ingest event | Finish the true drain, re-run projections, then answer. Order matters: projections after ingest. |
| Rows stuck in `dead_letter` | A chunk's extraction (or a relation stage) exhausted 3 attempts — usually glm non-JSON (#174) | `docker compose exec -T api python -m rememberstack.surfaces.cli ops replay <processing_id> --deployment <id> --attempts 3`, then wait for the drain again. In practice one replay round clears it; bound retries (the wrappers use 3 rounds) so a truly poisoned chunk stops the run loudly instead of looping. |
| Drain "stuck" with busy count barely moving | Relation-normalize is a single sequential worker by default; 400-claim conversations generate hours of tail | Scale workers (lease-based ledger makes this safe): `docker compose up -d --no-recreate --scale worker-normalize-relations=6 ...`. Remember `down -v` resets replica counts — re-apply scaling on every stack start. |
| run_shard refuses: "partial checkpoint; resume stages manually" | A previous attempt died mid-sample, leaving partial ingest/answer records in the run dir | For a shard dir with nothing else valuable: wipe stack + delete the run dir + start fresh. For a multi-sample run dir with completed samples: keep it — completed samples are checkpointed and skipped; only decide about the partial one. |
| Item failures recorded in run state | Per-item failures are terminal in that run | Missing items (never attempted, e.g. after a stage-level refusal) can simply be re-answered in the same run dir; genuinely failed items need a fresh prepare. Fresh prepares over the same store are cheap — ingest dedupes (D55). |
| Judge/answer cost cap hit | Caps are run-cumulative, not per-invocation | Pass generous run-absolute caps (`--max-evaluator-cost-usd`), sized from §7. |

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

**Known constraint (open issue):** the protocol fingerprint currently pins
`repository_revision`, so the merged summarize refuses runs prepared at
different commits even when every protocol identity field is identical. If
shards ran on a newer revision than an earlier partial run, compute the
combined score as the sum of the per-run official summaries — this is
exact, because each item is answered in exactly one run, missing items
score 0, and every run is scored over the same full manifest. Fix tracked
in `next-steps.md`.

## 7. Costs and durations (measured, GLM-5.2 extraction + luna answers)

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
- A per-conversation store dump *before each wipe* (the kit writes
  `<run>/forensics/<sample>-<ts>.sql`): claims with valid-time, the full
  claim-extraction decision ledger (`claim_extraction_decisions` — every
  claimify omission, selection drop with reason, grounding rejection with
  failed tokens). The ledger is what turns a wrong answer into a named,
  fixable gate in minutes.
