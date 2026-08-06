"""Pure renderer for the versioned agent-consumption skill (D51 + open query)."""

from __future__ import annotations

import json
from typing import Final

from rememberstack.core.open_query_prose import HONESTY_WARNINGS
from rememberstack.core.open_query_prose import RETRIEVAL_CHOICES
from rememberstack.core.open_query_prose import TWO_LAYER_HEADLINE_FULL
from rememberstack.model import ConsumptionRecipe
from rememberstack.model import ConsumptionScope
from rememberstack.model import ConsumptionSkillContext
from rememberstack.model import PublishedMounts
from rememberstack.model import RenderedConsumptionSkill

#: Bumped for the integrated open-query rewrite (Batch F).
CONSUMPTION_SKILL_VERSION: Final = "2.1.0"


def render_consumption_skill(
    *, context: ConsumptionSkillContext
) -> RenderedConsumptionSkill:
    """Render one complete ``SKILL.md`` from typed deployment state."""
    deployment = context.deployment
    sections = (
        _header(),
        _two_layer_headline(),
        _three_choices(),
        _deployment(context=context),
        _bound_examples(),
        _honesty_warnings(),
        _assured_operations(recipes=context.recipes),
        _grains(),
        _testimony(recipes=context.recipes),
        _time_and_media(),
        _envelope(),
        _mounts(mounts=context.mounts),
        _working_rules(),
    )
    return RenderedConsumptionSkill(
        deployment_id=deployment.deployment_id,
        version=CONSUMPTION_SKILL_VERSION,
        content="\n\n".join(sections).rstrip() + "\n",
    )


def _header() -> str:
    """The stable skill identity and revision."""
    return (
        "---\n"
        "name: rememberstack\n"
        "description: Use one configured RememberStack deployment without "
        "mixing facts, testimony, and compiled knowledge.\n"
        "---\n\n"
        "# Use RememberStack\n\n"
        f"Skill revision: `{CONSUMPTION_SKILL_VERSION}`. Follow these instructions "
        "when a task depends on the configured memory."
    )


def _two_layer_headline() -> str:
    """Open with the exact bound two-layer retrieval headline (design §6)."""
    return f"## Two deliberately separate truth layers\n\n{TWO_LAYER_HEADLINE_FULL}"


def _three_choices() -> str:
    """Present the three neutral retrieval choices without language steering."""
    bullets = "\n".join(f"- {choice}" for choice in RETRIEVAL_CHOICES)
    return (
        "## Choose how to query\n\n"
        "After the two-layer distinction, pick one of these three surfaces."
        " None is preferred in prose; pick by what the task needs:\n\n"
        f"{bullets}\n\n"
        "Open infrastructure entry points: `query_sql`, `explain_sql`,"
        " `query_cypher`, `explain_cypher`, `describe_query_space`,"
        " `search_query_space`, `list_saved_queries`, `describe_saved_query`,"
        " `run_saved_query`. Discover schema with `describe_query_space` or"
        " `remember query space` before writing filters."
    )


def _deployment(*, context: ConsumptionSkillContext) -> str:
    """Render deployment identity, language, scopes, and current K state."""
    deployment = context.deployment
    description = (
        f"\n- Purpose: {_literal(value=deployment.description)}"
        if deployment.description
        else ""
    )
    scopes = _scope_lines(scopes=deployment.scopes)
    knowledge_state = (
        "known empty: no K pages are registered"
        if deployment.knowledge_page_count == 0
        else f"{deployment.knowledge_page_count} K page(s) are registered"
    )
    empty_k = (
        "This deployment currently has no K pages. Use open SQL/Cypher or"
        " assured operations rather than inventing a missing summary. When"
        " mounts exist, fall back to the P3 corpus tree for orientation."
        if deployment.knowledge_page_count == 0
        else "If K returns `known_empty`, fall back to P3 or open query; never"
        " invent the missing synthesis."
    )
    return (
        "## This deployment\n\n"
        f"- Name: {_literal(value=deployment.name)}\n"
        f"- Slug: `{deployment.slug}`\n"
        f"- Deployment id: `{deployment.deployment_id}`\n"
        f"- Default language: `{deployment.default_language}`"
        f"{description}\n"
        f"- Plane K state: {knowledge_state}.\n"
        f"- Special-purpose scopes:\n{scopes}\n\n"
        f"{empty_k}"
    )


