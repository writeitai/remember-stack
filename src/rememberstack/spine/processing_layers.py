"""D143 processing layers: configuration, boundaries, gaps and lineage order.

A configured deployment (one with a ``deployment_processing_layers`` row)
decides at two **boundaries** whether a version enters a layer:

- the claims boundary, when ``embed_chunk`` completes;
- the facts boundary, when the version's claim occurrences are complete (the
  extract barrier, or the empty path of a version without chunks).

Every decision runs inside the completing transaction under the shared
**layer fence** and the D135 **lineage lock**, records a **gap** for the
version, and then runs one procedure — :func:`advance_on` — that enters the
lineage's next version once every earlier version reached the layer's reuse
**frontier**. The same procedure runs when a version reaches a frontier, when a
version is deleted and when ``setup`` seeds a layer that was switched on, so
the boundary and the backfill can never disagree.

An unconfigured deployment never reaches this module's decision code: each
caller checks :func:`layer_configuration_on` first and keeps today's path.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.model import EnqueueOutcome
from rememberstack.model import EnqueueWork
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model.client import ProcessingLayer
from rememberstack.model.client import ProcessingLayerState
from rememberstack.model.client import ProcessingLayerStates
from rememberstack.model.processing_layers import LayerAdmission
from rememberstack.model.processing_layers import LayerConfiguration
from rememberstack.model.processing_layers import LayerConfigurationError
from rememberstack.model.processing_layers import LayerSelectionError
from rememberstack.model.processing_layers import LayerUnavailableError
from rememberstack.model.processing_layers import UNCONFIGURED


def layer_configuration_on(
    *, connection: Connection, deployment_id: UUID
) -> LayerConfiguration:
    """Read the deployment's layer configuration (no row: unconfigured, all on)."""
    row = (
        connection.execute(_SELECT_CONFIGURATION, {"deployment_id": deployment_id})
        .mappings()
        .first()
    )
    if row is None:
        return UNCONFIGURED
    return LayerConfiguration(
        configured=True,
        claims_enabled=bool(row["claims_enabled"]),
        facts_enabled=bool(row["facts_enabled"]),
    )


def take_layer_fence_on(
    *, connection: Connection, deployment_id: UUID, exclusive: bool = False
) -> None:
    """Serialize layer decisions against a configuration change (D143 §3.3).

    The fence is an advisory lock, so it exists before any configuration row
    does: a boundary deciding under the old configuration cannot commit under
    the new one.
    """
    connection.execute(
        _EXCLUSIVE_FENCE if exclusive else _SHARED_FENCE,
        {"key": f"processing-layers:{deployment_id}"},
    )


def claims_boundary_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    version_id: UUID,
    lane: ProcessingLane | None,
) -> list[EnqueueOutcome]:
    """Decide the claims layer for a version whose ``embed_chunk`` just completed.

    Only called for configured deployments. Returns the work it enqueued.
    """
    configuration = layer_configuration_on(
        connection=connection, deployment_id=deployment_id
    )
    lineage = _lock_lineage_of_version(connection=connection, version_id=version_id)
    if lineage.deleted(version_id=version_id):
        return _enter_claims(
            connection=connection,
            deployment_id=deployment_id,
            version_id=version_id,
            lane=lane,
        )
    if not configuration.claims_enabled:
        for layer in (ProcessingLayer.CLAIMS, ProcessingLayer.FACTS):
            _record_gap(
                connection=connection,
                deployment_id=deployment_id,
                version_id=version_id,
                doc_id=lineage.doc_id,
                layer=layer,
                lane=ProcessingLane.BACKFILL,
            )
        return []
    _record_gap(
        connection=connection,
        deployment_id=deployment_id,
        version_id=version_id,
        doc_id=lineage.doc_id,
        layer=ProcessingLayer.CLAIMS,
        lane=lane or ProcessingLane.STEADY,
    )
    return advance_on(
        connection=connection,
        deployment_id=deployment_id,
        doc_id=lineage.doc_id,
        layer=ProcessingLayer.CLAIMS,
    )


def facts_boundary_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    version_id: UUID,
    lane: ProcessingLane | None,
) -> list[EnqueueOutcome]:
    """Decide the facts layer for a version whose claim occurrences are complete.

    Only called for configured deployments. A version that does not enter facts
    keeps an open facts gap and gets its claims tail once no earlier version of
    its lineage has fact work in flight (D143 §4.3).
    """
    configuration = layer_configuration_on(
        connection=connection, deployment_id=deployment_id
    )
    lineage = _lock_lineage_of_version(connection=connection, version_id=version_id)
    if lineage.deleted(version_id=version_id):
        if configuration.facts_enabled:
            return _enter_facts(
                connection=connection,
                deployment_id=deployment_id,
                version_id=version_id,
                lane=lane,
            )
        return _open_claims_tail(
            connection=connection,
            deployment_id=deployment_id,
            version_id=version_id,
            doc_id=lineage.doc_id,
            lane=lane,
        )
    _record_gap(
        connection=connection,
        deployment_id=deployment_id,
        version_id=version_id,
        doc_id=lineage.doc_id,
        layer=ProcessingLayer.FACTS,
        lane=(lane or ProcessingLane.STEADY)
        if configuration.facts_enabled
        else ProcessingLane.BACKFILL,
    )
    return advance_on(
        connection=connection,
        deployment_id=deployment_id,
        doc_id=lineage.doc_id,
        layer=ProcessingLayer.FACTS,
    )


