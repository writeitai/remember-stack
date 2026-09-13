"""Validate ordinary fact decisions against their supplied, locked evidence scope."""

from collections.abc import Mapping
from dataclasses import dataclass
import json
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid5

from rememberstack.model.fact_application import FactApplicationDecision
from rememberstack.model.fact_application import FactReference


@dataclass(frozen=True)
class ApplicationScope:
    """Store-verified participants in one canonical subject and fact plane.

    The store constructs this only after checking deployment, resolved identities,
    and complete input fingerprints under locks. Original assertions are supplied
    with their current destinations.
    """

    incoming_application_id: UUID
    fact_ids: frozenset[UUID]
    claim_ids: frozenset[UUID]
    assertion_targets: Mapping[UUID, UUID | None]


def new_fact_id(*, application_id: UUID, handle: str) -> UUID:
    """Resolve one local handle reproducibly, independently of decision retries."""
    name = json.dumps(["fact-v1", str(application_id), handle], separators=(",", ":"))
    return uuid5(NAMESPACE_URL, name)


def resolve_fact_reference(*, reference: FactReference, application_id: UUID) -> UUID:
    """Resolve a previously validated existing ID or local new-fact handle."""
    if reference.fact_id is not None:
        return reference.fact_id
    assert reference.new_handle is not None
    return new_fact_id(application_id=application_id, handle=reference.new_handle)


def validate_application_scope(
    *, decision: FactApplicationDecision, scope: ApplicationScope
) -> None:
    """Reject invented participants and stale support moves before any mutation.

    Identity and time compatibility are semantic decisions. This function imposes
    no equality, overlap, direction-of-date-change, or fact-kind gate.
    """
    if scope.incoming_application_id not in scope.assertion_targets:
        raise ValueError("incoming assertion is absent from the prepared scope")
    references = (
        decision.target,
        *(update.target for update in decision.updates),
        *(move.target for move in decision.support_moves),
    )
    if any(
        reference.fact_id is not None and reference.fact_id not in scope.fact_ids
        for reference in references
    ):
        raise ValueError("decision references a fact outside the supplied scope")
    if not set(decision.contradict_with) <= scope.fact_ids:
        raise ValueError("contradiction references a fact outside the supplied scope")
    for fact in decision.new_facts:
        if fact.assertion_application_id not in scope.assertion_targets:
            raise ValueError("new fact must derive from a supplied original assertion")
    replacements = [update.window for update in decision.updates]
    if decision.window is not None:
        replacements.append(decision.window)
    for replacement in replacements:
        if not set(replacement.supporting_claim_ids) <= scope.claim_ids:
            raise ValueError("window cites a claim outside the supplied scope")
    for move in decision.support_moves:
        if move.application_id == scope.incoming_application_id:
            raise ValueError("incoming support is assigned by target, not a move")
        if move.application_id not in scope.assertion_targets:
            raise ValueError("support move references an unseen original assertion")
        if scope.assertion_targets[move.application_id] != move.expected_fact_id:
            raise ValueError("support move no longer matches its expected source")
        if move.expected_fact_id not in scope.fact_ids:
            raise ValueError("support source is outside the supplied facts")
