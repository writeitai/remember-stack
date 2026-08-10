# Current LoCoMo next steps

**Status:** `RS-LoCoMo-Full-v11` completed at 979/1540 (63.57% judge
accuracy), official F1 0.5417, at tested revision `213551c7`. The answer and
retrieval findings remain relevant on current `main`; D86 changed E3 after the
run, so this is not an exact new score for current `main`. Evidence and proposed
changes are in
[`../../plan/analysis/locomo_v11_score_regression_analysis.md`](../../plan/analysis/locomo_v11_score_regression_analysis.md).

The 2026-07-31 findings and older work queues describe retired protocols and
tool surfaces. They remain historical evidence. The following queue is proposed
pending owner acceptance and stays small:

1. Replay the 393 `current_context` IDs with base `question_context` on the same
   stores/reader to test the primary routing hypothesis.
2. Independently ablate fact enrichment, entity enrichment, and temporal
   presentation; do not bundle the changes.
3. Restore V8-style evidence-first prompt priority while keeping all 22 D85
   paths freely selectable. Do not hard-code a first tool in the harness.
4. Compare pinned base retrieval with the already-built one-call multi-hop
   compound path, and trace one temporal plus one multi-facet ingest example.
5. Repair the 23 structured-output failures through a separately measured,
   bounded change.
6. Only after a diagnostic win, run the multi-hop/temporal subsets and then
   consider another full publication run. Every scored prompt, routing,
   catalog, model, or ingest change gets a distinct fingerprint.

Per-sample databases are disposable and need no routine backups. Preserve the
run directory and log; inspect a failed live store in place only when a specific
investigation needs it.