def frontier_reached_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    version_id: UUID,
    layer: ProcessingLayer,
    delete_gap: bool = True,
) -> list[EnqueueOutcome]:
    """A version reached a layer's frontier: delete its gap and advance the lineage.

    Only called for configured deployments: claims when the version's
    ``embed_claim`` succeeded, facts when its full reconcile succeeded. A
    claims-mode reconcile advances facts without deleting the facts gap: it
    releases a version waiting to enter facts (§5.2 step 6).
    """
    lineage = _lock_lineage_of_version(connection=connection, version_id=version_id)
    if delete_gap:
        connection.execute(
            _DELETE_GAP,
            {"deployment_id": deployment_id, "version_id": version_id, "layer": layer},
        )
    return advance_on(
        connection=connection,
        deployment_id=deployment_id,
        doc_id=lineage.doc_id,
        layer=layer,
    )


def lineage_changed_on(
    *, connection: Connection, deployment_id: UUID, doc_id: UUID
) -> list[EnqueueOutcome]:
    """A version of the lineage was deleted or the current version moved.

    Deletes the gaps of deleted versions and advances both layers, so a deleted
    head never blocks its successors and a repointed living lineage processes
    its new current version. A no-op for unconfigured deployments.
    """
    if not layer_configuration_on(
        connection=connection, deployment_id=deployment_id
    ).configured:
        return []
    take_layer_fence_on(connection=connection, deployment_id=deployment_id)
    _lock_lineage(connection=connection, doc_id=doc_id)
    connection.execute(
        _DELETE_GAPS_OF_DELETED_VERSIONS,
        {"deployment_id": deployment_id, "doc_id": doc_id},
    )
    outcomes = advance_on(
        connection=connection,
        deployment_id=deployment_id,
        doc_id=doc_id,
        layer=ProcessingLayer.CLAIMS,
    )
    outcomes.extend(
        advance_on(
            connection=connection,
            deployment_id=deployment_id,
            doc_id=doc_id,
            layer=ProcessingLayer.FACTS,
        )
    )
    return outcomes


def advance_on(
    *, connection: Connection, deployment_id: UUID, doc_id: UUID, layer: ProcessingLayer
) -> list[EnqueueOutcome]:
    """Enter the lineage's next version into ``layer`` when its order allows (§5.2).

    The caller holds the layer fence and the lineage lock. Steps:

    1. a disabled layer enters nothing (facts still opens the claims tails it
       now allows);
    2. a living lineage reopens its current version's discharged gap and
       discharges every other unentered gap;
    3. the target is the current version (living) or the oldest unentered gap
       (snapshot);
    4.–6. it is entered only when its sources layer is complete, every earlier
       version reached the frontier (or was discharged), and — for facts — its
       claim occurrences are complete.
    """
    configuration = layer_configuration_on(
        connection=connection, deployment_id=deployment_id
    )
    lineage = _read_lineage(
        connection=connection, deployment_id=deployment_id, doc_id=doc_id
    )
    if lineage.living and _reopen_current_gaps(
        connection=connection, lineage=lineage, layer=layer
    ):
        lineage = _read_lineage(
            connection=connection, deployment_id=deployment_id, doc_id=doc_id
        )
    if not configuration.enabled(layer=layer):
        if layer is ProcessingLayer.FACTS and configuration.claims_enabled:
            return _open_owed_claims_tails(
                connection=connection,
                lineage=lineage,
                facts_enabled=False,
                admitted=None,
            )
        return []
    if lineage.living:
        _discharge_superseded_gaps(connection=connection, lineage=lineage, layer=layer)
        lineage = _read_lineage(
            connection=connection, deployment_id=deployment_id, doc_id=doc_id
        )
    target = lineage.target(layer=layer)
    admitted = (
        target
        if target is not None and lineage.admits(target=target, layer=layer)
        else None
    )
    outcomes: list[EnqueueOutcome] = []
    if layer is ProcessingLayer.FACTS:
        outcomes.extend(
            _open_owed_claims_tails(
                connection=connection,
                lineage=lineage,
                facts_enabled=True,
                admitted=None if admitted is None else admitted.version_id,
            )
        )
    if admitted is None:
        return outcomes
    connection.execute(
        _STAMP_ENTERED,
        {
            "deployment_id": deployment_id,
            "version_id": admitted.version_id,
            "layer": layer,
        },
    )
    lane = admitted.gaps[layer].lane
    if layer is ProcessingLayer.CLAIMS:
        outcomes.extend(
            _enter_claims(
                connection=connection,
                deployment_id=deployment_id,
                version_id=admitted.version_id,
                lane=lane,
            )
        )
    else:
        outcomes.extend(
            _enter_facts(
                connection=connection,
                deployment_id=deployment_id,
                version_id=admitted.version_id,
                lane=lane,
            )
        )
    return outcomes


@dataclass(frozen=True, slots=True)
class LayerChange:
    """One layer whose enabled flag ``setup`` changed."""

    layer: ProcessingLayer
    enabled: bool


