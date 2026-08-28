# Claude Opus 5 review — D99 profile-lock contention typing

**Date:** 2026-08-28

**Reviewed commit:** `e8872adb`

**Verdict:** `REQUEST_CHANGES`

## Invocation

The review used the operator-required command shape:

```text
claude --dangerously-skip-permissions --model claude-opus-5 --effort xhigh -p "<review prompt>"
```

The prompt requested a read-only review of the narrow SQLSTATE `57014`
advisory-lock translation, worker safety, unrelated database-failure behavior,
clustering attestation, the real PostgreSQL proof, and the binding D99 text.

## Findings

1. **High — the profile snapshot's identity-epoch lock remained raw.** The
   first commit translated its member evidence locks but accidentally changed
   the re-entrant identity acquisition in `current_profile_entity_ids` instead
   of the bounded identity acquisition in `_locked_profile_state`. An exclusive
   identity holder could therefore still produce the same generic timeout and
   dead-letter path.
2. **Medium — the clustering attestation coverage was inverted.** Its identity
   acquisition is re-entrant in the only production caller, while its blocking
   evidence acquisition remains raw in the automatic-merge mode. Proposal-only
   D99 uses nonblocking member locks; automatic identity mutation deliberately
   retains stronger blocking semantics.
3. **Medium — message matching is PostgreSQL-19-specific.** Claude verified the
   advisory-lock `CONTEXT` text on the pinned `19beta3` image but did not see it
   on PostgreSQL 15–18. A future cleanup should use a shorter `lock_timeout` and
   SQLSTATE `55P03` so lock-only classification is structural rather than text
   based.
4. **Medium — no negative timeout proof.** The initial test proved the positive
   advisory-lock case but did not prove that a non-lock SQLSTATE `57014` remains
   `OperationalError`.
5. **Medium — the test entered the private snapshot helper.** It did not drive
   the public refresher and evidence-worker composition with a real busy lock.
6. **Low — the worker log overstated cache clearing.** Initial lock contention
   can occur before stale projection fields are cleared; fail-closed hash
   validation remains intact, but the log said the cache was empty.
7. **Low — typed contention from clustering would be swallowed with profile
   contention.** This was a consequence of the misplaced helper at the
   re-entrant clustering identity acquisition, not an intended expansion.

## What the reviewer approved

Claude confirmed that psycopg `QueryCanceled` reaches SQLAlchemy as
`OperationalError`, that `sqlstate` is exposed as expected, that the helper is
narrowly wrapped around lock statements, and that the v0.7.2 evidence-worker
boundary is otherwise a sound place to avoid replaying paid work. It also
verified the positive mechanism directly against the pinned PostgreSQL
`19beta3` image and reported cleanup of its temporary databases.

## Disposition

The high finding was fixed before PR creation: `_locked_profile_state` now
routes both its bounded shared identity lock and each member evidence lock
through the typed helper, while `current_profile_entity_ids` returned to its
pre-patch identity behavior. The real PostgreSQL proof now covers both lock
keys. A negative `pg_sleep` timeout proof preserves generic database failure,
and the worker log now says the projection remains fail-closed rather than
claiming it is empty.

The PostgreSQL-19 message dependency and wider public-boundary integration test
remain explicit follow-ups. Replacing the existing timeout policy is not needed
to repair the pinned PG19 failure and would broaden this urgent dependent PR.
