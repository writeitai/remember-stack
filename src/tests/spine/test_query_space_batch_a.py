"""The §9.1–§9.4 merge gates for the `memory_v1` schema contract.

Everything here runs against a real PostgreSQL at the migration head, because
the claims under test are claims about compiled SQL: that deletion is
fail-closed in every relation, that the two clocks are applied at one instant,
and that evidence counts distinct source lineages rather than repetitions.
None of that can be established by reading Python.

The corpus is one small, deliberately awkward deployment: a lineage with a
superseded version, a merged entity, an unresolved mention, two independent
sources for one fact, a repetition inside one source, a contradiction pair, a
fact whose only source is about to be forgotten, and a knowledge page citing a
claim coordinate. Every gate below reads that corpus; the mutating gates apply
their change inside a transaction and roll it back, so one seeded corpus serves
all of them.
"""

from collections.abc import Iterator
from collections.abc import Mapping
from datetime import datetime
from datetime import UTC
import json
from pathlib import Path
from typing import Any
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import make_url
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.model import DeploymentBootstrapInput
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.query_space import build_manifest
from rememberstack.spine.query_space import DELETION_TARGETS
from rememberstack.spine.query_space import EXECUTED_TARGETS
from rememberstack.spine.query_space import introspect_live_schema
from rememberstack.spine.query_space import live_schema_differences
from rememberstack.spine.query_space import load_manifest
from rememberstack.spine.query_space import load_matrix
from rememberstack.spine.query_space import MATRIX_SURFACES
from rememberstack.spine.query_space import orphan_quarantine_report
from rememberstack.spine.query_space import POSTGRESQL_MAJOR
from rememberstack.spine.query_space import QUARANTINE_CATEGORIES
from rememberstack.spine.query_space import render_manifest
from rememberstack.spine.query_space import VIEW_CONTRACTS
from rememberstack.spine.query_space.manifest import deployed_definition_differences
from rememberstack.spine.query_space.manifest import deployed_definitions
from rememberstack.spine.query_space.manifest import MANIFEST_PATH
from rememberstack.spine.query_space.source_definitions import (
    AUTHORIZATION_HELPER_VIEWS,
)
from rememberstack.spine.settings import load_database_settings

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("5a000000-0000-0000-0000-0000000000a1")