class ProcessingLayerCatalog:
    """Engine-bound reads and the ``setup``-owned writes of the configuration."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the catalog to the spine."""
        self._engine = engine

    def configuration(self, *, deployment_id: UUID) -> LayerConfiguration:
        """The deployment's current layer configuration."""
        with self._engine.connect() as connection:
            return layer_configuration_on(
                connection=connection, deployment_id=deployment_id
            )

    def states(self, *, deployment_id: UUID) -> ProcessingLayerStates:
        """Each switchable layer's state: enabled, backfilling or disabled."""
        with self._engine.connect() as connection:
            return layer_states_on(connection=connection, deployment_id=deployment_id)

    def admission(
        self,
        *,
        deployment_id: UUID,
        channels: frozenset[ProcessingLayer],
        requested: frozenset[ProcessingLayer] | None,
        mandatory: frozenset[ProcessingLayer] = frozenset(),
    ) -> LayerAdmission:
        """The D143 §7.2 admission of one read (see :func:`admit_layers_on`)."""
        with self._engine.connect() as connection:
            return admit_layers_on(
                connection=connection,
                deployment_id=deployment_id,
                channels=channels,
                requested=requested,
                mandatory=mandatory,
            )

    def reconcile_settings(
        self, *, deployment_id: UUID, claims_enabled: bool, facts_enabled: bool
    ) -> tuple[LayerChange, ...]:
        """Write the configured layers if they differ from the stored ones (§3.4).

        Returns one change per layer whose enabled flag moved. Once written,
        the row stays, so a configured deployment keeps the D143 paths.
        """
        if facts_enabled and not claims_enabled:
            raise LayerConfigurationError(
                "REMEMBERSTACK_SELFHOST_FACTS_LAYER=true requires "
                "REMEMBERSTACK_SELFHOST_CLAIMS_LAYER=true: facts are built from claims"
            )
        with self._engine.begin() as connection:
            take_layer_fence_on(
                connection=connection, deployment_id=deployment_id, exclusive=True
            )
            current = layer_configuration_on(
                connection=connection, deployment_id=deployment_id
            )
            if (current.claims_enabled, current.facts_enabled) == (
                claims_enabled,
                facts_enabled,
            ):
                return ()
            connection.execute(
                _UPSERT_CONFIGURATION,
                {
                    "deployment_id": deployment_id,
                    "claims_enabled": claims_enabled,
                    "facts_enabled": facts_enabled,
                },
            )
            return tuple(
                LayerChange(layer=layer, enabled=after)
                for layer, before, after in (
                    (ProcessingLayer.CLAIMS, current.claims_enabled, claims_enabled),
                    (ProcessingLayer.FACTS, current.facts_enabled, facts_enabled),
                )
                if before != after
            )

    def seed(self, *, deployment_id: UUID, batch_size: int) -> int:
        """Advance every lineage with an open unentered gap of an enabled layer.

        Runs one bounded transaction per lineage and layer, in batches, and is
        idempotent: advance enters nothing a second time. Returns how many
        lineage advances ran.
        """
        advanced = 0
        for layer in (ProcessingLayer.CLAIMS, ProcessingLayer.FACTS):
            after: UUID | None = None
            while True:
                with self._engine.connect() as connection:
                    configuration = layer_configuration_on(
                        connection=connection, deployment_id=deployment_id
                    )
                    if not configuration.configured:
                        return advanced
                    doc_ids = tuple(
                        connection.execute(
                            _SELECT_LINEAGES_WITH_UNENTERED_GAPS,
                            {
                                "deployment_id": deployment_id,
                                "layer": layer,
                                "after": after,
                                "limit": batch_size,
                            },
                        ).scalars()
                    )
                if not doc_ids:
                    break
                for doc_id in doc_ids:
                    with self._engine.begin() as connection:
                        take_layer_fence_on(
                            connection=connection, deployment_id=deployment_id
                        )
                        _lock_lineage(connection=connection, doc_id=doc_id)
                        advance_on(
                            connection=connection,
                            deployment_id=deployment_id,
                            doc_id=doc_id,
                            layer=layer,
                        )
                    advanced += 1
                after = doc_ids[-1]
        return advanced

    def gap_counts(self, *, deployment_id: UUID) -> Mapping[str, int]:
        """Open, entered and discharged gap counts per layer, for operators."""
        with self._engine.connect() as connection:
            rows = (
                connection.execute(_GAP_COUNTS, {"deployment_id": deployment_id})
                .mappings()
                .all()
            )
        return {f"{row['layer']}_{row['kind']}": int(row["count"]) for row in rows}


def layer_states_on(
    *, connection: Connection, deployment_id: UUID
) -> ProcessingLayerStates:
    """Derive each switchable layer's state from the configuration and open gaps."""
    configuration = layer_configuration_on(
        connection=connection, deployment_id=deployment_id
    )

    def state(layer: ProcessingLayer) -> ProcessingLayerState:
        if not configuration.enabled(layer=layer):
            return "disabled"
        if not configuration.configured:
            return "enabled"
        backfilling = connection.execute(
            _OPEN_GAP_EXISTS, {"deployment_id": deployment_id, "layer": layer}
        ).scalar_one()
        return "backfilling" if backfilling else "enabled"

    return ProcessingLayerStates(
        claims=state(ProcessingLayer.CLAIMS), facts=state(ProcessingLayer.FACTS)
    )


