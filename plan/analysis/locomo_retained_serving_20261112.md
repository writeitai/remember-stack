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

Add explicit full-profile opt-in `--use-current-retrieval`. Keep recorded source
inputs/configuration/readiness immutable, validate their original fingerprints,
require the same processing generations/models/documents, and separately require
the current serving revision/query manifest/tools/readiness. Fingerprint the
original source plus the actual new serving contract and both readiness
identities in the fresh output. Default trials retain their exact-source guard.
A generation/model/content mismatch must still fail before model calls.

The mode changes neither prompts, models, ranking, retrieval limits nor engine
eligibility. It performs no migration or ingestion itself. Operator rollout uses
normal native migrations and existing stores. Readiness still requires pipeline,
P1, graph and P3 capabilities. Returned source-version coordinates must match
every retained document. Existing answer/checkpoint files are never overwritten.

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