_ANCIENT = datetime(2020, 1, 1, tzinfo=UTC)
_PAST = datetime(2024, 1, 1, tzinfo=UTC)
_MID = datetime(2025, 1, 1, tzinfo=UTC)
_ENDED = datetime(2025, 6, 1, tzinfo=UTC)
_FUTURE = datetime(2099, 1, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def database_url() -> str:
    """The configured database, or a skip when the schema gates cannot run."""
    try:
        return load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for the schema gates")


@pytest.fixture(scope="module")
def database_engine(database_url: str) -> Iterator[Engine]:
    """Apply the real structural head so the query space is the shipped one."""
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


def _build_in_scratch_database(*, database_url: str, name: str) -> dict[str, Any]:
    """Create one throwaway database, migrate it to head, and read it back."""
    admin = create_engine(database_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            # the identifier is a literal from this module, never caller input
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        scratch_url = make_url(database_url).set(database=name)
        rendered = scratch_url.render_as_string(hide_password=False)
        config = Config(str(_ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", rendered)
        command.upgrade(config=config, revision="head")
        engine = create_engine(rendered)
        try:
            with engine.connect() as connection:
                return introspect_live_schema(connection).model_dump(mode="json")
        finally:
            engine.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


class _Corpus:
    """One deployment shaped to exercise every invariant the views compile."""

    def __init__(self, *, engine: Engine) -> None:
        self.engine = engine
        self.entity: dict[str, UUID] = {}
        self.alias: dict[str, UUID] = {}
        self.doc: dict[str, UUID] = {}
        self.version: dict[str, UUID] = {}
        self.representation: dict[str, UUID] = {}
        self.generation: dict[str, UUID] = {}
        self.section: dict[str, UUID] = {}
        self.chunk: dict[str, UUID] = {}
        self.claim: dict[str, UUID] = {}
        self.mention: dict[str, UUID] = {}
        self.decision: dict[str, UUID] = {}
        self.merge: dict[str, UUID] = {}
        self.fact: dict[str, UUID] = {}
        self.crossref: dict[str, UUID] = {}
        self.artifact: dict[str, UUID] = {}
        self.currency_event: dict[str, UUID] = {}
        self.adjudication: dict[str, UUID] = {}
        self.compilation_id = uuid4()
        self.contradiction_group = uuid4()
        self.lonely_contradiction_group = uuid4()
        with engine.begin() as connection:
            self._seed_entities(connection=connection)
            self._seed_documents(connection=connection)
            self._seed_testimony(connection=connection)
            self._seed_identity(connection=connection)
            self._seed_facts(connection=connection)
            self._seed_knowledge(connection=connection)

    # ── seeding ──────────────────────────────────────────────────────────
    def _seed_entities(self, *, connection: Connection) -> None:
        """Create survivors, a merged predecessor, and a provenance-free ghost."""
        for key, name, entity_type in (
            ("alice", "Alice Example", "Person"),
            ("acme", "Acme Corp", "Organization"),
            ("globex", "Globex Corp", "Organization"),
            ("initech", "Initech Corp", "Organization"),
            ("hermit", "Hermit Example", "Person"),
            ("ghost", "Ghost Example", "Person"),
            ("alice_dup", "Alice Dup", "Person"),
        ):
            entity_id = uuid4()
            self.entity[key] = entity_id
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id, type,"
                    " canonical_name, normalized_name, mention_count, graph_degree)"
                    " VALUES (:entity, :deployment, :type, :name, lower(:name), 0, 3)"
                ),
                {
                    "entity": entity_id,
                    "deployment": _DEPLOYMENT_ID,
                    "type": entity_type,
                    "name": name,
                },
            )
        connection.execute(
            text(
                "UPDATE entities SET status = 'merged', merged_into = :survivor"
                " WHERE entity_id = :absorbed"
            ),
            {"survivor": self.entity["alice"], "absorbed": self.entity["alice_dup"]},
        )
        for key, entity_key, alias_text in (
            ("alice_dup", "alice_dup", "A. Example"),
            ("hermit", "hermit", "Hermit"),
            ("ghost", "ghost", "Ghost"),
        ):
            alias_id = uuid4()
            self.alias[key] = alias_id
            connection.execute(
                text(
                    "INSERT INTO aliases (alias_id, deployment_id, entity_id,"
                    " alias_text, normalized_lemma, provenance)"
                    " VALUES (:alias, :deployment, :entity, :text, lower(:text),"
                    " 'llm_canonical')"
                ),
                {
                    "alias": alias_id,
                    "deployment": _DEPLOYMENT_ID,
                    "entity": self.entity[entity_key],
                    "text": alias_text,
                },
            )

    def _seed_documents(self, *, connection: Connection) -> None:
        """Create five lineages covering supersession, forgetting, and K citation."""
        self._lineage(
            connection=connection,
            key="primary",
            versions=(("v1", 1, False), ("v2", 2, True), ("vdel", 3, False)),
        )
        self._lineage(connection=connection, key="second", versions=(("v1", 1, True),))
        self._lineage(
            connection=connection, key="forgotten", versions=(("v1", 1, True),)
        )
        self._lineage(connection=connection, key="repcase", versions=(("v1", 1, True),))
        self._lineage(connection=connection, key="kcited", versions=(("v1", 1, True),))
        self._lineage(connection=connection, key="erased", versions=(("v1", 1, True),))
        # the permanent negatives: one tombstoned lineage, one tombstoned version,
        # and one superseded structure generation, so every "must be absent" fixture
        # has a row that really exists in the base tables
        connection.execute(
            text("UPDATE documents SET deleted_at = :at WHERE doc_id = :doc"),
            {"at": _MID, "doc": self.doc["erased"]},
        )
        connection.execute(
            text("UPDATE document_versions SET deleted_at = :at WHERE version_id = :v"),
            {"at": _MID, "v": self.version["primary.vdel"]},
        )
        self._superseded_generation(connection=connection, key="primary.v2")
        # a second chunk coordinate inside the cited lineage, so the knowledge
        # page cites two coordinates of one lineage and both resolve to a claim
        self._extra_chunk(connection=connection, key="kcited.v1", suffix="b")
        for key, source, target in (
            ("resolved", "primary", "second"),
            ("unresolved", "primary", None),
            ("from_forgotten", "forgotten", "primary"),
        ):
            crossref_id = uuid4()
            self.crossref[key] = crossref_id
            connection.execute(
                text(
                    "INSERT INTO document_crossrefs (crossref_id, deployment_id,"
                    " from_doc_id, to_doc_id, kind, raw_citation, context, resolved)"
                    " VALUES (:crossref, :deployment, :source, :target, 'cites',"
                    " 'raw citation text', 'surrounding context', :resolved)"
                ),
                {
                    "crossref": crossref_id,
                    "deployment": _DEPLOYMENT_ID,
                    "source": self.doc[source],
                    "target": None if target is None else self.doc[target],
                    "resolved": target is not None,
                },
            )

    def _lineage(
        self,
        *,
        connection: Connection,
        key: str,
        versions: tuple[tuple[str, int, bool], ...],
    ) -> None:
        """Create one lineage with its versions, readings, sections, and chunks."""
        doc_id = uuid4()
        self.doc[key] = doc_id
        connection.execute(
            text(
                "INSERT INTO documents (doc_id, deployment_id, source_kind, source_ref,"
                " source_uri, title, first_seen_at, last_observed_at)"
                " VALUES (:doc, :deployment, 'upload', :ref, :uri, :title, :seen, :seen)"
            ),
            {
                "doc": doc_id,
                "deployment": _DEPLOYMENT_ID,
                "ref": f"qs-{key}",
                "uri": f"mem://{key}",
                "title": f"Query space {key}",
                "seen": _PAST,
            },
        )
        for version_key, version_no, is_current in versions:
            content_hash = f"qs-{key}-{version_key}"
            connection.execute(
                text(
                    "INSERT INTO content_objects (deployment_id, content_hash, mime,"
                    " byte_size, raw_uri)"
                    " VALUES (:deployment, :hash, 'text/markdown', 10, :uri)"
                ),
                {
                    "deployment": _DEPLOYMENT_ID,
                    "hash": content_hash,
                    "uri": f"mem://raw/{content_hash}",
                },
            )
            version_id = uuid4()
            self.version[f"{key}.{version_key}"] = version_id
            connection.execute(
                text(
                    "INSERT INTO document_versions (version_id, deployment_id, doc_id,"
                    " content_hash, version_no, status, ingested_at, source_modified_at,"
                    " published_at, language, superseded_at)"
                    " VALUES (:version, :deployment, :doc, :hash, :no, 'ready', :at,"
                    " :at, :at, 'en', :superseded)"
                ),
                {
                    "version": version_id,
                    "deployment": _DEPLOYMENT_ID,
                    "doc": doc_id,
                    "hash": content_hash,
                    "no": version_no,
                    "at": _PAST,
                    "superseded": None if is_current else _MID,
                },
            )
            representation_id = self._representation(
                connection=connection, key=f"{key}.{version_key}", version_id=version_id
            )
            connection.execute(
                text(
                    "UPDATE document_versions SET current_representation_id = :rep"
                    " WHERE version_id = :version"
                ),
                {"rep": representation_id, "version": version_id},
            )
            if is_current:
                connection.execute(
                    text(
                        "UPDATE documents SET current_version_id = :version"
                        " WHERE doc_id = :doc"
                    ),
                    {"version": version_id, "doc": doc_id},
                )
            self._section_and_chunk(
                connection=connection,
                key=f"{key}.{version_key}",
                doc_id=doc_id,
                version_id=version_id,
                representation_id=representation_id,
            )

    def _representation(
        self, *, connection: Connection, key: str, version_id: UUID
    ) -> UUID:
        """Create one ready reading with one current structure generation."""
        representation_id = uuid4()
        self.representation[key] = representation_id
        connection.execute(
            text(
                "INSERT INTO document_representations (representation_id,"
                " deployment_id, version_id, route, markdown_uri, markdown_hash,"
                " status) VALUES (:rep, :deployment, :version, 'passthrough',"
                " :uri, :hash, 'ready')"
            ),
            {
                "rep": representation_id,
                "deployment": _DEPLOYMENT_ID,
                "version": version_id,
                "uri": f"mem://artifacts/{representation_id}/document.md",
                "hash": f"markdown-{key}",
            },
        )
        generation_id = uuid4()
        self.generation[key] = generation_id
        connection.execute(
            text(
                "INSERT INTO document_structure_generations (structure_generation_id,"
                " deployment_id, doc_id, version_id, representation_id,"
                " skeleton_version, skeleton_hash, skeleton_producer_family,"
                " route_tag, candidate_skeleton_hash, stats_version, stats)"
                " SELECT :generation, :deployment, v.doc_id, :version, :rep,"
                " 'skeleton-1', :hash, 'parser', 'parser', :hash, 'v1', '{}'::jsonb"
                " FROM document_versions v WHERE v.version_id = :version"
            ),
            {
                "generation": generation_id,
                "deployment": _DEPLOYMENT_ID,
                "version": version_id,
                "rep": representation_id,
                "hash": f"skeleton-{key}",
            },
        )
        connection.execute(
            text(
                "UPDATE document_representations"
                " SET current_structure_generation_id = :generation"
                " WHERE representation_id = :rep"
            ),
            {"generation": generation_id, "rep": representation_id},
        )
        return representation_id

    def _superseded_generation(self, *, connection: Connection, key: str) -> None:
        """Add a second, non-current structure generation with its own section."""
        generation_id = uuid4()
        self.generation[f"{key}.superseded"] = generation_id
        connection.execute(
            text(
                "INSERT INTO document_structure_generations (structure_generation_id,"
                " deployment_id, doc_id, version_id, representation_id,"
                " skeleton_version, skeleton_hash, skeleton_producer_family,"
                " route_tag, candidate_skeleton_hash, stats_version, stats)"
                " SELECT :generation, :deployment, v.doc_id, r.version_id, :rep,"
                " 'skeleton-0', :hash, 'parser', 'parser', :hash, 'v1', '{}'::jsonb"
                " FROM document_representations r JOIN document_versions v"
                " ON v.version_id = r.version_id WHERE r.representation_id = :rep"
            ),
            {
                "generation": generation_id,
                "deployment": _DEPLOYMENT_ID,
                "rep": self.representation[key],
                "hash": f"skeleton-superseded-{key}",
            },
        )
        section_id = uuid4()
        self.section[f"{key}.superseded"] = section_id
        connection.execute(
            text(
                "INSERT INTO document_sections (section_id, deployment_id, doc_id,"
                " version_id, representation_id, node_path, block_start, block_end,"
                " title, role, char_start, char_end, ordinal,"
                " structure_generation_id, normalized_title)"
                " SELECT :section, :deployment, v.doc_id, r.version_id, :rep, '0', 0, 1,"
                " 'Superseded section', 'body', 0, 100, 0, :generation,"
                " 'superseded section'"
                " FROM document_representations r JOIN document_versions v"
                " ON v.version_id = r.version_id WHERE r.representation_id = :rep"
            ),
            {
                "section": section_id,
                "deployment": _DEPLOYMENT_ID,
                "rep": self.representation[key],
                "generation": generation_id,
            },
        )

    def _section_and_chunk(
        self,
        *,
        connection: Connection,
        key: str,
        doc_id: UUID,
        version_id: UUID,
        representation_id: UUID,
    ) -> None:
        """Create one root section and one chunk inside a reading."""
        section_id = uuid4()
        self.section[key] = section_id
        connection.execute(
            text(
                "INSERT INTO document_sections (section_id, deployment_id, doc_id,"
                " version_id, representation_id, node_path, block_start, block_end,"
                " title, role, char_start, char_end, ordinal, summary,"
                " structure_generation_id, normalized_title)"
                " VALUES (:section, :deployment, :doc, :version, :rep, '0', 0, 1,"
                " :title, 'body', 0, 200, 0, 'Orientation summary.', :generation,"
                " :normalized)"
            ),
            {
                "section": section_id,
                "deployment": _DEPLOYMENT_ID,
                "doc": doc_id,
                "version": version_id,
                "rep": representation_id,
                "title": f"Section {key}",
                "generation": self.generation[key],
                "normalized": f"section {key}".lower(),
            },
        )
        chunk_id = uuid4()
        self.chunk[key] = chunk_id
        connection.execute(
            text(
                "INSERT INTO chunks (chunk_id, deployment_id, doc_id, version_id,"
                " representation_id, section_id, ordinal, block_start, block_end,"
                " chunk_content_hash, extraction_input_hash, char_start, char_end,"
                " token_count, embedding_text_hash, location_header,"
                " embedding_input_policy_version, policy_generation, embedding_version,"
                " chunker_version, created_at)"
                " VALUES (:chunk, :deployment, :doc, :version, :rep, :section, 0, 0, 1,"
                " :content_hash, :input_hash, 0, 200, 40, :embed_hash,"
                " 'Document > Section', 'policy-1', 'policy-gen-1', 'embed-1',"
                " 'chunker-1', :at)"
            ),
            {
                "chunk": chunk_id,
                "deployment": _DEPLOYMENT_ID,
                "doc": doc_id,
                "version": version_id,
                "rep": representation_id,
                "section": section_id,
                "content_hash": f"chunk-hash-{key}",
                "input_hash": f"input-hash-{key}",
                "embed_hash": f"embed-hash-{key}",
                "at": _PAST,
            },
        )

    def _extra_chunk(self, *, connection: Connection, key: str, suffix: str) -> None:
        """Add a second chunk to an existing reading, under the same section."""
        chunk_id = uuid4()
        self.chunk[f"{key}{suffix}"] = chunk_id
        connection.execute(
            text(
                "INSERT INTO chunks (chunk_id, deployment_id, doc_id, version_id,"
                " representation_id, section_id, ordinal, block_start, block_end,"
                " chunk_content_hash, extraction_input_hash, char_start, char_end,"
                " created_at)"
                " SELECT :chunk, :deployment, c.doc_id, c.version_id,"
                " c.representation_id, c.section_id, 1, 2, 3, :content_hash,"
                " :input_hash, 200, 400, :at"
                " FROM chunks c WHERE c.chunk_id = :source"
            ),
            {
                "chunk": chunk_id,
                "deployment": _DEPLOYMENT_ID,
                "content_hash": f"chunk-hash-{key}{suffix}",
                "input_hash": f"input-hash-{key}{suffix}",
                "at": _PAST,
                "source": self.chunk[key],
            },
        )

    def _claim(
        self,
        *,
        connection: Connection,
        key: str,
        chunk_key: str,
        current: bool = True,
        precision: str = "day",
        valid_from: datetime | None = _PAST,
        valid_until: datetime | None = _MID,
    ) -> None:
        """Create one accepted claim on a chunk with an explicit validity window."""
        claim_id = uuid4()
        self.claim[key] = claim_id
        doc_key = chunk_key.split(".", maxsplit=1)[0]
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, extractor_version, is_current_testimony,"
                " asserted_at, claim_valid_from, claim_valid_until,"
                " claim_valid_precision, claim_valid_kind, temporal_class, ingested_at)"
                " VALUES (:claim, :deployment, :doc, :chunk, :body, :body, 0, 40,"
                " true, true, 'extractor-1', :current, :asserted, :valid_from,"
                " :valid_until, CAST(:precision AS claim_valid_precision),"
                " 'proposition_validity', 'static', :ingested)"
            ),
            {
                "claim": claim_id,
                "deployment": _DEPLOYMENT_ID,
                "doc": self.doc[doc_key],
                "chunk": self.chunk[chunk_key],
                "body": f"Claim body for {key}.",
                "current": current,
                "asserted": _PAST,
                "valid_from": valid_from,
                "valid_until": valid_until,
                "precision": precision,
                "ingested": _PAST,
            },
        )

    def _seed_testimony(self, *, connection: Connection) -> None:
        """Create claims, their occurrences, and one currency transition."""
        self._claim(
            connection=connection, key="old", chunk_key="primary.v1", current=False
        )
        self._claim(connection=connection, key="a", chunk_key="primary.v2")
        self._claim(connection=connection, key="b", chunk_key="primary.v2")
        self._claim(
            connection=connection,
            key="instant",
            chunk_key="primary.v2",
            precision="instant",
            valid_from=_MID,
            valid_until=_MID,
        )
        self._claim(connection=connection, key="c", chunk_key="second.v1")
        self._claim(connection=connection, key="contra", chunk_key="second.v1")
        self._claim(connection=connection, key="forgotten", chunk_key="forgotten.v1")
        self._claim(connection=connection, key="kcited", chunk_key="kcited.v1")
        self._claim(connection=connection, key="kcited_b", chunk_key="kcited.v1b")
        self._claim(connection=connection, key="erased", chunk_key="erased.v1")

        # every claim is attached to the chunk it was extracted from, so a
        # deletion cell that must be proven on claim_occurrences_live has a row
        # to lose; claim "a" is attached twice to exercise the DISTINCT ON
        # collapse the declared key depends on
        for claim_key, chunk_key, derivation, at in (
            ("a", "primary.v2", "passthrough", _PAST),
            ("a", "primary.v2", "passthrough", _MID),
            ("b", "primary.v2", None, _PAST),
            ("instant", "primary.v2", "passthrough", _PAST),
            ("c", "second.v1", "passthrough", _PAST),
            ("contra", "second.v1", "passthrough", _PAST),
            ("old", "primary.v1", "passthrough", _ANCIENT),
            ("forgotten", "forgotten.v1", "passthrough", _PAST),
            ("kcited", "kcited.v1", "passthrough", _PAST),
            ("kcited_b", "kcited.v1b", "passthrough", _PAST),
            ("erased", "erased.v1", "passthrough", _PAST),
        ):
            connection.execute(
                text(
                    "INSERT INTO chunk_claims (deployment_id, chunk_id, claim_id,"
                    " derivation_kind, evidence_mode, source_locators, created_at)"
                    " VALUES (:deployment, :chunk, :claim, :derivation,"
                    " 'source_expression', '[]'::jsonb, :at)"
                ),
                {
                    "deployment": _DEPLOYMENT_ID,
                    "chunk": self.chunk[chunk_key],
                    "claim": self.claim[claim_key],
                    "derivation": derivation,
                    "at": at,
                },
            )

        for key, claim_key, doc_key in (
            ("reextracted", "old", "primary"),
            ("forgotten", "forgotten", "forgotten"),
            ("erased", "erased", "erased"),
        ):
            event_id = uuid4()
            self.currency_event[key] = event_id
            connection.execute(
                text(
                    "INSERT INTO testimony_currency_events (event_id, deployment_id,"
                    " claim_id, doc_id, reconciliation_id, became_current, reason,"
                    " from_extractor_version, from_version_id, occurred_at)"
                    " VALUES (:event, :deployment, :claim, :doc, :reconciliation,"
                    " false, 'reextracted', 'extractor-0', NULL, :at)"
                ),
                {
                    "event": event_id,
                    "deployment": _DEPLOYMENT_ID,
                    "claim": self.claim[claim_key],
                    "doc": self.doc[doc_key],
                    "reconciliation": uuid4(),
                    "at": _MID,
                },
            )

    def _seed_identity(self, *, connection: Connection) -> None:
        """Create mentions, their resolutions, and two merge events."""
        for key, chunk_key, entity_key, claim_key in (
            ("alice", "primary.v2", "alice_dup", "a"),
            ("acme", "primary.v2", "acme", "a"),
            ("globex", "primary.v2", "globex", "b"),
            ("initech", "primary.v2", "initech", "b"),
            ("unresolved", "primary.v2", None, None),
            ("old", "primary.v1", "alice", "old"),
            ("forgotten", "forgotten.v1", "hermit", "forgotten"),
            ("erased", "erased.v1", "ghost", "erased"),
        ):
            mention_id = uuid4()
            self.mention[key] = mention_id
            doc_key = chunk_key.split(".", maxsplit=1)[0]
            connection.execute(
                text(
                    "INSERT INTO mentions (mention_id, deployment_id, surface_form,"
                    " normalized_lemma, canonical_name_form, emitted_type, language,"
                    " claim_id, chunk_id, doc_id, char_start, char_end, created_at)"
                    " VALUES (:mention, :deployment, :surface, lower(:surface),"
                    " :surface, 'Person', 'en', :claim, :chunk, :doc, 0, 5, :at)"
                ),
                {
                    "mention": mention_id,
                    "deployment": _DEPLOYMENT_ID,
                    "surface": f"Mention {key}",
                    "claim": None if claim_key is None else self.claim[claim_key],
                    "chunk": self.chunk[chunk_key],
                    "doc": self.doc[doc_key],
                    "at": _PAST,
                },
            )
            if entity_key is None:
                continue
            decision_id = uuid4()
            self.decision[key] = decision_id
            connection.execute(
                text(
                    "INSERT INTO resolution_decisions (decision_id, deployment_id,"
                    " mention_id, entity_id, method, confidence, is_new_entity,"
                    " resolver_version, decided_at)"
                    " VALUES (:decision, :deployment, :mention, :entity, 'T0', 0.9,"
                    " false, 'resolver-1', :at)"
                ),
                {
                    "decision": decision_id,
                    "deployment": _DEPLOYMENT_ID,
                    "mention": mention_id,
                    "entity": self.entity[entity_key],
                    "at": _PAST,
                },
            )

        superseded_id = uuid4()
        self.decision["superseded"] = superseded_id
        connection.execute(
            text(
                "INSERT INTO resolution_decisions (decision_id, deployment_id,"
                " mention_id, entity_id, method, confidence, is_new_entity,"
                " resolver_version, decided_at, superseded_by)"
                " VALUES (:decision, :deployment, :mention, :entity, 'T3', 0.4, true,"
                " 'resolver-0', :at, :live)"
            ),
            {
                "decision": superseded_id,
                "deployment": _DEPLOYMENT_ID,
                "mention": self.mention["alice"],
                "entity": self.entity["alice"],
                "at": _ANCIENT,
                "live": self.decision["alice"],
            },
        )

        for key in ("merge", "unmerge"):
            merge_id = uuid4()
            self.merge[key] = merge_id
            connection.execute(
                text(
                    "INSERT INTO merge_events (merge_id, deployment_id, survivor_id,"
                    " absorbed_id, pre_merge_membership_snapshot, decided_at)"
                    " VALUES (:merge, :deployment, :survivor, :absorbed, '{}'::jsonb,"
                    " :at)"
                ),
                {
                    "merge": merge_id,
                    "deployment": _DEPLOYMENT_ID,
                    "survivor": self.entity["alice"],
                    "absorbed": self.entity["alice_dup"],
                    "at": _PAST,
                },
            )
        connection.execute(
            text("UPDATE merge_events SET reversed_by = :undo WHERE merge_id = :merge"),
            {"undo": self.merge["unmerge"], "merge": self.merge["merge"]},
        )

    def _relation(
        self,
        *,
        connection: Connection,
        key: str,
        predicate: str,
        object_key: str,
        valid_from: datetime | None,
        valid_until: datetime | None,
        ingested_at: datetime,
        invalidated_at: datetime | None = None,
        contradiction_group: UUID | None = None,
    ) -> None:
        """Create one bi-temporal relation between two survivor entities."""
        relation_id = uuid4()
        self.fact[key] = relation_id
        connection.execute(
            text(
                "INSERT INTO relations (relation_id, deployment_id, subject_entity_id,"
                " predicate, object_entity_id, valid_from, valid_until, ingested_at,"
                " invalidated_at, confidence, contradiction_group, fact_label,"
                " normalizer_version)"
                " VALUES (:relation, :deployment, :subject, :predicate, :object,"
                " :valid_from, :valid_until, :ingested, :invalidated, 0.8, :group,"
                " :label, 'normalizer-1')"
            ),
            {
                "relation": relation_id,
                "deployment": _DEPLOYMENT_ID,
                "subject": self.entity["alice"],
                "predicate": predicate,
                "object": self.entity[object_key],
                "valid_from": valid_from,
                "valid_until": valid_until,
                "ingested": ingested_at,
                "invalidated": invalidated_at,
                "group": contradiction_group,
                "label": f"Alice {predicate} {object_key}",
            },
        )

    def _evidence(
        self, *, connection: Connection, fact_key: str, claim_key: str, stance: str
    ) -> None:
        """Link one claim to one relation or observation with a stance."""
        doc_key = self._claim_lineage(claim_key=claim_key)
        table = (
            "relation_evidence" if fact_key != "observation" else "observation_evidence"
        )
        column = "relation_id" if table == "relation_evidence" else "observation_id"
        connection.execute(
            text(
                f"INSERT INTO {table} (deployment_id, {column}, claim_id, doc_id,"  # noqa: S608 -- both names come from the fixed literals above
                " stance, normalizer_version, created_at)"
                " VALUES (:deployment, :fact, :claim, :doc,"
                " CAST(:stance AS evidence_stance), 'normalizer-1', :at)"
            ),
            {
                "deployment": _DEPLOYMENT_ID,
                "fact": self.fact[fact_key],
                "claim": self.claim[claim_key],
                "doc": self.doc[doc_key],
                "stance": stance,
                "at": _PAST,
            },
        )

    def _claim_lineage(self, *, claim_key: str) -> str:
        """Return the lineage key a fixture claim was asserted by."""
        return {
            "old": "primary",
            "a": "primary",
            "b": "primary",
            "instant": "primary",
            "c": "second",
            "contra": "second",
            "forgotten": "forgotten",
            "kcited": "kcited",
            "kcited_b": "kcited",
            "erased": "erased",
        }[claim_key]

    def _seed_facts(self, *, connection: Connection) -> None:
        """Create the fact layer: current, ended, future, invalidated, contradicted."""
        self._relation(
            connection=connection,
            key="current",
            predicate="works_for",
            object_key="acme",
            valid_from=_PAST,
            valid_until=None,
            ingested_at=_PAST,
        )
        self._relation(
            connection=connection,
            key="open_ended",
            predicate="knows",
            object_key="globex",
            valid_from=None,
            valid_until=None,
            ingested_at=_PAST,
        )
        self._relation(
            connection=connection,
            key="ended",
            predicate="member_of",
            object_key="globex",
            valid_from=_PAST,
            valid_until=_ENDED,
            ingested_at=_PAST,
        )
        self._relation(
            connection=connection,
            key="future",
            predicate="reports_to",
            object_key="initech",
            valid_from=_PAST,
            valid_until=None,
            ingested_at=_FUTURE,
        )
        self._relation(
            connection=connection,
            key="invalidated",
            predicate="related_to",
            object_key="initech",
            valid_from=_PAST,
            valid_until=None,
            ingested_at=_PAST,
            invalidated_at=_MID,
        )
        self._relation(
            connection=connection,
            key="contradicted_a",
            predicate="affiliated_with",
            object_key="globex",
            valid_from=_PAST,
            valid_until=None,
            ingested_at=_PAST,
            contradiction_group=self.contradiction_group,
        )
        self._relation(
            connection=connection,
            key="contradicted_b",
            predicate="affiliated_with",
            object_key="initech",
            valid_from=_PAST,
            valid_until=None,
            ingested_at=_PAST,
            contradiction_group=self.contradiction_group,
        )
        self._relation(
            connection=connection,
            key="erased_only",
            predicate="uses",
            object_key="acme",
            valid_from=_PAST,
            valid_until=None,
            ingested_at=_PAST,
        )
        # the fact whose only source is about to be forgotten is contradicted by
        # a second one, so the lineage deletion cell can be proven on the
        # contradiction projection instead of passing there vacuously
        self._relation(
            connection=connection,
            key="lonely",
            predicate="knows_about",
            object_key="initech",
            valid_from=_PAST,
            valid_until=None,
            ingested_at=_PAST,
            contradiction_group=self.lonely_contradiction_group,
        )
        connection.execute(
            text(
                "UPDATE relations SET contradiction_group = :group"
                " WHERE relation_id = :relation"
            ),
            {
                "group": self.lonely_contradiction_group,
                "relation": self.fact["open_ended"],
            },
        )

        observation_id = uuid4()
        self.fact["observation"] = observation_id
        connection.execute(
            text(
                "INSERT INTO observations (observation_id, deployment_id,"
                " subject_entity_id, statement, valid_from, valid_until, ingested_at,"
                " confidence, obs_label, normalizer_version)"
                " VALUES (:observation, :deployment, :subject,"
                " 'Alice holds the title VP of Engineering.', :valid_from, NULL, :at,"
                " 0.7, 'Alice is VP of Engineering.', 'normalizer-1')"
            ),
            {
                "observation": observation_id,
                "deployment": _DEPLOYMENT_ID,
                "subject": self.entity["alice"],
                "valid_from": _PAST,
                "at": _PAST,
            },
        )

        for fact_key, claim_key, stance in (
            ("current", "a", "supports"),
            ("current", "b", "supports"),
            ("current", "c", "supports"),
            ("current", "contra", "contradicts"),
            ("open_ended", "a", "supports"),
            ("ended", "a", "supports"),
            ("future", "a", "supports"),
            ("invalidated", "a", "supports"),
            ("contradicted_a", "c", "supports"),
            ("contradicted_b", "c", "supports"),
            ("lonely", "forgotten", "supports"),
            ("erased_only", "erased", "supports"),
            ("current", "old", "supports"),
            ("observation", "a", "supports"),
        ):
            self._evidence(
                connection=connection,
                fact_key=fact_key,
                claim_key=claim_key,
                stance=stance,
            )

        for key, table, column, fact_key in (
            ("relation", "relation_adjudications", "relation_id", "ended"),
            (
                "observation",
                "observation_adjudications",
                "observation_id",
                "observation",
            ),
        ):
            adjudication_id = uuid4()
            self.adjudication[key] = adjudication_id
            connection.execute(
                text(
                    f"INSERT INTO {table} (adjudication_id, deployment_id, {column},"  # noqa: S608 -- both names come from the fixed literals above
                    " outcome, method, confidence, adjudicator_version, decided_at)"
                    " VALUES (:adjudication, :deployment, :fact, 'supersede', 'exact',"
                    " 0.9, 'adjudicator-1', :at)"
                ),
                {
                    "adjudication": adjudication_id,
                    "deployment": _DEPLOYMENT_ID,
                    "fact": self.fact[fact_key],
                    "at": _MID,
                },
            )

    def _seed_knowledge(self, *, connection: Connection) -> None:
        """Create cited pages, an uncited page, and a tombstoned parent.

        The uncited page is the D46 anomaly the query space is fail-closed
        about: it is active and not tombstoned, but it cites nothing, so it can
        show no provenance for its prose and is absent from `pages_live` and
        counted in the operator quarantine report instead.
        """
        for key, page_kind, status in (
            ("compiled", "compiled", "active"),
            ("authored", "authored", "active"),
            ("uncited", "authored", "active"),
            ("tombstoned", "compiled", "tombstoned"),
        ):
            artifact_id = uuid4()
            self.artifact[key] = artifact_id
            connection.execute(
                text(
                    "INSERT INTO knowledge_artifacts (artifact_id, deployment_id,"
                    " layer, page_kind, git_path, kind, page_summary, content_hash,"
                    " inputs_hash, writer_version, last_compiled_at, status)"
                    " VALUES (:artifact, :deployment, 'K1',"
                    " CAST(:page_kind AS knowledge_page_kind), :path, 'summary',"
                    " 'Compiled orientation summary.', 'content-1', 'inputs-1',"
                    " :writer, :at, CAST(:status AS knowledge_artifact_status))"
                ),
                {
                    "artifact": artifact_id,
                    "deployment": _DEPLOYMENT_ID,
                    "page_kind": page_kind,
                    "path": f"k/{key}.md",
                    "writer": "writer-1" if page_kind == "compiled" else None,
                    "at": _MID,
                    "status": status,
                },
            )
        connection.execute(
            text(
                "UPDATE knowledge_artifacts SET parent_artifact_id = :parent"
                " WHERE artifact_id = :artifact"
            ),
            {
                "parent": self.artifact["tombstoned"],
                "artifact": self.artifact["authored"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_refresh_queue (refresh_id, deployment_id,"
                " artifact_id, trigger, payload, status, enqueued_at)"
                " VALUES (:refresh, :deployment, :artifact, 'authored_review',"
                " '{\"redaction_required\": true}'::jsonb, 'pending', :at)"
            ),
            {
                "refresh": uuid4(),
                "deployment": _DEPLOYMENT_ID,
                "artifact": self.artifact["authored"],
                "at": _MID,
            },
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_compilations (compilation_id, deployment_id,"
                " artifact_id, inputs_hash, candidate_count, cited_count,"
                " uncited_count, writer_version, compiled_at)"
                " VALUES (:compilation, :deployment, :artifact, 'inputs-1', 3, 2, 1,"
                " 'writer-1', :at)"
            ),
            {
                "compilation": self.compilation_id,
                "deployment": _DEPLOYMENT_ID,
                "artifact": self.artifact["compiled"],
                "at": _MID,
            },
        )
        for artifact_key, lineage, chunk_hash, relation_key, role in (
            ("compiled", "kcited", "chunk-hash-kcited.v1", None, "supports"),
            ("compiled", "kcited", "chunk-hash-kcited.v1b", None, "supports"),
            ("compiled", None, None, "current", "cites"),
            ("compiled", "forgotten", None, None, "cites"),
            ("compiled", "erased", None, None, "cites"),
            # the authored page cites one live lineage, so it is published and
            # its review flags are exercised; the uncited page cites nothing
            ("authored", "primary", None, None, "cites"),
        ):
            connection.execute(
                text(
                    "INSERT INTO knowledge_artifact_evidence (evidence_link_id,"
                    " deployment_id, artifact_id, claim_lineage_id,"
                    " claim_chunk_content_hash, relation_id, doc_id, role)"
                    " VALUES (:link, :deployment, :artifact, :lineage, :hash,"
                    " :relation, :doc, CAST(:role AS knowledge_evidence_role))"
                ),
                {
                    "link": uuid4(),
                    "deployment": _DEPLOYMENT_ID,
                    "artifact": self.artifact[artifact_key],
                    "lineage": None if chunk_hash is None else self.doc[str(lineage)],
                    "hash": chunk_hash,
                    "relation": (
                        None if relation_key is None else self.fact[relation_key]
                    ),
                    "doc": (
                        self.doc[str(lineage)]
                        if chunk_hash is None and lineage is not None
                        else None
                    ),
                    "role": role,
                },
            )


@pytest.fixture(scope="module")
def corpus(database_engine: Engine) -> _Corpus:
    """Bootstrap one deployment and seed the invariant-exercising corpus."""
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="query-space-batch-a",
            name="Query space Batch A",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    return _Corpus(engine=database_engine)


def _rows(
    *, connection: Connection, sql: str, **parameters: Any
) -> list[Mapping[str, Any]]:
    """Run one read and return its rows as mappings."""
    return [
        dict(row)
        for row in connection.execute(text(sql), parameters or None).mappings().all()
    ]


def _scalar(*, connection: Connection, sql: str, **parameters: Any) -> Any:
    """Run one read and return its single value."""
    return connection.execute(text(sql), parameters or None).scalar_one()


def _fixture_cases(corpus: _Corpus) -> dict[str, tuple[str, dict[str, Any]]]:
    """Map each manifest fixture case to a boolean query that proves it.

    Every case is a single boolean read, so a positive fixture ("this row must
    appear") and a negative one ("this row must not") are executed by the same
    harness and neither can quietly become prose.
    """
    schema = "memory_v1"
    return {
        "documents_live.live_lineage_present": (
            f"SELECT EXISTS (SELECT 1 FROM {schema}.documents_live"
            " WHERE doc_id = :doc AND has_current_ready_content)",
            {"doc": corpus.doc["primary"]},
        ),
        "documents_live.tombstoned_lineage_absent": (
            f"SELECT NOT EXISTS (SELECT 1 FROM {schema}.documents_live"
            " WHERE doc_id = :doc)",
            {"doc": corpus.doc["erased"]},
        ),
        "document_versions_visible.live_version_present": (
            f"SELECT EXISTS (SELECT 1 FROM {schema}.document_versions_visible"
            " WHERE version_id = :version AND NOT is_current_version)",
            {"version": corpus.version["primary.v1"]},
        ),
        "document_versions_visible.tombstoned_version_absent": (
            f"SELECT NOT EXISTS (SELECT 1 FROM {schema}.document_versions_visible"
            " WHERE version_id = :version)",
            {"version": corpus.version["primary.vdel"]},
        ),
        "sections_live.current_representation_section_present": (
            f"SELECT EXISTS (SELECT 1 FROM {schema}.sections_live"
            " WHERE section_id = :section)",
            {"section": corpus.section["primary.v2"]},
        ),
        "sections_live.superseded_generation_section_absent": (
            f"SELECT NOT EXISTS (SELECT 1 FROM {schema}.sections_live"
            " WHERE section_id = :section)",
            {"section": corpus.section["primary.v2.superseded"]},
        ),
        "chunks_live.current_representation_chunk_present": (
            f"SELECT EXISTS (SELECT 1 FROM {schema}.chunks_live"
            " WHERE chunk_id = :chunk AND section_id IS NOT NULL)",
            {"chunk": corpus.chunk["primary.v2"]},
        ),
        "chunks_live.superseded_version_chunk_absent": (
            f"SELECT NOT EXISTS (SELECT 1 FROM {schema}.chunks_live"
            " WHERE chunk_id = :chunk)",
            {"chunk": corpus.chunk["primary.v1"]},
        ),
        "claims_visible_history.superseded_testimony_present": (
            f"SELECT EXISTS (SELECT 1 FROM {schema}.claims_visible_history"
            " WHERE claim_id = :claim AND NOT is_current_testimony)",
            {"claim": corpus.claim["old"]},
        ),
        "claims_visible_history.forgotten_lineage_claim_absent": (
            f"SELECT NOT EXISTS (SELECT 1 FROM {schema}.claims_visible_history"
            " WHERE claim_id = :claim)",
            {"claim": corpus.claim["erased"]},
        ),
        "claims_live.current_testimony_present": (
            f"SELECT EXISTS (SELECT 1 FROM {schema}.claims_live"
            " WHERE claim_id = :claim)",
            {"claim": corpus.claim["a"]},
        ),
        "claims_live.superseded_testimony_absent": (
            f"SELECT NOT EXISTS (SELECT 1 FROM {schema}.claims_live"
            " WHERE claim_id = :claim)",
            {"claim": corpus.claim["old"]},
        ),
        "claim_occurrences_live.current_chunk_occurrence_present": (
            f"SELECT EXISTS (SELECT 1 FROM {schema}.claim_occurrences_live"
            " WHERE claim_id = :claim AND chunk_id = :chunk"
            " AND derivation_kind = 'passthrough')",
            {"claim": corpus.claim["a"], "chunk": corpus.chunk["primary.v2"]},
        ),
        "claim_occurrences_live.repeated_attachment_collapsed": (
            f"SELECT count(*) = 1 FROM {schema}.claim_occurrences_live"
            " WHERE claim_id = :claim AND chunk_id = :chunk"
            " AND derivation_kind = 'passthrough'",
            {"claim": corpus.claim["a"], "chunk": corpus.chunk["primary.v2"]},
        ),
        "testimony_currency_events_visible.reextraction_transition_present": (
            f"SELECT EXISTS (SELECT 1 FROM {schema}.testimony_currency_events_visible"
            " WHERE event_id = :event AND reason = 'reextracted'"
            " AND NOT became_current)",
            {"event": corpus.currency_event["reextracted"]},
        ),
        "testimony_currency_events_visible.forgotten_lineage_transition_absent": (
            f"SELECT NOT EXISTS (SELECT 1 FROM"
            f" {schema}.testimony_currency_events_visible WHERE event_id = :event)",
            {"event": corpus.currency_event["erased"]},
        ),
        # the lineage has two mentions of this survivor, one in the current
        # version and one in a superseded version; only current content counts
        "entity_document_mentions.exact_current_content_count_present": (
            f"SELECT mention_count = 1 FROM {schema}.entity_document_mentions"
            " WHERE entity_id = :entity AND doc_id = :doc",
            {"entity": corpus.entity["alice"], "doc": corpus.doc["primary"]},
        ),
        "entity_document_mentions.forgotten_lineage_pair_absent": (
            f"SELECT NOT EXISTS (SELECT 1 FROM {schema}.entity_document_mentions"
            " WHERE doc_id = :doc)",
            {"doc": corpus.doc["erased"]},
        ),
        "entities_current.survivor_with_live_provenance_present": (
            f"SELECT EXISTS (SELECT 1 FROM {schema}.entities_current"
            " WHERE entity_id = :entity AND live_document_count > 0)",
            {"entity": corpus.entity["alice"]},
        ),
        "entities_current.merged_entity_absent": (
            f"SELECT NOT EXISTS (SELECT 1 FROM {schema}.entities_current"
            " WHERE entity_id = :entity)",
            {"entity": corpus.entity["alice_dup"]},
        ),
        "entity_aliases_current.alias_redirected_to_survivor": (
            f"SELECT EXISTS (SELECT 1 FROM {schema}.entity_aliases_current"
            " WHERE alias_id = :alias AND source_entity_id = :absorbed"
            " AND entity_id = :survivor)",
            {
                "alias": corpus.alias["alice_dup"],
                "absorbed": corpus.entity["alice_dup"],
                "survivor": corpus.entity["alice"],
            },
        ),
        "entity_aliases_current.forgotten_entity_alias_absent": (
            f"SELECT NOT EXISTS (SELECT 1 FROM {schema}.entity_aliases_current"
            " WHERE alias_id = :alias)",
            {"alias": corpus.alias["ghost"]},
        ),
        "mentions_live.unresolved_mention_present": (
            f"SELECT EXISTS (SELECT 1 FROM {schema}.mentions_live"
            " WHERE mention_id = :mention AND resolved_entity_id IS NULL)",
            {"mention": corpus.mention["unresolved"]},
        ),
        "mentions_live.superseded_version_mention_absent": (
            f"SELECT NOT EXISTS (SELECT 1 FROM {schema}.mentions_live"
            " WHERE mention_id = :mention)",
            {"mention": corpus.mention["old"]},
        ),
        "identity_events_visible.resolution_and_merge_arms_present": (
            f"SELECT (SELECT count(DISTINCT object_kind) FROM"
            f" {schema}.identity_events_visible WHERE event_id IN"
            " (:decision, :merge)) = 2",
            {"decision": corpus.decision["alice"], "merge": corpus.merge["merge"]},
        ),
        "identity_events_visible.forgotten_source_event_absent": (
            f"SELECT NOT EXISTS (SELECT 1 FROM {schema}.identity_events_visible"
            " WHERE event_id = :decision)",
            {"decision": corpus.decision["erased"]},
        ),
        "fact_claim_evidence_live.both_stances_present": (
            f"SELECT (SELECT count(DISTINCT stance) FROM"
            f" {schema}.fact_claim_evidence_live WHERE fact_id = :fact) = 2",
            {"fact": corpus.fact["current"]},
        ),
        "fact_claim_evidence_live.superseded_testimony_absent": (
            f"SELECT NOT EXISTS (SELECT 1 FROM {schema}.fact_claim_evidence_live"
            " WHERE claim_id = :claim)",
            {"claim": corpus.claim["old"]},
        ),
        "evidence_lineage.repetition_does_not_add_a_lineage": (
            f"SELECT count(*) = 2 FROM {schema}.evidence_lineage"
            " WHERE fact_id = :fact AND stance = 'supports'",
            {"fact": corpus.fact["current"]},
        ),
        "evidence_lineage.forgotten_lineage_evidence_absent": (
            f"SELECT NOT EXISTS (SELECT 1 FROM {schema}.evidence_lineage"
            " WHERE doc_id = :doc)",
            {"doc": corpus.doc["erased"]},
        ),
        "facts_visible_history.invalidated_fact_present": (
            f"SELECT EXISTS (SELECT 1 FROM {schema}.facts_visible_history"
            " WHERE fact_id = :fact AND invalidated_at IS NOT NULL)",
            {"fact": corpus.fact["invalidated"]},
        ),
        "facts_visible_history.forgotten_provenance_fact_absent": (
            f"SELECT NOT EXISTS (SELECT 1 FROM {schema}.facts_visible_history"
            " WHERE fact_id = :fact)",
            {"fact": corpus.fact["erased_only"]},
        ),
        "facts_current.open_window_fact_present": (
            f"SELECT EXISTS (SELECT 1 FROM {schema}.facts_current"
            " WHERE fact_id = :fact AND valid_from IS NULL AND valid_until IS NULL)",
            {"fact": corpus.fact["open_ended"]},
        ),
        "facts_current.ended_window_fact_absent": (
            f"SELECT NOT EXISTS (SELECT 1 FROM {schema}.facts_current"
            " WHERE fact_id = :fact)",
            {"fact": corpus.fact["ended"]},
        ),
        "contradiction_members_current.both_sides_present": (
            f"SELECT count(*) = 2 FROM {schema}.contradiction_members_current"
            " WHERE contradiction_group = :group",
            {"group": corpus.contradiction_group},
        ),
        "contradiction_members_current.ungrouped_fact_absent": (
            f"SELECT NOT EXISTS (SELECT 1 FROM"
            f" {schema}.contradiction_members_current WHERE fact_id = :fact)",
            {"fact": corpus.fact["current"]},
        ),
        "graph_edges_current.survivor_endpoints_present": (
            f"SELECT EXISTS (SELECT 1 FROM {schema}.graph_edges_current"
            " WHERE relation_id = :relation AND subject_entity_id = :subject)",
            {"relation": corpus.fact["current"], "subject": corpus.entity["alice"]},
        ),
        "graph_edges_current.observation_never_projects": (
            f"SELECT NOT EXISTS (SELECT 1 FROM {schema}.graph_edges_current"
            " WHERE relation_id = :observation)",
            {"observation": corpus.fact["observation"]},
        ),
        "graph_edges_visible_history.invalidated_edge_present": (
            f"SELECT EXISTS (SELECT 1 FROM {schema}.graph_edges_visible_history"
            " WHERE relation_id = :relation AND invalidated_at IS NOT NULL)",
            {"relation": corpus.fact["invalidated"]},
        ),
        "graph_edges_visible_history.forgotten_provenance_edge_absent": (
            f"SELECT NOT EXISTS (SELECT 1 FROM"
            f" {schema}.graph_edges_visible_history WHERE relation_id = :relation)",
            {"relation": corpus.fact["erased_only"]},
        ),
        "document_crossrefs_live.both_endpoints_live_present": (
            f"SELECT EXISTS (SELECT 1 FROM {schema}.document_crossrefs_live"
            " WHERE crossref_id = :crossref)",
            {"crossref": corpus.crossref["resolved"]},
        ),
        "document_crossrefs_live.forgotten_target_absent": (
            f"SELECT NOT EXISTS (SELECT 1 FROM {schema}.document_crossrefs_live"
            " WHERE crossref_id = :crossref)",
            {"crossref": corpus.crossref["unresolved"]},
        ),
        "pages_live.compiled_page_present": (
            f"SELECT EXISTS (SELECT 1 FROM {schema}.pages_live"
            " WHERE artifact_id = :artifact AND page_kind = 'compiled'"
            " AND NOT is_stale)",
            {"artifact": corpus.artifact["compiled"]},
        ),
        # a page is published only while it is not tombstoned AND still cites
        # something visible; both omissions are proven by one read
        "pages_live.tombstoned_and_uncited_pages_absent": (
            f"SELECT NOT EXISTS (SELECT 1 FROM {schema}.pages_live"
            " WHERE artifact_id IN (:tombstoned, :uncited))",
            {
                "tombstoned": corpus.artifact["tombstoned"],
                "uncited": corpus.artifact["uncited"],
            },
        ),
        "page_evidence_visible.claim_coordinate_link_present": (
            f"SELECT EXISTS (SELECT 1 FROM {schema}.page_evidence_visible"
            " WHERE artifact_id = :artifact AND target_kind = 'claim'"
            " AND target_id = :doc AND link_count = 2"
            " AND array_length(claim_chunk_content_hashes, 1) = 2)",
            {"artifact": corpus.artifact["compiled"], "doc": corpus.doc["kcited"]},
        ),
        "page_evidence_visible.forgotten_target_link_absent": (
            f"SELECT NOT EXISTS (SELECT 1 FROM {schema}.page_evidence_visible"
            " WHERE target_id = :doc)",
            {"doc": corpus.doc["erased"]},
        ),
        "changes_visible.fact_ingest_event_present": (
            f"SELECT EXISTS (SELECT 1 FROM {schema}.changes_visible"
            " WHERE object_kind = 'relation_ingest' AND object_id = :fact)",
            {"fact": corpus.fact["current"]},
        ),
        "changes_visible.forgotten_lineage_change_absent": (
            f"SELECT NOT EXISTS (SELECT 1 FROM {schema}.changes_visible"
            " WHERE object_id = :claim)",
            {"claim": corpus.claim["erased"]},
        ),
    }


# ── §9.1 DDL / manifest identity ─────────────────────────────────────────


def test_live_introspection_equals_the_checked_in_manifest(corpus: _Corpus) -> None:
    """What the running database exposes is exactly what the manifest publishes.

    The two sides of this comparison come from different places on purpose: the
    manifest is built from the authored DDL and the declared contract, and the
    live side is read from `pg_catalog` alone. A declared column type or comment
    that the database does not agree with fails here rather than being compared
    with itself.
    """
    with corpus.engine.connect() as connection:
        differences = live_schema_differences(connection=connection)
        live = introspect_live_schema(connection)

    assert differences == ()
    assert live.postgresql_major == POSTGRESQL_MAJOR
    assert len(live.views) == len(VIEW_CONTRACTS)


def test_the_checked_in_manifest_is_the_generated_one(corpus: _Corpus) -> None:
    """The published file is the generator's output, hash and rendering alike."""
    generated = build_manifest()
    checked_in = load_manifest()
    assert generated["hash_members"] == checked_in["hash_members"]
    assert generated["surface_manifest_hash"] == checked_in["surface_manifest_hash"]
    assert MANIFEST_PATH.read_text(encoding="utf-8") == render_manifest(generated)


def test_two_independent_builds_deploy_the_same_schema(database_url: str) -> None:
    """Two separate databases, built from scratch, deploy one identical surface.

    Each build gets its own database and its own migration run, so nothing is
    shared but the repository: the object identifiers, the creation order, and
    the timing all differ. What must not differ is a single column, type, or
    comment — and each build must also equal the checked-in manifest, which is
    what makes "two independent builds agree" a statement about the contract
    rather than about one machine.
    """
    first = _build_in_scratch_database(
        database_url=database_url, name="rememberstack_qs_build_a"
    )
    second = _build_in_scratch_database(
        database_url=database_url, name="rememberstack_qs_build_b"
    )

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    manifest = load_manifest()
    views = manifest["hash_members"]["views_schema"]["views"]
    published = {
        str(view["name"]): [
            (str(column["name"]), str(column["type"]), str(column["comment"]))
            for column in view["columns"]
        ]
        for view in views
    }
    deployed = {
        str(view["name"]): [
            (str(column["name"]), str(column["type"]), str(column["comment"]))
            for column in view["columns"]
        ]
        for view in first["views"]
    }
    assert deployed == published


def test_query_space_exposes_no_undocumented_grants(corpus: _Corpus) -> None:
    """Only the bound roles hold privileges, and only the bound ones.

    Batch B introduces the role split (design §4.2 as amended: physical
    routing plus grants, no row-level security), so "no grants at all" is no
    longer the property to assert — "no grant beyond the enumerated ones" is.
    The query role reads the public views and nothing else; PUBLIC holds
    nothing anywhere.
    """
    # The query login is per deployment (Batch B): its name carries the
    # database, so the gate matches the prefix rather than a fixed name.
    allowed_grantees = {"rememberstack_view_owner"}
    query_role_prefix = "rememberstack_query"
    with corpus.engine.connect() as connection:
        view_grants = _rows(
            connection=connection,
            sql=(
                "SELECT grantee, privilege_type FROM"
                " information_schema.role_table_grants"
                " WHERE table_schema = 'memory_v1'"
                " GROUP BY grantee, privilege_type"
            ),
        )
        public_grants = _rows(
            connection=connection,
            sql=(
                "SELECT table_name, privilege_type FROM"
                " information_schema.role_table_grants"
                " WHERE table_schema = 'memory_v1' AND grantee = 'PUBLIC'"
            ),
        )

    for row in view_grants:
        grantee = row["grantee"]
        is_query_role = grantee.startswith(query_role_prefix)
        assert grantee in allowed_grantees or is_query_role, (
            f"unexpected grantee {grantee}"
        )
        if is_query_role:
            assert row["privilege_type"] == "SELECT", (
                "query role holds more than SELECT"
            )
    assert public_grants == []


def test_every_declared_row_key_is_unique_on_the_fixture_corpus(
    corpus: _Corpus,
) -> None:
    """A declared key really identifies one row, null key parts included."""
    with corpus.engine.connect() as connection:
        for contract in VIEW_CONTRACTS:
            key = ", ".join(contract.row_key)
            duplicates = _scalar(
                connection=connection,
                sql=(
                    "SELECT count(*) FROM (SELECT 1 FROM"  # noqa: S608 -- names come from the checked-in contract
                    f" memory_v1.{contract.name} GROUP BY {key}"
                    " HAVING count(*) > 1) AS duplicated"
                ),
            )
            assert duplicates == 0, contract.name


def test_no_column_declared_non_null_is_ever_null(corpus: _Corpus) -> None:
    """Nullability is declared, so it is proven by execution rather than trusted."""
    with corpus.engine.connect() as connection:
        for contract in VIEW_CONTRACTS:
            populated = _scalar(
                connection=connection,
                sql=f"SELECT count(*) FROM memory_v1.{contract.name}",  # noqa: S608 -- names come from the checked-in contract
            )
            assert populated > 0, f"{contract.name} has no fixture rows to prove on"
            for column in sorted(contract.not_null):
                nulls = _scalar(
                    connection=connection,
                    sql=(
                        f"SELECT count(*) FROM memory_v1.{contract.name}"  # noqa: S608 -- names come from the checked-in contract
                        f" WHERE {column} IS NULL"
                    ),
                )
                assert nulls == 0, f"{contract.name}.{column}"


def test_every_bound_vocabulary_covers_the_values_that_occur(corpus: _Corpus) -> None:
    """A text column bound to a vocabulary never emits a value outside it."""
    with corpus.engine.connect() as connection:
        for contract in VIEW_CONTRACTS:
            for column, vocabulary in sorted(contract.enum_values.items()):
                observed = {
                    str(row[column])
                    for row in _rows(
                        connection=connection,
                        sql=(
                            f"SELECT DISTINCT {column} FROM memory_v1.{contract.name}"  # noqa: S608 -- names come from the checked-in contract
                            f" WHERE {column} IS NOT NULL"
                        ),
                    )
                }
                assert observed <= set(vocabulary), f"{contract.name}.{column}"


def test_every_view_and_column_comment_is_a_complete_sentence(corpus: _Corpus) -> None:
    """Discovery text is prose a stranger can read, not a fragment."""
    manifest = load_manifest()
    views_schema = manifest["hash_members"]["views_schema"]
    assert isinstance(views_schema, dict)
    views = views_schema["views"]
    assert isinstance(views, list)
    for view in views:
        assert isinstance(view, dict)
        comment = str(view["comment"])
        assert comment[0].isupper() and comment.endswith(".")
        assert len(comment) > 200, view["name"]
        columns = view["columns"]
        assert isinstance(columns, list)
        for column in columns:
            assert isinstance(column, dict)
            text_value = str(column["comment"])
            assert text_value[0].isupper(), f"{view['name']}.{column['name']}"
            assert text_value.endswith("."), f"{view['name']}.{column['name']}"


def test_declared_join_keys_resolve_inside_the_query_space(corpus: _Corpus) -> None:
    """Every documented join path names real columns on a real public relation."""
    manifest = load_manifest()
    views_schema = manifest["hash_members"]["views_schema"]
    assert isinstance(views_schema, dict)
    views = views_schema["views"]
    assert isinstance(views, list)
    columns_by_view = {
        str(view["name"]): {
            str(column["name"])
            for column in view["columns"]
            if isinstance(column, dict)
        }
        for view in views
        if isinstance(view, dict)
    }
    for contract in VIEW_CONTRACTS:
        for join in contract.join_keys:
            schema, _, target = join.target.partition(".")
            assert schema == "memory_v1"
            assert target in columns_by_view, join.target
            assert set(join.columns) <= columns_by_view[contract.name], contract.name


def test_every_manifest_fixture_case_is_executed_and_passes(corpus: _Corpus) -> None:
    """The declared positive and negative fixtures are proofs, not prose."""
    cases = _fixture_cases(corpus)
    declared = {
        fixture
        for contract in VIEW_CONTRACTS
        for fixture in (contract.positive_fixture, contract.negative_fixture)
    }
    assert set(cases) == declared
    assert len(declared) == 2 * len(VIEW_CONTRACTS)

    with corpus.engine.connect() as connection:
        for case_id, (sql, parameters) in sorted(cases.items()):
            assert _scalar(connection=connection, sql=sql, **parameters) is True, (
                case_id
            )


# ── §9.2 D48 deletion matrix ─────────────────────────────────────────────


def _forbidden_identifiers(*, corpus: _Corpus, target_id: str) -> set[str]:
    """Collect every identifier a deletion target must remove from every view."""
    sets: dict[str, tuple[object, ...]] = {
        "lineage": (
            corpus.doc["forgotten"],
            corpus.version["forgotten.v1"],
            corpus.representation["forgotten.v1"],
            corpus.generation["forgotten.v1"],
            corpus.section["forgotten.v1"],
            corpus.chunk["forgotten.v1"],
            corpus.claim["forgotten"],
            corpus.mention["forgotten"],
            corpus.decision["forgotten"],
            corpus.currency_event["forgotten"],
            corpus.crossref["from_forgotten"],
            corpus.fact["lonely"],
            corpus.entity["hermit"],
            corpus.alias["hermit"],
        ),
        "version": (
            corpus.version["primary.v1"],
            corpus.representation["primary.v1"],
            corpus.generation["primary.v1"],
            corpus.section["primary.v1"],
            corpus.chunk["primary.v1"],
            corpus.claim["old"],
            corpus.mention["old"],
            corpus.currency_event["reextracted"],
        ),
        "representation": (
            corpus.representation["repcase.v1"],
            corpus.generation["repcase.v1"],
            corpus.section["repcase.v1"],
            corpus.chunk["repcase.v1"],
        ),
        "claim": (corpus.claim["a"],),
        "fact_provenance": (corpus.fact["current"],),
        "p2_edge": (corpus.fact["current"],),
        "k_target": (
            corpus.doc["kcited"],
            corpus.version["kcited.v1"],
            corpus.representation["kcited.v1"],
            corpus.generation["kcited.v1"],
            corpus.section["kcited.v1"],
            corpus.chunk["kcited.v1"],
            corpus.chunk["kcited.v1b"],
            corpus.claim["kcited"],
            corpus.claim["kcited_b"],
            "chunk-hash-kcited.v1",
            "chunk-hash-kcited.v1b",
        ),
    }
    return {str(value) for value in sets[target_id]}


def _apply_deletion(*, connection: Connection, corpus: _Corpus, target_id: str) -> None:
    """Apply one enumerated deletion inside the caller's transaction."""
    if target_id in {"lineage", "k_target"}:
        doc_key = "forgotten" if target_id == "lineage" else "kcited"
        connection.execute(
            text("UPDATE documents SET deleted_at = now() WHERE doc_id = :doc"),
            {"doc": corpus.doc[doc_key]},
        )
        return
    if target_id == "version":
        connection.execute(
            text(
                "UPDATE document_versions SET deleted_at = now()"
                " WHERE version_id = :version"
            ),
            {"version": corpus.version["primary.v1"]},
        )
        return
    if target_id == "representation":
        replacement = uuid4()
        generation = uuid4()
        connection.execute(
            text(
                "INSERT INTO document_representations (representation_id,"
                " deployment_id, version_id, route, status)"
                " VALUES (:rep, :deployment, :version, 'passthrough', 'ready')"
            ),
            {
                "rep": replacement,
                "deployment": _DEPLOYMENT_ID,
                "version": corpus.version["repcase.v1"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_structure_generations (structure_generation_id,"
                " deployment_id, doc_id, version_id, representation_id,"
                " skeleton_version, skeleton_hash, skeleton_producer_family,"
                " route_tag, candidate_skeleton_hash, stats_version, stats)"
                " VALUES (:generation, :deployment, :doc, :version, :rep,"
                " 'skeleton-2', 'skeleton-hash-2', 'parser', 'parser',"
                " 'skeleton-hash-2', 'v1', '{}'::jsonb)"
            ),
            {
                "generation": generation,
                "deployment": _DEPLOYMENT_ID,
                "doc": corpus.doc["repcase"],
                "version": corpus.version["repcase.v1"],
                "rep": replacement,
            },
        )
        connection.execute(
            text(
                "UPDATE document_representations"
                " SET current_structure_generation_id = :generation"
                " WHERE representation_id = :rep"
            ),
            {"generation": generation, "rep": replacement},
        )
        connection.execute(
            text(
                "UPDATE document_versions SET current_representation_id = :rep"
                " WHERE version_id = :version"
            ),
            {"rep": replacement, "version": corpus.version["repcase.v1"]},
        )
        return
    if target_id == "claim":
        connection.execute(
            text("DELETE FROM chunk_claims WHERE claim_id = :claim"),
            {"claim": corpus.claim["a"]},
        )
        connection.execute(
            text("DELETE FROM claims WHERE claim_id = :claim"),
            {"claim": corpus.claim["a"]},
        )
        return
    if target_id in {"fact_provenance", "p2_edge"}:
        connection.execute(
            text("DELETE FROM relation_evidence WHERE relation_id = :relation"),
            {"relation": corpus.fact["current"]},
        )
        return
    raise AssertionError(f"unenumerated deletion target {target_id!r}")


def _reachable_values(
    *, connection: Connection, relation: str, forbidden: set[str]
) -> set[str]:
    """Return every forbidden identifier reachable through one relation.

    The relation is named in full, because the matrix crosses every deletion
    target with the private helper as well as with the public relations.
    """
    reachable: set[str] = set()
    for row in _rows(connection=connection, sql=f"SELECT * FROM {relation}"):  # noqa: S608 -- names come from the checked-in matrix
        for value in row.values():
            if isinstance(value, list):
                reachable |= {str(item) for item in value} & forbidden
            elif value is not None and str(value) in forbidden:
                reachable.add(str(value))
    return reachable


def _matrix_cells() -> Mapping[tuple[str, str], Mapping[str, Any]]:
    """Index the checked-in matrix by its (target, surface) coordinate."""
    cells = load_matrix()["cells"]
    assert isinstance(cells, list)
    indexed = {
        (str(cell["target_id"]), str(cell["surface"])): cell
        for cell in cells
        if isinstance(cell, dict)
    }
    assert len(indexed) == len(cells), "the matrix repeats a coordinate"
    return indexed


def test_d48_deletion_matrix_proves_every_executed_cell(corpus: _Corpus) -> None:
    """Every executed cell proves its own status: no target, no cell, is vacuous.

    An `applicable` cell must be reachable *before* its mutation and empty
    after; a `not_applicable` cell must be empty on both sides, which is what
    turns "this relation cannot carry that identifier" from a claim into a
    check; a `not_caller_reachable` cell must name a relation that is outside
    the query space and carries no grant at all.
    """
    cells = _matrix_cells()
    executed: set[tuple[str, str]] = set()

    for target in EXECUTED_TARGETS:
        forbidden = _forbidden_identifiers(corpus=corpus, target_id=target.target_id)
        assert forbidden, target.target_id
        with corpus.engine.begin() as connection:
            before = {
                surface.name: _reachable_values(
                    connection=connection, relation=surface.name, forbidden=forbidden
                )
                for surface in MATRIX_SURFACES
            }
            _apply_deletion(
                connection=connection, corpus=corpus, target_id=target.target_id
            )
            for surface in MATRIX_SURFACES:
                cell = cells[(target.target_id, surface.name)]
                after = _reachable_values(
                    connection=connection, relation=surface.name, forbidden=forbidden
                )
                coordinate = f"{target.target_id} -> {surface.name}"
                if cell["status"] == "applicable":
                    assert before[surface.name], f"{coordinate} is vacuous"
                    assert after == set(), f"{coordinate} leaks {sorted(after)}"
                elif cell["basis"] == "no_identifier_of_this_class":
                    assert before[surface.name] == set(), (
                        f"{coordinate} is declared not applicable but reachable"
                    )
                    assert after == set(), coordinate
                else:
                    assert cell["basis"] == "not_caller_reachable", coordinate
                    assert not surface.caller_reachable, coordinate
                executed.add((target.target_id, surface.name))
            connection.rollback()

    assert executed == {
        coordinate for coordinate, cell in cells.items() if cell["status"] != "deferred"
    }


def test_the_private_helper_cells_prove_their_own_non_reachability(
    corpus: _Corpus,
) -> None:
    """A `not_caller_reachable` cell names a relation a caller cannot read.

    Every private helper is checked, not only the one the matrix crosses with
        each target: all seven carry rules the public relations depend on, and all
    seven would be a way around those rules if a grant ever appeared on them.
    """
    helpers = {
        surface.name for surface in MATRIX_SURFACES if not surface.caller_reachable
    } | {f"public.{name}" for name in AUTHORIZATION_HELPER_VIEWS}
    assert len(helpers) == len(AUTHORIZATION_HELPER_VIEWS)
    with corpus.engine.connect() as connection:
        for helper in sorted(helpers):
            schema, _, relation = helper.partition(".")
            assert schema != "memory_v1"
            # A REVOKE materializes a relation's default ACL, so the honest
            # property after Batch B's role split is "no grantee other than
            # the owner", not "relacl is NULL".
            foreign_grants = _rows(
                connection=connection,
                sql=(
                    "SELECT grantee, privilege_type FROM"
                    " information_schema.role_table_grants"
                    " WHERE table_schema = :schema AND table_name = :relation"
                    " AND grantee <> 'rememberstack_view_owner'"
                ),
                schema=schema,
                relation=relation,
            )
            # The owner's own implicit privileges are not a grant to anyone
            # else; every other grantee — including PUBLIC and the query role
            # — must be absent.
            assert foreign_grants == [], f"{helper} is granted to {foreign_grants}"


def test_deferred_matrix_cells_name_the_batch_that_will_execute_them(
    corpus: _Corpus,
) -> None:
    """A target this batch cannot build is recorded, not silently omitted."""
    cells = _matrix_cells()
    deferred = {target.target_id for target in DELETION_TARGETS if target.deferred}
    assert deferred == {"p1_candidate", "corpus_body"}
    for (target_id, _surface), cell in cells.items():
        if target_id in deferred:
            assert cell["status"] == "deferred"
            assert "Deferred to Batch " in str(cell["expectation"])
    for target in DELETION_TARGETS:
        if target.deferred:
            assert target.executed_in in {"C", "D"}, target.target_id


def test_a_path_with_one_invalid_edge_returns_no_partial_row(corpus: _Corpus) -> None:
    """A composed evidence path drops as a unit when any hop stops being visible."""
    audit = (
        "SELECT count(*) FROM memory_v1.facts_current AS f"
        " JOIN memory_v1.fact_claim_evidence_live AS e"
        " USING (deployment_id, fact_kind, fact_id)"
        " JOIN memory_v1.claims_live AS c USING (deployment_id, claim_id)"
        " JOIN memory_v1.documents_live AS d"
        " ON d.deployment_id = c.deployment_id AND d.doc_id = c.doc_id"
        " WHERE f.fact_id = :fact AND d.doc_id = :doc"
    )
    with corpus.engine.begin() as connection:
        before = _scalar(
            connection=connection,
            sql=audit,
            fact=corpus.fact["current"],
            doc=corpus.doc["second"],
        )
        assert before > 0
        connection.execute(
            text("UPDATE documents SET deleted_at = now() WHERE doc_id = :doc"),
            {"doc": corpus.doc["second"]},
        )
        after = _scalar(
            connection=connection,
            sql=audit,
            fact=corpus.fact["current"],
            doc=corpus.doc["second"],
        )
        connection.rollback()

    assert after == 0


# ── D48 coordinate binding and fail-closed resolution ────────────────────

_MENTION_ROW = (
    "SELECT doc_id, resolved_entity_id, resolution_method, resolution_confidence,"
    " resolution_is_new_entity, resolved_at FROM memory_v1.mentions_live"
    " WHERE mention_id = :mention"
)


def test_a_mention_cannot_borrow_a_live_lineage_from_its_chunk(corpus: _Corpus) -> None:
    """Repointing a mention's own lineage at a tombstone removes the mention.

    The mention's association names two coordinates — a chunk and a lineage —
    and both are authorized. Without the lineage half, a row whose recorded
    lineage has been forgotten would still be published under the *chunk's*
    live lineage, which is precisely the identifier the forget was supposed to
    remove.
    """
    with corpus.engine.begin() as connection:
        before = _rows(
            connection=connection, sql=_MENTION_ROW, mention=corpus.mention["alice"]
        )
        counted_before = _scalar(
            connection=connection,
            sql=(
                "SELECT mention_count FROM memory_v1.entity_document_mentions"
                " WHERE entity_id = :entity AND doc_id = :doc"
            ),
            entity=corpus.entity["alice"],
            doc=corpus.doc["primary"],
        )
        connection.execute(
            text("UPDATE mentions SET doc_id = :doc WHERE mention_id = :mention"),
            {"doc": corpus.doc["erased"], "mention": corpus.mention["alice"]},
        )
        after = _rows(
            connection=connection, sql=_MENTION_ROW, mention=corpus.mention["alice"]
        )
        counted_after = _rows(
            connection=connection,
            sql=(
                "SELECT mention_count FROM memory_v1.entity_document_mentions"
                " WHERE entity_id = :entity AND doc_id = :doc"
            ),
            entity=corpus.entity["alice"],
            doc=corpus.doc["primary"],
        )
        connection.rollback()

    assert len(before) == 1
    assert before[0]["doc_id"] == corpus.doc["primary"]
    assert counted_before == 1
    assert after == [], "the mention is exposed through its chunk's lineage"
    assert counted_after == [], "the count still includes the removed mention"


def test_a_tombstoned_chunk_lineage_cannot_authorize_sibling_surfaces(
    corpus: _Corpus,
) -> None:
    """A chunk's live identifiers never authorize claims, entities, or events.

    The transcript tables use logical foreign keys, so this deliberately
    inconsistent row is possible: the chunk keeps a live version coordinate
    but names a tombstoned document lineage. Every sibling must bind the
    chunk's own lineage before publishing anything derived from it.
    """
    with corpus.engine.begin() as connection:
        connection.execute(
            text("UPDATE chunks SET doc_id = :erased WHERE chunk_id = :chunk"),
            {"erased": corpus.doc["erased"], "chunk": corpus.chunk["primary.v2"]},
        )
        counts = {
            "chunk": _scalar(
                connection=connection,
                sql=("SELECT count(*) FROM memory_v1.chunks_live WHERE chunk_id = :id"),
                id=corpus.chunk["primary.v2"],
            ),
            "claim_history": _scalar(
                connection=connection,
                sql=(
                    "SELECT count(*) FROM memory_v1.claims_visible_history"
                    " WHERE claim_id = :id"
                ),
                id=corpus.claim["a"],
            ),
            "claim_live": _scalar(
                connection=connection,
                sql="SELECT count(*) FROM memory_v1.claims_live WHERE claim_id = :id",
                id=corpus.claim["a"],
            ),
            "occurrence": _scalar(
                connection=connection,
                sql=(
                    "SELECT count(*) FROM memory_v1.claim_occurrences_live"
                    " WHERE chunk_id = :id"
                ),
                id=corpus.chunk["primary.v2"],
            ),
            "mention": _scalar(
                connection=connection,
                sql="SELECT count(*) FROM memory_v1.mentions_live WHERE mention_id = :id",
                id=corpus.mention["acme"],
            ),
            "entity": _scalar(
                connection=connection,
                sql=(
                    "SELECT count(*) FROM memory_v1.entities_current"
                    " WHERE entity_id = :id"
                ),
                id=corpus.entity["acme"],
            ),
            "identity_event": _scalar(
                connection=connection,
                sql=(
                    "SELECT count(*) FROM memory_v1.identity_events_visible"
                    " WHERE event_id = :id"
                ),
                id=corpus.decision["acme"],
            ),
        }
        connection.rollback()

    assert counts == {name: 0 for name in counts}


def test_identity_events_cite_only_mentions_live(corpus: _Corpus) -> None:
    """A historical mention cannot leave an identity event in a live transcript."""
    with corpus.engine.connect() as connection:
        entity_visible = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM memory_v1.entities_current"
                " WHERE entity_id = :entity"
            ),
            entity=corpus.entity["alice"],
        )
        mention_visible = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM memory_v1.mentions_live"
                " WHERE mention_id = :mention"
            ),
            mention=corpus.mention["old"],
        )
        event_visible = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM memory_v1.identity_events_visible"
                " WHERE event_id = :event"
            ),
            event=corpus.decision["old"],
        )

    assert entity_visible == 1, "the entity gate must not decide this test"
    assert mention_visible == 0
    assert event_visible == 0


