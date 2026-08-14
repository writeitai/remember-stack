# REVIEW r6 — Claude

# Verdict: APPROVE_WITH_NITS

Review written to `design/reviews/REVIEW_claude-opus_request_path_meter_export_design_r6_2026-08-13.md` (matching the r1–r4 convention). Nothing implemented.

**The r5 blocker is closed.** The three ways this scheme normally breaks all check out:
- **Replay** — same cursor ⇒ same frozen bound, same start key, total order `(occurred_at, source, cost_id)`. A row can't sneak in behind the page because `safety_lag` (60s) strictly exceeds the enforced insert→commit ceiling (`statement_timeout` + `idle_in_transaction_session_timeout` ≈ 30s). Stable.
- **Truncated page** — `next_cursor` widens the horizon while advancing the key; widening a bound can't skip a key. No hole.
- **Late commit** — same 60s > 30s argument. The `SET LOCAL` GUCs are what turn this into a proof rather than a hope.

`server_time` from DB `clock_timestamp()` is the right call — an exporter clock running ahead of the DB would punch exactly the hole `safety_lag` exists to close.

**The one item worth fixing before implementation (N1):** required test #2 is off by one poll, and its stated rationale is wrong. Waiting `> safety_lag` changes neither the row's `occurred_at` nor the *frozen* horizon on the cursor you already hold. Insert R, sleep 61s, poll with `NC1` → bound is still `H1 < t`, row absent. It takes the *next* poll — the one using the refreshed `next_cursor` — to see it. So: insert → wait → poll (refresh, empty) → poll (row appears).

That's a text defect, not a design defect (§5.2's rule itself is unambiguous), which is why this is APPROVE_WITH_NITS. But the literal test goes red, and the tempting "fix" is to give cursored requests a fresh horizon — reinstating the exact r5 inconsistency. One sentence fixes it: the horizon advances only when a new `next_cursor` is issued, so steady-state export latency is `poll_interval + safety_lag`, not `safety_lag`. Consumers need that to pick a poll interval.

Six smaller nits: clamp `bound = min(cursor.horizon_at_issue, request_horizon)` (a corrupt cursor or backwards clock step can otherwise skip rows permanently — the one failure mode the design refuses everywhere else); pin the `ORDER BY`/keyset predicate; read the clock as the snapshot's first statement; page-field nullability (`cursor` on a no-cursor request is undefined, and the golden test forces the question); the self-contradictory §4.5 mismatch sentence; spine-pool headroom now that every interactive embed takes a connection.
