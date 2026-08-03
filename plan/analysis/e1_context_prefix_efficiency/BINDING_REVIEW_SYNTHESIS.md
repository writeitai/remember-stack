# Binding design review synthesis (PR pass)

**PR:** https://github.com/writeitai/remember-stack/pull/199  
**Reviews:** `external_agents/fable_binding.md`, `external_agents/codex_binding.md`  
**Date:** 2026-08-03

## Verdict (both)

**Accept as binding architecture with required amendments** — not reject.

Core D80 direction stands. Residual work is **cross-doc consistency and executable
detail**, not a return to per-chunk location LLM or contextual embedders.

## Prior must-fix landing

| Item | Status after first binding draft |
|---|---|
| H9 typed groundable location | **Landed** in policy + E2 amendment |
| Connector metadata called out | **Landed** as contract requirement |
| Model-independent counter | **Landed** |
| Vector reuse triple | **Landed** (then **contradiction fixed** in follow-up: policy bump attestation) |
| Failure-boundary work graph | **Landed** (orchestration still thin on batch ownership) |
| D37 no full body in PG | **Landed** (postgres_schema then amended) |
| Slack provisional + eval | **Partial** → binding follow-up: provisional **compact header** for short message atoms with coords until eval |
| No global i/N | **Landed** |
| Design home e1/D80 | **Landed** |

## Follow-up amendments applied after binding review

1. Policy purity: no live filter capability in pure function; compact-header default for short message atoms with coords.  
2. Migration/reuse single rule + dual-generation cutover prose.  
3. `postgres_schema_design.md` chunks columns for D80 stamps.  
4. `e0_files_design.md` supersedes summary→E1-embed channel.  
5. e1 §7 aligned with attestation rule.

## Still open before implementation (not blockers for design merge if tracked)

- Full E2 bundle rewrite (typed location element records end-to-end in examples/spikes)  
- Orchestration: batch call_key ownership, crash between P1 and PG  
- Connector span-aware multi-message metadata schema (detailed)  
- Claim-row scalar inheritance decision in retrieval  
- Provider oversize preflight for atomic giant blocks  

## Recommendation

Merge design PR when follow-up amendments above are on the branch; track open items as
implementation design spikes / follow-on design PRs rather than reopening H1–H9.

## Implementer-ready gate (final)

After closing E2/orch/connector/claims contracts and Fable’s two must-fixes
(generation grains; P1 body-only text):

- **Fable:** Ready to implement (`fable_impl_ready.md`)
- **Codex:** Ready to implement (`codex_impl_ready.md`)

See `IMPL_READY.md`.
