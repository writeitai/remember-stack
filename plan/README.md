# Planning corpus

Requirements describe what the engine must do in `requirements/`. Accepted
architecture lives in `designs/`; research in `analysis/` is non-binding.
`plans/` owns build order. The canonical numbered authority log is
[decisions.md](../decisions.md). Design acceptance does not establish shipped behavior.

For the temporal program, start with [D107 temporal clocks](designs/temporal_clocks_design.md),
then [D110 writes, corrections and lifecycle](designs/temporal_write_and_lifecycle_design.md).
D111's unknown-start coexistence amendment lives in D107 §4.2.1, with
[supporting analysis](analysis/temporal_undated_state_coexistence.md).
D112's complete dated-state support targets live in D110 §3.3.1, with
[supporting analysis](analysis/temporal_state_evidence_targets.md).
The [complete schema amendment](designs/temporal_write_and_lifecycle_schema.sql)
is incorporated by the [PostgreSQL design](designs/postgres_schema_design.md).
[Temporal sequencing](plans/temporal_clocks.md) tracks implementation dependencies;
linked analyses preserve the alternatives and validation limits.
