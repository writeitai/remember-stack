"""Current-protocol P3 attestation through public per-version publication guards."""

from dataclasses import dataclass
from uuid import UUID

from remember.client import MemoryClient
from remember.models import DocumentSearchFilters
from remember.models import ReadinessRequirements


@dataclass(frozen=True)
class CorpusPublication:
    """One ready folder identity, matched to this run's exact ingest."""

    doc_id: UUID
    version_id: UUID
    representation_id: UUID
    path: str
    revision: str


def capture_publications(
    *, client: MemoryClient, versions: dict[UUID, UUID]
) -> tuple[CorpusPublication, ...]:
    """Resolve each served folder between two identical marker observations.

    Aggregate readiness deliberately has no global P3 version. These bounded
    single-version requests obtain the actual publication digests instead.
    Local folder identities and byte inventories are verified by P3Mount.
    """
    if not versions:
        raise ValueError("P3 attestation requires exact ingests")
    publications: list[CorpusPublication] = []
    require = ReadinessRequirements(pipeline=False, p1=False, live_graph=False, p3=True)
    for doc_id, version_id in sorted(versions.items()):
        before = client.pipeline_readiness(version_ids=(version_id,), require=require)
        page = client.search_documents(filters=DocumentSearchFilters(doc_ids=(doc_id,)))
        after = client.pipeline_readiness(version_ids=(version_id,), require=require)
        first, last = before.capabilities["p3"], after.capabilities["p3"]
        if (
            not before.ready
            or not after.ready
            or not first.ready
            or not last.ready
            or not first.version
            or first.version != last.version
            or len(page.documents) != 1
            or page.next_cursor is not None
        ):
            raise ValueError("P3 publication changed or is incomplete")
        document = page.documents[0]
        if (
            document.doc_id != doc_id
            or document.version_id != version_id
            or not document.served_version
            or document.p3_path is None
            or document.representation_id is None
        ):
            raise ValueError("P3 selection differs from exact ingests")
        publications.append(
            CorpusPublication(
                doc_id,
                version_id,
                document.representation_id,
                document.p3_path,
                first.version,
            )
        )
    return tuple(publications)
