# Antigravity review: LoCoMo Codex subscription evaluator

**Reviewer:** Antigravity (`agy`)
**Date:** 2026-09-07
**Branch:** `feat/locomo-codex-subscription` against `origin/main`
**Scope:** analysis, binding benchmark amendment, adapter, protocol, CLI wiring,
dependency pin, and tests
**Final verdict:** **APPROVE**

## Review method

Both rounds were executed read-only with the operator-required command shape:

```text
agy --dangerously-skip-permissions --print-timeout 180m0s -p "<review prompt>"
```

The reviewer inspected the current diff and surrounding implementation. Its
focus included the authentication boundary, sandbox and agent-action controls,
strict output schema, accounting, protocol fingerprints, provider composition,
backward compatibility, dependency packaging, typing, and test coverage.

## Round one

The initial verdict was **Changes Requested**. It identified:

1. A blocker: ingest preflight hardcoded `temperature=0.0` and no reasoning
   effort, which the Codex adapter correctly rejects.
2. A high-severity diagnostic issue: a non-completed turn without counters
   could lose its provider error behind an accounting error.
3. The official synchronous SDK raises failed turns before returning its
   collected result, so the synthetic failed-turn test overstated available
   partial-usage accounting.
4. No unit test exercised the actual official-SDK call boundary.
5. A concern that no-network behavior might be prompt-only.
6. Use of `usage.last` rather than `usage.total`.
7. Forced account refresh on every call.
8. Low-severity private-helper coupling already shared by the Vertex adapter.
9. A required rebase onto the new dependency-group layout on `main`.

## Remediation

- [`runner.py`](../../benchmarks/locomo/runner.py) now passes the prepared
  protocol's answer temperature and reasoning effort into preflight. A staged
  Codex-protocol test covers the fresh-ingest path.
- [`codex_subscription.py`](../../src/rememberstack/adapters/codex_subscription.py)
  preserves a non-completed turn's real error whether or not counters exist,
  consumes `usage.total`, and reads the account without forcing refresh.
- The analysis and binding amendment explicitly state the synchronous SDK's
  failed-turn partial-usage limitation rather than inventing accounting.
- A direct mocked-SDK test verifies the ChatGPT account check, ephemeral thread,
  deny-all approvals, read-only sandbox at thread and turn levels, output schema,
  no forced refresh, and turn-total accounting.
- Inspection of pinned `openai-codex==0.147.0` showed that
  `Thread.run(..., sandbox=Sandbox.read_only)` serializes a
  `ReadOnlySandboxPolicy` whose `networkAccess` defaults to `false`. The
  reviewer withdrew its prompt-only network concern; trace rejection remains
  defense in depth.
- The strict-schema helper stays shared by private import, matching the existing
  Vertex adapter and avoiding unrelated refactoring.
- The branch was rebased onto `origin/main` at `1a909768`; the SDK dependency is
  in the `benchmark` extra and the `dev` group, not the server group.

## Round two

Antigravity verified each remediation and independently ran static analysis and
focused tests. It reported:

- Ruff: clean.
- Pyright: 0 errors, warnings, or informational diagnostics.
- Focused adapter/protocol/runner tests: all passed.
- Remaining actionable findings: **none**.
- Verdict: **APPROVE**.

The implementer additionally ran the whole test suite with the one unrelated
clean-main MIME assertion deselected: 1,680 passed and 742 skipped. Running the
suite without that deselection produces the same baseline failure where macOS
classifies a temporary `note.md` as `application/octet-stream` instead of
`text/markdown`.

After the approval, GitHub CI found that the new test file was absent from the
repository-specific test inventory. The file was added to the unit shard and
the inventory check was rerun locally; this delivery-gate fix does not change
the reviewed implementation.

Immediately before merge, the branch was rebased again onto `origin/main` at
`b776b3e5`, which had advanced the benchmark from full-v22 to full-v24 for the
D107 temporal-extraction pins. The additive Codex variant was rolled to
`full-v24-codex-subscription`; its provider, authentication, sandbox,
accounting, and stage-composition contracts are unchanged.
