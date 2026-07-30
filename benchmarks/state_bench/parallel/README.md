# Parallel STATE-Bench evaluation

STATE-Bench test tasks are independent after train learnings exist. Prefer this
three-layer schedule over LoCoMo-style volume wipes.

## Layers

| Layer | What | How |
|---|---|---|
| **L1** | Tasks inside one domain | `run_batch --num-workers N` (STATE native thread pool) |
| **L2** | Domains | One `run_cell.sh` process per domain |
| **L3** | Arms / sub-protocols | Separate prepared run dirs; schedule cells concurrently |
| **L4** | Hosts | Assign domains (or task subsets) to hosts; never share Compose volumes |

RememberStack **reuses one deployment per domain** for all test tasks in that
domain. That is the opposite of LoCoMo publication isolation and is the main
wall-time win.

## Plan a matrix

```bash
uv run python -m benchmarks.state_bench.parallel.make_matrix \
  --tier smoke \
  --arm empty --arm rememberstack \
  --sub-protocol shared \
  --domain travel \
  --num-workers 8 \
  --output /tmp/state-matrix.json
```

Episode preflight is printed as JSON (`cells`, `episodes`). Convert to USD with
your provider rates before `--execute` campaigns.

## Prepare once per arm

```bash
uv run python -m benchmarks.state_bench prepare \
  --tier smoke \
  --arm empty \
  --sub-protocol shared \
  --state-bench-root /path/to/STATE-Bench \
  --agent-model-name gpt-5.1 \
  --domain travel \
  --output .benchmark-runs/state-smoke-empty
```

For `rememberstack`, ingest the prepared `documents/` into an isolated
deployment, drain the pipeline, build projections, then run cells with
`MemoryClient` env pointed at that API.

## Run cells in parallel

Prepare **one run dir per (arm, domain)** for RS/BM25. The cell runner reads
`run.json` + `manifest.json`, maps arm → agent class, and passes **only the
manifest task IDs** to STATE (`--tasks`), never the default `split=all`.

```bash
# Install rememberstack + state-bench into one Python env before multi-cell runs.
# domain parallelism (L2) for one empty arm prepared with all domains:
for domain in travel customer_support shopping_assistant; do
  nohup benchmarks/state_bench/parallel/run_cell.sh \
    --run-dir .benchmark-runs/state-smoke-empty \
    --domain "$domain" \
    --num-workers 12 \
    --execute \
    > "logs/$domain.log" 2>&1 &
done
wait
```

Agent class is derived from `run.json` arm (not a free-form flag):

| Arm | Agent class |
|---|---|
| empty | `EmptyMemoryAgent` |
| bm25 | `Bm25MemoryAgent` |
| rememberstack | `RememberStackMemoryAgent` |

Tune `--num-workers` to provider rate limits (STATE docs suggest ~10 for OpenAI).
Locked GPT-5.4 simulator/judge traffic often dominates — raise workers until
throttling, then back off.

**Env:** the process that runs `state_bench.scripts.run_batch` must import both
`state_bench` and `rememberstack` (for the RS arm). Prefer installing
`rememberstack` into the STATE-Bench venv, or installing `state-bench` into the
RememberStack venv. `PYTHONPATH` alone is not enough if `pydantic-settings` is
missing.

## What not to parallelize carelessly

- Two **rememberstack** cells against **one** half-ingested deployment.  
- Publication Track A LoCoMo and Track B STATE on the **same** API account cap.  
- Changing `run.json` mid-matrix (immutable prepare).