def test_a_claim_cannot_borrow_a_representation_from_another_lineage(
    corpus: _Corpus,
) -> None:
    """The claim's chunk representation must belong to its visible version."""
    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE chunks SET representation_id = :representation"
                " WHERE chunk_id = :chunk"
            ),
            {
                "representation": corpus.representation["erased.v1"],
                "chunk": corpus.chunk["primary.v2"],
            },
        )
        raw = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM chunks WHERE chunk_id = :chunk"
                " AND representation_id = :representation"
            ),
            chunk=corpus.chunk["primary.v2"],
            representation=corpus.representation["erased.v1"],
        )
        visible = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM memory_v1.claims_visible_history"
                " WHERE claim_id = :claim"
            ),
            claim=corpus.claim["a"],
        )
        entity = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM memory_v1.entities_current"
                " WHERE entity_id = :entity"
            ),
            entity=corpus.entity["acme"],
        )
        identity_event = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM memory_v1.identity_events_visible"
                " WHERE event_id = :event"
            ),
            event=corpus.decision["acme"],
        )
        fact = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM memory_v1.facts_visible_history"
                " WHERE fact_id = :fact"
            ),
            fact=corpus.fact["current"],
        )
        connection.rollback()

    assert raw == 1, "the mismatched logical coordinate must exist"
    assert visible == 0
    assert entity == 0
    assert identity_event == 0
    assert fact == 0


