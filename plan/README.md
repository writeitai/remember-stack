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

For the processing improvements, see [D120 clear prompts](designs/processing_prompt_clarity_design.md),
[D121 concise evidence](designs/concise_adjudication_inputs_design.md),
[D122 document references](designs/document_reference_context_design.md) and
[D123 contextual nomination](designs/contextual_fact_nomination_design.md).
The [analysis](analysis/lean_processing_contracts.md) explains the costs and
alternatives; the [delivery plan](plans/lean_processing_delivery.md) coordinates
these with D119 multi-span extraction in PR #400. Grouped adjudication remains
an [unchosen proposal](../design/proposals/grouped_fact_adjudication.md).

D107's [canonical arithmetic and extraction](designs/temporal_clocks_design.md)
remain applicable. Its other fact-time rules and the D110–D113 framework are
superseded by D118's explicit authority map. Their documents and SQL remain
marked as historical artifacts; the [PostgreSQL design](designs/postgres_schema_design.md)
no longer incorporates the withdrawn DDL.
