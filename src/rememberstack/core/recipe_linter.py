"""The recipe registration linter (D50/D41): grain semantics, mechanically.

A recipe declares two enums — `output_grain` and `answer_intent` — and a
`chain` of primitive ops. The database CHECK enforces the headline bar
(`current_facts` ⇒ `fact` grain); this linter enforces the *chain-level*
rules the DB cannot see, so a registration that would let a recipe lie about
what it returns is rejected before it ever reaches a surface:

- **`current_facts` may ride only validity-filtered fact operations.** This is
  the D41 bar in full: a recipe that answers "what holds now" must compose
  lookups or a registered compound operation that filters both temporal clocks
  — never a claims search, which is evidence ("what a source *asserted*"), not
  fact.
- **The chain's terminal grain must match `output_grain`.** A recipe that
  ends on a claims search cannot advertise `fact`; one that ends on a K-page
  read cannot advertise `evidence`. The grain a caller reads is the grain the
  last step actually produces.
- **Each intent implies a shape.** `assertion_history` is evidence-grain,
  `change_feed` ends on the delta, `audit` ends on a decision trail. These
  keep the MCP tool a caller sees honest about what it will get back.

`fuse` produces an **evidence-grade ordering**, not the confirmed records
themselves: its output is a ranking of candidate ids still to be hydrated
(matching the `fuse` primitive's own grain), so a recipe that ends on a fuse
is evidence, never fact. The op vocabulary the linter accepts is exactly the
set the executor can run — a chain never lints only to fail at execution.
"""

from dataclasses import dataclass

from rememberstack.model import Grain
from rememberstack.model import Recipe
from rememberstack.model import RecipeAnswerIntent


class RecipeLintError(Exception):
    """A recipe registration the linter rejected, with the reason stated."""


@dataclass(frozen=True)
class _OpSpec:
    """What a chain op produces, for the mechanical grain checks."""

    grain: Grain  # the D49 grain this op's envelope carries
    validity_filtered: bool  # filters BOTH clocks to "now" (the current_facts bar)
    min_inputs: int = 0  # prior steps this op must consume
    max_inputs: int | None = None  # cap where the executor consumes a fixed count


# The op vocabulary a recipe chain may compose — exactly the set the executor
# implements, so a lint-clean chain always runs. `validity_filtered` is the
# strict current-instant test (both clocks): only point-in-time lookups and
# explicitly registered compound operations qualify. `aggregate` is NOT one
# of them — its forms span history (timeline)
# or count live-but-expired rows — so it can never sit in a `current_facts`
# recipe. `fuse` returns an evidence-grade ranking (candidates to hydrate);
# `hydrate_claims` turns that ranking into claim text while keeping scores.
_OPS: dict[str, _OpSpec] = {
    "resolve": _OpSpec(Grain.FACT, validity_filtered=False),
    "lookup_relations": _OpSpec(Grain.FACT, validity_filtered=True),
    "lookup_observations": _OpSpec(Grain.FACT, validity_filtered=True),
    "aggregate": _OpSpec(Grain.FACT, validity_filtered=False),
    "search_claims": _OpSpec(Grain.EVIDENCE, validity_filtered=False),
    "search_chunks": _OpSpec(Grain.EVIDENCE, validity_filtered=False),
    "nominate_claims": _OpSpec(Grain.EVIDENCE, validity_filtered=False),
    "nominate_chunks": _OpSpec(Grain.EVIDENCE, validity_filtered=False),
    "hydrate_claims": _OpSpec(
        Grain.EVIDENCE, validity_filtered=False, min_inputs=1, max_inputs=1
    ),
    "hydrate_chunks": _OpSpec(
        Grain.EVIDENCE, validity_filtered=False, min_inputs=1, max_inputs=1
    ),
    "combine_evidence": _OpSpec(Grain.EVIDENCE, validity_filtered=False, min_inputs=1),
    "hydrate_relation": _OpSpec(Grain.COMPOSITE, validity_filtered=False),
    "transcript": _OpSpec(Grain.COMPOSITE, validity_filtered=False),
    "delta": _OpSpec(Grain.COMPOSITE, validity_filtered=False),
    "pages_about": _OpSpec(Grain.COMPILED, validity_filtered=False),
    "documents_about": _OpSpec(Grain.EVIDENCE, validity_filtered=False),
    "claims_about": _OpSpec(Grain.EVIDENCE, validity_filtered=False),
    "claims_as_of": _OpSpec(Grain.EVIDENCE, validity_filtered=False),
    "chunk_neighbors": _OpSpec(Grain.EVIDENCE, validity_filtered=False),
    "current_context": _OpSpec(Grain.FACT, validity_filtered=True),
    "multi_hop_context": _OpSpec(Grain.EVIDENCE, validity_filtered=False),
    "graph_neighborhood": _OpSpec(Grain.FACT, validity_filtered=True),
    "graph_path": _OpSpec(Grain.FACT, validity_filtered=True),
    "fuse": _OpSpec(Grain.EVIDENCE, validity_filtered=False, min_inputs=1),
}

