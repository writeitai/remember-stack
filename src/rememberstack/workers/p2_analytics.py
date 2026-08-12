"""Graph analytics on the freshly built snapshot (p2 §7, D11 → D72).

PageRank (salience prior), k-core (hub-ness), weakly-connected components
(ER-health signal), and **Louvain community detection** all run natively in
the engine on the snapshot the rebuild just validated — Louvain included,
which D72 corrected after verifying it live on the deployed build (the
vendored survey behind D11 said it was absent, so the original plan carried
an external igraph pass; the simpler mechanism removes it).

Results are written **back to Postgres** and never reprojected into the
graph's node tables: analytics are graph-DERIVED, so loading them into the
projection would be circular (D6). Community *labels* — the K1 navigation
aid — are an optional batched micro-LLM call over each community's top
members by PageRank; without a provider the communities are unlabeled,
which costs navigation polish and nothing load-bearing.
"""

from typing import cast
from typing import Final
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid5

import ladybug
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.model import ModelRequest
from rememberstack.model import ProviderAccountingError
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.spine.projection import ProjectionCatalog

COMMUNITY_DETECTOR_VERSION: Final = "p2-communities-2026.07b:louvain-native-temp0"
"""The analytics pass's component version (D12); the algorithm is part of
the identity because a detector swap changes every assignment. 07b pins the
label call's temperature=0.0 — labels are navigation-only, but identical
builds should not wander."""

_PROJECTION: Final = "analytics_graph"
_LABEL_MEMBERS: Final = 8
"""How many top-PageRank members the labeling prompt sees per community."""

_LABEL_PROMPT: Final = (
    "Each numbered line lists the most central members of one cluster in a"
    " knowledge graph. Give EVERY cluster a short topic label (2-5 words) an"
    " agent could navigate by, returning the cluster's number with its"
    " label.\n\n{clusters}"
)


class CommunityLabelItem(BaseModel):
    """One cluster's label, keyed by its position in the batched prompt."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    index: int = Field(default=-1)
    label: str = Field(default="")


class CommunityLabels(BaseModel):
    """The batched labeling call's structured output (p2 §7)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    labels: tuple[CommunityLabelItem, ...] = ()