def admit_layers_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    channels: frozenset[ProcessingLayer],
    requested: frozenset[ProcessingLayer] | None,
    mandatory: frozenset[ProcessingLayer] = frozenset(),
) -> LayerAdmission:
    """Apply the D143 §7.2 rules to one read.

    A requested or mandatory layer the deployment does not serve is
    ``LayerUnavailableError`` (409); a mandatory layer the request excluded, or
    a selection leaving none of the read's channels, is ``LayerSelectionError``
    (422). A deployment without a configuration row serves everything, so an
    unconfigured read with no selection consults every channel unchanged.
    """
    states = layer_states_on(connection=connection, deployment_id=deployment_id)

    def served(layer: ProcessingLayer) -> bool:
        if layer is ProcessingLayer.CLAIMS:
            return states.claims != "disabled"
        if layer is ProcessingLayer.FACTS:
            return states.facts != "disabled"
        return True

    def backfilling(layer: ProcessingLayer) -> bool:
        if layer is ProcessingLayer.CLAIMS:
            return states.claims == "backfilling"
        if layer is ProcessingLayer.FACTS:
            return states.facts == "backfilling"
        return False

    for layer in sorted(requested or (), key=lambda item: item.value):
        if not served(layer):
            raise LayerUnavailableError(
                layer=layer,
                message=f"this deployment does not process the {layer.value} layer",
            )
    for layer in sorted(mandatory, key=lambda item: item.value):
        if not served(layer):
            raise LayerUnavailableError(
                layer=layer,
                message=(
                    f"this request needs the {layer.value} layer, which this"
                    " deployment does not process"
                ),
            )
        if requested is not None and layer not in requested:
            raise LayerSelectionError(
                layer=layer,
                message=(
                    f"this request needs the {layer.value} layer, which the"
                    " layers selection excludes"
                ),
            )
    selected = channels if requested is None else channels & requested
    consulted = frozenset(layer for layer in selected if served(layer))
    if not consulted:
        if requested is not None and not selected:
            raise LayerSelectionError(
                layer=None,
                message="the layers selection excludes every layer this read uses",
            )
        layer = sorted(channels, key=lambda item: item.value)[0]
        raise LayerUnavailableError(
            layer=layer,
            message=f"this deployment does not process the {layer.value} layer",
        )
    return LayerAdmission(
        consulted=consulted,
        unconsulted=channels - consulted,
        backfilling=frozenset(layer for layer in consulted if backfilling(layer)),
    )


def hold_window_reviews_on(*, connection: Connection, deployment_id: UUID) -> bool:
    """Whether ``review_fact_windows`` work is held because facts are disabled."""
    return not layer_configuration_on(
        connection=connection, deployment_id=deployment_id
    ).facts_enabled


def occurrences_complete_on(*, connection: Connection, version_id: UUID) -> bool:
    """Whether every claim occurrence of the version is attached (D143 §5.1).

    True only after the version's ``embed_chunk`` succeeded — so its chunk grid
    is final — and its extract barrier is ready, or that grid is empty. A
    version that has not chunked yet is never "complete with no chunks".
    """
    from rememberstack.spine.work_ledger import extract_barrier_ready_on

    embed = _embed_row(connection=connection, version_id=version_id)
    if embed is None or embed.status != "succeeded":
        return False
    return extract_barrier_ready_on(
        connection=connection,
        deployment_id=embed.deployment_id,
        representation_id=embed.representation_id,
        chunker_version=_grid_generation(
            connection=connection, representation_id=embed.representation_id
        ),
        extractor_version=_extractor_version(),
    )


# --- lineage state -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Gap:
    """One gap row of a version and layer."""

    lane: ProcessingLane
    entered: bool
    discharged: bool


@dataclass(frozen=True, slots=True)
class _VersionState:
    """What lineage ordering needs to know about one version."""

    version_id: UUID
    version_no: int
    deleted: bool
    sources_complete: bool
    occurrences_complete: bool
    claims_frontier: bool
    facts_frontier: bool
    claims_reconcile_status: str | None
    gaps: Mapping[ProcessingLayer, _Gap]

    def frontier(self, *, layer: ProcessingLayer) -> bool:
        """Whether the version reached ``layer``'s frontier."""
        if layer is ProcessingLayer.CLAIMS:
            return self.claims_frontier
        return self.facts_frontier

    def settled(self, *, layer: ProcessingLayer) -> bool:
        """Reached the frontier, or will never be processed by the layer."""
        gap = self.gaps.get(layer)
        return self.frontier(layer=layer) or (gap is not None and gap.discharged)

    def facts_settled(self) -> bool:
        """No fact work of this version is in flight (§4.3)."""
        gap = self.gaps.get(ProcessingLayer.FACTS)
        return self.facts_frontier or (gap is not None and not gap.entered)

    @property
    def has_claims_tail(self) -> bool:
        """Whether the version's claims-mode reconcile was ever enqueued."""
        return self.claims_reconcile_status is not None


@dataclass(frozen=True, slots=True)
class _Lineage:
    """A locked lineage's versions, mode and current version."""

    deployment_id: UUID
    doc_id: UUID
    living: bool
    lineage_deleted: bool
    current_version_id: UUID | None
    versions: tuple[_VersionState, ...]

    def target(self, *, layer: ProcessingLayer) -> _VersionState | None:
        """The version advance would enter next, if any (step 3)."""
        candidates = [
            version
            for version in self.versions
            if not version.deleted
            and (gap := version.gaps.get(layer)) is not None
            and not gap.entered
            and not gap.discharged
        ]
        if self.living:
            return next(
                (
                    version
                    for version in candidates
                    if version.version_id == self.current_version_id
                ),
                None,
            )
        return candidates[0] if candidates else None

    def admits(self, *, target: _VersionState, layer: ProcessingLayer) -> bool:
        """Steps 4–6: sources done, earlier versions settled, occurrences done."""
        if not target.sources_complete:
            return False
        for version in self.versions:
            if version.version_no >= target.version_no:
                break
            if version.deleted or not version.sources_complete:
                continue
            if not version.settled(layer=layer):
                return False
        if layer is ProcessingLayer.FACTS:
            # Step 6: occurrences complete, and a claims tail that already
            # exists has finished its currency-changing reconcile.
            return target.occurrences_complete and (
                not target.has_claims_tail
                or target.claims_reconcile_status == "succeeded"
            )
        return True


