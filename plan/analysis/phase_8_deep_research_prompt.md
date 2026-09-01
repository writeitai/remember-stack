# WP-8.1 — External Deep-Research Prompt

Copy the prompt below into another research system. It is intentionally self-contained so the
researcher does not need repository access.

---

You are conducting a rigorous, current benchmark-landscape review for **RememberStack**, an
open-source memory backend for AI agents. Research as of the day you run this prompt; state the
cutoff date explicitly.

## System being evaluated

RememberStack ingests heterogeneous documents and conversations into:

- immutable, source-linked claims ("what a source said");
- adjudicated relations and observations ("what the system currently holds true") with world-time
  and system-time validity;
- rebuildable vector, graph, and filesystem projections; and
- precomputed, cited knowledge pages.

Its query path is read-only and makes zero LLM calls. It supports semantic and lexical retrieval,
graph traversal, multi-hop composition, provenance hydration, as-of queries, explicit
contradiction surfacing, typed negative answers, watched-source edits/retractions/deletions, and a
hard-forget workflow intended to make forgotten content indistinguishable from never-ingested
content across active stores and restores.

The benchmark program has two different goals:

1. compare against other memory systems on shared, recognized external benchmarks; and
2. demonstrate capabilities that standard benchmarks omit, without inventing a large bespoke
   framework.

We want good evidence at **modest cost**. Separate one-time ingestion/build cost, retrieval cost,
answer-model cost, and evaluator/judge cost. Prefer deterministic scoring and inject-once/query-many
datasets. Full expensive runs may be publication-only; development needs small, fixed,
representative subsets.

## Candidate set

Investigate at least:

- LoCoMo and any corrected/current official variants;
- LongMemEval, LongMemEval-V2, and their S/M/oracle or public tiers;
- MemoryAgentBench, especially FactConsolidation-SH/MH;
- MultiHop-RAG and reasonable multi-hop alternatives such as HotpotQA or MuSiQue;
- Workspace-Bench and other benchmarks that test dependency-aware work over heterogeneous files;
- DMR;
- STATE-Bench;
- MemoryArena;
- Mem2ActBench;
- GroupMemBench;
- LoCoMo-Plus; and
- any newer benchmark that materially supersedes these.

Do not assume this list is correct. Find stronger replacements when evidence supports them.
Treat the draft five-surface portfolio as a hypothesis to attack: recommend fewer benchmarks if
the evidence shows redundancy, and recommend replacements only when they add a missing capability
or materially improve comparability.

Candidate reproduced baselines are:

- BM25;
- a minimal dense-RAG floor using the same benchmark-native units and frozen reader;
- Mem0 OSS; and
- Graphiti OSS.

Evaluate whether these are the smallest fair baseline set. Distinguish Graphiti OSS from the
hosted Zep product, and distinguish reproduced results from vendor-reported numbers.

## Questions to answer for every benchmark

1. **Standing and currency**
   - Is it peer reviewed?
   - Is it actually used by current memory systems?
   - What is the latest official dataset/code revision?
   - Is there a leaderboard or only incomparable vendor harnesses?

2. **What it measures**
   - Retrieval, answer generation, agent behavior, or the whole system?
   - Static recall, multi-session reasoning, multi-hop reasoning, temporal reasoning, knowledge
     updates, conflicting facts, abstention, provenance, source edits/retractions/deletions,
     selective forgetting, privacy deletion, or memory-guided action?
   - Does it evaluate memory incrementally, or merely pass a long context to a model?
   - If it evaluates a whole artifact-producing agent, is there a matched native control that can
     isolate the incremental memory surface, and which metrics remain irreducibly agent-level?

3. **Scale and cost**
   - Number of corpora/histories, source tokens, sessions/turns, questions, and repeated runs.
   - Whether histories are shared across questions (inject once/query many).
   - Required embedding, ingestion, reader, controller, simulator, and judge calls.
   - For heterogeneous-file suites: file counts and bytes by format, whether full noisy
     workspaces must be indexed, deterministic versus provider-backed conversion, and whether
     ingestion can be amortized across tasks without cross-task contamination.
   - Hardware requirements and hosted-provider dependencies.
   - A transparent cost formula plus a current-price estimate for:
     - an adapter smoke run;
     - a fixed development subset; and
     - the full publishable run.
   - If prices or token counts are uncertain, show a range and the assumptions; do not invent a
     precise number.

4. **Scoring quality**
   - Deterministic exact/F1/retrieval metrics versus LLM-as-judge.
   - Evidence labels and support for Recall@k, MRR/nDCG, complete-evidence success, null/abstention
     accuracy, P50/P95 latency, and returned-context size.
   - Judge model/prompt sensitivity, label quality, leakage, answer-key issues, excluded categories,
     and known corrected datasets.
   - Whether system-specific prompts or hand-written exceptions make published scores
     non-comparable.

5. **Reproducibility and legal use**
   - Direct links to official paper, code, dataset, evaluator, and license.
   - Exact commit/tag/data revision suitable for pinning.
   - Whether the dataset may be redistributed, downloaded at run time only, or has
     non-commercial restrictions.
   - Setup fragility, stale dependencies, missing artifacts, or proprietary APIs.

6. **Fit for RememberStack**
   - Which benchmark gives shared-ground competitor comparison?
   - Which tests likely strengths: bi-temporal current-state selection, updates/supersession,
     contradiction preservation, provenance, cross-document graph paths, lifecycle events, honest
     negatives, and hard forget?
   - Which capability has no credible external benchmark and therefore needs a small deterministic
     scenario derived from the existing product contract?
   - Identify semantic mismatches. For example, a "latest fact wins" benchmark is not the same as
     preserving contradictory evidence while selecting a current fact.

## Research standards

- Cite every material factual claim with a direct URL.
- Prefer official papers, official repositories, dataset cards, evaluator code, and current
  documentation. Use independent audits only for integrity criticism, label them clearly, and
  include reproducible evidence.
- Compare publication date with event/release date for recent work.
- Do not copy vendor headline scores into one table unless protocols match exactly.
- Do not recommend a benchmark simply because it is new or large.
- Do not propose implementation code or a generalized benchmark platform.
- Be explicit about uncertainty and missing information.

## Required output

1. **Executive recommendation:** the minimum viable external suite, a fuller publication suite,
   and a clear do-not-run/defer list.
2. **Landscape table:** benchmark, year/venue, adoption evidence, abilities, scale, scoring,
   estimated cost class, license, reproducibility, and verdict.
3. **Detailed analysis of finalists:** exact protocol, fit, limitations, and fair comparison rules.
4. **Cost plan:** smoke/development/publication tiers with call/token formulas and current price
   assumptions.
5. **Baseline recommendation:** smallest fair reproduced set and which published results may
   appear only as context.
6. **Capability-gap map:** standard benchmark coverage versus RememberStack's unique contracts.
7. **Pinning manifest:** recommended code commit/tag, dataset revision, judge/reader model, prompts,
   and scorer version.
8. **Risks and open questions:** especially benchmark integrity, licensing, and hidden sources of
   non-comparability.

End with a concrete sequence for implementing adapters without scope creep.

---
