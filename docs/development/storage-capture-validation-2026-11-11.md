# Storage capture encoding checks

The initial implementation supplies the closed D152 record types and pure
canonical encoding/exact-membership checks. It does not collect native bytes,
authenticate a provider, qualify a storage profile, persist capture completion
or authorise a financial charge. Synthetic protocol fixtures are not evidence
of a real storage cut.

On 2026-11-11 the focused suite passed **13 tests in 0.81 seconds** under
Python 3.12.2 and Pydantic 2.13.4. It checks zero-byte reference preservation,
bounded streaming consumption, exact empty sets and ACK replay, missing-page
refusal, duplicate/reordered members, payload tampering, noncanonical unsigned
values and content-value suppression in validation errors.

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