def test_sections_and_chunks_null_cross_lineage_section_coordinates(
    corpus: _Corpus,
) -> None:
    """Parent and chunk section identifiers resolve only inside one live tree."""
    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_sections SET node_path = 'cross-lineage',"
                " structure_generation_id = :generation"
                " WHERE section_id = :section"
            ),
            {
                "generation": corpus.generation["primary.v2"],
                "section": corpus.section["erased.v1"],
            },
        )
        connection.execute(
            text(
                "UPDATE document_sections SET parent_section_id = :erased"
                " WHERE section_id = :current"
            ),
            {
                "erased": corpus.section["erased.v1"],
                "current": corpus.section["primary.v2"],
            },
        )
        connection.execute(
            text("UPDATE chunks SET section_id = :erased WHERE chunk_id = :chunk"),
            {
                "erased": corpus.section["erased.v1"],
                "chunk": corpus.chunk["primary.v2"],
            },
        )
        section = _rows(
            connection=connection,
            sql=(
                "SELECT parent_section_id FROM memory_v1.sections_live"
                " WHERE section_id = :section"
            ),
            section=corpus.section["primary.v2"],
        )[0]
        chunk = _rows(
            connection=connection,
            sql=(
                "SELECT section_id FROM memory_v1.chunks_live WHERE chunk_id = :chunk"
            ),
            chunk=corpus.chunk["primary.v2"],
        )[0]
        erased_visible = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM memory_v1.sections_live"
                " WHERE section_id = :section"
            ),
            section=corpus.section["erased.v1"],
        )
        connection.rollback()

    assert section["parent_section_id"] is None
    assert chunk["section_id"] is None
    assert erased_visible == 0


