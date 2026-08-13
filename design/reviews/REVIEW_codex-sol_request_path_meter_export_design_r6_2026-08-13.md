# REVIEW r6 — Codex

**Verdict: REQUEST_CHANGES**

One blocker remains in the cursor contract.

At an empty poll at `t0`, `next_cursor` contains horizon `t0 − safety_lag` ([lines 528–535](</Users/jpuc/code/moje/remember-stack-m1-agent1/plan/designs/request_path_metering_and_cost_export_design.md:528>)). If a row is inserted afterward and the consumer waits beyond `safety_lag`, polling that cursor still uses its old horizon ([lines 517–520](</Users/jpuc/code/moje/remember-stack-m1-agent1/plan/designs/request_path_metering_and_cost_export_design.md:517>)), so the row cannot appear as required by the test at lines 547–550. That poll only emits a new cursor containing the now-fresh horizon; the row appears on a second poll using that new cursor.

The design must either:

- Specify this one-poll advancement behavior and change the test accordingly, or
- Change the cursor protocol if first-poll visibility after waiting is required.

A stateless cursor issued before the wait cannot simultaneously preserve a frozen replay horizon and incorporate a future horizon when later consumed.
