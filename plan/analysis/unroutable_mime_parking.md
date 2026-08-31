# Unroutable input: park the work, keep the document

**Status:** analysis (non-binding). Supports **D106** and the
`e0_files_design.md` §3 amendment that makes conversion routability a
scheduling decision at ingest.
**Date:** 2026-09-03. Engine evidence read at `origin/main` `68ab6115`.
Statements about the managed fleet's live route table come from a **different
repository** (`ultimate-memory-cloud`) and cannot be checked here; no engine
reasoning depends on them.

---

## 1. What happened before D106

The conversion router was consulted for the first time **inside the convert
worker**. `ConversionRouter.converter_for` resolves a MIME by exact dictionary
lookup and raises `UnroutableMimeError` when the key is absent; the ingest path
never asked. So an upload of a type the deployment could not convert was
admitted, hashed, written to immutable raw storage, returned as a normal
accepted-not-ready receipt, and one stage later:

```python
except UnroutableMimeError as err:
    self._catalog.mark_version_failed(version_id=source.version_id, error=str(err))
    raise NonRetryableHandlerError(str(err)) from err
```

The version was marked `failed`, the work row landed in the dead-letter queue,
and the caller discovered a terminal state by polling readiness.

## 2. What was actually wrong with that

Not the storing. **The storing was right, and an earlier draft of this analysis
got that badly wrong** by proposing to refuse the upload outright. Three facts
in the tree say otherwise:

- `plan/designs/e0_files_design.md` §5 mounts the **raw bucket** read-only,
  reached by following an explicit `raw_uri` pointer from a corpus stub;
- the corpus projection `LEFT JOIN`s `document_representations` and selects
  `content_objects.raw_uri` — there is no `status = 'ready'` filter, so a
  version with no representation still projects;
- `workers/p3.py` renders `- Full text: (not converted)` when `markdown_uri`
  is absent, and still emits the `raw_uri` line beside it.

An unconverted document is therefore a **designed state**, not an accident. It
appears in the corpus filesystem, and an agent can follow the pointer into the
raw mount and read the original — a PDF it can open, an image it can look at, a
recording it can play. Refusing the upload would delete that capability to fix
something else entirely.

What was wrong was the **work**:

- a row in the dead-letter queue for something that was never broken, only
  unsupported, which makes the DLQ a worse signal for everything else in it;
- a version marked `failed`, which says the document is defective when the
  truth is that the deployment has no converter for it yet;
- an attempt consumed to discover something the route table already knew;
- and no way back. Identical bytes are the D55 no-op, no API requests
  reprocessing, and adding the route later did not convert versions that had
  already failed without one. Recovery meant an operator replaying dead-letter
  rows one at a time.

## 3. Park, don't fail

The ledger already has the right primitive. Parking sets `status='pending'`
with a `defer_reason`, consumes no attempt, records no error, and — per D67 —
"can never cause dead-lettering". Budget parking uses it for healthy work
waiting on a spend window. Work waiting on a converter is the same shape.

So the convert row is **enqueued already parked** with `no_route`. Ingest knows
the route table, so the decision is made before the row exists and no attempt
is ever spent on it.

### 3.1 Why the reason excludes it, not a timestamp

Budget parking sets a future `not_before`: the budget window genuinely rolls at
a known time. `no_route` has no such instant — it waits on a *configuration*
fact, a converter that may never be registered. Two ways to express that:

| Option | Verdict |
| --- | --- |
| Far-future `not_before` sentinel | Rejected. It lies about when the work is due, and anything that recomputes `not_before` (lane promotion, a janitor, a replay) could silently release it into a guaranteed failure. |
| Short `not_before`, re-park on each wake | Rejected. It busy-polls a configuration change that may never come, and every cycle spends an attempt to re-learn what the route table already says. |
| **Exclude `defer_reason = 'no_route'` from the claim (chosen)** | The row is not due because of *what it is waiting for*, not when. `_CLAIM_SELECT` skips it; releasing is an explicit act. |

### 3.2 Why releasing is explicit

No process can observe another process's restart, and the route table is
deployment configuration. `WorkLedger.resume_no_route` (and
`remember ops resume-no-route`) is run by whoever registered the converter.
That is the recovery the dead-letter path could not offer: the backlog that
arrived before the converter existed converts with no re-upload and no per-row
replay.

## 4. Where the decision is made

The route table is deployment policy (D61). Two placement questions.

**In E0, not on a surface.** Three ingresses reach E0 without sharing a
handler — HTTP `POST /ingest`, the local MCP `ingest` tool, and the connector
sync worker, the latter two calling the composed port directly. A check on the
HTTP handler would leave two of three still enqueueing doomed work.
`UploadIngestor` is the one object all three write through, which is what the
library boundary already requires: *ingestion always writes through E0*.

**Against the deployment's own table, matched exactly.**
`build_conversion_routes` raises `UnknownConverterError` on an unknown adapter,
so composition fails at startup rather than producing a router with fewer keys
than its configuration. A running process's router keys are exactly its
configured keys, and the membership test ingest performs is the one the router
performs. The guarantee is per-configuration, not global: ingest and the
convert worker are separately composed, so a route-table change leaves a window
where one has restarted and the other has not. That window is why
`UnroutableMimeError` stays in the worker, non-retryable.

Matching is exact because `converter_for` is an exact dict lookup. Normalising
`text/plain; charset=utf-8` at ingest but not in the router would schedule work
the worker then dead-letters — the outcome this removes. Normalisation belongs
in the router, where both callers inherit it.

`routable_mimes` is a **required** constructor argument. A default of "no
check" would make the behaviour as strong as every composer remembering to pass
it; every deployment has a route table, so omission expresses only a mistake.

## 5. What this deliberately does not do

- It does not make any format supported. Registering an adapter is a separate
  act; this only stops treating an unregistered format as a failure.
- It does not validate that the bytes match the declared type. An MP3 labelled
  `text/plain` is scheduled and fails in the converter, correctly — that is a
  content error, not a routing one.
- It does not cover the managed-metering path. `record_managed_measurement_on`
  enqueues convert after admission from a spine module that does not hold the
  route table, so that path still dead-letters. Closing it needs either the
  table or the decision carried to that path; it is named here rather than
  left silent.

## 6. Sources

- `src/rememberstack/core/conversion.py` — `ConversionRouter.converter_for`,
  `STOCK_CONVERSION_ROUTE_NAMES`.
- `src/rememberstack/adapters/converters/__init__.py` —
  `build_conversion_routes`, `UnknownConverterError`.
- `src/rememberstack/workers/e0.py` — `UploadIngestor`, the convert stage's
  `UnroutableMimeError` handling.
- `src/rememberstack/spine/work_ledger.py` — `_CLAIM_SELECT`, `park_for_budget`,
  `resume_no_route`.
- `src/rememberstack/spine/projection.py` — `_SELECT_CORPUS_DOCUMENTS`, the
  `LEFT JOIN` that projects unconverted versions.
- `src/rememberstack/workers/p3.py` — the stub builder's `(not converted)`
  rendering beside `raw_uri`.
- `plan/designs/e0_files_design.md` §5 — the read-only raw mount.
- `decisions.md` D12, D40, D55, D61, D65, D67.
