# Current LoCoMo next steps

**Status:** reset for `RS-LoCoMo-Full-v10`; wait for the fresh current-system
publication result before choosing product changes.

The 2026-07-31 findings and the v8/v9 work queue describe retired protocols and
tool surfaces. They remain historical evidence, but they must not be projected
onto v10. The current queue is intentionally small:

1. Run all ten conversations from one exact `main` revision with fresh isolated
   stores and the v10 protocol.
2. Publish the score, costs, failures, and per-category diagnostics from that
   run without combining artifacts from any older revision or protocol.
3. Build the next ranked work queue only from v10 traces. Any prompt, recipe,
   model, or extraction change requires a new fingerprinted protocol and a new
   measurement.

Per-sample databases are disposable and need no routine backups. Preserve the
run directory and log; inspect a failed live store in place only when a specific
investigation needs it.
