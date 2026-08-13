# Sequencing — request-path metering, cost export, login

**Status:** sequencing only (not design). Binding how:
[request_path_metering_and_cost_export_design.md](../designs/request_path_metering_and_cost_export_design.md).

Work packages after the design is dual-reviewed and accepted:

| WP | Change | Exit |
| --- | --- | --- |
| WP-M.1 | Alembic `surface_cost_ledger` + catalog contract + schema doc amendment | migration upgrades; catalog test green |
| WP-M.2 | SQL recorder + QueryEngine._embed + sandbox embed + HTTP request scopes | tests in the design §8 (search/operation/open_query) |
| WP-M.3 | Export producer HTTP + `remember ops cost-export` | allowlist, cursor, heartbeat, token isolation tests |
| WP-M.4 | `remember login` / `logout` | file modes, revoke-before-unlink, precedence |
| WP-M.5 | Website configuration + getting-started (same PR as the user-visible WP it belongs to) | docs match shipped flags |

Issue #258 covers WP-M.1–M.3. Login needs its own issue before WP-M.4.
One release train after merge (tag/publish is a separate operator step).
