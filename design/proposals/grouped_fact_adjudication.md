# Group small ordered sets of fact assertions

**Status:** proposal, explicitly not accepted for implementation. 2026-09-14.
**User direction:** record point 6 for a separate decision after the individual
processing improvements. No implementing agent may treat this file as a task.

## Problem and proposed alternative

Several pending assertions can share the same event/source evidence. After D121
compacts each individual prompt, repetition between calls may remain. The
alternative is one call for a small contiguous prefix of already admitted work
on the same canonical subject, with shared evidence shown once and an ordered
outcome for every incoming assertion.

Initial experimental group limits of four and eight are comparison points, not
accepted defaults. Do not wait to fill a group or skip queue heads to cherry-pick
all facts about one event. No event scheduler. If contexts do not overlap, a group
may cost more; measure its whole prompt, schema and output.

## Contract changes requiring a future decision

Each assertion retains its own meaning, provenance and receipt. A later item can
refer to an earlier item's new fact through a local handle. One coherent snapshot
is inferred outside locks, revalidated and applied sequentially in one transaction.
A stale or invalid plan retries/rolls back the bounded group, with no silent
partial completion. Source deletion, support moves and per-assertion repair jobs
must remain correct. These are application-boundary changes, not just prompt
formatting; they require binding design before code.

## Adoption trigger and comparison

Consider acceptance only after clear individual prompts, concise inputs, source
references and candidate nomination have measured validation results. First
measure context overlap without inference. Then compare total input/output cost,
call count, latency, retried work, candidate coverage, lost assertions and duplicate
facts. Reject a saving that depends on merging distinct propositions or ignoring
retry amplification. Provider-free tests are not model-semantic evidence.

The retained alternative is ordinary one-assertion application with compact
inputs. It has simpler retries and already preserves ordered receipts. Grouping
must earn its additional coordination through measured end-to-end processing
benefit; it is not needed to deliver D119–D123.