def test_occurrences_and_currency_events_bind_the_claim_lineage(
    corpus: _Corpus,
) -> None:
    """Associations cannot place another lineage's claim under a live row."""
    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE chunk_claims SET claim_id = :foreign_claim"
                " WHERE chunk_id = :chunk AND claim_id = :local_claim"
            ),
            {
                "foreign_claim": corpus.claim["c"],
                "chunk": corpus.chunk["primary.v2"],
                "local_claim": corpus.claim["a"],
            },
        )
        occurrence = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM memory_v1.claim_occurrences_live"
                " WHERE chunk_id = :chunk AND claim_id = :claim"
            ),
            chunk=corpus.chunk["primary.v2"],
            claim=corpus.claim["c"],
        )
        connection.execute(
            text(
                "UPDATE testimony_currency_events SET claim_id = :foreign_claim"
                " WHERE event_id = :event"
            ),
            {
                "foreign_claim": corpus.claim["c"],
                "event": corpus.currency_event["reextracted"],
            },
        )
        event = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM memory_v1.testimony_currency_events_visible"
                " WHERE event_id = :event"
            ),
            event=corpus.currency_event["reextracted"],
        )
        connection.execute(
            text(
                "UPDATE testimony_currency_events SET claim_id = :claim,"
                " from_version_id = :foreign_version WHERE event_id = :event"
            ),
            {
                "claim": corpus.claim["old"],
                "foreign_version": corpus.version["erased.v1"],
                "event": corpus.currency_event["reextracted"],
            },
        )
        from_version = _scalar(
            connection=connection,
            sql=(
                "SELECT from_version_id"
                " FROM memory_v1.testimony_currency_events_visible"
                " WHERE event_id = :event"
            ),
            event=corpus.currency_event["reextracted"],
        )
        connection.rollback()

    assert occurrence == 0
    assert event == 0
    assert from_version is None


