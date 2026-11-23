# Implementation Core Values

The non-negotiables for every line of code in this repository. Work packages, designs, and
reviews all assume these; a PR that violates one is not done, regardless of what it delivers.

1. **All code is fully typed.** Every function signature, every public surface, no untyped
   escape hatches. Pydantic models at boundaries; `TypedDict` / `enum` / `Literal` internally;
   pyright green at the strictest practical setting.

2. **No magic settings and values.** Every environment variable and every default value comes
   from the Pydantic settings file — never `os.environ`, never a literal buried in code. If a
   number or switch can be configured, it has exactly one home: settings.

3. **Simplicity beats complexity.** Do not add `if` statements, branches, or feature flags
   unless they are absolutely needed. Every conditional is a state to test and a path to
   maintain; prefer one straight path that does the right thing.

4. **Code is structured for the reader.** The most important functions appear at the top of
   the file; technical helpers and child functions appear below them. A reader meets the
   point of the module first and the plumbing last.

5. **Everything is well-tested, and the tests are thought out.** Tests verify behavior and
   the design's contracts — not line coverage for its own sake, and never mere existence of
   code. A test that couldn't fail for a real reason is not a test.

6. **Exceptions are never buried or trimmed.** Every exception either propagates, or is
   handled in a way that keeps the failure **visible**: the full traceback logged
   (`logger.exception(...)`; `traceback.print_exc()` where no logging exists), the failure
   recorded where the system tracks failures (a failed result, processing state, the dead
   letter), and the caller seeing a failure — never a fabricated success value
   (`return []` on error is a lie to the caller). Continuing past a contained failure is
   fine — a batch skips its poison item and dead-letters it; hard-failing everything is not
   the goal. What is banned is the failure *disappearing*: `except: pass`, `str(e)`-only
   handlers (the traceback and cause chain are destroyed), silent fallbacks. Re-wrapping
   preserves the chain (`raise ... from err`); catch-to-log lives at explicit boundaries
   (worker/CLI top level; per-item boundaries in batch loops) — not at every layer, which
   reports one failure many times. Real exception objects stay reachable for Sentry-class
   capture behind the telemetry port. "Failures never disappear," at code level.
   **[D158](decisions.md) controls how a handled failure is reported**, and refines this
   value in two places:
   - A degraded but successful outcome (a structuring fallback, a missing summary, a
     skipped poison chunk) is allowed when the failure is logged once at ERROR with a
     stable `error_code`. That record is the visible failure; the result is not a
     fabricated success.
   - Where the exception text can carry customer input (the query sandbox), the record
     omits `exc_info` and carries `exception_type` and a fixed message instead of the
     traceback text.

7. **Arguments are passed by name.** Call sites read as documentation:
   `pack(text=body, max_blocks=limit)`, never `pack(body, 512)`.

8. **Every function has a docstring.**
