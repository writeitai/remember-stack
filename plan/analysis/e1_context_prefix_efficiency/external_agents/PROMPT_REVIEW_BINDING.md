# Design review — binding D80 / e1 embedding-input policy (PR pass)

You are an independent **design reviewer**. The proposal has been **promoted to binding
design documents**. Review those docs for correctness, completeness, and residual risks.
Think at high-level decisions **and** concrete contracts (policy section, P1 scalars, E2
grounding, work graph, Slack/message metadata).

## Working directory

`/Users/jpuc/code/moje/ultimate_memory/ugm_3/ugm`

## Required reading (binding — primary)

1. `plan/designs/e1_embedding_input_policy.md` — **new normative design**
2. `decisions.md` — **D80** (and D63 amendment note, D79 supersession note for summary→embed)
3. `plan/designs/e1_chunks_design.md` — §5 (rewritten embed path), §7 (vector reuse), §8 storage
4. `plan/designs/e2_e3_claims_relations_design.md` — D80 grounding amendment
5. `plan/designs/retrieval_design.md` — D80 scalar/hydration amendment
6. `plan/designs/orchestration_design.md` — cold-read chain update

## Optional context (analysis — not binding)

`plan/analysis/e1_context_prefix_efficiency/REVIEW_SYNTHESIS.md` — prior Fable/Codex review of the pre-binding proposal (check whether must-fixes landed).

## Fixed owner constraints

- Conventional embedders only; interchangeable `texts → vectors`
- Contextual embedders non-goal
- No hotfix-as-architecture
- Short messages (Slack) and long chats/papers

## Report structure (required)

1. **Verdict** — Accept as binding / Accept with required amendments / Reject  
2. **Prior must-fix checklist** — for each item from the first review, say **Landed / Partial / Missing** with citation  
   - H9 typed groundable location (not bare header removal)  
   - Connector metadata contract called out  
   - Model-independent policy length counter  
   - D56 vector reuse = text hash + policy + embedder generation  
   - Work graph at failure boundaries (not pure-fn ledger spam)  
   - Storage D37 (no full body in PG)  
   - Slack body_only provisional + eval gate  
   - No global i/N in default headers  
   - Design home under e1 / D80  
3. **High-level decisions** — any residual Accept/Amend/Reject on H1–H9 as now written in binding docs  
4. **Mechanism deep-dives** — embedding-input policy design; P1 scalars; work graph; E2; migrations  
5. **Gaps that still block implementation** (if any)  
6. **Ranked: must fix before merge / should fix / fine**  
7. **Executive summary** ≤12 lines  

## Rules

- Adversarial but constructive; cite paths.  
- Do **not** modify designs or code — review only.  
- Distinguish binding text vs analysis.

## Output path

Write complete review ONLY to:

`plan/analysis/e1_context_prefix_efficiency/external_agents/<AGENT>_binding.md`

where `<AGENT>` is `fable` or `codex`.
