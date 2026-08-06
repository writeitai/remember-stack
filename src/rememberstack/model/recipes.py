"""Assured-operation descriptor values: frozen query plans as data, not code.

A **recipe** value is a named, versioned composition of zero-LLM primitives.
The shipping registry exposes exactly the three D83 assured operations; other
query patterns belong in the saved-query registry under ``examples.*``. The
older descriptor set remains private only for frozen benchmark and primitive
regression fixtures. The load-bearing property is unchanged: a recipe adds no
capability, so it is exactly its ``chain`` and can be checked by replaying that
chain and diffing the result.

Two declared enums make the D41 grain bar ("claims never answer *is it true
now*") a mechanical constraint rather than a prose judgment: `output_grain`
(the D49 envelope grain the recipe returns) and `answer_intent` (what kind of
question it answers). The database CHECK enforces the headline rule
(`current_facts` ⇒ `fact` grain); the registration linter (`core`) validates
the chain itself against the same enums.
"""

from enum import StrEnum

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from rememberstack.model.envelope import Grain


class RecipeAnswerIntent(StrEnum):
    """What kind of question a recipe answers (the D50 `answer_intent`).

    The intent is what the grain linter reasons over: `current_facts` may
    only ride validity-filtered fact primitives, `assertion_history` is the
    evidence-grain "what did sources assert" read, `audit` is the decision
    trail, `change_feed` is the delta, and `orientation` is a pre-paid
    synthesis (K pages, briefs).
    """

    CURRENT_FACTS = "current_facts"
    ASSERTION_HISTORY = "assertion_history"
    ORIENTATION = "orientation"
    AUDIT = "audit"
    CHANGE_FEED = "change_feed"


class RecipeStep(BaseModel):
    """One primitive invocation in a recipe chain (retrieval §3 op + settings).

    `op` names a §3 primitive; `settings` are the FIXED arguments the recipe
    freezes (channel sets, RRF constants, rerank weights); `bind` maps a
    primitive keyword to the name of a recipe parameter the caller supplies;
    and `inputs` names the prior steps (by index) whose outputs this op
    consumes — how `fuse` references the two searches above it. A recipe with
    one step is the common case (a recipe over a single primitive with frozen
    settings); the chain generalizes to fusion pipelines.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    op: str
    settings: dict[str, object] = Field(default_factory=dict)
    bind: dict[str, str] = Field(default_factory=dict)
    inputs: tuple[int, ...] = ()


class Recipe(BaseModel):
    """One recipe registry row (D50): the frozen plan and its declared grain.

    `parameters` is a JSON-Schema-shaped description of the arguments a
    caller passes (rendered into the MCP tool signature). `chain` is the
    ordered primitive composition — the recipe's entire behavior. The two
    enums are the contract the linter and the DB CHECK enforce.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    parameters: dict[str, object] = Field(default_factory=dict)
    chain: tuple[RecipeStep, ...] = Field(min_length=1)
    output_grain: Grain
    answer_intent: RecipeAnswerIntent
    version: int = Field(default=1, ge=1)
