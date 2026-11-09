# Answer-only prompt comparisons on a processed LoCoMo store

Status: non-binding analysis, 2026-11-09.

The current full answer loop can accept a prompt template, but the CLI cannot
compare templates without preparing a new canonical run. Editing an existing
run's fingerprint or clearing its answers would destroy the original experiment
identity. Re-ingesting changes the extraction output and adds paid work, so it
also makes a prompt comparison harder to interpret.

The existing retrieval-ablation runner already saves independent answers,
traces, grades, costs, and source/runner revisions. Extend it with a `full`
profile that uses the canonical SDK/P3 tools and the source run's model,
retrieval budget, and call limits. An optional `--answer-prompt` file supplies
the sole experimental variable. Freeze its exact bytes in the output and bind
them through the existing answer-prompt hash. Resume must reject a changed file.
The source fingerprint already binds retrieval settings and models; neither
the original run nor its database needs modification.

Alternatives are a second bespoke experiment script, manual state edits, and a
new ingestion per prompt. The existing runner avoids duplicating answer/judge
logic and preserves honest provenance with less machinery. This is a benchmark
tooling change, not a new retrieval path, model routing policy, or ingestion
generation. No D56 paid-work/reuse identity changes.

The full profile must validate live source identity before and after answering,
including exact ingests, serving revision, query surface, all enabled layers,
and P3 publication. Probe timestamps are observations, not serving identity:
reuse the canonical readiness fingerprint, which ignores those timestamps but
retains model/version/capability identity. A literal readiness-object equality
check would reject an otherwise identical fresh probe.

Keep the judge prompt/schema/model identical to the source; reject drift before
spending. Run all selected questions, retain failed trials, and report repeated
prompt development as development rather than held-out evaluation. A candidate
may be promoted to the canonical prompt only after review for generality and
regression checks. Score thresholds do not authorize inspecting gold answers
inside the answering agent or changing the judge.

Each 20-question trial pays for generation and grading but no ingestion. Its
reported evaluator spend is checkpointed and bounded by the existing run
ceiling; provider credit limits remain the hard shared monetary boundary.
Failures leave durable partial output for exact resume. Store backups remain
the existing native workflow, and trial outputs must be archived separately.