def test_fact_membership_and_evidence_share_one_claim_bound_authority(
    corpus: _Corpus,
) -> None:
    """Mismatched evidence cannot publish a fact through any downstream view."""
    facts = (corpus.fact["current"], corpus.fact["open_ended"])
    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE relation_evidence SET doc_id = :erased"
                " WHERE relation_id = ANY(:facts)"
            ),
            {"erased": corpus.doc["erased"], "facts": list(facts)},
        )
        assert (
            _scalar(
                connection=connection,
                sql="SELECT count(*) FROM relation_evidence WHERE relation_id = ANY(:facts)",
                facts=list(facts),
            )
            > 0
        )
        surfaces = (
            "v_memory_fact_visible",
            "memory_v1.fact_claim_evidence_live",
            "memory_v1.evidence_lineage",
            "memory_v1.facts_visible_history",
            "memory_v1.facts_current",
            "memory_v1.contradiction_members_current",
            "memory_v1.graph_edges_current",
            "memory_v1.graph_edges_visible_history",
            "memory_v1.changes_visible",
        )
        counts = {
            surface: _scalar(
                connection=connection,
                sql=f"SELECT count(*) FROM {surface} WHERE "  # noqa: S608 -- fixed test relation names
                + (
                    "relation_id = ANY(:facts)"
                    if "graph_edges" in surface
                    else "object_id = ANY(:facts)"
                    if surface == "memory_v1.changes_visible"
                    else "fact_id = ANY(:facts)"
                ),
                facts=list(facts),
            )
            for surface in surfaces
        }
        cited = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM memory_v1.page_evidence_visible"
                " WHERE target_kind = 'relation' AND target_id = :fact"
            ),
            fact=corpus.fact["current"],
        )
        connection.rollback()

    assert counts == {surface: 0 for surface in surfaces}
    assert cited == 0


def test_evidence_for_a_nonexistent_fact_is_never_public(corpus: _Corpus) -> None:
    """A logical evidence row cannot manufacture a fact-catalog identifier."""
    missing_fact = uuid4()
    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO relation_evidence (deployment_id, relation_id,"
                " claim_id, doc_id, stance, normalizer_version, created_at)"
                " VALUES (:deployment, :fact, :claim, :doc, 'supports',"
                " 'normalizer-1', :at)"
            ),
            {
                "deployment": _DEPLOYMENT_ID,
                "fact": missing_fact,
                "claim": corpus.claim["a"],
                "doc": corpus.doc["primary"],
                "at": _MID,
            },
        )
        bridge = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM memory_v1.fact_claim_evidence_live"
                " WHERE fact_id = :fact"
            ),
            fact=missing_fact,
        )
        lineages = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM memory_v1.evidence_lineage WHERE fact_id = :fact"
            ),
            fact=missing_fact,
        )
        connection.rollback()

    assert bridge == 0
    assert lineages == 0


def test_an_orphan_claim_cannot_authorize_a_fact_or_any_sibling(
    corpus: _Corpus,
) -> None:
    """Fact provenance must be a fully visible historical claim coordinate."""
    orphan_claim = uuid4()
    missing_chunk = uuid4()
    fact = uuid4()
    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, extractor_version, is_current_testimony,"
                " asserted_at, ingested_at)"
                " VALUES (:claim, :deployment, :doc, :chunk, 'orphan claim',"
                " 'orphan claim', 0, 12, true, true, 'extractor-1', true,"
                " :at, :at)"
            ),
            {
                "claim": orphan_claim,
                "deployment": _DEPLOYMENT_ID,
                "doc": corpus.doc["primary"],
                "chunk": missing_chunk,
                "at": _PAST,
            },
        )
        connection.execute(
            text(
                "INSERT INTO relations (relation_id, deployment_id,"
                " subject_entity_id, predicate, object_entity_id, ingested_at,"
                " contradiction_group, fact_label, normalizer_version)"
                " SELECT :fact, deployment_id, subject_entity_id, predicate,"
                " object_entity_id, :at, :group, 'orphan-backed fact',"
                " 'normalizer-1' FROM relations WHERE relation_id = :source"
            ),
            {
                "fact": fact,
                "at": _PAST,
                "group": uuid4(),
                "source": corpus.fact["current"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO relation_evidence (deployment_id, relation_id,"
                " claim_id, doc_id, stance, normalizer_version, created_at)"
                " VALUES (:deployment, :fact, :claim, :doc, 'supports',"
                " 'normalizer-1', :at)"
            ),
            {
                "deployment": _DEPLOYMENT_ID,
                "fact": fact,
                "claim": orphan_claim,
                "doc": corpus.doc["primary"],
                "at": _PAST,
            },
        )
        claim_visible = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM memory_v1.claims_visible_history"
                " WHERE claim_id = :claim"
            ),
            claim=orphan_claim,
        )
        surfaces = (
            "v_memory_fact_visible",
            "memory_v1.fact_claim_evidence_live",
            "memory_v1.evidence_lineage",
            "memory_v1.facts_visible_history",
            "memory_v1.facts_current",
            "memory_v1.contradiction_members_current",
            "memory_v1.graph_edges_current",
            "memory_v1.graph_edges_visible_history",
            "memory_v1.changes_visible",
        )
        counts = {
            surface: _scalar(
                connection=connection,
                sql=f"SELECT count(*) FROM {surface} WHERE "  # noqa: S608 -- fixed test relation names
                + (
                    "relation_id = :fact"
                    if "graph_edges" in surface
                    else "object_id = :fact"
                    if surface == "memory_v1.changes_visible"
                    else "fact_id = :fact"
                ),
                fact=fact,
            )
            for surface in surfaces
        }
        connection.rollback()

    assert claim_visible == 0
    assert counts == {surface: 0 for surface in surfaces}


def test_a_fact_with_an_invisible_endpoint_is_absent_from_fact_and_evidence_views(
    corpus: _Corpus,
) -> None:
    """Every declared entity join key resolves inside ``entities_current``."""
    with corpus.engine.begin() as connection:
        assert (
            _scalar(
                connection=connection,
                sql=(
                    "SELECT count(*) FROM memory_v1.entities_current"
                    " WHERE entity_id = :entity"
                ),
                entity=corpus.entity["ghost"],
            )
            == 0
        )
        before_report = orphan_quarantine_report(
            connection=connection, deployment_id=str(_DEPLOYMENT_ID)
        )
        connection.execute(
            text(
                "UPDATE relations SET object_entity_id = :ghost"
                " WHERE relation_id = :fact"
            ),
            {"ghost": corpus.entity["ghost"], "fact": corpus.fact["current"]},
        )
        catalog = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM memory_v1.facts_visible_history"
                " WHERE fact_id = :fact"
            ),
            fact=corpus.fact["current"],
        )
        bridge = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM memory_v1.fact_claim_evidence_live"
                " WHERE fact_id = :fact"
            ),
            fact=corpus.fact["current"],
        )
        after_report = orphan_quarantine_report(
            connection=connection, deployment_id=str(_DEPLOYMENT_ID)
        )
        connection.rollback()

    before_counts = {
        category.category: category.row_count for category in before_report.categories
    }
    after_counts = {
        category.category: category.row_count for category in after_report.categories
    }
    assert catalog == 0
    assert bridge == 0
    assert after_counts["knowledge_citation_without_visible_target"] == (
        before_counts["knowledge_citation_without_visible_target"] + 1
    )


def test_corrupt_coordinates_leak_no_identifier_through_any_surface(
    corpus: _Corpus,
) -> None:
    """The sibling audit covers all 24 caller-reachable views.

    The focused tests above prove which malformed coordinate causes each row
    to disappear. This test combines representative malformed associations and
    then scans the complete surface inventory, so fixing only the named views
    while leaving a downstream sibling open cannot pass.
    """
    missing_fact = uuid4()
    forbidden = {
        str(corpus.chunk["primary.v2"]),
        str(corpus.claim["a"]),
        str(corpus.mention["acme"]),
        str(corpus.decision["acme"]),
        str(corpus.entity["acme"]),
        str(corpus.fact["current"]),
        str(corpus.fact["open_ended"]),
        str(corpus.section["erased.v1"]),
        str(corpus.version["erased.v1"]),
        str(missing_fact),
    }
    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE chunks SET representation_id = :representation"
                " WHERE chunk_id = :chunk"
            ),
            {
                "representation": corpus.representation["erased.v1"],
                "chunk": corpus.chunk["primary.v2"],
            },
        )
        connection.execute(
            text("UPDATE chunks SET section_id = :section WHERE chunk_id = :chunk"),
            {
                "section": corpus.section["erased.v1"],
                "chunk": corpus.chunk["primary.v2"],
            },
        )
        connection.execute(
            text(
                "UPDATE relation_evidence SET doc_id = :doc"
                " WHERE relation_id = ANY(:facts)"
            ),
            {
                "doc": corpus.doc["erased"],
                "facts": [corpus.fact["current"], corpus.fact["open_ended"]],
            },
        )
        connection.execute(
            text(
                "UPDATE testimony_currency_events SET from_version_id = :version"
                " WHERE event_id = :event"
            ),
            {
                "version": corpus.version["erased.v1"],
                "event": corpus.currency_event["reextracted"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO relation_evidence (deployment_id, relation_id,"
                " claim_id, doc_id, stance, normalizer_version, created_at)"
                " VALUES (:deployment, :fact, :claim, :doc, 'supports',"
                " 'normalizer-1', :at)"
            ),
            {
                "deployment": _DEPLOYMENT_ID,
                "fact": missing_fact,
                "claim": corpus.claim["old"],
                "doc": corpus.doc["primary"],
                "at": _MID,
            },
        )

        public_surfaces = tuple(
            surface for surface in MATRIX_SURFACES if surface.caller_reachable
        )
        assert len(public_surfaces) == 24
        leaks = {
            surface.name: _reachable_values(
                connection=connection, relation=surface.name, forbidden=forbidden
            )
            for surface in public_surfaces
        }
        connection.rollback()

    assert leaks == {surface.name: set() for surface in public_surfaces}


def test_a_resolution_to_an_absent_entity_nulls_the_whole_resolution(
    corpus: _Corpus,
) -> None:
    """Resolution metadata is published together with its survivor or not at all.

    Retiring the entity a live decision names leaves the decision in place. The
    mention stays visible — an unresolved mention is still testimony — but every
    resolution column goes null together, so the schema never describes a
    decision about an identity it will not show.
    """
    with corpus.engine.begin() as connection:
        before = _rows(
            connection=connection, sql=_MENTION_ROW, mention=corpus.mention["acme"]
        )
        connection.execute(
            text("UPDATE entities SET status = 'retired' WHERE entity_id = :entity"),
            {"entity": corpus.entity["acme"]},
        )
        after = _rows(
            connection=connection, sql=_MENTION_ROW, mention=corpus.mention["acme"]
        )
        pairs = _rows(
            connection=connection,
            sql=(
                "SELECT 1 FROM memory_v1.entity_document_mentions"
                " WHERE entity_id = :entity"
            ),
            entity=corpus.entity["acme"],
        )
        connection.rollback()

    assert before[0]["resolved_entity_id"] == corpus.entity["acme"]
    assert before[0]["resolution_method"] == "T0"
    assert len(after) == 1, "an unresolved mention stays visible"
    assert after[0]["resolved_entity_id"] is None
    assert after[0]["resolution_method"] is None
    assert after[0]["resolution_confidence"] is None
    assert after[0]["resolution_is_new_entity"] is None
    assert after[0]["resolved_at"] is None
    assert pairs == []


def test_every_mention_count_equals_the_mentions_it_can_show(corpus: _Corpus) -> None:
    """The count is the number of transcript rows, on every pair, exactly."""
    with corpus.engine.connect() as connection:
        rows = _rows(
            connection=connection,
            sql=(
                "SELECT edm.entity_id, edm.doc_id, edm.mention_count,"
                " (SELECT count(*) FROM memory_v1.mentions_live AS m"
                "  WHERE m.deployment_id = edm.deployment_id"
                "    AND m.resolved_entity_id = edm.entity_id"
                "    AND m.doc_id = edm.doc_id) AS transcript_rows"
                " FROM memory_v1.entity_document_mentions AS edm"
            ),
        )
        superseded_mentions = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM mentions m JOIN chunks c"
                " ON c.deployment_id = m.deployment_id AND c.chunk_id = m.chunk_id"
                " WHERE c.version_id = :version"
            ),
            version=corpus.version["primary.v1"],
        )

    assert rows, "the corpus must have counted pairs for this to prove anything"
    for row in rows:
        assert row["mention_count"] == row["transcript_rows"], row
    assert superseded_mentions > 0, (
        "the corpus must contain a mention of superseded content, or the "
        "current-content restriction is untested"
    )


def test_a_page_whose_last_visible_citation_is_forgotten_leaves_with_it(
    corpus: _Corpus,
) -> None:
    """A page is published only while it can still show where its prose came from.

    §3.3's general rule puts K rows among the rows that need an `EXISTS`
    through provenance, and D46 records that both page kinds carry citations.
    So a page with no visible citation is not an ordinary uncited page: it is
    an anomaly, and the fail-closed reading keeps it out of the public surface
    and in the operator's report instead.
    """
    with corpus.engine.begin() as connection:
        published_before = _scalar(
            connection=connection,
            sql="SELECT count(*) FROM memory_v1.pages_live WHERE artifact_id = :page",
            page=corpus.artifact["authored"],
        )
        connection.execute(
            text("UPDATE documents SET deleted_at = now() WHERE doc_id = :doc"),
            {"doc": corpus.doc["primary"]},
        )
        published_after = _scalar(
            connection=connection,
            sql="SELECT count(*) FROM memory_v1.pages_live WHERE artifact_id = :page",
            page=corpus.artifact["authored"],
        )
        links_after = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM memory_v1.page_evidence_visible"
                " WHERE artifact_id = :page"
            ),
            page=corpus.artifact["authored"],
        )
        still_cited = _scalar(
            connection=connection,
            sql="SELECT count(*) FROM memory_v1.pages_live WHERE artifact_id = :page",
            page=corpus.artifact["compiled"],
        )
        connection.rollback()

    assert published_before == 1
    assert published_after == 0, "a page with no visible citation is not published"
    assert links_after == 0, "no link outlives the page that carried it"
    assert still_cited == 1, "a page keeping one visible citation stays published"


