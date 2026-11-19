"""Small authority fakes for local-store purge tests; PG+MinIO proves real replay."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.core.corpus_export import CorpusDocument
from rememberstack.spine.corpus_export import CorpusReaderCatalog
from rememberstack.spine.corpus_publication import CorpusPublicationCatalog
from rememberstack.spine.corpus_publication import CorpusPublicationLock
from rememberstack.workers.corpus_publication import CorpusPublisher


@dataclass
class _Lock:
    deployment_id: UUID

    def check(self) -> None:
        pass


class ScrubbedAuthority:
    def __init__(self, *, survivors: tuple[CorpusDocument, ...]) -> None:
        self.survivors = survivors
        self.recorded: dict[UUID, set[str]] = {}

    @contextmanager
    def locked(
        self, *, deployment_id: UUID, **_: object
    ) -> Iterator[CorpusPublicationLock]:
        yield cast(CorpusPublicationLock, _Lock(deployment_id))

    @contextmanager
    def documents(self, **_: object) -> Iterator[Iterator[CorpusDocument]]:
        yield iter(self.survivors)

    def clear(self, **_: object) -> None:
        return None

    def paths(self, *, doc_id: UUID, **_: object) -> set[str]:
        return set(self.recorded.get(doc_id, ()))

    def set_paths(self, *, doc_id: UUID, paths: set[str], **_: object) -> None:
        self.recorded[doc_id] = set(paths)


def scrubbed_publisher(
    *, store: LocalFSObjectStore, authority: ScrubbedAuthority
) -> CorpusPublisher:
    return CorpusPublisher(
        catalog=cast(CorpusPublicationCatalog, authority),
        readers=cast(CorpusReaderCatalog, authority),
        raw_store=store,
        artifact_store=store,
        corpus_store=store,
    )