def _read_lineage(
    *, connection: Connection, deployment_id: UUID, doc_id: UUID
) -> _Lineage:
    """Load the ordering state of every version of one lineage."""
    from rememberstack.workers.p1 import P1_EMBED_CLAIMS_VERSION
    from rememberstack.workers.reconcile import RECONCILE_CLAIMS_VERSION
    from rememberstack.workers.reconcile import RECONCILE_VERSION

    head = connection.execute(_SELECT_LINEAGE, {"doc_id": doc_id}).mappings().one()
    gap_rows = (
        connection.execute(
            _SELECT_LINEAGE_GAPS, {"deployment_id": deployment_id, "doc_id": doc_id}
        )
        .mappings()
        .all()
    )
    gaps: dict[UUID, dict[ProcessingLayer, _Gap]] = {}
    for row in gap_rows:
        gaps.setdefault(row["version_id"], {})[ProcessingLayer(row["layer"])] = _Gap(
            lane=ProcessingLane(str(row["lane"])),
            entered=row["entered_at"] is not None,
            discharged=row["discharged_at"] is not None,
        )
    versions: list[_VersionState] = []
    for row in (
        connection.execute(
            _SELECT_LINEAGE_VERSIONS,
            {
                "deployment_id": deployment_id,
                "doc_id": doc_id,
                "embed_version": _embed_version(),
                "embed_claim_version": P1_EMBED_CLAIMS_VERSION,
                "reconcile_version": RECONCILE_VERSION,
                "claims_reconcile_version": RECONCILE_CLAIMS_VERSION,
            },
        )
        .mappings()
        .all()
    ):
        version_id = row["version_id"]
        sources_complete = row["embed_status"] == "succeeded"
        occurrences = sources_complete and occurrences_complete_on(
            connection=connection, version_id=version_id
        )
        versions.append(
            _VersionState(
                version_id=version_id,
                version_no=int(row["version_no"]),
                deleted=row["deleted_at"] is not None or head["deleted_at"] is not None,
                sources_complete=sources_complete,
                occurrences_complete=occurrences,
                claims_frontier=occurrences
                and row["embed_claim_status"] == "succeeded",
                facts_frontier=row["reconcile_status"] == "succeeded",
                claims_reconcile_status=row["claims_reconcile_status"],
                gaps=gaps.get(version_id, {}),
            )
        )
    return _Lineage(
        deployment_id=deployment_id,
        doc_id=doc_id,
        living=str(head["versioning_mode"]) == "living",
        lineage_deleted=head["deleted_at"] is not None,
        current_version_id=head["current_version_id"],
        versions=tuple(versions),
    )


@dataclass(frozen=True, slots=True)
class _LockedLineage:
    """The lineage of a version, locked, with the deletion state boundaries need."""

    doc_id: UUID
    lineage_deleted: bool
    deleted_versions: frozenset[UUID]

    def deleted(self, *, version_id: UUID) -> bool:
        """Whether the version or its whole lineage is deleted."""
        return self.lineage_deleted or version_id in self.deleted_versions


def _lock_lineage_of_version(
    *, connection: Connection, version_id: UUID
) -> _LockedLineage:
    """Take the D135 lineage lock of a version's document and read its deletion."""
    row = (
        connection.execute(_SELECT_VERSION_LINEAGE, {"version_id": version_id})
        .mappings()
        .one()
    )
    _lock_lineage(connection=connection, doc_id=row["doc_id"])
    deleted = (
        connection.execute(_SELECT_LINEAGE_DELETION, {"doc_id": row["doc_id"]})
        .mappings()
        .one()
    )
    return _LockedLineage(
        doc_id=row["doc_id"],
        lineage_deleted=deleted["lineage_deleted_at"] is not None,
        deleted_versions=frozenset(
            connection.execute(
                _SELECT_DELETED_VERSIONS, {"doc_id": row["doc_id"]}
            ).scalars()
        ),
    )


def _lock_lineage(*, connection: Connection, doc_id: UUID) -> None:
    """The D135 lineage lock: the ``documents`` row, ``FOR UPDATE``."""
    connection.execute(_LOCK_LINEAGE, {"doc_id": doc_id})


def _reopen_current_gaps(
    *, connection: Connection, lineage: _Lineage, layer: ProcessingLayer
) -> bool:
    """Step 1: reopen the current version's discharged gaps, even when disabled.

    A deletion can make an older, discharged version current again; its gaps
    must be open so the layer processes it (now, or when it is switched on and
    seeded). Reopening a claims gap also reopens the facts gap. Returns whether
    anything was reopened.
    """
    current = next(
        (v for v in lineage.versions if v.version_id == lineage.current_version_id),
        None,
    )
    gap = None if current is None else current.gaps.get(layer)
    if current is None or gap is None or not gap.discharged:
        return False
    for reopened in _dependent_layers(layer=layer):
        connection.execute(
            _REOPEN_CURRENT_GAP,
            {
                "deployment_id": lineage.deployment_id,
                "version_id": current.version_id,
                "layer": reopened,
            },
        )
    return True