def test_the_uncited_page_is_absent_but_countable(corpus: _Corpus) -> None:
    """What the public surface omits, the operator report makes visible."""
    with corpus.engine.connect() as connection:
        published = _scalar(
            connection=connection,
            sql="SELECT count(*) FROM memory_v1.pages_live WHERE artifact_id = :page",
            page=corpus.artifact["uncited"],
        )
        exists_in_base = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM knowledge_artifacts"
                " WHERE artifact_id = :page AND status = 'active'"
            ),
            page=corpus.artifact["uncited"],
        )
        report = orphan_quarantine_report(
            connection=connection, deployment_id=str(_DEPLOYMENT_ID)
        )

    counts = {category.category: category.row_count for category in report.categories}
    assert exists_in_base == 1, "the page really exists and is really active"
    assert published == 0
    assert counts["page_without_visible_citation"] == 1


def test_a_merge_cycle_resolves_to_no_survivor_at_all(corpus: _Corpus) -> None:
    """A chain that never terminates yields no survivor, not two of them.

    Two entities each recorded as merged into the other is corrupt state that
    no schema constraint can prevent. Resolving it by "the furthest row
    reached" would make each entity its own survivor and republish both as if
    no merge had happened; resolving it fail-closed drops both from every
    survivor-joined relation and reports them to the operator instead.
    """
    first, second = uuid4(), uuid4()
    with corpus.engine.begin() as connection:
        for entity_id, name in ((first, "Cycle One"), (second, "Cycle Two")):
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id, type,"
                    " canonical_name, normalized_name, mention_count, graph_degree)"
                    " VALUES (:entity, :deployment, 'Person', :name, lower(:name), 0, 0)"
                ),
                {"entity": entity_id, "deployment": _DEPLOYMENT_ID, "name": name},
            )
        for entity_id, other in ((first, second), (second, first)):
            # the schema records a merge as a redirect plus the merged status;
            # what it cannot enforce is that the redirects are acyclic
            connection.execute(
                text(
                    "UPDATE entities SET merged_into = :other, status = 'merged'"
                    " WHERE entity_id = :entity"
                ),
                {"other": other, "entity": entity_id},
            )
        connection.execute(
            text(
                "UPDATE resolution_decisions SET entity_id = :entity"
                " WHERE decision_id = :decision"
            ),
            {"entity": first, "decision": corpus.decision["acme"]},
        )
        connection.execute(
            text(
                "UPDATE relations SET subject_entity_id = :entity"
                " WHERE relation_id = :relation"
            ),
            {"entity": first, "relation": corpus.fact["current"]},
        )
        survivors = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM v_memory_entity_survivor"
                " WHERE entity_id IN (:first, :second)"
            ),
            first=first,
            second=second,
        )
        current = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM memory_v1.entities_current"
                " WHERE entity_id IN (:first, :second)"
            ),
            first=first,
            second=second,
        )
        mention = _rows(
            connection=connection, sql=_MENTION_ROW, mention=corpus.mention["acme"]
        )
        endpoint_fact = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM memory_v1.facts_visible_history"
                " WHERE fact_id = :fact"
            ),
            fact=corpus.fact["current"],
        )
        quarantined = orphan_quarantine_report(
            connection=connection, deployment_id=str(_DEPLOYMENT_ID)
        )
        connection.rollback()

    assert survivors == 0, "a cycle must resolve to nothing"
    assert current == 0
    assert len(mention) == 1 and mention[0]["resolved_entity_id"] is None
    assert endpoint_fact == 0, "a fact with an unresolvable endpoint drops as a unit"
    counts = {
        category.category: category.row_count for category in quarantined.categories
    }
    assert counts["entity_merge_chain_unresolved"] == 2


def test_a_long_acyclic_merge_chain_resolves_without_a_guessed_depth_bound(
    corpus: _Corpus,
) -> None:
    """A valid chain resolves regardless of length; only cycles fail closed."""
    chain = [uuid4() for _ in range(70)]
    with corpus.engine.begin() as connection:
        for position, entity_id in enumerate(chain):
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id, type,"
                    " canonical_name, normalized_name, mention_count, graph_degree)"
                    " VALUES (:entity, :deployment, 'Person', :name, lower(:name), 0, 0)"
                ),
                {
                    "entity": entity_id,
                    "deployment": _DEPLOYMENT_ID,
                    "name": f"Chain {position}",
                },
            )
        for position, entity_id in enumerate(chain[:-1]):
            connection.execute(
                text(
                    "UPDATE entities SET merged_into = :next, status = 'merged'"
                    " WHERE entity_id = :entity"
                ),
                {"next": chain[position + 1], "entity": entity_id},
            )
        resolved = {
            str(row["entity_id"]): str(row["survivor_entity_id"])
            for row in _rows(
                connection=connection,
                sql=(
                    "SELECT entity_id, survivor_entity_id FROM"
                    " v_memory_entity_survivor WHERE entity_id = ANY(:ids)"
                ),
                ids=chain,
            )
        }
        connection.rollback()

    terminal = str(chain[-1])
    assert resolved[str(chain[0])] == terminal
    assert resolved[str(chain[-5])] == terminal, "a short chain still resolves"
    assert resolved[terminal] == terminal


# ── §9.3 D41 clocks ──────────────────────────────────────────────────────

#: The §3.3 current predicate, applied to the raw clocks at a bound instant.
_CURRENT_AT = (
    "SELECT coalesce(array_agg(h.fact_id ORDER BY h.fact_id), '{}'::uuid[])"
    " FROM memory_v1.facts_visible_history AS h"
    " WHERE h.ingested_at <= :at AND h.invalidated_at IS NULL"
    " AND (h.valid_from IS NULL OR h.valid_from <= :at)"
    " AND (h.valid_until IS NULL OR h.valid_until > :at)"
)

#: The §3.3 bitemporal as-of predicate, with the two instants kept separate.
_AS_OF = (
    "SELECT coalesce(array_agg(h.fact_id ORDER BY h.fact_id), '{}'::uuid[])"
    " FROM memory_v1.facts_visible_history AS h"
    " WHERE h.ingested_at <= :believed_at"
    " AND (h.invalidated_at IS NULL OR h.invalidated_at > :believed_at)"
    " AND (h.valid_from IS NULL OR h.valid_from <= :valid_at)"
    " AND (h.valid_until IS NULL OR h.valid_until > :valid_at)"
)


def test_facts_current_is_exactly_the_d41_predicate_at_its_own_instant(
    corpus: _Corpus,
) -> None:
    """The view is the predicate, evaluated once per statement.

    Both halves are read in one statement, so `statement_timestamp()` in the
    hand-written predicate is by definition the same instant the view emits as
    `evaluated_at`; any divergence is a difference in the predicate itself.
    """
    with corpus.engine.connect() as connection:
        row = _rows(
            connection=connection,
            sql=(
                "SELECT (SELECT array_agg(fact_id ORDER BY fact_id)"
                " FROM memory_v1.facts_current) AS view_ids,"
                " (SELECT array_agg(h.fact_id ORDER BY h.fact_id)"
                " FROM memory_v1.facts_visible_history AS h"
                " WHERE h.ingested_at <= statement_timestamp()"
                " AND h.invalidated_at IS NULL"
                " AND (h.valid_from IS NULL"
                "      OR h.valid_from <= statement_timestamp())"
                " AND (h.valid_until IS NULL"
                "      OR h.valid_until > statement_timestamp())) AS predicate_ids,"
                " (SELECT min(evaluated_at) = max(evaluated_at)"
                " FROM memory_v1.facts_current) AS one_instant"
            ),
        )[0]

    assert row["view_ids"] == row["predicate_ids"]
    assert row["one_instant"] is True
    assert corpus.fact["current"] in row["view_ids"]


def test_valid_from_is_inclusive_and_valid_until_is_exclusive(corpus: _Corpus) -> None:
    """World validity is the half-open interval `[valid_from, valid_until)`."""
    just_before_end = datetime(2025, 5, 31, 23, 59, 59, 999999, tzinfo=UTC)
    with corpus.engine.connect() as connection:
        at_start = _scalar(connection=connection, sql=_CURRENT_AT, at=_PAST)
        just_inside = _scalar(
            connection=connection, sql=_CURRENT_AT, at=just_before_end
        )
        at_end = _scalar(connection=connection, sql=_CURRENT_AT, at=_ENDED)

    assert corpus.fact["ended"] in at_start, "valid_from is inclusive"
    assert corpus.fact["ended"] in just_inside
    assert corpus.fact["ended"] not in at_end, "valid_until is exclusive"


def test_null_endpoints_are_open_and_future_ingestion_is_not_yet_believed(
    corpus: _Corpus,
) -> None:
    """A null endpoint is unbounded; a fact is not current before it was learned."""
    with corpus.engine.connect() as connection:
        long_ago = _scalar(connection=connection, sql=_CURRENT_AT, at=_ANCIENT)
        later = _scalar(connection=connection, sql=_CURRENT_AT, at=_MID)
        far_future = _scalar(connection=connection, sql=_CURRENT_AT, at=_FUTURE)

    assert list(long_ago) == [], "nothing had been ingested yet at that instant"
    assert corpus.fact["open_ended"] in later, "a null valid_from is unbounded before"
    assert corpus.fact["open_ended"] in far_future, "a null valid_until never expires"
    assert corpus.fact["ended"] in later
    assert corpus.fact["ended"] not in far_future
    assert corpus.fact["future"] not in later, "not believed before it was ingested"
    assert corpus.fact["future"] in far_future


def test_invalidation_is_transaction_time_and_equality_already_excludes(
    corpus: _Corpus,
) -> None:
    """A fact stops being believed at `invalidated_at`, not after it."""
    just_before = datetime(2024, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
    with corpus.engine.connect() as connection:
        believed = _scalar(
            connection=connection, sql=_AS_OF, valid_at=_MID, believed_at=just_before
        )
        at_invalidation = _scalar(
            connection=connection, sql=_AS_OF, valid_at=_MID, believed_at=_MID
        )
        current_now = _scalar(connection=connection, sql=_CURRENT_AT, at=_MID)

    assert corpus.fact["invalidated"] in believed
    assert corpus.fact["invalidated"] not in at_invalidation
    assert corpus.fact["invalidated"] not in current_now


def test_distinct_valid_at_and_believed_at_select_different_membership(
    corpus: _Corpus,
) -> None:
    """The two clocks answer two different questions and are not interchangeable."""
    with corpus.engine.connect() as connection:
        held_then_known_now = _scalar(
            connection=connection, sql=_AS_OF, valid_at=_MID, believed_at=_FUTURE
        )
        held_then_known_then = _scalar(
            connection=connection, sql=_AS_OF, valid_at=_MID, believed_at=_MID
        )

    assert corpus.fact["future"] in held_then_known_now
    assert corpus.fact["future"] not in held_then_known_then
    assert corpus.fact["ended"] in held_then_known_then


def test_one_statement_observes_one_shared_evaluation_instant(corpus: _Corpus) -> None:
    """Every current relation in one statement answers at the same instant."""
    with corpus.engine.connect() as connection:
        row = _rows(
            connection=connection,
            sql=(
                "SELECT (SELECT min(evaluated_at) FROM memory_v1.facts_current)"
                " AS facts_at,"
                " (SELECT min(evaluated_at) FROM memory_v1.graph_edges_current)"
                " AS edges_at,"
                " (SELECT min(evaluated_at)"
                " FROM memory_v1.contradiction_members_current) AS members_at"
            ),
        )[0]
        later = _scalar(
            connection=connection,
            sql="SELECT min(evaluated_at) FROM memory_v1.facts_current",
        )

    assert row["facts_at"] == row["edges_at"] == row["members_at"]
    assert later > row["facts_at"], "a later statement evaluates at a later instant"


def test_no_claim_row_is_ever_accepted_as_a_current_fact(corpus: _Corpus) -> None:
    """Testimony and adjudicated truth stay separate relations with separate ids."""
    with corpus.engine.connect() as connection:
        overlap = _scalar(
            connection=connection,
            sql=(
                "SELECT count(*) FROM memory_v1.claims_live AS c"
                " JOIN memory_v1.facts_current AS f ON f.fact_id = c.claim_id"
            ),
        )
        claim_columns = {
            str(row["column_name"])
            for row in _rows(
                connection=connection,
                sql=(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema = 'memory_v1' AND table_name = 'claims_live'"
                ),
            )
        }

    assert overlap == 0
    assert not claim_columns & {"evaluated_at", "support_state", "evidence_count"}


def test_claim_evidence_overlap_is_inclusive_at_both_endpoints(corpus: _Corpus) -> None:
    """An instant claim has equal endpoints a half-open rule would erase."""
    overlap = (
        "SELECT coalesce(array_agg(claim_id ORDER BY claim_id), '{}'::uuid[])"
        " FROM memory_v1.claims_visible_history"
        " WHERE claim_valid_precision <> 'unknown'"
        " AND claim_valid_from <= :window_to"
        " AND (claim_valid_until IS NULL OR claim_valid_until >= :window_from)"
    )
    with corpus.engine.connect() as connection:
        at_instant = _scalar(
            connection=connection, sql=overlap, window_from=_MID, window_to=_MID
        )
        after_instant = _scalar(
            connection=connection, sql=overlap, window_from=_ENDED, window_to=_ENDED
        )

    assert corpus.claim["instant"] in at_instant, "equal endpoints still match"
    assert corpus.claim["a"] in at_instant, "the upper endpoint is inclusive"
    assert corpus.claim["instant"] not in after_instant


# ── §9.4 D54 lifecycle ───────────────────────────────────────────────────

_COUNTS = (
    "SELECT evidence_count, contradict_count, support_state"
    " FROM memory_v1.facts_current WHERE fact_id = :fact"
)


def _counts(*, connection: Connection, fact_id: UUID) -> Mapping[str, Any]:
    """Read one fact's live counts and support state."""
    return _rows(connection=connection, sql=_COUNTS, fact=fact_id)[0]


def _add_supporting_claim(
    *, connection: Connection, corpus: _Corpus, chunk_key: str, doc_key: str
) -> UUID:
    """Insert one more current-testimony supporting claim for the fixture fact."""
    claim_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
            " claim_text, source_span, char_start, char_end, anchor_ok,"
            " window_membership_ok, extractor_version, is_current_testimony,"
            " asserted_at, claim_valid_precision, ingested_at)"
            " VALUES (:claim, :deployment, :doc, :chunk, 'Another assertion.',"
            " 'Another assertion.', 0, 18, true, true, 'extractor-2', true, :at,"
            " 'unknown', :at)"
        ),
        {
            "claim": claim_id,
            "deployment": _DEPLOYMENT_ID,
            "doc": corpus.doc[doc_key],
            "chunk": corpus.chunk[chunk_key],
            "at": _MID,
        },
    )
    connection.execute(
        text(
            "INSERT INTO relation_evidence (deployment_id, relation_id, claim_id,"
            " doc_id, stance, normalizer_version)"
            " VALUES (:deployment, :relation, :claim, :doc, 'supports',"
            " 'normalizer-1')"
        ),
        {
            "deployment": _DEPLOYMENT_ID,
            "relation": corpus.fact["current"],
            "claim": claim_id,
            "doc": corpus.doc[doc_key],
        },
    )
    return claim_id


def test_repetition_inside_one_source_leaves_both_counts_unchanged(
    corpus: _Corpus,
) -> None:
    """Saying the same thing twice in one document is not corroboration."""
    with corpus.engine.begin() as connection:
        before = _counts(connection=connection, fact_id=corpus.fact["current"])
        _add_supporting_claim(
            connection=connection,
            corpus=corpus,
            chunk_key="primary.v2",
            doc_key="primary",
        )
        after = _counts(connection=connection, fact_id=corpus.fact["current"])
        connection.rollback()

    assert before["evidence_count"] == 2
    assert after["evidence_count"] == before["evidence_count"]
    assert after["contradict_count"] == before["contradict_count"]