KNOWN_OPS = frozenset(_OPS)
"""The primitive ops a recipe chain may name — exactly the executor's set."""

_DROPPABLE_OPS = frozenset(
    {"search_claims", "search_chunks", "hydrate_claims", "hydrate_chunks"}
)
"""Ops whose D48 drop count must reach the terminal answer."""


def lint_recipe(recipe: Recipe) -> None:
    """Validate a recipe against the D50/D41 grain rules, or raise.

    Runs before every registration: a chain that would let a recipe
    misreport its grain, or a `current_facts` recipe that reaches for
    evidence, never becomes a row. Raises `RecipeLintError` naming the first
    violation; returns None when the recipe is well-formed.
    """
    _check_ops_and_inputs(recipe)
    terminal_grain = _OPS[recipe.chain[-1].op].grain
    if terminal_grain != recipe.output_grain:
        raise RecipeLintError(
            f"recipe {recipe.name!r} declares output_grain"
            f" {recipe.output_grain.value!r} but its chain ends on a"
            f" {terminal_grain.value!r}-grain op"
        )
    _check_intent(recipe)


def _check_ops_and_inputs(recipe: Recipe) -> None:
    """Every op is known, and every input references an earlier step."""
    for index, step in enumerate(recipe.chain):
        spec = _OPS.get(step.op)
        if spec is None:
            raise RecipeLintError(
                f"recipe {recipe.name!r} step {index} names unknown op"
                f" {step.op!r}; known ops: {', '.join(sorted(KNOWN_OPS))}"
            )
        if spec.max_inputs is not None and len(step.inputs) > spec.max_inputs:
            raise RecipeLintError(
                f"step {index}: op {step.op!r} consumes at most"
                f" {spec.max_inputs} input(s); a lint-clean chain must run"
                " exactly as written"
            )
        if len(step.inputs) < spec.min_inputs:
            raise RecipeLintError(
                f"recipe {recipe.name!r} step {index} ({step.op}) needs at"
                f" least {spec.min_inputs} input(s)"
            )
        if len(set(step.inputs)) != len(step.inputs):
            raise RecipeLintError(
                f"recipe {recipe.name!r} step {index} ({step.op}) repeats an"
                " input; one upstream envelope cannot be counted twice"
            )
        for referenced in step.inputs:
            if not 0 <= referenced < index:
                raise RecipeLintError(
                    f"recipe {recipe.name!r} step {index} references step"
                    f" {referenced}, which is not an earlier step"
                )
    for index, step in enumerate(recipe.chain[:-1]):
        if step.op not in _DROPPABLE_OPS:
            continue
        consumers = tuple(
            (later_index, later)
            for later_index, later in enumerate(
                recipe.chain[index + 1 :], start=index + 1
            )
            if index in later.inputs
        )
        if not consumers or any(
            consumer_index != len(recipe.chain) - 1 or consumer.op != "combine_evidence"
            for consumer_index, consumer in consumers
        ):
            raise RecipeLintError(
                f"recipe {recipe.name!r} step {index} ({step.op}) can drop"
                " nominations but its count does not flow directly into the"
                " terminal combine_evidence answer"
            )


def _check_intent(recipe: Recipe) -> None:
    """The answer_intent → chain-shape rules (the mechanical grain bar)."""
    intent = recipe.answer_intent
    if intent is RecipeAnswerIntent.CURRENT_FACTS:
        if recipe.output_grain is not Grain.FACT:
            raise RecipeLintError(
                f"recipe {recipe.name!r} answers current_facts but is not"
                " fact-grain (the D41 bar)"
            )
        for index, step in enumerate(recipe.chain):
            spec = _OPS[step.op]
            if not (spec.validity_filtered and spec.grain is Grain.FACT):
                raise RecipeLintError(
                    f"recipe {recipe.name!r} answers current_facts but step"
                    f" {index} ({step.op}) is not a validity-filtered fact"
                    " primitive — 'what holds now' never rides evidence or a"
                    " history-spanning aggregate (D41)"
                )
    elif intent is RecipeAnswerIntent.ASSERTION_HISTORY:
        if recipe.output_grain is not Grain.EVIDENCE:
            raise RecipeLintError(
                f"recipe {recipe.name!r} answers assertion_history but is not"
                " evidence-grain — 'what sources asserted' is evidence (D41)"
            )
    elif intent is RecipeAnswerIntent.CHANGE_FEED:
        if recipe.chain[-1].op != "delta":
            raise RecipeLintError(
                f"recipe {recipe.name!r} answers change_feed but does not end"
                " on the delta primitive"
            )
    elif intent is RecipeAnswerIntent.AUDIT:
        if recipe.chain[-1].op not in {"transcript", "hydrate_relation"}:
            raise RecipeLintError(
                f"recipe {recipe.name!r} answers audit but does not end on a"
                " decision trail (transcript or hydrate_relation)"
            )
    # ORIENTATION is deliberately shape-permissive: a shaped overview may be a
    # fact aggregate, a K page, or a bundle — its honesty is the grain match
    # already checked, not a fixed op.
