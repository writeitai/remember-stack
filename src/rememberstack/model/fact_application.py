"""Closed ordinary adjudication output for D118 fact identity and window edits."""

from typing import Annotated
from typing import Literal
from typing import Self
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator
from pydantic import StringConstraints

from rememberstack.model.fact_windows import GroundedFactWindow

_NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
AssertionKind = Literal["relation", "observation"]


class FactReference(BaseModel):
    """An existing supplied fact or one local new-fact handle, never both."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: UUID | None = None
    new_handle: _NonEmpty | None = None

    @model_validator(mode="after")
    def one_reference(self) -> Self:
        """Reject an ambiguous or missing target before resolving any store ID."""
        if (self.fact_id is None) == (self.new_handle is None):
            raise ValueError("exactly one of fact_id and new_handle is required")
        return self

    @property
    def key(self) -> str:
        """A collision-free comparison key across existing and newly named facts."""
        return (
            f"fact:{self.fact_id}"
            if self.fact_id is not None
            else f"new:{self.new_handle}"
        )


class NewFact(BaseModel):
    """A new identity whose statement comes from an actual supplied assertion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    handle: _NonEmpty
    assertion_application_id: UUID


class FactWindowUpdate(BaseModel):
    """One explicit full-window change to a supplied or newly named fact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target: FactReference
    window: GroundedFactWindow


class AssertionSupportMove(BaseModel):
    """Move one original assertion's support, guarded by its expected location."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    application_id: UUID
    expected_fact_id: UUID
    target: FactReference


class FactApplicationDecision(BaseModel):
    """Identity, date changes and support assignments in one ordinary decision.

    Validation here checks a self-consistent answer. The store must additionally
    validate every reference and cited claim against its locked prepared inputs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    target: FactReference
    stance: Literal["supports", "contradicts"] = "supports"
    new_facts: tuple[NewFact, ...] = ()
    window: GroundedFactWindow | None = None
    updates: tuple[FactWindowUpdate, ...] = ()
    support_moves: tuple[AssertionSupportMove, ...] = ()
    contradict_with: tuple[UUID, ...] = ()
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
            target.new_handle
            for target in (self.target, *(move.target for move in self.support_moves))
            if target.new_handle is not None
        }
        if assigned_handles != handles:
            raise ValueError(
                "every new fact must receive an explicit evidence assignment"
            )
        used_handles = {
            target.new_handle for target in references if target.new_handle is not None
        }
        if used_handles != handles:
            raise ValueError("new handles must be declared and used in the decision")
        updated = [update.target.key for update in self.updates]
        if self.window is not None:
            updated.append(self.target.key)
        if len(set(updated)) != len(updated):
            raise ValueError("a decision cannot replace one window twice")
        moved = [move.application_id for move in self.support_moves]
        if len(set(moved)) != len(moved):
            raise ValueError("an assertion cannot be moved twice")
        if self.target.fact_id in self.contradict_with:
            raise ValueError("a fact cannot contradict itself")
        return self