def test_reextracting_one_lineage_leaves_both_counts_unchanged(corpus: _Corpus) -> None:
    """A new extraction generation replaces testimony without inflating evidence."""
    with corpus.engine.begin() as connection:
        before = _counts(connection=connection, fact_id=corpus.fact["current"])
        connection.execute(
            text(
                "UPDATE claims SET is_current_testimony = false"
                " WHERE claim_id IN (:first, :second)"
            ),
            {"first": corpus.claim["a"], "second": corpus.claim["b"]},
        )
        _add_supporting_claim(
            connection=connection,
            corpus=corpus,
            chunk_key="primary.v2",
            doc_key="primary",
        )
        after = _counts(connection=connection, fact_id=corpus.fact["current"])
        connection.rollback()

    assert after["evidence_count"] == before["evidence_count"] == 2
    assert after["contradict_count"] == before["contradict_count"] == 1


def test_a_second_lineage_moves_the_matching_count_by_one(corpus: _Corpus) -> None:
    """An independent source is corroboration, and only for its own stance."""
    with corpus.engine.begin() as connection:
        before = _counts(connection=connection, fact_id=corpus.fact["current"])
        _add_supporting_claim(
            connection=connection,
            corpus=corpus,
            chunk_key="forgotten.v1",
            doc_key="forgotten",
        )
        after = _counts(connection=connection, fact_id=corpus.fact["current"])
        connection.rollback()

    assert after["evidence_count"] == before["evidence_count"] + 1
    assert after["contradict_count"] == before["contradict_count"]


def test_the_two_stances_stay_distinct_and_come_from_evidence_lineage(
    corpus: _Corpus,
) -> None:
    """Both counts equal the distinct lineages taking that stance, exactly."""
    with corpus.engine.connect() as connection:
        counts = _counts(connection=connection, fact_id=corpus.fact["current"])
        lineages = {
            str(row["stance"]): int(row["lineages"])
            for row in _rows(
                connection=connection,
                sql=(
                    "SELECT stance, count(DISTINCT doc_id) AS lineages"
                    " FROM memory_v1.evidence_lineage WHERE fact_id = :fact"
                    " GROUP BY stance"
                ),
                fact=corpus.fact["current"],
            )
        }

    assert lineages == {"supports": 2, "contradicts": 1}
    assert counts["evidence_count"] == lineages["supports"]
    assert counts["contradict_count"] == lineages["contradicts"]


def test_processing_loss_alone_opens_the_withdrawn_state(corpus: _Corpus) -> None:
    """`withdrawn` comes from the open review row and from nothing else."""
    with corpus.engine.begin() as connection:
        review_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO review_queue (review_id, deployment_id, item_kind,"
                " candidate, blast_radius, confidence, expected_impact, status)"
                " VALUES (:review, :deployment, 'support_withdrawn',"
                " jsonb_build_object('fact_kind', 'relation', 'fact_id',"
                " CAST(:fact AS text)), 1, 0.5, 0.5,"
                " 'pending')"
            ),
            {
                "review": review_id,
                "deployment": _DEPLOYMENT_ID,
                "fact": str(corpus.fact["current"]),
            },
        )
        flagged = _counts(connection=connection, fact_id=corpus.fact["current"])
        history_state = _scalar(
            connection=connection,
            sql=(
                "SELECT support_state_current FROM memory_v1.facts_visible_history"
                " WHERE fact_id = :fact"
            ),
            fact=corpus.fact["current"],
        )
        edge_state = _scalar(
            connection=connection,
            sql=(
                "SELECT support_state FROM memory_v1.graph_edges_current"
                " WHERE relation_id = :fact"
            ),
            fact=corpus.fact["current"],
        )
        stored = _scalar(
            connection=connection,
            sql="SELECT evidence_count FROM relations WHERE relation_id = :fact",
            fact=corpus.fact["current"],
        )
        connection.execute(
            text(
                "UPDATE review_queue SET status = 'accepted', resolved_at = now()"
                " WHERE review_id = :review"
            ),
            {"review": review_id},
        )
        closed = _counts(connection=connection, fact_id=corpus.fact["current"])
        connection.rollback()

    assert flagged["support_state"] == "withdrawn"
    assert history_state == "withdrawn"
    assert edge_state == "withdrawn"
    assert flagged["evidence_count"] == 2, "the flag does not touch the counts"
    assert stored == 0, "the cached column is untouched; the state is read-time"
    assert closed["support_state"] == "current", "closing the row restores it"


def test_withdrawal_is_bound_to_fact_kind_when_uuids_collide(corpus: _Corpus) -> None:
    """A relation review cannot mark a same-UUID observation withdrawn."""
    fact_id = corpus.fact["current"]
    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO observations (observation_id, deployment_id,"
                " subject_entity_id, statement, valid_from, ingested_at,"
                " confidence, obs_label, normalizer_version)"
                " VALUES (:fact, :deployment, :subject, 'Colliding observation',"
                " :at, :at, 0.7, 'Colliding observation', 'normalizer-1')"
            ),
            {
                "fact": fact_id,
                "deployment": _DEPLOYMENT_ID,
                "subject": corpus.entity["alice"],
                "at": _PAST,
            },
        )
        connection.execute(
            text(
                "INSERT INTO observation_evidence (deployment_id, observation_id,"
                " claim_id, doc_id, stance, normalizer_version, created_at)"
                " VALUES (:deployment, :fact, :claim, :doc, 'supports',"
                " 'normalizer-1', :at)"
            ),
            {
                "deployment": _DEPLOYMENT_ID,
                "fact": fact_id,
                "claim": corpus.claim["a"],
                "doc": corpus.doc["primary"],
                "at": _PAST,
            },
        )
        connection.execute(
            text(
                "INSERT INTO review_queue (review_id, deployment_id, item_kind,"
                " candidate, blast_radius, confidence, expected_impact, status)"
                " VALUES (:review, :deployment, 'support_withdrawn',"
                " jsonb_build_object('fact_kind', 'relation', 'fact_id',"
                " CAST(:fact AS text)), 1, 0.5, 0.5, 'pending')"
            ),
            {"review": uuid4(), "deployment": _DEPLOYMENT_ID, "fact": fact_id},
        )
        states = {
            str(row["fact_kind"]): str(row["support_state_current"])
            for row in connection.execute(
                text(
                    "SELECT fact_kind, support_state_current"
                    " FROM memory_v1.facts_visible_history"
                    " WHERE deployment_id = :deployment AND fact_id = :fact"
                ),
                {"deployment": _DEPLOYMENT_ID, "fact": fact_id},
            )
            .mappings()
            .all()
        }
        connection.rollback()

    assert states == {"relation": "withdrawn", "observation": "current"}


def test_source_deletion_and_a_zero_count_never_infer_withdrawal(
    corpus: _Corpus,
) -> None:
    """Losing sources reduces counts; only a review row changes the state."""
    with corpus.engine.begin() as connection:
        connection.execute(
            text("UPDATE documents SET deleted_at = now() WHERE doc_id = :doc"),
            {"doc": corpus.doc["second"]},
        )
        after_deletion = _counts(connection=connection, fact_id=corpus.fact["current"])
        connection.execute(
            text(
                "UPDATE claims SET is_current_testimony = false"
                " WHERE claim_id IN (:first, :second)"
            ),
            {"first": corpus.claim["a"], "second": corpus.claim["b"]},
        )
        at_zero = _counts(connection=connection, fact_id=corpus.fact["current"])
        connection.rollback()

    assert after_deletion["evidence_count"] == 1
    assert after_deletion["contradict_count"] == 0
    assert after_deletion["support_state"] == "current"
    assert at_zero["evidence_count"] == 0
    assert at_zero["support_state"] == "current", "a zero count is not a withdrawal"


def test_history_keeps_a_processing_withdrawn_fact_with_zero_support(
    corpus: _Corpus,
) -> None:
    """Surviving provenance, not current support, decides historical visibility."""
    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE claims SET is_current_testimony = false"
                " WHERE claim_id IN (:a, :b, :c, :contra)"
            ),
            {
                "a": corpus.claim["a"],
                "b": corpus.claim["b"],
                "c": corpus.claim["c"],
                "contra": corpus.claim["contra"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO review_queue (review_id, deployment_id, item_kind,"
                " candidate, blast_radius, confidence, expected_impact, status)"
                " VALUES (:review, :deployment, 'support_withdrawn',"
                " jsonb_build_object('fact_kind', 'relation', 'fact_id',"
                " CAST(:fact AS text)), 1, 0.5, 0.5,"
                " 'deferred')"
            ),
            {
                "review": uuid4(),
                "deployment": _DEPLOYMENT_ID,
                "fact": str(corpus.fact["current"]),
            },
        )
        row = _rows(
            connection=connection,
            sql=(
                "SELECT evidence_count_current, support_state_current"
                " FROM memory_v1.facts_visible_history WHERE fact_id = :fact"
            ),
            fact=corpus.fact["current"],
        )[0]
        connection.rollback()

    assert row["evidence_count_current"] == 0
    assert row["support_state_current"] == "withdrawn", "a deferred row is still open"


# ── operator-only quarantine report ──────────────────────────────────────


def test_quarantine_report_counts_what_the_public_views_omit(corpus: _Corpus) -> None:
    """Orphans stay out of every public path but stay countable for operators."""
    with corpus.engine.connect() as connection:
        report = orphan_quarantine_report(
            connection=connection, deployment_id=str(_DEPLOYMENT_ID)
        )

    counts = {category.category: category.row_count for category in report.categories}
    assert tuple(counts) == QUARANTINE_CATEGORIES
    assert counts["fact_without_visible_membership"] >= 1
    assert counts["entity_without_surviving_provenance"] == 1
    assert counts["crossref_without_live_endpoints"] >= 1
    assert counts["section_outside_current_generation"] >= 1
    assert counts["knowledge_citation_without_visible_target"] >= 1
    assert counts["currency_event_without_live_lineage"] >= 1
    assert counts["page_without_visible_citation"] == 1
    assert counts["claim_without_chunk"] == 0
    assert counts["chunk_without_version"] == 0
    assert counts["evidence_lineage_mismatch"] == 0
    assert counts["entity_merge_chain_unresolved"] == 0, (
        "the seeded merge chain terminates, so nothing is unresolvable here"
    )
    assert report.total_rows == sum(counts.values())
    # the report is counts and repair guidance only: no corpus content leaves it
    for category in report.categories:
        assert category.repair
        assert category.explanation.endswith(".")


def test_deployed_definition_drift_is_detected(
    database_engine: Engine, database_url: str
) -> None:
    """A body swap that preserves the interface is caught, not waved through.

    `CREATE OR REPLACE VIEW` can keep every column, type, and comment while
    replacing what the view returns. Shape comparison reports that as clean and
    the manifest hash cannot see it at all — the hash is taken over the
    authored DDL, so it describes the checkout, not the server. The pairwise
    definition comparison against an independently migrated build is what
    closes that gap.
    """
    admin = create_engine(database_url, isolation_level="AUTOCOMMIT")
    # A unique name per run: a leftover scratch database from an interrupted
    # run would otherwise be migrated a second time and fail on its own types.
    scratch_name = f"remember_defcheck_{uuid4().hex[:8]}"
    try:
        with admin.connect() as connection:
            connection.execute(
                text(f'DROP DATABASE IF EXISTS "{scratch_name}" WITH (FORCE)')
            )
            connection.execute(text(f'CREATE DATABASE "{scratch_name}"'))
        rendered = (
            make_url(database_url)
            .set(database=scratch_name)
            .render_as_string(hide_password=False)
        )
        config = Config(str(_ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", rendered)
        command.upgrade(config=config, revision="head")
        reference = create_engine(rendered)
        try:
            with database_engine.connect() as live, reference.connect() as intended:
                assert (
                    deployed_definition_differences(connection=live, reference=intended)
                    == ()
                )
                original = deployed_definitions(live)["memory_v1.claims_live"]
                assert original

            # Swap the body, keep the interface, and roll the swap back.
            with database_engine.begin() as live:
                live.execute(
                    text(
                        "CREATE OR REPLACE VIEW memory_v1.claims_live AS"
                        " SELECT q.* FROM (SELECT * FROM memory_v1.claims_live) AS q"
                        " WHERE false"
                    )
                )
                with reference.connect() as intended:
                    drift = deployed_definition_differences(
                        connection=live, reference=intended
                    )
                assert "memory_v1.claims_live definition differs" in " ".join(drift)
                live.rollback()

            with database_engine.connect() as live, reference.connect() as intended:
                assert (
                    deployed_definition_differences(connection=live, reference=intended)
                    == ()
                )
        finally:
            reference.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(
                text(f'DROP DATABASE IF EXISTS "{scratch_name}" WITH (FORCE)')
            )
        admin.dispose()


def test_an_entity_seen_only_in_superseded_content_stays_a_member(
    corpus: _Corpus,
) -> None:
    """Membership is the surviving-lineage floor; the counts are current-only.

    The distinction matters in exactly this case: an entity whose every
    mention was superseded by a later version of a live lineage is still
    associated with surviving provenance, so it remains published — with both
    current-content counts at zero, which is the honest number. The probe runs
    inside a rolled-back transaction so the shared corpus keeps its shape.
    """
    entity_id = uuid4()
    mention_id = uuid4()
    with corpus.engine.connect() as connection:
        transaction = connection.begin()
        try:
            superseded = (
                connection.execute(
                    text(
                        "SELECT ch.chunk_id, ch.doc_id, cc.claim_id"
                        " FROM chunks ch"
                        " JOIN documents d ON d.doc_id = ch.doc_id"
                        "  AND d.deployment_id = ch.deployment_id"
                        " JOIN chunk_claims cc ON cc.chunk_id = ch.chunk_id"
                        "  AND cc.deployment_id = ch.deployment_id"
                        " WHERE d.deployment_id = :deployment"
                        "   AND d.deleted_at IS NULL"
                        "   AND ch.version_id <> d.current_version_id"
                        " LIMIT 1"
                    ),
                    {"deployment": _DEPLOYMENT_ID},
                )
                .mappings()
                .first()
            )
            assert superseded is not None, "the corpus must contain superseded content"
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id, type,"
                    " canonical_name, normalized_name) VALUES (:entity,"
                    " :deployment, 'Person', 'Superseded Only', 'superseded only')"
                ),
                {"entity": entity_id, "deployment": _DEPLOYMENT_ID},
            )
            connection.execute(
                text(
                    "INSERT INTO mentions (mention_id, deployment_id,"
                    " surface_form, normalized_lemma, canonical_name_form,"
                    " emitted_type, language, claim_id, chunk_id, doc_id,"
                    " char_start, char_end, created_at)"
                    " VALUES (:mention, :deployment, 'Superseded Only',"
                    " 'superseded only', 'Superseded Only', 'Person', 'en',"
                    " :claim, :chunk, :doc, 0, 5, now())"
                ),
                {
                    "mention": mention_id,
                    "deployment": _DEPLOYMENT_ID,
                    "claim": superseded["claim_id"],
                    "chunk": superseded["chunk_id"],
                    "doc": superseded["doc_id"],
                },
            )
            connection.execute(
                text(
                    "INSERT INTO resolution_decisions (decision_id,"
                    " deployment_id, mention_id, entity_id, method, confidence,"
                    " resolver_version, decided_at) VALUES (:decision,"
                    " :deployment, :mention, :entity, 'T0', 1.0,"
                    " 'batch-a-proofs', now())"
                ),
                {
                    "decision": uuid4(),
                    "deployment": _DEPLOYMENT_ID,
                    "mention": mention_id,
                    "entity": entity_id,
                },
            )

            published = (
                connection.execute(
                    text(
                        "SELECT live_mention_count, live_document_count"
                        " FROM memory_v1.entities_current"
                        " WHERE deployment_id = :deployment"
                        "   AND entity_id = :entity"
                    ),
                    {"deployment": _DEPLOYMENT_ID, "entity": entity_id},
                )
                .mappings()
                .all()
            )
            rows = connection.execute(
                text(
                    "SELECT count(*) FROM memory_v1.entity_document_mentions"
                    " WHERE deployment_id = :deployment AND entity_id = :entity"
                ),
                {"deployment": _DEPLOYMENT_ID, "entity": entity_id},
            ).scalar_one()
        finally:
            transaction.rollback()

    assert len(published) == 1, "a superseded-only association is still provenance"
    assert published[0]["live_mention_count"] == 0
    assert published[0]["live_document_count"] == 0
    assert rows == 0
