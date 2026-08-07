# Operations design notes

Non-secret operational policy for running RememberStack (compose, smokes,
benchmarks). Binding engine product design remains under `plan/designs/`.

| Document | What it answers |
| --- | --- |
| [`openrouter-embedding-routing.md`](openrouter-embedding-routing.md) | Which OpenRouter hosts to prefer for `qwen/qwen3-embedding-8b`, env knobs, refresh procedure |
| [`pr-ci-fast-gate.md`](pr-ci-fast-gate.md) | PR CI under ~10 min (hard `PR gate` + soft path integration; full suite on nightly) |
| [`p2-projection-hang-beam-smoke.md`](p2-projection-hang-beam-smoke.md) | Why P2 rebuild stuck on BEAM smoke (`graph_edges_visible_history` / `entities_current`) |
