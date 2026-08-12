# LoCoMo worker scaling and model provenance

Status: non-binding incident analysis, 2026-08-12. The accepted operational
contract is in `design/benchmarks/runbook.md` §§4–5 and
`benchmarks/locomo/sharding/README.md` §3.

## Problem

An RS-LoCoMo-Full-v13 shard was launched by `run_shard.sh`, which exported the
protocol's Luna/Qwen model bindings. A later, separate Compose scaling command
created additional extract-claims and adjudicate-observations containers.
Compose evaluated that command against the checkout's ambient `.env`, whose
self-host defaults selected GLM. The original containers remained on Luna, so
one Compose project contained both configurations.

The benchmark's saved readiness correctly described the intended Luna
deployment, but it did not attest every replica. The cost ledger exposed the
contradiction: observation calls from the additional replicas resolved to
`z-ai/glm-4.7-flash`, including malformed completions that exhausted the
32,000-token response ceiling. A mixed-model run cannot produce a protocol-valid
score even when every work item eventually succeeds.

## Options considered

1. Keep ad-hoc scaling and require operators to repeat all exported variables.
   This is fragile because the two commands can silently diverge again.
2. Add a second scaler script that reconstructs the runner environment. This
   creates another operational entry point and more state to keep aligned.
3. Make replica counts inputs to the existing runner and verify the resulting
   container environments. This preserves one launch authority and fails before
   paid ingest when any replica differs.

Option 3 is the smallest complete fix. The runner owns all replica counts,
attests every app container's source revision and model environment before
ingest and during every drain poll, and rejects post-launch contamination
before scoring. Raw invalid-completion capture lives
under the already backed-up private app-state volume, so diagnosis no longer
depends on ephemeral container files.

## Consequences

- Existing mixed-model runs are evidence for the incident, not scoreable runs.
- Parallel extraction and observation adjudication remain enabled.
- Operators tune concurrency only through the shard runner's `LOCOMO_*_WORKERS`
  inputs.
- Captured model output is source-derived data. It is private, mode 0600,
  excluded from logs, and travels with the complete off-host store backup.
