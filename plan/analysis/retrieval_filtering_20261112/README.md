# Read-only retrieval evidence, 2026-11-12

These artifacts support the [non-binding reliability analysis](../retrieval_filtering_reliability_20261112.md).
They contain no generated answers, changed grades, credentials or database URLs.

## Reading the evidence

- `measurements.json` summarizes operation timings, continuation accounts,
  prototype comparisons and expensive actual-plan nodes.
- `cases-conv26.json`, `cases-conv26-unscoped.json` and `cases-conv30.json`
  contain exact diagnostic inputs. Conv-26 uses saved tool arguments from
  terminal timeout item `conv-26/qa/0123`; conv-30 selects the first scoped,
  current-time deadline for each tool, independently of gold/judge labels.
- `conv26/` and `conv30/` contain scoped native-operation captures.
  `conv26-unscoped/` uses the same question/vector without an entity filter
  to identify the index path; it is not a substitute answer strategy.
- `*/metadata.json` records actual PostgreSQL/extension versions and the
  stored vector's source/hash. The vector is 1,536-dimensional. It is **not**
  the question embedding; the in-memory provider performs no network call.
- Each operation's `statements.json[.gz]` contains emitted SQL, safe bind
  parameters, stack labels and driver execution time. A streaming cursor's
  subsequent fetch time is outside driver execute time; use the whole
  continuation-call duration and accounts for that channel.
- `plan-NNN.json[.gz]` refers to a statement ordinal. Some contain estimates
  only; inspect `analyze` before treating rows as measured. Independently
  replayed plans use the controls recorded in the file. Normal-planner
  EXPLAIN of an index-nomination statement is not the native cursor's
  forced/attested plan. The native implementation checks its expected index
  before fetching; successful accounts establish that attestation passed.
- `conv26-compare/` and `conv30-compare/` contain readable original/prototype
  `.sql` and results/plans. Execution and EXPLAIN run separately under
  individual 20-second statement limits, with `transaction_timeout=0`, JIT
  off and parallel query workers disabled for comparability. These are
  transaction-local diagnostic controls, not API configuration changes.
- `*-receipt.json` records helper revision, unchanged original answer-state
  hashes, zero provider calls and restoration of retained PostgreSQL to its
  prior stopped state. API, object store, ingestion workers and setup are
  never started by these diagnostics.
- `gcs-receipt.json` records immutable off-host archive generation, SHA-256,
  CRC32C and independent complete download/manifest validation. This is an
  analysis archive; it does not replace the separate full-store backups.
- `compressed-artifacts.json` records raw sizes/hashes for larger JSON
  files compressed losslessly with gzip. `artifact-manifest.json` records
  published file hashes. SQL vector literals in plans/parameters are redacted.

The native fact/claims captures use an operation-wide 20-second deadline.
The campaign's original operation limit remains 120 seconds. The prototype
statement limit is independent of the native operation limit. A cancelled
EXPLAIN has no completed actual-plan tree; its timeout is a censored result.

## Reproduction

Use a retained store or an isolated copy with the recorded source/schema,
one diagnostic reader, and a private URL supplied via
`REMEMBERSTACK_DIAGNOSTIC_DATABASE_URL`. Do not commit or print that URL.
The helpers configure default read-only transactions and verify read-only
state. They do not run setup, migrations, ANALYZE or provider calls.

From the repository root, with the project's dependencies installed:

```bash
PYTHONPATH=src:. python scripts/diagnose_retrieval_sql.py \
  --cases plan/analysis/retrieval_filtering_20261112/cases-conv26.json \
  --output /tmp/retrieval-native \
  --operation-seconds 20 --plan-seconds 20 --analyze-limit 1

PYTHONPATH=src:. python scripts/compare_retrieval_sql.py \
  --capture /tmp/retrieval-native --output /tmp/retrieval-comparison \
  --seconds 20
```

The comparison helper expects a slow captured scoped fact nomination and/or
claim hydration statement matching the inspected SQL shape. Its guarded
string transformations are measurement prototypes, deliberately **not** a
runtime SQL builder. A different slow stage should be diagnosed separately.
For this recorded conv-30 comparison, use
`--variants claims-native-1,claims-materialized-1,claims-materialized-256`.

To inspect a compressed plan with standard Python:

```bash
python -m gzip -d -c \
  plan/analysis/retrieval_filtering_20261112/conv26-compare/claims-native-16.json.gz
```

## Interpretation limits

These are single-reader SQL observations on two static retained stores,
while unrelated conversations in the authorized campaign run elsewhere on
the same VM. They do not establish production throughput, eight-reader
latency, exact question recall, complete historical-scope equivalence or a
new LoCoMo score. Plans add instrumentation overhead; ordered experiments
have cache effects. Completed subset hashes compare all returned column
values after sorting rows, not merely IDs. A full-batch original timeout
does not establish full-batch value equivalence.

The SQL-only shared-coverage result is evidence for a general implementation
candidate. It is not an accepted design, a shipped fix or permission to omit
entity/source/time checks. Original generated answers, judges and GCS store
backups remain authoritative for the original campaign.