def _bound_examples() -> str:
    """The same worked-example set discovery publishes (design §6)."""
    from rememberstack.core.open_query_prose import bound_worked_examples

    sections: list[str] = ["## Bound worked examples"]
    for example in bound_worked_examples():
        title = str(example["title"])
        purpose = str(example["purpose"])
        language = str(example["language"])
        body = str(example["body"])
        sections.append(f"### {title}\n\n{purpose}\n\n```{language}\n{body}\n```")
    return "\n\n".join(sections)


def _honesty_warnings() -> str:
    """The design-required honesty warnings for open results."""
    bullets = "\n".join(f"- {warning}" for warning in HONESTY_WARNINGS)
    return f"## Honesty rules for open results\n\n{bullets}"


def _assured_operations(*, recipes: tuple[ConsumptionRecipe, ...]) -> str:
    """Name the complete three-operation assured surface."""
    core = {"resolve_entity", "question_context", "current_context"}
    enabled_core = [recipe for recipe in recipes if recipe.name in core]
    if enabled_core:
        core_rows = "\n".join(
            f"- `{recipe.name}` — `{recipe.output_grain}` / "
            f"`{recipe.answer_intent}`: {_one_line(value=recipe.description)}"
            for recipe in enabled_core
        )
    else:
        core_rows = (
            "- The three assured operations are"
            " `resolve_entity`, `question_context`, and `current_context`"
            " (D49 Envelope contracts)."
        )
    return (
        "## Assured operations\n\n"
        "Exactly three platform intent operations ship with D49 Envelope"
        " contracts:\n\n"
        f"{core_rows}\n\n"
        "All other shipped retrieval patterns are open SQL/Cypher or non-tool "
        "`examples.*` saved queries; there is no compatibility recipe catalog."
    )


def _grains() -> str:
    """Teach the claim-to-fact-to-compiled terminology ladder."""
    return (
        "## Keep the grains separate\n\n"
        "- A **claim** is immutable testimony: what one source asserted. It is "
        "evidence grain and may be stale, superseded, or contradicted.\n"
        "- A **relation** links two entities; an **observation** records a value or "
        "statement about one entity. Together they are the **fact layer**: the "
        "system's adjudicated, validity-filtered holdings. Questions of the form "
        '"is this true now?" go here by default.\n'
        "- A **compiled K page** is pre-paid synthesis. It is compiled grain and "
        "must be read with its compile time, stale flag, and open-flag count.\n"
        "- A **core belief** is a stricter configured K tier, not a new source of "
        "truth.\n\n"
        "Never blend evidence and facts into one unlabeled answer. If a task asks "
        "both what someone said and what the system believes, return separate "
        "evidence-grain and fact-grain parts."
    )


def _testimony(*, recipes: tuple[ConsumptionRecipe, ...]) -> str:
    """Teach current testimony, historical opt-in, and withdrawn support."""
    del recipes
    return (
        "## Testimony currency and shaky support\n\n"
        "Claim search and claim views default to **current testimony**. Claims "
        "left behind by a living document's newer version or by a newer "
        "extraction generation are history, not current search results. "
        "`claims_as_of` means **what sources asserted as of a past system time**; "
        "it never means what is true now. Use `examples.claims_as_of` through "
        "`run_saved_query`, or query `claims_visible_history` with an inclusive "
        "overlap predicate.\n\n"
        "A fact with `support: withdrawn` has lost all current-testimony support "
        "because a toolchain re-read did not re-derive it. It still stands while "
        "review is open, but it is shaky: report the caveat, inspect its transcript "
        "and evidence, and do not make it load-bearing without verification."
    )


def _time_and_media() -> str:
    """Keep the three media/time coordinates and derivation labels distinct."""
    return (
        "## Time and media\n\n"
        'Do not collapse these into "the timestamp":\n\n'
        "- a source locator such as `start_ms` says **where in a file** evidence "
        "occurs;\n"
        "- `valid_from` / `valid_until` say **when a fact held in the world**;\n"
        "- `ingested_at` / `believed_at` say **when the system knew it**.\n\n"
        "Cypher results disclose `built_at` and `age_seconds` for the P2 snapshot "
        "cut. Freshness targets and alerts never relabel, reject, or silently "
        "refresh a snapshot answer.\n\n"
        "For media-derived evidence, read `evidence_mode`: `source_expression` is "
        "rendered speech/text, `model_observation` is what a model reports seeing, "
        "and `model_interpretation` is the model's interpretation. When tone or a "
        "visual detail matters, follow the source locator to the raw interval or "
        "region. The transcript is a map, not the territory."
    )