class AnalyticsSettings(BaseSettings):
    """The analytics pass's knobs (D70 port config; D22 numbers)."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_P2_ANALYTICS_")

    label_model: str = Field(default="openai/gpt-5.6-luna")
    min_community_size_to_label: int = Field(default=3, ge=1)


class GraphAnalyticsWorker:
    """Compute snapshot analytics natively; write assignments to Postgres."""

    def __init__(
        self,
        *,
        catalog: ProjectionCatalog,
        model_provider: ModelProviderPort | None = None,
        settings: AnalyticsSettings | None = None,
    ) -> None:
        """Bind to the spine and (optionally) the labeling model seat."""
        self._catalog = catalog
        self._model_provider = model_provider
        self._settings = settings or AnalyticsSettings()

    def compute(
        self, *, snapshot_id: UUID, connection: ladybug.Connection
    ) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...]]:
        """Run every algorithm on the snapshot; return rows, write nothing.

        Computing and writing are deliberately separate (Codex review): the
        algorithms need the writer's live graph, but their rows must not
        outlive a snapshot that later fails validation or upload — so the
        caller persists them only after the snapshot publishes.
        """
        connection.execute("INSTALL algo")
        connection.execute("LOAD algo")
        connection.execute(
            # analytics measure CURRENT connectivity: the snapshot
            # deliberately retains invalidated and expired edges for
            # transaction-time as-of (D69), but a withdrawn fact must not
            # inflate centrality or fuse two communities (Codex review).
            # The engine's filtered projection applies this per edge.
            f"CALL PROJECT_GRAPH('{_PROJECTION}', ['Entity'],"
            " {'RELATES': 'r.invalidated_at IS NULL AND (r.valid_until IS NULL"
            " OR r.valid_until > current_timestamp())'})"
        )
        pagerank = _algorithm_scores(connection, algorithm="PAGE_RANK")
        k_core = _algorithm_scores(connection, algorithm="K_CORE_DECOMPOSITION")
        components = _algorithm_scores(
            connection, algorithm="WEAKLY_CONNECTED_COMPONENTS"
        )
        louvain = _algorithm_scores(connection, algorithm="LOUVAIN")
        degrees = _degrees(connection)
        names = _names(connection)

        members: dict[object, list[UUID]] = {}
        for entity_id, group in louvain.items():
            members.setdefault(group, []).append(entity_id)
        communities: list[dict[str, object]] = []
        community_ids: dict[object, UUID] = {}
        labels = self._labels(members=members, pagerank=pagerank, names=names)
        for group, group_members in sorted(
            members.items(), key=lambda item: str(item[0])
        ):
            # the id derives from the MEMBER SET, never the engine's group
            # label: Louvain's numbering is order-dependent, so a re-run
            # could otherwise reuse an id for a different community
            # (Codex review). Same members ⇒ same id, always.
            fingerprint = ",".join(sorted(str(member) for member in group_members))
            community_id = uuid5(
                NAMESPACE_URL, f"rememberstack:community:{snapshot_id}:{fingerprint}"
            )
            community_ids[group] = community_id
            communities.append(
                {
                    "community_id": community_id,
                    "label": labels.get(group),
                    "size": len(group_members),
                    "algorithm": "louvain",
                }
            )
        metrics: tuple[dict[str, object], ...] = tuple(
            {
                "entity_id": entity_id,
                "community_id": community_ids.get(louvain.get(entity_id)),
                "pagerank": float(cast("float", pagerank.get(entity_id, 0.0))),
                "degree": int(cast("int", degrees.get(entity_id, 0))),
                "k_core": int(cast("int", k_core.get(entity_id, 0))),
                "component_id": uuid5(
                    NAMESPACE_URL,
                    f"rememberstack:component:{snapshot_id}:{components.get(entity_id)}",
                ),
            }
            for entity_id in names
        )
        return tuple(communities), metrics

    def persist(
        self,
        *,
        deployment_id: UUID,
        snapshot_id: UUID,
        communities: tuple[dict[str, object], ...],
        metrics: tuple[dict[str, object], ...],
    ) -> dict[str, int]:
        """Write a PUBLISHED snapshot's analytics back to Postgres (D6)."""
        self._catalog.record_graph_analytics(
            deployment_id=deployment_id,
            snapshot_id=snapshot_id,
            communities=communities,
            metrics=metrics,
            detector_version=COMMUNITY_DETECTOR_VERSION,
            label_model=(
                self._settings.label_model if self._model_provider is not None else None
            ),
        )
        return {"communities": len(communities), "entities": len(metrics)}

    def _labels(
        self,
        *,
        members: dict[object, list[UUID]],
        pagerank: dict[UUID, object],
        names: dict[UUID, str],
    ) -> dict[object, str]:
        """Short navigation labels for every eligible community — ONE call.

        Batched by contract (p2 §7): community count must not multiply
        rebuild latency, and thousands of small clusters must not become
        thousands of sequential calls (Codex review). Skipped entirely
        without a model seat; a failed call yields no labels rather than a
        failed rebuild — labels are navigation aids, nothing load-bearing
        reads them.
        """
        if self._model_provider is None:
            return {}
        eligible = {
            group: group_members
            for group, group_members in members.items()
            if len(group_members) >= self._settings.min_community_size_to_label
        }
        if not eligible:
            return {}
        ordered = sorted(eligible, key=str)
        blocks: list[str] = []
        for index, group in enumerate(ordered):
            central = sorted(
                eligible[group],
                key=lambda entity: float(cast("float", pagerank.get(entity, 0.0))),
                reverse=True,
            )[:_LABEL_MEMBERS]
            listing = ", ".join(names.get(entity, "") for entity in central)
            blocks.append(f"{index}. {listing}")
        try:
            response = self._model_provider.generate(
                request=ModelRequest(
                    model=self._settings.label_model,
                    prompt=_LABEL_PROMPT.format(clusters="\n".join(blocks)),
                    temperature=0.0,
                ),
                response_type=CommunityLabels,
            )
        except ProviderAccountingError:
            raise
        except Exception:  # noqa: BLE001 — a label never fails the analytics
            return {}
        return {
            ordered[item.index]: item.label
            for item in response.output.labels
            if 0 <= item.index < len(ordered) and item.label
        }


def _algorithm_scores(
    connection: ladybug.Connection, *, algorithm: str
) -> dict[UUID, object]:
    """One algorithm's per-entity output, keyed by entity id."""
    result = connection.execute(f"CALL {algorithm}('{_PROJECTION}') RETURN *")
    assert isinstance(result, ladybug.QueryResult)
    scores: dict[UUID, object] = {}
    while result.has_next():
        row = cast("list[object]", result.get_next())
        node = cast("dict[str, object]", row[0])
        scores[cast("UUID", node["id"])] = row[1]
    return scores


def _degrees(connection: ladybug.Connection) -> dict[UUID, int]:
    """Relation degree per entity (undirected: the blast-radius input)."""
    result = connection.execute(
        "MATCH (e:Entity) OPTIONAL MATCH (e)-[r:RELATES]-()"
        " WHERE r.invalidated_at IS NULL"
        " AND (r.valid_until IS NULL OR r.valid_until > current_timestamp())"
        " RETURN e.id, count(r)"
    )
    assert isinstance(result, ladybug.QueryResult)
    degrees: dict[UUID, int] = {}
    while result.has_next():
        row = cast("list[object]", result.get_next())
        degrees[cast("UUID", row[0])] = cast("int", row[1])
    return degrees


def _names(connection: ladybug.Connection) -> dict[UUID, str]:
    """Every emitted entity's canonical name (the labeling input)."""
    result = connection.execute("MATCH (e:Entity) RETURN e.id, e.name")
    assert isinstance(result, ladybug.QueryResult)
    names: dict[UUID, str] = {}
    while result.has_next():
        row = cast("list[object]", result.get_next())
        names[cast("UUID", row[0])] = cast("str", row[1])
    return names
