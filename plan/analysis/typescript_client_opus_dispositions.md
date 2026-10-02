# Opus design round 1 dispositions

**Status:** non-binding review notes. Original reviewed cloud commit: 7fe498f5.
Full original report is retained in cloud analysis/reviews/typescript-client-opus-design-round-1.md.

| Finding | Disposition | Binding home |
| --- | --- | --- |
| H1 engine authority/private design | D141 full public design; D95 reduced to provider/naming | D141, engine §1, cloud counterpart |
| H2 private schema/fork CI/deadlock/issuer coupling | No private schemas; issuer-independent get/whoami; one-way provider check public artifacts/pinned source | Engine gate 5; cloud provider obligations |
| H3 wrong baseline/inventory/missing exports/citations | Source SHA target; normative JSON includes constructors, resolver, root exports and query properties; post-tag differences and corrected test citation | Inventory and §2 |
| H4 unsafe 421 write retry | Never replay writes; refresh next call; D136 and D90 amended | Engine §4/D136 |
| M1 read classification/cache/next-call recovery | Source _READ_ROUTES, no timeout/abort retry, TTL refresh with pinned ID, write refresh without replay | §3–4, adaptations |
| M2 D90 contradictions/errors | Explicit keys follow caller; stored keys restricted; actual account methods; validated optional diagnostic fields | Cloud D90 §9/13, engine errors |
| M3 selective search privacy | Keep Python GET/POST behavior; disclose remaining URL metadata | §5 |
| M4 undeclared differences | Explicit adaptation table, fixed MIME mapping, Windows refusal, fetch proxy/CA adapter | §7 |
| M5 non-exported errors/hierarchy | Mapping table with fields/classes; compare class/code not repr | §7 exceptions |
| M6 defaults/strictness | 2020-12 schemas; fill defaults; output declarations non-optional; no coercion adaptation | §4/7 |
| M7 integer precision | Reject unsafe parsed/request integers without rounding; explicit cast/string path for SQL | §7 |
| M8 generator scope/version/alternatives | Type-only 3.1→3.0 projection, pinned 0.30.0; native null/const/union/binary experiment; alternative proposal | §4/analysis/proposals |
| M9 CLI/MCP support/gates | Public Connection/credential read/schema/issuer/catalogue contracts; CLI owns writes/locks/journal; separate design gates | §7 and delivery plan |
| M10 real released engines | Add TS lane to existing 0.15.0/0.16.0/candidate matrix, explicit absent features | Gate 7 |
| M11 entry/publish scope | Restore Layer 5 entry for other work; scoped TS implementation exception; publication needs owner approval/D5 | Cloud roadmap and release |
| L1 resolution cancellation/cache | Process cache fingerprint/project; waiter abort isolated | §7 |
| L2 units | timeoutMs/pollIntervalMs; retryAfter seconds | §7 |
| L3 fixture comparison | Normalize date instant/UUID case/query order; retain values/defaults | Gate 4 |
| L4 nested casing | Schema-owned nested snake_case/ISO strings | §7 |
| L5 credential/key details | Nonblocking/no-follow regular file; Bearer/linebreak/empty env checks | §3 |
| L6 connector wording | Provider connector implementations are separate; SDK methods retained | D141/scope |
| L7 proposals | Browser core and alternate generators have adoption triggers | proposal |
