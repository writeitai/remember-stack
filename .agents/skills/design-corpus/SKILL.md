---
name: design-corpus
description: The RememberStack planning-corpus discipline. Use whenever creating, editing, reviewing, or accepting plan/ requirements, analysis, binding designs, proposals, decisions, or research for an architectural choice. Covers analysis before design, the root decision log, current-state plan/designs/, and writing for a cold reader.
---

# Design corpus

The planning corpus must let a reader with no conversation history find the
current contract and understand why it was chosen. Read `CLAUDE.md` and
`plan/README.md` for this repository's full-scope and source-precedence rules.

## Source roles

- `plan/requirements/` states what the complete engine must do.
- `plan/analysis/` contains the bulk of research, comparisons, objections, and
  working notes. It is non-binding; dead ends and superseded reasoning may stay
  visible if clearly labeled.
- `plan/designs/` states the accepted, binding **current state**. It is not an
  additive archive of every design ever accepted. A reader must be able to
  understand current scope, flows, contracts, failure/recovery behavior, and
  rationale without reconciling contradictory old and new rules. When a new
  design changes existing logic, update or remove every affected binding
  rule, example, implementation contract, and link. Remove a wholly obsolete
  design from the binding set, or reduce its old path to a short successor
  pointer where inbound links need continuity. Preserve history in analysis,
  the decision log, and git. A supersession banner alone does not make a
  contradictory body acceptable.
- `plan/proposals/` holds unchosen alternatives to the planning architecture;
  `design/proposals/` holds unchosen operational, efficiency, and architecture
  tracks. Use the directory closest to the affected binding topic. Each live
  proposal states its adoption trigger; do not present it as binding. Mark
  adopted proposals as historical and link their accepted design.
- `plan/plans/` owns implementation sequence, not architectural truth.
- Root `decisions.md` is the numbered log of consequential architectural and
  product choices. Put detailed operating contracts in the binding design;
  link the analysis that justifies the choice. If a later decision supersedes
  an earlier one, mark that **inside the earlier entry**, near its heading or
  status, and link the successor. For partial supersession, identify the
  replaced clauses and what remains binding. Retain the original decision as
  history; a note only in the new entry or index does not warn a reader who
  lands on the old one.

Keep `plan/README.md` navigable so the binding answer is easy to find.
Accepted design does not establish shipped behavior; inspect implementation
and tests before making as-built or public documentation claims.

## Analysis before design

Before writing or materially changing a binding design:

1. Frame the problem and constraints in analysis. Inspect the affected current
   design, decisions, implementation, and relevant tests.
2. Compare real alternatives, costs, operational and security consequences,
   and failure behavior. Cite dated authoritative sources for volatile
   external facts. Resolve substantive disagreements in writing.
3. Update the current binding design, not just a new standalone document.
   Record high-level decisions in the log and keep viable unchosen paths in
   `plan/proposals/` or `design/proposals/` according to the affected topic.
   Put delivery order in `plan/plans/`.

The test is whether a skeptical reader can reconstruct the choice from the
corpus alone. Follow `CLAUDE.md`: designs describe the full intended engine,
not a phased MVP; the separate public docs site describes only what ships.

## Cold-reader checks

- Explain a method in plain language, with a concrete example where useful;
  do not rely on jargon or conversation history.
- Make the current binding contract explicit; remove conflicting old rules
  from affected `plan/designs/` files.
- Mark a superseded decision in its own entry, including surviving scope for
  a partial supersession.
- Do not cite analysis or proposals as settled architecture, or design
  acceptance as proof that code ships.
