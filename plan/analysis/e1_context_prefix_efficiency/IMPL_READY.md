# D80 implementer-ready gate

**PR:** https://github.com/writeitai/remember-stack/pull/199  
**Bar:** an implementer can start without inventing contracts.

## Final review (both)

| Agent | File | Verdict |
|---|---|---|
| Fable | `external_agents/fable_impl_ready.md` | **Ready to implement** |
| Codex | `external_agents/codex_impl_ready.md` | **Ready to implement** |

Both prior must-fixes closed: generation grains + P1 body-only text column.

## Contracts closed for day-one implementation

1. Location facts + embedding-input policy + embedding text (D80)  
2. Conditional header modes + provisional short-message compact header  
3. Typed `LocationElement` + E2 grounding union (no free-form prefix)  
4. Connector metadata **minimum** fields + structure-only fallback  
5. Claim filters: join, no claim-scalar inheritance  
6. embed_chunk batch / call_key / poison / P1-then-PG crash recovery  
7. P1 key `(chunk_id, policy_generation, embedder_generation)`; text = body only  

## Explicit non-goals (no scope creep)

- Contextual embedders  
- Per-chunk location LLM default  
- Full Slack field catalogs beyond min contract  
- Frozen numeric knobs without eval  
- Claim-row scalar inheritance  

## Next phase

Implementation PRs against these contracts; not further design iteration unless
implementation discovers a contradiction.
