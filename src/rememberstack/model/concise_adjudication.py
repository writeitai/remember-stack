"""Closed model-facing adjudication answer that uses attempt-local handles (D121)."""

from typing import Annotated
from typing import Literal
from typing import Self

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator
from pydantic import StringConstraints

from rememberstack.model.fact_windows import FactWindow

_NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class PromptNewFact(BaseModel):
    """A new fact whose statement comes from a supplied original assertion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    handle: _NonEmpty = Field(
        description=(
            "A new-fact name local to this answer, such as win or N1. It must "
            "not reuse a supplied F, C, A, E, or S name."
        )
    )
    assertion: _NonEmpty = Field(
        description="The supplied assertion whose content this new fact records, such as A1."
    )


class PromptGroundedWindow(BaseModel):
    """One complete chosen-window replacement plus the claims that support it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    window: FactWindow
    supporting_claims: Annotated[
        tuple[_NonEmpty, ...],
        Field(
            min_length=1,
            description="Supplied claim names such as C1 that justify this window.",
        ),
    ]

    @model_validator(mode="after")
    def distinct_support(self) -> Self:
        """Reject repeated claim names rather than counting them twice."""
        if len(set(self.supporting_claims)) != len(self.supporting_claims):
            raise ValueError("supporting claim handles must be distinct")
        return self


class PromptWindowUpdate(BaseModel):
    """One explicit full-window change to a supplied or newly named fact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target: _NonEmpty = Field(
        description="F-name of a supplied fact, or a new-fact name declared in new_facts."
    )
    window: PromptGroundedWindow


class PromptSupportMove(BaseModel):
    """Move one original assertion's support, guarded by its expected fact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assertion: _NonEmpty = Field(
        description="The original assertion to reassign, such as A2. Not the incoming assertion."
    )
    expected_fact: _NonEmpty = Field(
        description="The F-name where that assertion currently sits, such as F1."
    )
    target: _NonEmpty = Field(
        description="The F-name or new-fact name that should receive this assertion."
    )


class PromptFactDecision(BaseModel):
    """Identity, date changes and support assignments using this attempt's names.

    The engine translates these names through the exact prepared attempt. A name
    from another attempt, even the same spelling F1, is invalid.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    target: _NonEmpty = Field(
        description=(
            "The fact that should receive the incoming assertion: an F-name from "
            "the prompt, or a new-fact name declared in new_facts."
        )
    )
    stance: Literal["supports", "contradicts"] = "supports"
    new_facts: tuple[PromptNewFact, ...] = ()
    window: PromptGroundedWindow | None = None
    updates: tuple[PromptWindowUpdate, ...] = ()
    support_moves: tuple[PromptSupportMove, ...] = ()
    contradict_with: tuple[_NonEmpty, ...] = ()
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    rationale: _NonEmpty

    @model_validator(mode="after")
    def consistent_references(self) -> Self:
        """Prevent ambiguous updates, double moves and unsupported new handles."""
        handles = {fact.handle for fact in self.new_facts}
        if len(handles) != len(self.new_facts):
            raise ValueError("new-fact handles must be unique")
        references = (
            self.target,
            *(update.target for update in self.updates),
            *(move.target for move in self.support_moves),
        )
        assigned_handles = {
            name
            for name in (self.target, *(move.target for move in self.support_moves))
            if name in handles
        }
        if assigned_handles != handles:
            raise ValueError(
                "every new fact must receive an explicit evidence assignment"
            )
        used_handles = {name for name in references if name in handles}
        if used_handles != handles:
            raise ValueError("new handles must be declared and used in the decision")
        updated = [update.target for update in self.updates]
        if self.window is not None:
            updated.append(self.target)
        if len(set(updated)) != len(updated):
            raise ValueError("a decision cannot replace one window twice")
        moved = [move.assertion for move in self.support_moves]
        if len(set(moved)) != len(moved):
            raise ValueError("an assertion cannot be moved twice")
        if self.target in self.contradict_with:
            raise ValueError("a fact cannot contradict itself")
        return self
