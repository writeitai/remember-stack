# Batch B contract corrections: authority reconciliation

**Status:** non-binding implementation analysis  
**Date:** 2026-08-05  
**Binding inputs:** `plan/designs/open_query_space_design.md` §4 and §6; D68 in
`decisions.md`; the shipped Batch B migration and executor

## Question

Post-implementation review found six small mismatches around the SQL sandbox:
the analytical temporary-file value disagrees with the one deployment role,
NUL input reaches a parser that truncates it, rejected/failed results inherit a
false `empty_result`, discovery hand-copies only part of `TierLimits`, the
design names a weaker isolation level than the executor needs, and the tenancy
prose implies a per-role database deny that PostgreSQL ACLs cannot express.
The question is whether any mismatch requires a new role, policy engine, or
tenancy mechanism.

It does not. Each has an existing authority that the implementation or prose
should follow.

## Evidence and authority

| Finding | Local evidence | Authority and narrow correction |
|---|---|---|
| Temporary-file cap | `src/rememberstack/spine/migrations/versions/p9_02_0023_query_space_roles.py` pins `temp_file_limit = 65536kB` on the single deployment query login, while `src/rememberstack/surfaces/query_sandbox/limits.py` advertises 1 GiB for analytical requests. | The durable role setting is the enforceable authority. Keep one role and one 64 MiB cap; do not invent an analytical login solely for a larger temp allowance. Amend §4.3's analytical value and `ANALYTICAL_LIMITS` to 64 MiB. |
| NUL input | `src/rememberstack/surfaces/query_sandbox/grammar.py::_assert_single_readonly_statement` calls `pglast.parse_sql` before checking the raw text. A NUL can make the parser ignore the suffix. | Raw request bytes are the authority before parsing. Reject `\x00` as `parse_error` before `parse_sql`; do not build a second lexer. |
| Failure emptiness | `QueryResult.empty_result` defaults false, and both failure constructors in `src/rememberstack/surfaces/query_sandbox/executor.py` omit it even though failures contain zero rows. | The serialized result is the authority. Every rejected/failed response has no rows and must say `empty_result=true`; this does not turn it into a D49 negative. |
| Discovery limits | `src/rememberstack/surfaces/query_sandbox/discovery.py` manually selects six fields from the 19-field `TierLimits` dataclass. The manifest already enumerates the dataclass fields. | `TierLimits` is the one limit authority. Serialize it with `dataclasses.asdict`; do not maintain a second list. |
| Transaction isolation | `QuerySandboxExecutor._transaction` uses `REPEATABLE READ` because nomination confirmation and the caller statement must share one database snapshot, while §4.2 says `READ COMMITTED`. | The D48 snapshot requirement and executor transaction are authoritative. Amend §4.2 to `READ ONLY, REPEATABLE READ`; do not weaken the executor. |
| Cross-database `CONNECT` | The migration revokes `PUBLIC` only on the database being migrated. `test_a_deployment_login_cannot_reach_another_deployment` proves isolation between two migrated deployment databases, not every arbitrary database in the cluster. | D68 protects deployment content, not socket reachability to an empty administrative database. Provisioning must revoke `PUBLIC` before a database receives deployment content or query credentials, and the pool/HBA route only to the bound database. PostgreSQL documents that effective privileges are the sum of direct, membership, and `PUBLIC` grants, so revoking from one login cannot override `PUBLIC`; `CONNECT` is also checked alongside `pg_hba.conf`. Do not add event triggers or claim a negative ACL PostgreSQL does not have. |

PostgreSQL references: [Privileges](https://www.postgresql.org/docs/current/ddl-priv.html)
and [REVOKE](https://www.postgresql.org/docs/current/sql-revoke.html), retrieved
2026-08-05. The former records default `PUBLIC` `CONNECT`/`TEMPORARY` grants and
the separate HBA check; the latter records additive effective privileges.

## Recommended contract

1. One deployment query login remains sufficient. Its enforced temp-file cap
   is 64 MiB for both interactive and entitled analytical requests.
2. Validation rejects a NUL before invoking pglast.
3. Every zero-row failure/rejection reports `empty_result=true` while retaining
   its non-success termination and error code.
4. Discovery publishes every `TierLimits` field under its exact dataclass name.
5. SQL execution is read-only repeatable-read because one public request may
   contain bounded internal confirmation work plus the caller statement.
6. Every content-bearing deployment database is migrated before content or
   query credentials exist: `PUBLIC` loses `CONNECT`, that database's derived
   login receives it, and the pool/HBA configuration routes the login only to
   that database. Arbitrary unprovisioned databases are outside the content
   boundary and must never contain deployment data.

## Rejected expansion

- A second analytical PostgreSQL role only to express a larger temp cap.
- Row-level security; D68 physical routing and grants remain the chosen model.
- Cluster event triggers that mutate every future database ACL.
- A custom lexer to compensate for one raw NUL precondition.
- A second hand-maintained discovery schema alongside `TierLimits`.

These alternatives add mechanisms without closing a requirement the existing
authorities leave open.
