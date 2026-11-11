# Storage capture checks

The initial implementation supplies the closed D152 record types and pure
canonical encoding/exact-membership checks. It does not collect native bytes,
authenticate a provider, qualify a storage profile, persist capture completion
or authorise a financial charge. Synthetic protocol fixtures are not evidence
of a real storage cut.

On 2026-11-11 the focused suite passed **22 tests in 0.58 seconds** under
Python 3.12.2 and Pydantic 2.13.4. It checks zero-byte reference preservation,
bounded streaming consumption, exact empty sets and ACK replay, missing-page
refusal, duplicate/reordered members, payload tampering, noncanonical unsigned
values and content-value suppression in validation errors. It also round-trips
the JSON manifest, independently matches the six recorded existing-v3 terminal
digest vectors and refuses a host-local timestamp without native timezone
evidence. The v3 helper preserves its existing plain hash, UUID/string forms,
nulls and JSON integers; it does not apply the new capture-domain encoding.

Run in the normal repository development environment:

```bash
uv run pytest src/tests/core/test_storage_capture.py -q
uv run ruff check src/rememberstack/model/storage_capture.py \
  src/rememberstack/core/storage_capture.py src/tests/core/test_storage_capture.py
uv run pyright src/rememberstack/model/storage_capture.py \
  src/rememberstack/core/storage_capture.py src/tests/core/test_storage_capture.py
```

Scoped Ruff/format and Pyright checks passed. The local worktree reused an
already installed engine environment with an explicit Python interpreter for
Pyright; no dependency or image download was needed. Full current-head CI and
the actual native/provider/profile tests remain delivery requirements. The
existing two-version/provider-call tests must still qualify D56 when the native
consumer is integrated with ingestion.

The separate native relation guard uses the existing PostgreSQL SQL lock
paths, including unchanged-value TOAST reloptions and unchanged-tablespace
index DDL. It installs only a fixed function using already provisioned
definer/collector roles; it creates no LOGIN, membership, secret, migration,
CLI, scheduler or API. Its supported schema/build, native deadline and
transaction bounds are explicit. Allocation is sampled while those locks
remain held; rollback release restores the logically unchanged catalog
options but does not erase the operator's WAL/cleanup cost.

The completed primitive counterexamples and ordinary-role observations are
tracked in [the inventory analysis](../../plan/analysis/storage_inventory/README.md).
The new full-engine native regression suite uses only
`REMEMBERSTACK_STORAGE_GUARD_TEST_DATABASE_URL`, refuses a database whose name
does not start `capture_guard_`, and requires that scratch database to start
empty. It never reads the normal application database setting or resets an
existing schema. An external disposable-container harness owns removal.
Full-schema execution of this new fixed function remains under qualification
at this checkpoint; it is not a completed D152 T/clock/view witness or a
classified/chargeable capture. No role or function has been installed live.