def _discharge_superseded_gaps(
    *, connection: Connection, lineage: _Lineage, layer: ProcessingLayer
) -> None:
    """Step 2: discharge the unentered gaps of every non-current version (§5.3).

    Entered gaps are never discharged: their work is running and finishes.
    Discharging a claims gap also discharges the facts gap, because a version
    that never extracts can never reach its facts boundary.
    """
    for version in lineage.versions:
        gap = version.gaps.get(layer)
        if (
            version.version_id == lineage.current_version_id
            or gap is None
            or gap.entered
            or gap.discharged
        ):
            continue
        for discharged in _dependent_layers(layer=layer):
            connection.execute(
                _DISCHARGE_GAP,
                {
                    "deployment_id": lineage.deployment_id,
                    "version_id": version.version_id,
                    "doc_id": lineage.doc_id,
                    "layer": discharged,
                    "lane": gap.lane,
                },
            )


def _dependent_layers(*, layer: ProcessingLayer) -> tuple[ProcessingLayer, ...]:
    """The layer and every layer built from it (claims carries facts)."""
    if layer is ProcessingLayer.CLAIMS:
        return (ProcessingLayer.CLAIMS, ProcessingLayer.FACTS)
    return (layer,)


def _open_owed_claims_tails(
    *,
    connection: Connection,
    lineage: _Lineage,
    facts_enabled: bool,
    admitted: UUID | None,
) -> list[EnqueueOutcome]:
    """Step 0: open every claims tail the lineage owes and §4.3 now allows.

    A version owes a tail when its occurrences are complete, it has no tail
    yet, and it will not enter facts now: facts are disabled, its facts gap is
    discharged, or (snapshot) it waits behind an earlier unentered facts gap.
    The version being admitted to facts never owes one. The tail opens once no
    earlier version has fact work in flight. Opening is idempotent.
    """
    outcomes: list[EnqueueOutcome] = []
    unentered_before = False
    for index, version in enumerate(lineage.versions):
        gap = version.gaps.get(ProcessingLayer.FACTS)
        waits_behind_gap = unentered_before
        if (
            not version.deleted
            and gap is not None
            and not gap.entered
            and not gap.discharged
        ):
            unentered_before = True
        if (
            version.version_id == admitted
            or version.deleted
            or gap is None
            or gap.entered
            or version.has_claims_tail
            or not version.occurrences_complete
        ):
            continue
        will_not_enter = (
            not facts_enabled
            or gap.discharged
            or (not lineage.living and waits_behind_gap)
        )
        if not will_not_enter:
            continue
        earlier = lineage.versions[:index]
        if all(
            other.deleted or not other.sources_complete or other.facts_settled()
            for other in earlier
        ):
            outcomes.extend(
                _open_claims_tail(
                    connection=connection,
                    deployment_id=lineage.deployment_id,
                    version_id=version.version_id,
                    doc_id=lineage.doc_id,
                    lane=gap.lane,
                )
            )
    return outcomes


# --- entering a layer ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class _EmbedRow:
    """The version's ``embed_chunk`` work row: the sources layer's last stage."""

    deployment_id: UUID
    representation_id: UUID
    content_hash: str
    status: str


def _enter_claims(
    *,
    connection: Connection,
    deployment_id: UUID,
    version_id: UUID,
    lane: ProcessingLane | None,
) -> list[EnqueueOutcome]:
    """Enqueue what the claims boundary enqueues today for the version.

    One ``extract_claims`` job per chunk; a version without chunks has its claim
    occurrences complete at once and goes straight to its facts boundary.
    """
    from rememberstack.spine.work_ledger import enqueue_on

    embed = _require_embed_row(connection=connection, version_id=version_id)
    chunk_ids = tuple(
        connection.execute(
            _SELECT_CHUNK_IDS,
            {
                "representation_id": embed.representation_id,
                "chunker_version": _grid_generation(
                    connection=connection, representation_id=embed.representation_id
                ),
            },
        ).scalars()
    )
    if not chunk_ids:
        return facts_boundary_on(
            connection=connection,
            deployment_id=deployment_id,
            version_id=version_id,
            lane=lane,
        )
    return [
        enqueue_on(
            connection=connection,
            work=EnqueueWork(
                deployment_id=deployment_id,
                target_kind=ProcessingTarget.CHUNK,
                target_id=chunk_id,
                stage=PipelineStage.EXTRACT_CLAIMS,
                component_version=_extractor_version(),
                content_hash=embed.content_hash,
                lane=lane,
                payload={
                    "version_id": str(version_id),
                    "representation_id": str(embed.representation_id),
                    "chunk_id": str(chunk_id),
                },
            ),
        )
        for chunk_id in chunk_ids
    ]


def _enter_facts(
    *,
    connection: Connection,
    deployment_id: UUID,
    version_id: UUID,
    lane: ProcessingLane | None,
) -> list[EnqueueOutcome]:
    """Open the ordinary E3 fan-out (or the empty observation flush) for the version."""
    from rememberstack.spine.work_ledger import enqueue_claim_normalize_fanout_on
    from rememberstack.workers.e3 import E3_NORMALIZER_VERSION

    embed = _require_embed_row(connection=connection, version_id=version_id)
    return enqueue_claim_normalize_fanout_on(
        connection=connection,
        deployment_id=deployment_id,
        version_id=version_id,
        representation_id=embed.representation_id,
        chunker_version=_grid_generation(
            connection=connection, representation_id=embed.representation_id
        ),
        extractor_version=_extractor_version(),
        normalize_component_version=E3_NORMALIZER_VERSION,
        content_hash=embed.content_hash,
        lane=lane,
    )


