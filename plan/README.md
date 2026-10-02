# Planning corpus

Requirements describe what the engine must do in `requirements/`. Accepted
architecture lives in `designs/`; research in `analysis/` is non-binding.
`plans/` owns build order. The canonical numbered authority log is
[decisions.md](../decisions.md). Design acceptance does not establish shipped behavior.

For package and release identity, start with [D108's unified distribution
design](designs/unified_remember_distribution_design.md). The
[release-identity analysis](analysis/remember_release_identity_and_pypi_retirement.md)
explains why GitHub preserves the `remember-stack` history while public Python
releases expose only `remember`, and why the retired PyPI name is archived
rather than deleted.

For credentials and client surfaces — the one signed key, the shared
`remember.mcp_tools` catalogue, the `project` routing argument, `remember mcp`
engine and bridge modes, `remember setup`, and the perimeter's signed-key
contract — start with [D136's design](designs/one_key_client_surfaces_design.md)
and its [analysis](analysis/one_key_client_surfaces_analysis.md). It amends the
credential and login parts of D92/D108.

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

For coherent claims and distributed source evidence, start with
[D119 multi-span extraction](designs/multi_span_claim_extraction_design.md).
The [analysis](analysis/multi_span_claim_extraction.md) explains the LoCoMo
findings and reuse constraints. Implementation is not implied by design acceptance.

For which file formats the engine accepts and what it produces from each — the format
registry, full/profile/card postures and extraction eligibility — see
[D133 format conversion](designs/format_conversion_design.md), with D139's
**OCR on every accepted PDF page** in
[the workspace family design](designs/workspace_formats_design.md#7-office-pdf-email-and-notebook-details);
for finding files and
filtering by author or date, [D134 document metadata and search](designs/document_metadata_and_search_design.md).
The families actually shipped for workspaces, and how they serve coding agents, are
[D138 workspace formats](designs/workspace_formats_design.md).
The [analysis](analysis/format_coverage_and_conversion_architecture.md) records the
current coverage and rejected alternatives; the
[delivery order](plans/format_coverage_delivery.md) sequences the work.

For conversational anaphora and question-affirmation resolution across dialogue turns,
see [D131 cross-turn conversational anaphora extraction](designs/cross_turn_conversational_anaphora_extraction_design.md)
and the [analysis](analysis/cross_turn_conversational_anaphora_analysis.md).

For byte-authoritative ingest MIME and explicit classes on every object write,
see [D132 in the E0 file design](designs/e0_files_design.md#2-storage-layout--gcs-holds-bodies-postgres-holds-the-index)
and the [analysis](analysis/content_detection_and_object_classes_20260923.md).

For deleting a document through the API, SDK, CLI or MCP, see D135 in the
[evidence lifecycle design §8](designs/evidence_lifecycle_design.md#8-deletion--deletion-removes-the-documents-contribution-uniformly)
and the [analysis](analysis/public_document_deletion.md). Erasure remains
[D74 hard-forget](designs/hard_forget_design.md).

For documents whose versions are in force for declared periods (policies, price
lists, standards editions, contract amendments, statutes), stable section keys,
reading text as of a date, and section-level cross-references that are pinned or
floating, start with [D140's design](designs/effective_time_and_section_references_design.md)
and its [analysis](analysis/version_effective_time_and_section_references.md); the
[delivery plan](plans/effective_time_and_section_references.md) says which parts ship
together. It also makes D56's version reuse hold for dated versions (text origin time).

For the full TypeScript counterpart, see [D141 TypeScript client](designs/typescript_client_design.md),
[analysis](analysis/typescript_client_parity.md), the [normative parity inventory](designs/typescript_client_parity.json)
and [delivery gates](plans/typescript_client.md). CLI/MCP are separate package boundaries.
