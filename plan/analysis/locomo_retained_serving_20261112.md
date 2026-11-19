# Retained LoCoMo ingestion with an upgraded serving runtime

Working analysis for the owner-authorized full rerun after D153. D153's binding
reuse contract says the SQL/index migration does not change paid processing
generations. Its final acceptance paragraph explicitly permits a fresh answer/
judge pass over retained stores with distinct execution provenance.

The existing retrieval-ablation records `source_processing_revision` and
`ablation_runner_revision`, but its live guard still insists the serving build
and query-space hash equal the original processing run. This correctly rejects
silent drift, but cannot express a deliberate read-path upgrade. Editing the
old run.json/readiness/fingerprint would falsify ingestion provenance; repeating
paid ingestion only to satisfy a read-build assertion is unnecessary.

The selected implementation is a native `reuse-ingestion` preparation command.
It creates a fresh current execution with an immutable `retained_ingestion`
origin, preserves the original config/state bytes and fingerprints, and records
actual new serving readiness. It re-renders/compares the same immutable document
and image inputs without model calls, copies only ingestion IDs and verified
readiness, and starts with no answers/judgments. Ingestion is forbidden on that
scoring fork. The old/new processing generations, model bindings, version rows,
content coordinates and current serving build/surface all remain strict guards.

This supersedes the earlier idea of relaxing live guards with a current-serving
flag. That flag would also require new backup restore-identity handling. A fresh
execution run expresses current serving identity to the existing native six-
archive backup/restore path, while explicitly preserving its actual original
paid processing source. No live guard or native backup invariant is weakened.
Default runs retain unchanged byte representations/fingerprints when no retained
origin exists. The new mode changes no prompts, ranking, ingestion generations
or provider work, and introduces no legacy serving fallback.

Costs: one newly requested full 1,540-question answer/judge pass plus completion
of conv48 pending processing and first ingestion of conv49/50; no paid processing
for the seven finished conversations. Fresh OpenRouter key allowance was checked
by the operator and is recorded outside source; no secret values are retained.
All new answers/traces/judgments and native stores require GCS archive readback.
This is benchmark execution provenance, not a new product visibility rule or
release recovery coordinator. The native original pipeline revision is retained
for finishing ingestion, then serving is upgraded explicitly.

Sources: benchmarks/locomo/ablation.py (`_guard_live_source`,
`_configuration_base`), runner.py (`_readiness_matches_protocol`,
`_require_exact_live_ingests`), D153 §6/§9 and the live campaign's preserved
source/backup records. Implementation and Opus review are required before use.