def _open_claims_tail(
    *,
    connection: Connection,
    deployment_id: UUID,
    version_id: UUID,
    doc_id: UUID,
    lane: ProcessingLane | None,
) -> list[EnqueueOutcome]:
    """Enqueue ``embed_claim`` and the claims-mode reconcile (idempotent, §4.2)."""
    from rememberstack.spine.work_ledger import enqueue_on
    from rememberstack.workers.p1 import P1_EMBED_CLAIMS_VERSION
    from rememberstack.workers.reconcile import RECONCILE_CLAIMS_VERSION

    embed = _require_embed_row(connection=connection, version_id=version_id)
    coordinates: dict[str, object] = {
        "version_id": str(version_id),
        "representation_id": str(embed.representation_id),
    }
    return [
        enqueue_on(
            connection=connection,
            work=EnqueueWork(
                deployment_id=deployment_id,
                target_kind=ProcessingTarget.DOCUMENT_VERSION,
                target_id=version_id,
                stage=PipelineStage.EMBED_CLAIM,
                component_version=P1_EMBED_CLAIMS_VERSION,
                content_hash=embed.content_hash,
                lane=lane,
                payload=coordinates,
            ),
        ),
        enqueue_on(
            connection=connection,
            work=EnqueueWork(
                deployment_id=deployment_id,
                target_kind=ProcessingTarget.DOCUMENT_VERSION,
                target_id=version_id,
                stage=PipelineStage.RECONCILE,
                component_version=RECONCILE_CLAIMS_VERSION,
                content_hash=embed.content_hash,
                lane=lane,
                payload={**coordinates, "doc_id": str(doc_id)},
            ),
        ),
    ]


def _record_gap(
    *,
    connection: Connection,
    deployment_id: UUID,
    version_id: UUID,
    doc_id: UUID,
    layer: ProcessingLayer,
    lane: ProcessingLane,
) -> None:
    """Insert the version's gap once; an existing gap keeps its lane and state."""
    connection.execute(
        _INSERT_GAP,
        {
            "deployment_id": deployment_id,
            "version_id": version_id,
            "doc_id": doc_id,
            "layer": layer,
            "lane": lane,
        },
    )


def _embed_row(*, connection: Connection, version_id: UUID) -> _EmbedRow | None:
    """The version's ``embed_chunk`` row at this build's generation, if any."""
    row = (
        connection.execute(
            _SELECT_EMBED_ROW,
            {"version_id": version_id, "embed_version": _embed_version()},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    payload = row["payload"] or {}
    return _EmbedRow(
        deployment_id=row["deployment_id"],
        representation_id=UUID(str(payload["representation_id"])),
        content_hash=str(row["content_hash"]),
        status=str(row["status"]),
    )


def _require_embed_row(*, connection: Connection, version_id: UUID) -> _EmbedRow:
    """The embed row of a version a boundary or advance is entering."""
    embed = _embed_row(connection=connection, version_id=version_id)
    if embed is None:
        raise LookupError(f"version {version_id} has no embed_chunk work row")
    return embed


def _embed_version() -> str:
    """The ``embed_chunk`` component version this build composes."""
    from rememberstack.workers.e1 import E1_EMBED_VERSION

    return E1_EMBED_VERSION


def _extractor_version() -> str:
    """The extractor generation this build composes."""
    from rememberstack.workers.e1 import E2_EXTRACTOR_VERSION

    return E2_EXTRACTOR_VERSION


def _grid_generation(*, connection: Connection, representation_id: UUID) -> str:
    """The packing generation of a representation's chunk grid (D58).

    The generation the chunks were written with, read from the rows rather
    than assumed from this process's parameters; an empty grid has none and
    any generation reads it as empty.
    """
    generation = connection.execute(
        _SELECT_GRID_GENERATION, {"representation_id": representation_id}
    ).scalar_one_or_none()
    return "" if generation is None else str(generation)


_SELECT_CONFIGURATION = text(
    """
    SELECT claims_enabled, facts_enabled
    FROM deployment_processing_layers
    WHERE deployment_id = :deployment_id
    """
)

_UPSERT_CONFIGURATION = text(
    """
    INSERT INTO deployment_processing_layers (
      deployment_id, claims_enabled, facts_enabled, changed_at
    ) VALUES (:deployment_id, :claims_enabled, :facts_enabled, now())
    ON CONFLICT (deployment_id) DO UPDATE SET
      claims_enabled = EXCLUDED.claims_enabled,
      facts_enabled = EXCLUDED.facts_enabled,
      changed_at = EXCLUDED.changed_at
    """
)

_SHARED_FENCE = text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:key, 0))")
_EXCLUSIVE_FENCE = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")

_LOCK_LINEAGE = text("SELECT doc_id FROM documents WHERE doc_id = :doc_id FOR UPDATE")

_SELECT_VERSION_LINEAGE = text(
    "SELECT doc_id FROM document_versions WHERE version_id = :version_id"
)

_SELECT_LINEAGE_DELETION = text(
    "SELECT deleted_at AS lineage_deleted_at FROM documents WHERE doc_id = :doc_id"
)

_SELECT_DELETED_VERSIONS = text(
    """
    SELECT version_id FROM document_versions
    WHERE doc_id = :doc_id AND deleted_at IS NOT NULL
    """
)

_SELECT_LINEAGE = text(
    """
    SELECT versioning_mode::text AS versioning_mode, current_version_id, deleted_at
    FROM documents
    WHERE doc_id = :doc_id
    """
)

