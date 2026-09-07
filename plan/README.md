# Planning corpus

Requirements describe what the engine must do in `requirements/`. Accepted
architecture lives in `designs/`; research in `analysis/` is non-binding.
`plans/` owns build order. The canonical numbered authority log is
[decisions.md](../decisions.md). Design acceptance does not establish shipped behavior.

For the temporal program, start with [D118 mutable fact windows](designs/mutable_fact_windows_design.md)
and the [implementation sequence](plans/temporal_clocks.md). The
[analysis](analysis/lean_mutable_fact_windows.md) explains the independent audits
and rejected alternatives. PR #384 withdraws the old incomplete implementation;
this design does not claim that the replacement runtime ships.

D107's [canonical arithmetic and extraction](designs/temporal_clocks_design.md)
remain applicable. Its other fact-time rules and the D110–D113 framework are
superseded by D118's explicit authority map. Their documents and SQL remain
marked as historical artifacts; the [PostgreSQL design](designs/postgres_schema_design.md)
no longer incorporates the withdrawn DDL.
