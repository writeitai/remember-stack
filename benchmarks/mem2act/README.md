# RS-Mem2Act-v1 setup

Track B adapter for **Mem2ActBench** (memory → grounded tool call).

- Upstream: `https://github.com/Cantaloupe-M/Mem2ActBench` @ `b007269`
- Binding design: `plan/designs/mem2act_benchmark_design.md`
- **Does not vendor** the multi‑MB jsonl — pass `--dataset-root`

## Why this suite

- Deterministic gold tool calls (no Azure GPT‑5.4 judge)
- 323 resolved / 400 released items (77 excluded until session link fixed)
- Natural empty vs RememberStack comparison
- Paper-class memory systems (incl. Mem0-like) are weak at active memory→action

## Prepare (no provider)

```bash
git clone https://github.com/Cantaloupe-M/Mem2ActBench.git /data/Mem2ActBench
git -C /data/Mem2ActBench checkout b00726940b5abbe9bd324bdd7a2cb272f5c62a29

uv run python -m benchmarks.mem2act prepare \
  --tier smoke \
  --arm empty \
  --dataset-root /data/Mem2ActBench \
  --reader-model openai/gpt-4o-mini \
  --output .benchmark-runs/mem2act-smoke-empty
```

Arms: `empty` | `rememberstack` | `full_context`.

## Score offline

Write a JSON list of `ScoreRecord` objects, then:

```bash
uv run python -m benchmarks.mem2act summarize --scores scores.json
```

## Tiers

| Tier | Items |
|---|---:|
| smoke | 12 |
| development | 40 |
| publication | 323 (resolved only) |