_SELECT_LINEAGE_VERSIONS = text(
    """
    SELECT v.version_id, v.version_no, v.deleted_at,
           (SELECT p.status::text FROM processing_state p
            WHERE p.deployment_id = :deployment_id
              AND p.target_kind = 'document_version' AND p.target_id = v.version_id
              AND p.stage = 'embed_chunk' AND p.component_version = :embed_version)
             AS embed_status,
           (SELECT p.status::text FROM processing_state p
            WHERE p.deployment_id = :deployment_id
              AND p.target_kind = 'document_version' AND p.target_id = v.version_id
              AND p.stage = 'embed_claim'
              AND p.component_version = :embed_claim_version)
             AS embed_claim_status,
           (SELECT p.status::text FROM processing_state p
            WHERE p.deployment_id = :deployment_id
              AND p.target_kind = 'document_version' AND p.target_id = v.version_id
              AND p.stage = 'reconcile'
              AND p.component_version = :reconcile_version)
             AS reconcile_status,
           (SELECT p.status::text FROM processing_state p
            WHERE p.deployment_id = :deployment_id
              AND p.target_kind = 'document_version' AND p.target_id = v.version_id
              AND p.stage = 'reconcile'
              AND p.component_version = :claims_reconcile_version)
             AS claims_reconcile_status
    FROM document_versions v
    WHERE v.deployment_id = :deployment_id AND v.doc_id = :doc_id
    ORDER BY v.version_no
    """
)

_SELECT_LINEAGE_GAPS = text(
    """
    SELECT version_id, layer, lane::text AS lane, entered_at, discharged_at
    FROM processing_layer_gaps
    WHERE deployment_id = :deployment_id AND doc_id = :doc_id
    """
)

_SELECT_EMBED_ROW = text(
    """
    SELECT deployment_id, payload, content_hash, status::text AS status
    FROM processing_state
    WHERE target_kind = 'document_version' AND target_id = :version_id
      AND stage = 'embed_chunk' AND component_version = :embed_version
    """
)

_SELECT_GRID_GENERATION = text(
    """
    SELECT chunker_version FROM chunks
    WHERE representation_id = :representation_id
    ORDER BY created_at DESC, chunker_version DESC
    LIMIT 1
    """
)

_SELECT_CHUNK_IDS = text(
    """
    SELECT chunk_id FROM chunks
    WHERE representation_id = :representation_id AND chunker_version = :chunker_version
    ORDER BY ordinal
    """
)

_INSERT_GAP = text(
    """
    INSERT INTO processing_layer_gaps (deployment_id, version_id, doc_id, layer, lane)
    VALUES (:deployment_id, :version_id, :doc_id, :layer, CAST(:lane AS processing_lane))
    ON CONFLICT (deployment_id, version_id, layer) DO NOTHING
    """
)

_STAMP_ENTERED = text(
    """
    UPDATE processing_layer_gaps SET entered_at = now()
    WHERE deployment_id = :deployment_id AND version_id = :version_id
      AND layer = :layer AND entered_at IS NULL AND discharged_at IS NULL
    """
)

_DELETE_GAP = text(
    """
    DELETE FROM processing_layer_gaps
    WHERE deployment_id = :deployment_id AND version_id = :version_id AND layer = :layer
    """
)

_DELETE_GAPS_OF_DELETED_VERSIONS = text(
    """
    DELETE FROM processing_layer_gaps g
    WHERE g.deployment_id = :deployment_id AND g.doc_id = :doc_id
      AND EXISTS (
        SELECT 1 FROM document_versions v JOIN documents d ON d.doc_id = v.doc_id
        WHERE v.version_id = g.version_id
          AND (v.deleted_at IS NOT NULL OR d.deleted_at IS NOT NULL)
      )
    """
)

_REOPEN_CURRENT_GAP = text(
    """
    UPDATE processing_layer_gaps SET discharged_at = NULL
    WHERE deployment_id = :deployment_id AND version_id = :version_id
      AND layer = :layer AND discharged_at IS NOT NULL
    """
)

_DISCHARGE_GAP = text(
    """
    INSERT INTO processing_layer_gaps (
      deployment_id, version_id, doc_id, layer, lane, discharged_at
    ) VALUES (
      :deployment_id, :version_id, :doc_id, :layer,
      CAST(:lane AS processing_lane), now()
    )
    ON CONFLICT (deployment_id, version_id, layer) DO UPDATE
      SET discharged_at = now()
      WHERE processing_layer_gaps.entered_at IS NULL
        AND processing_layer_gaps.discharged_at IS NULL
    """
)

_OPEN_GAP_EXISTS = text(
    """
    SELECT EXISTS (
      SELECT 1 FROM processing_layer_gaps g
      JOIN document_versions v ON v.version_id = g.version_id
      JOIN documents d ON d.doc_id = v.doc_id
      WHERE g.deployment_id = :deployment_id AND g.layer = :layer
        AND g.discharged_at IS NULL
        AND v.deleted_at IS NULL AND d.deleted_at IS NULL
    )
    """
)

_SELECT_LINEAGES_WITH_UNENTERED_GAPS = text(
    """
    SELECT DISTINCT doc_id FROM processing_layer_gaps
    WHERE deployment_id = :deployment_id AND layer = :layer
      AND entered_at IS NULL AND discharged_at IS NULL
      AND (CAST(:after AS uuid) IS NULL OR doc_id > CAST(:after AS uuid))
    ORDER BY doc_id
    LIMIT :limit
    """
)

_GAP_COUNTS = text(
    """
    SELECT layer,
           CASE WHEN discharged_at IS NOT NULL THEN 'discharged'
                WHEN entered_at IS NOT NULL THEN 'entered'
                ELSE 'open' END AS kind,
           count(*) AS count
    FROM processing_layer_gaps
    WHERE deployment_id = :deployment_id
    GROUP BY 1, 2
    """
)
