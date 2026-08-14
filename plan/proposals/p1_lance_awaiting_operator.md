# Proposal: durable `awaiting_operator` + writer quiet gate

**Status:** rejected for D93 (2026-08-14)  
**Adoption trigger:** a staffed operations product that wants a ticket when IVF
cannot finish, and a human-driven quiet window. Not RememberStack self-host.

## Idea

After N rate-defers, M post-train conflicts, or T hours of continuous defer,
set `operator_state = awaiting_operator` and stop automatic retrain. An
operator may set `writer_gate=hold` so embed/label stop starting new Lance
batches, force one heavy, then release the hold.

## Why it was considered

IVF retrain is multi-hour and can lose the commit if writers never quiet.
A terminal visible state is more honest than claiming the index will
eventually catch up.

## Why it lost

RememberStack maintain is an unattended loop. A flag that requires a person
is a stop the engine cannot clear. Compact still works; search still works;
the next natural quiet (end of an ingest wave) is enough for autonomy.

See `plan/analysis/p1_lance_autonomous_heavy_analysis.md`.
