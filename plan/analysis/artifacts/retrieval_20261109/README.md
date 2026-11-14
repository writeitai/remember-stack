# Retained-store retrieval measurement, 2026-11-09

The [analysis](../../retrieval_cost_and_conv47_20261109.md) explains scope,
results, alternatives and the three conv-47 misses. This folder contains
non-secret summaries and the exact executed measurement harness. It changes
no production algorithm or binding design.

- `measurements.json`: five observed passes of 24 saved requests, nested wall
  phases, SQL counters and serialization. Quantiles use nearest-rank p95.
- `profile.py`: instrumentation. Its archived-vector embedder makes no network
  call and rejects unknown query inputs. A generative call would fail because
  this deliberately partial diagnostic provider exposes no such method.
- `runtime.json`: source, image, harness and original-state identities.
- `store-evidence.json`: all 15 checked product fingerprints match R4.
- `embedding-probes.json`: three separate, billed live query-embedding probes.
  Do not combine their times with replay medians as an end-to-end measurement.
- `plans-summary.json`: three individual archived-statement plans, including
  planning versus execution and logical buffer-hit counters.
- `misses.json`: unchanged R4 answers/grades, raw benchmark reference turns,
  returned chunk positions and usage. Token totals are across agent calls.
- `gcs-receipt.json`: new immutable off-host report and verification hashes.

The report archive contains the full timing events and SQL texts, without SQL
parameters, plus plans and operator probe source. The old input fixture is
the `post-fix` directory in the retained diagnostic report:
`gs://remember-stack-locomo-backups/reports/locomo-retrieval-deadline-20261106/20261106T182955Z-7200a4a2/`.
The paid R4 store is the native six-archive checkpoint linked by UMC PR #783.
Do not rerun ingestion to reproduce this profile.

Reproduction requires a disposable physical copy of the R4 PostgreSQL RC1
store, started with the original extensions and no exposed network ports.
The original store stays stopped. Run the harness in image
`rememberstack-retrieval:8cd181fe0`, as root for its private output directory,
with only `/app/src` overlaid read-only from source `fb4a5f5a4`. Mount the
copy's Unix socket at `/pgsocket`, old diagnostics at `/old:ro`, and an empty
writable output directory at `/report` containing `profile.py`.

```sh
docker run --rm --user 0:0 --network none --entrypoint python \
  -e PYTHONPATH=/app/src:/app \
  -v "$SOURCE/src:/app/src:ro" \
  -v "$COPY_SOCKET:/pgsocket" \
  -v "$OLD_DIAGNOSTICS:/old:ro" \
  -v "$REPORT:/report" \
  rememberstack-retrieval:8cd181fe0 /report/profile.py
```

These are operator-supplied non-secret paths, not shell variables to set to
another project. Never mount over all of `/app`: that hides the image's Python
dependency environment. The harness uses the copied database's
`rememberstack` diagnostic account. It excludes managed routing, authentication,
HTTP serialization/transport, production metering overhead and live model
latency; it is not a full current-main deployment validation. The stored
schema is `p9_45_0066`; no migration or setup command was part of this test.
The sample selection favors earlier failed requests. Instrument logging adds
unsubtracted overhead. First pass is not a verified OS-cache-cold result.

No new answer, judgment or extraction was generated. Original ingestion and
result archives are unchanged. The temporary database and copied data were
removed after report verification; the report is retained off-host.