def _envelope() -> str:
    """Teach response honesty fields for both Envelope and QueryResult."""
    return (
        "## Read the whole response\n\n"
        "For assured operations, check Envelope `grain`, applied "
        "`valid_at`/`believed_at`, identity regime, per-store freshness, "
        "truncation/continuation, and `dropped_by_hydration`. A result inside a "
        "live contradiction group must include or point to its co-members; "
        "report the competing sides instead of silently picking one.\n\n"
        "For open SQL/Cypher, every answer is `QueryResult/v1`. Inspect "
        "`grade` (`exploratory_tabular` or `snapshot_graph`), "
        "`truncated`/`truncation_reason`, warnings, `p2_snapshot` provenance, "
        "confirmation/nomination drop counts, and `saved_query` stamps. "
        "Empty SQL is untyped; never promote it to a D49 negative.\n\n"
        "Negative Envelope results require different moves:\n\n"
        "- `unknown_entity`: widen resolution or search;\n"
        "- `known_empty`: the entity exists but no matching result is known within "
        "the stated freshness;\n"
        "- `boundary`: re-plan using the named workaround.\n\n"
        "Hard-forgotten material is intentionally indistinguishable from content "
        "that never existed."
    )


def _mounts(*, mounts: PublishedMounts | None) -> str:
    """Render exact mount paths or the unmounted parity rule."""
    if mounts is None:
        availability = (
            "No mounts are available in this harness. Use API, CLI, or MCP for "
            "orientation, readable artifacts, and query operations."
        )
    else:
        availability = (
            "The four read-only mounts are available:\n\n"
            f"- P3 corpus tree: `{mounts.p3}`\n"
            f"- E0 artifacts: `{mounts.artifacts}`\n"
            f"- raw originals (off the navigation path; audited): `{mounts.raw}`\n"
            f"- plane K checkout: `{mounts.knowledge}`"
        )
    return (
        "## Filesystem first when mounts exist\n\n"
        f"{availability}\n\n"
        "When mounts exist, prefer them for navigation, reading, and grep. Reserve "
        "API/CLI/MCP for operations with no filesystem equivalent: open SQL and "
        "Cypher, semantic/lexical SRFs, graph traversal, temporal as-of queries, "
        "hydration, transcripts, and deltas. Start in P3 or K, not raw. Follow an "
        "explicit raw pointer only when the original is needed, and use the "
        "deployment's audited raw-access mechanism."
    )


def _working_rules() -> str:
    """End with a compact operational checklist for the consuming agent."""
    return (
        "## Before acting on a memory answer\n\n"
        "1. Did I use facts, not claims, for a current-truth question?\n"
        "2. Did I keep fact, evidence, and compiled grains labeled separately?\n"
        "3. For Cypher, did I disclose and respect `built_at` / age rather than "
        "treat the row as live?\n"
        "4. Did I inspect caps, drops, truncation, contradictions, and withdrawn "
        "support?\n"
        "5. Did I re-ground snapshot ids in live SQL when the task needs current "
        "truth?\n"
        "6. Did I hydrate to evidence or raw source when the stakes required it?"
    )


def _scope_lines(*, scopes: tuple[ConsumptionScope, ...]) -> str:
    """Render special-purpose scope rows without inventing a default K page."""
    if not scopes:
        return "  - none registered"
    return "\n".join(
        f"  - `{scope.slug}` ({_one_line(value=scope.name)})"
        + (f" at `{scope.git_path}`" if scope.git_path else "")
        + (f": {_one_line(value=scope.description)}" if scope.description else "")
        for scope in scopes
    )


def _literal(*, value: str) -> str:
    """Render deployment-controlled prose as one explicit JSON string literal."""
    literal = json.dumps(value, ensure_ascii=False).replace("`", "'")
    return f"`{literal}`"


def _one_line(*, value: str) -> str:
    """Collapse deployment-controlled display text to one Markdown-safe line."""
    return " ".join(value.split()).replace("`", "'")
