"""D159 forget: the record, the re-ingest guard and the PostgreSQL sweep.

The forget record (``forgets``) is the only forget state. The hide (in
``workers.forget``) tombstones the lineage and inserts the record in one
transaction; this module owns the record and the sweep's PostgreSQL step:
collect content-free IDs, then delete or clear everything derived from the
document, in one transaction, treating "already gone" as success.
"""

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import json
from typing import Any
from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.engine import RowMapping

from rememberstack.core import source_identity_hash
from rememberstack.core.fact_label import deterministic_fact_label
from rememberstack.model import DocumentForgottenError
from rememberstack.model import EnqueueWork
from rememberstack.model import Forget
from rememberstack.model import ForgetNotFoundError
from rememberstack.model import ForgetStatus
from rememberstack.model import ObjectKey
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingTarget
from rememberstack.spine.work_ledger import enqueue_on
from rememberstack.spine.work_ledger import FORGOTTEN_WORK

FORGET_COMPONENT_VERSION: Final = "forget-v1"
"""The sweep generation; each sweep's work row is ``forget-v1/<sweep>``."""

UPLOAD_SOURCE_KIND: Final = "upload"
"""A one-shot upload's lineage is its bytes; the content hash guards it."""


@dataclass(frozen=True)
class SweepFindings:
    """What one sweep's PostgreSQL step found and what the later steps act on."""

    removed: bool
    """The step deleted or cleared residue; the job sweeps again after a quiet period."""
    object_keys: tuple[ObjectKey, ...]
    """Opaque original keys to purge (retired raw originals, D147 locators)."""
    compiled_artifact_ids: tuple[UUID, ...]
    """Compiled K pages whose citations to the document this step removed."""
    blocking_k_paths: tuple[str, ...]
    """Authored pages and curation sidecars whose current file still cites it."""
    profile_entity_ids: tuple[UUID, ...]
    """Surviving entities whose profile this step cleared, for a refresh."""


def sweep_work(
    *,
    deployment_id: UUID,
    forget_id: UUID,
    sweep: int,
    failures: int,
    not_before: datetime | None,
    first: bool = False,
    reapply: bool = False,
) -> EnqueueWork:
    """One sweep's work row: it targets the record, one row per sweep (D159 §3.4).

    ``first`` marks the sweep right after a hide or a reapply: it is never the
    last one, because it does not follow a quiet period. ``reapply`` marks a
    series ``forget-reapply`` started after a restore.
    """
    return EnqueueWork(
        deployment_id=deployment_id,
        target_kind=ProcessingTarget.FORGET,
        target_id=forget_id,
        stage=PipelineStage.FORGET,
        component_version=f"{FORGET_COMPONENT_VERSION}/{sweep}",
        content_hash=str(forget_id),
        lane=None,
        payload={
            "forget_id": str(forget_id),
            "sweep": sweep,
            "failures": failures,
            "first": first,
            "reapply": reapply,
        },
        not_before=not_before,
    )


class ForgetCatalog:
    """Own forget records, the re-ingest guard and the PostgreSQL sweep step."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the catalog to the authoritative spine database."""
        self._engine = engine

    def record(self, *, deployment_id: UUID, forget_id: UUID) -> Forget:
        """Return one record, or raise ``ForgetNotFoundError``."""
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(_SELECT + " WHERE deployment_id = :d AND forget_id = :f"),
                    {"d": deployment_id, "f": forget_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ForgetNotFoundError(str(forget_id))
        return _forget(row=row)

    def records(self, *, deployment_id: UUID) -> tuple[Forget, ...]:
        """Every record of a deployment, oldest first."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    _SELECT
                    + " WHERE deployment_id = :d ORDER BY requested_at, forget_id"
                ),
                {"d": deployment_id},
            ).mappings()
            return tuple(_forget(row=row) for row in rows)

    def record_for_doc_on(
        self, *, connection: Connection, deployment_id: UUID, doc_id: UUID
    ) -> Forget | None:
        """The lineage's record, read in the caller's transaction."""
        row = (
            connection.execute(
                text(_SELECT + " WHERE deployment_id = :d AND doc_id = :doc"),
                {"d": deployment_id, "doc": doc_id},
            )
            .mappings()
            .one_or_none()
        )
        return _forget(row=row) if row is not None else None

    def insert_on(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        doc_id: UUID,
        forget_id: UUID,
    ) -> tuple[Forget, bool]:
        """Insert the record under the lineage row lock the caller holds.

        Returns the record and whether this call created it; a concurrent
        forget that committed first wins and its record is returned.
        """
        document = (
            connection.execute(
                text("""SELECT source_kind, source_ref FROM documents
                WHERE deployment_id = :d AND doc_id = :doc"""),
                {"d": deployment_id, "doc": doc_id},
            )
            .mappings()
            .one()
        )
        identity = (
            None
            if document["source_kind"] == UPLOAD_SOURCE_KIND
            or document["source_ref"] is None
            else source_identity_hash(
                deployment_id=deployment_id,
                source_kind=str(document["source_kind"]),
                source_ref=str(document["source_ref"]),
            )
        )
        row = (
            connection.execute(
                _INSERT_RECORD,
                {
                    "forget_id": forget_id,
                    "d": deployment_id,
                    "doc": doc_id,
                    "identity": identity,
                },
            )
            .mappings()
            .one_or_none()
        )
        if row is not None:
            return _forget(row=row), True
        existing = self.record_for_doc_on(
            connection=connection, deployment_id=deployment_id, doc_id=doc_id
        )
        if existing is None:
            raise RuntimeError(f"forget record for {doc_id} vanished on conflict")
        return existing, False

    def rearm_on(self, *, connection: Connection, forget: Forget) -> None:
        """``forget-reapply``: the record is erasing again until its sweeps finish."""
        connection.execute(
            text("""UPDATE forgets SET status = 'erasing', completed_at = NULL
            WHERE deployment_id = :d AND forget_id = :f"""),
            {"d": forget.deployment_id, "f": forget.forget_id},
        )

    def guard_ingest(
        self,
        *,
        deployment_id: UUID,
        source_kind: str,
        source_ref: str,
        content_hash: str,
    ) -> None:
        """Refuse forgotten content before any bytes are written (D159 §3.6)."""
        with self._engine.connect() as connection:
            guard_forgotten_on(
                connection=connection,
                deployment_id=deployment_id,
                source_kind=source_kind,
                source_ref=source_ref,
                content_hash=content_hash,
            )

    def sweep_postgres(self, *, forget: Forget) -> SweepFindings:
        """Collect, then delete or clear, in one transaction (D159 §3.3 steps 0-1)."""
        with self._engine.begin() as connection:
            return _Sweep(connection=connection, forget=forget).run()

    def k_paths_for_artifacts(
        self, *, deployment_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> tuple[str, ...]:
        """Exact body and curation paths of K artifacts, for history erasure."""
        if not artifact_ids:
            return ()
        with self._engine.connect() as connection:
            values = connection.execute(
                _K_PATHS_FOR_ARTIFACTS,
                {"deployment_id": deployment_id, "artifact_ids": list(artifact_ids)},
            ).scalars()
            return tuple(sorted({str(value) for value in values if value is not None}))

    def still_blocking(self, *, forget: Forget) -> tuple[str, ...]:
        """The listed authored paths whose current file still cites the document."""
        with self._engine.connect() as connection:
            collected = _collected(
                connection=connection,
                deployment_id=forget.deployment_id,
                forget_id=forget.forget_id,
            )
            return _blocking_paths(
                connection=connection, forget=forget, collected=collected
            )

    def authored_artifact_ids(self, *, forget: Forget) -> tuple[UUID, ...]:
        """The authored artifacts collected as citing the document."""
        with self._engine.connect() as connection:
            collected = _collected(
                connection=connection,
                deployment_id=forget.deployment_id,
                forget_id=forget.forget_id,
            )
        return tuple(sorted((UUID(key) for key in collected["authored"]), key=str))

    def finish(
        self, *, forget: Forget, blocking_k_paths: tuple[str, ...]
    ) -> ForgetStatus:
        """A clean sweep after the quiet period: complete, or wait for redaction."""
        status = (
            ForgetStatus.WAITING_FOR_REDACTION
            if blocking_k_paths
            else ForgetStatus.COMPLETE
        )
        with self._engine.begin() as connection:
            connection.execute(
                text("""UPDATE forgets SET status = CAST(:status AS forget_status),
                blocking_k_paths = CAST(:paths AS text[]), last_error = NULL,
                completed_at = CASE WHEN :status = 'complete'
                                    THEN COALESCE(completed_at, now()) END
                WHERE deployment_id = :d AND forget_id = :f"""),
                {
                    "d": forget.deployment_id,
                    "f": forget.forget_id,
                    "status": status.value,
                    "paths": sorted(blocking_k_paths),
                },
            )
        return status

    def live_originals(self, *, forget: Forget) -> frozenset[str]:
        """Original keys under the document's prefix another lineage still uses."""
        with self._engine.connect() as connection:
            return frozenset(
                str(value)
                for value in connection.execute(
                    text("""SELECT raw_uri FROM content_objects
                    WHERE deployment_id = :d AND purged_at IS NULL
                      AND (raw_uri LIKE CAST(:doc AS text) || '/%'
                           OR raw_uri LIKE 'uploads/%')
                      AND content_hash = ANY(CAST(:hashes AS text[]))"""),
                    {
                        "d": forget.deployment_id,
                        "doc": forget.doc_id,
                        "hashes": list(forget.content_hashes),
                    },
                ).scalars()
            )

    def collected_artifact_ids(self, *, forget: Forget) -> tuple[UUID, ...]:
        """The compiled K artifacts the record names, for a reapply."""
        with self._engine.connect() as connection:
            collected = _collected(
                connection=connection,
                deployment_id=forget.deployment_id,
                forget_id=forget.forget_id,
            )
        return tuple(UUID(value) for value in collected["k_artifact_ids"])

    def schedule(self, *, work: EnqueueWork) -> None:
        """Enqueue one sweep outside a handler's completion transaction."""
        with self._engine.begin() as connection:
            enqueue_on(connection=connection, work=work)

    def record_error(self, *, forget: Forget, code: str) -> None:
        """Keep the content-free code of a failed sweep visible on the record."""
        with self._engine.begin() as connection:
            connection.execute(
                text("""UPDATE forgets SET last_error = :code
                WHERE deployment_id = :d AND forget_id = :f"""),
                {"d": forget.deployment_id, "f": forget.forget_id, "code": code},
            )


def guard_forgotten_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    source_kind: str,
    source_ref: str,
    content_hash: str,
) -> None:
    """Raise ``DocumentForgottenError`` when a record guards this identity or hash.

    Called before bytes are written and again at registration under the
    lineage row lock. A record's released hash or identity no longer guards
    (a deliberate re-add); the sweep keys never change.
    """
    identity = (
        None
        if source_kind == UPLOAD_SOURCE_KIND
        else source_identity_hash(
            deployment_id=deployment_id, source_kind=source_kind, source_ref=source_ref
        )
    )
    row = (
        connection.execute(
            _FORGOTTEN_INGEST,
            {"d": deployment_id, "identity": identity, "content_hash": content_hash},
        )
        .mappings()
        .first()
    )
    if row is not None:
        raise DocumentForgottenError(
            forget_id=row["forget_id"], forgotten_at=row["requested_at"]
        )


def retry_delay(*, quiet_period_s: float, failures: int) -> timedelta:
    """The quiet period, doubled per consecutive failed sweep, capped at 64x."""
    return timedelta(seconds=quiet_period_s * 2 ** min(failures, 6))


def utc_now() -> datetime:
    """The application clock used to schedule the next sweep."""
    return datetime.now(timezone.utc)


class _Sweep:
    """One sweep's PostgreSQL step inside one transaction."""

    def __init__(self, *, connection: Connection, forget: Forget) -> None:
        self._c = connection
        self._forget = forget
        self._removed = 0

    def run(self) -> SweepFindings:
        forget = self._forget
        self._c.execute(
            text("""SELECT 1 FROM forgets WHERE deployment_id = :d AND forget_id = :f
            FOR UPDATE"""),
            {"d": forget.deployment_id, "f": forget.forget_id},
        )
        collected = _collected(
            connection=self._c,
            deployment_id=forget.deployment_id,
            forget_id=forget.forget_id,
        )
        p = self._collect(collected=collected)
        self._c.execute(
            text("""UPDATE forgets SET collected = CAST(:collected AS jsonb)
            WHERE deployment_id = :d AND forget_id = :f"""),
            {
                "d": forget.deployment_id,
                "f": forget.forget_id,
                "collected": json.dumps(collected, sort_keys=True),
            },
        )
        self._exec(_SKIP_DOCUMENT_WORK, p, residue=False)
        profile_entities = self._shared_wording(p=p)
        self._exclusive_facts(p=p)
        self._entities(p=p)
        compiled = self._knowledge(p=p)
        for statement in _SUBTREE:
            self._exec(statement, p)
        self._retire_originals(p=p, collected=collected)
        for statement in _DEPLOYMENT_FIXTURES:
            self._exec(statement, p, residue=False)
        self._stale_labels(collected=collected)
        self._c.execute(
            text("""UPDATE forgets SET collected = CAST(:collected AS jsonb)
            WHERE deployment_id = :d AND forget_id = :f"""),
            {
                "d": forget.deployment_id,
                "f": forget.forget_id,
                "collected": json.dumps(collected, sort_keys=True),
            },
        )
        return SweepFindings(
            removed=self._removed > 0,
            object_keys=tuple(ObjectKey(key) for key in collected["object_keys"]),
            compiled_artifact_ids=compiled,
            blocking_k_paths=_blocking_paths(
                connection=self._c, forget=forget, collected=collected
            ),
            profile_entity_ids=profile_entities,
        )

    def _exec(
        self, statement: Any, params: dict[str, Any], residue: bool = True
    ) -> int:  # noqa: ANN401
        count = self._c.execute(statement, params).rowcount
        if residue and count > 0:
            self._removed += count
        return count

    def _ids(self, statement: str, params: dict[str, Any]) -> list[UUID]:
        return sorted(
            {
                UUID(str(value))
                for value in self._c.execute(text(statement), params).scalars()
            },
            key=str,
        )

    def _collect(self, *, collected: dict[str, Any]) -> dict[str, Any]:
        """Step 0: IDs only, gathered before the deletes remove their links."""
        forget = self._forget
        p: dict[str, Any] = {
            "deployment_id": forget.deployment_id,
            "doc_id": forget.doc_id,
            "version_ids": list(forget.version_ids),
            "content_hashes": list(forget.content_hashes),
        }
        p["version_ids"] = sorted(
            set(p["version_ids"])
            | set(
                self._ids(
                    "SELECT version_id FROM document_versions"
                    " WHERE deployment_id=:deployment_id AND doc_id=:doc_id",
                    p,
                )
            ),
            key=str,
        )
        p["claim_ids"] = self._ids(
            "SELECT claim_id FROM claims WHERE deployment_id=:deployment_id AND doc_id=:doc_id",
            p,
        )
        p["chunk_ids"] = self._ids(
            "SELECT chunk_id FROM chunks WHERE deployment_id=:deployment_id AND doc_id=:doc_id",
            p,
        )
        p["mention_ids"] = self._ids(
            "SELECT mention_id FROM mentions"
            " WHERE deployment_id=:deployment_id AND doc_id=:doc_id",
            p,
        )
        p["forgotten_doc_ids"] = self._ids(
            "SELECT doc_id FROM forgets WHERE deployment_id=:deployment_id", p
        )
        linked_relations = self._ids(_LINKED_RELATIONS, p)
        linked_observations = self._ids(_LINKED_OBSERVATIONS, p)
        p["linked_fact_ids"] = sorted(
            set(linked_relations) | set(linked_observations), key=str
        )
        p["fact_ids"] = sorted(
            set(p["linked_fact_ids"])
            | {UUID(value) for value in collected["fact_ids"]},
            key=str,
        )
        p["exclusive_fact_ids"] = self._ids(_EXCLUSIVE_FACTS, p)
        p["shared_fact_ids"] = sorted(
            set(p["linked_fact_ids"]) - set(p["exclusive_fact_ids"]), key=str
        )
        resolved = self._ids(_RESOLVED_ENTITIES, p)
        endpoints = self._ids(_FACT_ENDPOINTS, p)
        p["resolved_entity_ids"] = sorted(set(resolved) | set(endpoints), key=str)
        p["entity_ids"] = sorted(
            set(p["resolved_entity_ids"])
            | {UUID(value) for value in collected["entity_ids"]},
            key=str,
        )
        p["exclusive_entity_ids"] = self._ids(_EXCLUSIVE_ENTITIES, p)
        compiled = self._ids(
            _CITING_ARTIFACTS.format(facts="linked_fact_ids")
            + " AND a.page_kind = 'compiled'",
            p,
        )
        authored = self._ids(
            _CITING_ARTIFACTS.format(facts="fact_ids")
            + " AND a.page_kind = 'authored'",
            p,
        )
        p["compiled_artifact_ids"] = compiled
        for artifact_id in authored:
            if str(artifact_id) not in collected["authored"]:
                collected["authored"][str(artifact_id)] = self._c.execute(
                    text(
                        "SELECT content_hash FROM knowledge_artifacts"
                        " WHERE deployment_id=:deployment_id AND artifact_id=:a"
                    ),
                    {**p, "a": artifact_id},
                ).scalar_one_or_none()
        p["k_artifact_ids"] = sorted(
            set(compiled) | {UUID(value) for value in collected["k_artifact_ids"]},
            key=str,
        )
        object_keys = {
            str(value)
            for value in self._c.execute(_OWNED_ORIGINALS, p).scalars()
            if value is not None
        }
        collected["fact_ids"] = sorted(
            {*collected["fact_ids"], *map(str, p["fact_ids"])}
        )
        collected["entity_ids"] = sorted(
            {*collected["entity_ids"], *map(str, p["entity_ids"])}
        )
        collected["k_artifact_ids"] = sorted(
            {*collected["k_artifact_ids"], *map(str, p["k_artifact_ids"])}
        )
        collected["object_keys"] = sorted({*collected["object_keys"], *object_keys})
        return p

    def _shared_wording(self, *, p: dict[str, Any]) -> tuple[UUID, ...]:
        """Recompute or clear wording that came from the document (D159 §3.3)."""
        self._exec(_CLEAR_APPLICATION_INPUTS, p)
        self._exec(_REPLACE_SHARED_STATEMENTS, p)
        for kind, table in (("relation", "relations"), ("observation", "observations")):
            label_clear = (
                "fact_label=NULL,fact_label_version=NULL,"
                if kind == "relation"
                else "obs_label=NULL,"
            )
            self._exec(
                text(f"""UPDATE {table} SET
                  valid_from=NULL,valid_until=NULL,valid_precision='unknown',
                  window_claim_ids='{{}}'
                  WHERE deployment_id=:deployment_id
                    AND window_claim_ids && CAST(:claim_ids AS uuid[])"""),
                p,
            )
            self._exec(
                text(f"""UPDATE {table} SET {label_clear}
                  embedding=NULL,embedding_model=NULL,embedding_input_policy_version=NULL,
                  embedding_text_hash=NULL,period_basis=NULL,updated_at=now()
                  WHERE deployment_id=:deployment_id
                    AND {kind}_id=ANY(CAST(:shared_fact_ids AS uuid[]))"""),
                p,
            )
            self._exec(
                text("""DELETE FROM fact_window_review_attempts
                  WHERE deployment_id=:deployment_id AND fact_kind=:kind
                    AND fact_id=ANY(CAST(:shared_fact_ids AS uuid[]))"""),
                {**p, "kind": kind},
            )
            self._exec(
                text(f"""UPDATE {kind}_adjudications SET
                  triggering_claim_id=NULL, features=NULL, consumed_claim_ids='{{}}'
                  WHERE deployment_id=:deployment_id AND
                    (consumed_claim_ids && CAST(:claim_ids AS uuid[])
                     OR triggering_claim_id=ANY(CAST(:claim_ids AS uuid[])))"""),
                p,
            )
        cleared = self._c.execute(_CLEAR_PROFILES, p).scalars().all()
        self._removed += len(cleared)
        return tuple(sorted({UUID(str(value)) for value in cleared}, key=str))

    def _exclusive_facts(self, *, p: dict[str, Any]) -> None:
        """Delete facts only this document evidenced, with their adjudications."""
        p["exclusive_predicates"] = list(
            self._c.execute(
                text("""SELECT DISTINCT predicate FROM relations
                WHERE deployment_id=:deployment_id
                  AND relation_id=ANY(CAST(:exclusive_fact_ids AS uuid[]))"""),
                p,
            ).scalars()
        )
        for statement in _EXCLUSIVE_FACT_DELETES:
            self._exec(statement, p)
        self._exec(_PRUNE_PREDICATES, p)

    def _entities(self, *, p: dict[str, Any]) -> None:
        """Retire exclusive entities content-free; repair shared names and aliases."""
        p["doc_lemmas"] = list(
            self._c.execute(
                text("""SELECT DISTINCT lemma FROM mentions m
                CROSS JOIN LATERAL (VALUES (m.normalized_lemma),
                  (lower(m.canonical_name_form))) v(lemma)
                WHERE m.deployment_id=:deployment_id AND m.doc_id=:doc_id
                  AND v.lemma IS NOT NULL"""),
                p,
            ).scalars()
        )
        for statement in _ENTITY_STATEMENTS:
            self._exec(statement, p)

    def _knowledge(self, *, p: dict[str, Any]) -> tuple[UUID, ...]:
        """Remove compiled-page citations; compiled pages are rebuilt (step 4)."""
        if not p["compiled_artifact_ids"]:
            return ()
        self._exec(_DELETE_COMPILED_CITATIONS, p)
        for statement in _COMPILED_ARTIFACT_STATEMENTS:
            self._exec(statement, p)
        return tuple(p["compiled_artifact_ids"])

    def _retire_originals(
        self, *, p: dict[str, Any], collected: dict[str, Any]
    ) -> None:
        """Retire raw originals no surviving version uses; keep their keys."""
        hashes = [value for value in p["content_hashes"] if value]
        if not hashes:
            return
        # Lock the content rows first, then re-check in a new statement: an
        # ingest attaching to a row locks it too, so it either registered its
        # version before (the hash stays in use) or sees the row retired.
        self._c.execute(
            text("""SELECT content_hash FROM content_objects
            WHERE deployment_id=:deployment_id
              AND content_hash=ANY(CAST(:hashes AS text[]))
            ORDER BY content_hash FOR UPDATE"""),
            {**p, "hashes": hashes},
        )
        retired = (
            self._c.execute(_RETIRE_ORIGINALS, {**p, "hashes": hashes}).scalars().all()
        )
        self._removed += len(retired)
        collected["object_keys"] = sorted(
            {*collected["object_keys"], *(str(key) for key in retired if key)}
        )

    def _stale_labels(self, *, collected: dict[str, Any]) -> None:
        """Clear a collected relation label that no longer matches its parts.

        A label job started before the hide can write the old wording back
        after a sweep. Labels are deterministic, so a label that differs from
        the one its current names would give is residue; an ordinary relabel
        is not.
        """
        rows = self._c.execute(
            text("""SELECT r.relation_id, r.fact_label, r.predicate,
                   s.canonical_name AS subject, o.canonical_name AS object
            FROM relations r
            JOIN entities s ON s.entity_id = r.subject_entity_id
            JOIN entities o ON o.entity_id = r.object_entity_id
            WHERE r.deployment_id=:d AND r.fact_label IS NOT NULL
              AND r.relation_id=ANY(CAST(:ids AS uuid[]))"""),
            {"d": self._forget.deployment_id, "ids": collected["fact_ids"]},
        ).mappings()
        stale = [
            row["relation_id"]
            for row in rows
            if row["fact_label"]
            != deterministic_fact_label(
                subject=row["subject"],
                predicate=row["predicate"],
                object_name=row["object"],
            )
        ]
        if stale:
            self._exec(
                text("""UPDATE relations SET fact_label=NULL, fact_label_version=NULL,
                  embedding=NULL, embedding_model=NULL,
                  embedding_input_policy_version=NULL, embedding_text_hash=NULL,
                  updated_at=now()
                WHERE deployment_id=:deployment_id
                  AND relation_id=ANY(CAST(:ids AS uuid[]))"""),
                {"deployment_id": self._forget.deployment_id, "ids": stale},
            )


def _collected(
    *, connection: Connection, deployment_id: UUID, forget_id: UUID
) -> dict[str, Any]:
    """The record's collected IDs, with every key present."""
    value = connection.execute(
        text("SELECT collected FROM forgets WHERE deployment_id=:d AND forget_id=:f"),
        {"d": deployment_id, "f": forget_id},
    ).scalar_one()
    collected: dict[str, Any] = dict(value or {})
    for key in ("fact_ids", "entity_ids", "k_artifact_ids", "object_keys"):
        collected[key] = sorted(collected.get(key) or ())
    collected["authored"] = dict(collected.get("authored") or {})
    return collected


def _blocking_paths(
    *, connection: Connection, forget: Forget, collected: dict[str, Any]
) -> tuple[str, ...]:
    """Collected authored pages whose current file still cites the document.

    A page still cites while a synced citation names the document, or while
    its content is unchanged since it was collected (its citation of an
    erased fact went with the fact, but the owner has not edited it yet).
    Compiled-page curation sidecars are not inspected here.
    """
    authored = collected["authored"]
    if not authored:
        return ()
    rows = connection.execute(
        text("""SELECT a.artifact_id, a.git_path, a.content_hash,
               EXISTS (SELECT 1 FROM knowledge_artifact_evidence e
                       WHERE e.deployment_id = a.deployment_id
                         AND e.artifact_id = a.artifact_id
                         AND (e.doc_id = :doc OR e.claim_lineage_id = :doc
                              OR e.relation_id = ANY(CAST(:facts AS uuid[]))))
                 AS cites
        FROM knowledge_artifacts a
        WHERE a.deployment_id = :d AND a.artifact_id = ANY(CAST(:ids AS uuid[]))"""),
        {
            "d": forget.deployment_id,
            "doc": forget.doc_id,
            "facts": collected["fact_ids"],
            "ids": sorted(authored),
        },
    ).mappings()
    paths: set[str] = set()
    for row in rows:
        unchanged = row["content_hash"] == authored.get(str(row["artifact_id"]))
        if row["cites"] or unchanged:
            paths.add(str(row["git_path"]))
    return tuple(sorted(paths))


def _forget(*, row: RowMapping) -> Forget:
    """Validate one database row as the typed record."""
    return Forget(
        forget_id=row["forget_id"],
        deployment_id=row["deployment_id"],
        doc_id=row["doc_id"],
        version_ids=tuple(row["version_ids"]),
        content_hashes=tuple(row["content_hashes"]),
        source_identity_hash=row["source_identity_hash"],
        status=ForgetStatus(str(row["status"])),
        blocking_k_paths=tuple(row["blocking_k_paths"]),
        last_error=row["last_error"],
        requested_at=row["requested_at"],
        completed_at=row["completed_at"],
    )


_SELECT = """
    SELECT forget_id, deployment_id, doc_id, version_ids, content_hashes,
           source_identity_hash, status::text AS status, blocking_k_paths,
           last_error, requested_at, completed_at
    FROM forgets
"""

_INSERT_RECORD = text(
    """
    INSERT INTO forgets (
        forget_id, deployment_id, doc_id, version_ids, content_hashes,
        source_identity_hash
    )
    SELECT :forget_id, :d, :doc,
           ARRAY(SELECT version_id FROM document_versions
                 WHERE deployment_id = :d AND doc_id = :doc ORDER BY version_id),
           ARRAY(SELECT DISTINCT content_hash FROM document_versions
                 WHERE deployment_id = :d AND doc_id = :doc ORDER BY content_hash),
           :identity
    ON CONFLICT (deployment_id, doc_id) DO NOTHING
    RETURNING forget_id, deployment_id, doc_id, version_ids, content_hashes,
              source_identity_hash, status::text AS status, blocking_k_paths,
              last_error, requested_at, completed_at
    """
)

_FORGOTTEN_INGEST = text(
    """
    SELECT forget_id, requested_at
    FROM forgets
    WHERE deployment_id = :d
      AND (
        (CAST(:identity AS text) IS NOT NULL
         AND source_identity_hash = :identity AND NOT identity_released)
        OR (:content_hash = ANY(content_hashes)
            AND NOT :content_hash = ANY(released_hashes))
      )
    ORDER BY requested_at, forget_id
    LIMIT 1
    """
)

_K_PATHS_FOR_ARTIFACTS = text(
    """
    SELECT git_path FROM knowledge_artifacts
    WHERE deployment_id = :deployment_id AND artifact_id = ANY(:artifact_ids)
    UNION
    SELECT curation_path FROM knowledge_artifacts
    WHERE deployment_id = :deployment_id AND artifact_id = ANY(:artifact_ids)
      AND curation_path IS NOT NULL
    """
)

_SKIP_DOCUMENT_WORK = text(
    """
    UPDATE processing_state
    SET status = 'skipped', defer_reason = NULL, lease_until = NULL,
        finished_at = now(), payload = NULL, last_error = NULL
    WHERE deployment_id = :deployment_id
      AND stage <> 'forget'
      AND status IN ('pending', 'failed', 'running')
      AND """
    + FORGOTTEN_WORK.format(work="processing_state")
)

# Facts the document's evidence or its claims' windows touch.
_LINKED_RELATIONS = """
    SELECT relation_id FROM relation_evidence
    WHERE deployment_id=:deployment_id
      AND (doc_id=:doc_id OR claim_id=ANY(CAST(:claim_ids AS uuid[])))
    UNION
    SELECT relation_id FROM relations
    WHERE deployment_id=:deployment_id AND window_claim_ids && CAST(:claim_ids AS uuid[])
"""

_LINKED_OBSERVATIONS = """
    SELECT observation_id FROM observation_evidence
    WHERE deployment_id=:deployment_id
      AND (doc_id=:doc_id OR claim_id=ANY(CAST(:claim_ids AS uuid[])))
    UNION
    SELECT observation_id FROM observations
    WHERE deployment_id=:deployment_id AND window_claim_ids && CAST(:claim_ids AS uuid[])
"""

# A fact is exclusive when no other, not forgotten document supports it
# (current or archived testimony alike). Counterevidence alone cannot keep
# the forgotten assertion's wording.
_EXCLUSIVE_FACTS = """
    SELECT r.relation_id FROM relations r
    WHERE r.deployment_id=:deployment_id
      AND r.relation_id=ANY(CAST(:fact_ids AS uuid[]))
      AND NOT EXISTS (
        SELECT 1 FROM relation_evidence e
        WHERE e.deployment_id=r.deployment_id AND e.relation_id=r.relation_id
          AND e.stance='supports' AND e.doc_id<>:doc_id
          AND NOT e.doc_id=ANY(CAST(:forgotten_doc_ids AS uuid[]))
          AND NOT e.claim_id=ANY(CAST(:claim_ids AS uuid[])))
    UNION
    SELECT o.observation_id FROM observations o
    WHERE o.deployment_id=:deployment_id
      AND o.observation_id=ANY(CAST(:fact_ids AS uuid[]))
      AND NOT EXISTS (
        SELECT 1 FROM observation_evidence e
        WHERE e.deployment_id=o.deployment_id AND e.observation_id=o.observation_id
          AND e.stance='supports' AND e.doc_id<>:doc_id
          AND NOT e.doc_id=ANY(CAST(:forgotten_doc_ids AS uuid[]))
          AND NOT e.claim_id=ANY(CAST(:claim_ids AS uuid[])))
"""

_RESOLVED_ENTITIES = """
    SELECT DISTINCT d.entity_id FROM resolution_decisions d
    WHERE d.deployment_id=:deployment_id
      AND d.mention_id=ANY(CAST(:mention_ids AS uuid[]))
"""

_FACT_ENDPOINTS = """
    SELECT subject_entity_id FROM relations
    WHERE deployment_id=:deployment_id AND relation_id=ANY(CAST(:linked_fact_ids AS uuid[]))
    UNION
    SELECT object_entity_id FROM relations
    WHERE deployment_id=:deployment_id AND relation_id=ANY(CAST(:linked_fact_ids AS uuid[]))
    UNION
    SELECT subject_entity_id FROM observations
    WHERE deployment_id=:deployment_id
      AND observation_id=ANY(CAST(:linked_fact_ids AS uuid[]))
"""

# An entity is exclusive when it was resolved from this document's mentions
# and from no mention of another, not forgotten document, and no surviving
# fact uses it.
_EXCLUSIVE_ENTITIES = """
    SELECT e.entity_id FROM entities e
    WHERE e.deployment_id=:deployment_id
      AND e.entity_id=ANY(CAST(:entity_ids AS uuid[]))
      AND e.status <> 'retired'
      AND EXISTS (
        SELECT 1 FROM resolution_decisions d JOIN mentions m
          ON m.deployment_id=d.deployment_id AND m.mention_id=d.mention_id
        WHERE d.deployment_id=e.deployment_id AND d.entity_id=e.entity_id
          AND m.doc_id=:doc_id)
      AND NOT EXISTS (
        SELECT 1 FROM resolution_decisions d JOIN mentions m
          ON m.deployment_id=d.deployment_id AND m.mention_id=d.mention_id
        WHERE d.deployment_id=e.deployment_id AND d.entity_id=e.entity_id
          AND d.superseded_by IS NULL AND m.doc_id<>:doc_id
          AND NOT m.doc_id=ANY(CAST(:forgotten_doc_ids AS uuid[])))
      AND NOT EXISTS (
        SELECT 1 FROM relations r
        WHERE r.deployment_id=e.deployment_id
          AND (r.subject_entity_id=e.entity_id OR r.object_entity_id=e.entity_id)
          AND NOT r.relation_id=ANY(CAST(:exclusive_fact_ids AS uuid[])))
      AND NOT EXISTS (
        SELECT 1 FROM observations o
        WHERE o.deployment_id=e.deployment_id AND o.subject_entity_id=e.entity_id
          AND NOT o.observation_id=ANY(CAST(:exclusive_fact_ids AS uuid[])))
"""

# K pages citing the document's evidence or a fact it touched. Compiled pages
# are rebuilt when a sweep finds such a link; authored pages are listed for
# their owner when they cite the document or any collected fact.
_CITING_ARTIFACTS = """
    SELECT DISTINCT a.artifact_id FROM knowledge_artifacts a
    JOIN knowledge_artifact_evidence e
      ON e.deployment_id=a.deployment_id AND e.artifact_id=a.artifact_id
    WHERE a.deployment_id=:deployment_id
      AND (e.doc_id=:doc_id OR e.claim_lineage_id=:doc_id
           OR e.relation_id=ANY(CAST(:{facts} AS uuid[])))
"""

# Opaque keys of originals the document owns: its own prefix or a D147
# direct-upload locator. A shared original keeps the key it was first stored
# under and is never touched while another lineage uses it.
_OWNED_ORIGINALS = text(
    """
    SELECT raw_uri FROM content_objects
    WHERE deployment_id=:deployment_id
      AND content_hash=ANY(CAST(:content_hashes AS text[]))
      AND (raw_uri LIKE CAST(:doc_id AS text) || '/%' OR raw_uri LIKE 'uploads/%')
    """
)

_CLEAR_APPLICATION_INPUTS = text(
    """
    UPDATE fact_applications SET
      prepared=NULL, decision=NULL, attempt_id=NULL, input_hash=NULL,
      input_claim_ids=ARRAY(SELECT id FROM unnest(input_claim_ids) id
                            WHERE NOT id=ANY(CAST(:claim_ids AS uuid[])) ORDER BY id)
    WHERE deployment_id=:deployment_id
      AND input_claim_ids && CAST(:claim_ids AS uuid[])
    """
)

# A shared observation's statement is rewritten from a surviving supporter.
_REPLACE_SHARED_STATEMENTS = text(
    """
    WITH replacements AS (
      SELECT f.observation_id, replacement.statement
      FROM observations f
      CROSS JOIN LATERAL (
        SELECT coalesce(n.output->'observations'->a.output_ordinal->>'statement',
                        c.claim_text) AS statement
        FROM observation_evidence e
        JOIN claims c ON c.deployment_id=e.deployment_id AND c.claim_id=e.claim_id
        LEFT JOIN fact_applications a ON a.deployment_id=e.deployment_id
          AND a.claim_id=e.claim_id AND a.support_observation_id=e.observation_id
          AND a.applied_at IS NOT NULL AND a.support_stance='supports'
        LEFT JOIN normalization_outputs n ON n.deployment_id=a.deployment_id
          AND n.claim_id=a.claim_id AND n.normalizer_version=a.normalizer_version
        WHERE e.deployment_id=f.deployment_id AND e.observation_id=f.observation_id
          AND e.stance='supports' AND e.doc_id<>:doc_id
          AND NOT e.doc_id=ANY(CAST(:forgotten_doc_ids AS uuid[]))
          AND NOT e.claim_id=ANY(CAST(:claim_ids AS uuid[]))
        ORDER BY c.is_current_testimony DESC, (a.application_id IS NULL),
                 c.asserted_at NULLS LAST, c.claim_id, a.application_id
        LIMIT 1
      ) replacement
      WHERE f.deployment_id=:deployment_id
        AND f.observation_id=ANY(CAST(:shared_fact_ids AS uuid[]))
        AND f.statement IS DISTINCT FROM replacement.statement
    ) UPDATE observations f SET statement=replacements.statement, obs_label=NULL,
      embedding=NULL, embedding_model=NULL, embedding_input_policy_version=NULL,
      embedding_text_hash=NULL, updated_at=now()
    FROM replacements
    WHERE f.deployment_id=:deployment_id AND f.observation_id=replacements.observation_id
    """
)

# Surviving entities the document touched lose their profile and vector until
# ordinary work recomputes them from what survives.
_CLEAR_PROFILES = text(
    """
    WITH RECURSIVE touched(entity_id) AS (
      SELECT entity_id FROM entities
      WHERE deployment_id=:deployment_id
        AND entity_id=ANY(CAST(:resolved_entity_ids AS uuid[]))
      UNION
      SELECT e.merged_into FROM entities e JOIN touched t USING (entity_id)
      WHERE e.deployment_id=:deployment_id AND e.merged_into IS NOT NULL
    )
    UPDATE entities SET profile_summary=NULL, embedding=NULL, embedding_model=NULL,
      embedding_input_policy_version=NULL, embedding_text_hash=NULL, updated_at=now()
    WHERE deployment_id=:deployment_id
      AND entity_id IN (SELECT entity_id FROM touched)
      AND NOT entity_id=ANY(CAST(:exclusive_entity_ids AS uuid[]))
      AND (profile_summary IS NOT NULL OR embedding IS NOT NULL
           OR embedding_text_hash IS NOT NULL)
    RETURNING entity_id
    """
)

_EXCLUSIVE_FACT_DELETES = (
    text(
        """
        UPDATE relation_adjudications SET superseded_by = NULL
        WHERE deployment_id=:deployment_id AND superseded_by IN (
            SELECT adjudication_id FROM relation_adjudications
            WHERE deployment_id=:deployment_id
              AND (relation_id=ANY(CAST(:exclusive_fact_ids AS uuid[]))
                   OR related_relation_id=ANY(CAST(:exclusive_fact_ids AS uuid[]))))
        """
    ),
    text(
        """
        DELETE FROM relation_adjudications
        WHERE deployment_id=:deployment_id
          AND (relation_id=ANY(CAST(:exclusive_fact_ids AS uuid[]))
               OR related_relation_id=ANY(CAST(:exclusive_fact_ids AS uuid[])))
        """
    ),
    text(
        """
        UPDATE observation_adjudications SET superseded_by = NULL
        WHERE deployment_id=:deployment_id AND superseded_by IN (
            SELECT adjudication_id FROM observation_adjudications
            WHERE deployment_id=:deployment_id
              AND (observation_id=ANY(CAST(:exclusive_fact_ids AS uuid[]))
                   OR related_observation_id=ANY(CAST(:exclusive_fact_ids AS uuid[]))))
        """
    ),
    text(
        """
        DELETE FROM observation_adjudications
        WHERE deployment_id=:deployment_id
          AND (observation_id=ANY(CAST(:exclusive_fact_ids AS uuid[]))
               OR related_observation_id=ANY(CAST(:exclusive_fact_ids AS uuid[])))
        """
    ),
    text(
        """
        DELETE FROM fact_window_review_attempts
        WHERE deployment_id=:deployment_id
          AND fact_id=ANY(CAST(:exclusive_fact_ids AS uuid[]))
        """
    ),
    text(
        """
        DELETE FROM relation_evidence
        WHERE deployment_id=:deployment_id
          AND (doc_id=:doc_id OR relation_id=ANY(CAST(:exclusive_fact_ids AS uuid[])))
        """
    ),
    text(
        """
        DELETE FROM observation_evidence
        WHERE deployment_id=:deployment_id
          AND (doc_id=:doc_id
               OR observation_id=ANY(CAST(:exclusive_fact_ids AS uuid[])))
        """
    ),
    text(
        """
        DELETE FROM relations
        WHERE deployment_id=:deployment_id
          AND relation_id=ANY(CAST(:exclusive_fact_ids AS uuid[]))
        """
    ),
    text(
        """
        DELETE FROM observations
        WHERE deployment_id=:deployment_id
          AND observation_id=ANY(CAST(:exclusive_fact_ids AS uuid[]))
        """
    ),
)

# A source-generated `other:` predicate no remaining relation uses goes.
_PRUNE_PREDICATES = text(
    """
    DELETE FROM predicates p
    WHERE p.deployment_id=:deployment_id AND p.tier='other'
      AND p.predicate=ANY(CAST(:exclusive_predicates AS text[]))
      AND NOT EXISTS (SELECT 1 FROM relations r
                      WHERE r.deployment_id=p.deployment_id AND r.predicate=p.predicate)
      AND NOT EXISTS (SELECT 1 FROM predicates c
                      WHERE c.deployment_id=p.deployment_id
                        AND c.parent_predicate=p.predicate)
    """
)

_ENTITY_STATEMENTS = (
    # Exclusive entities: content-free retired rows (merge logs reference them).
    text(
        """
        DELETE FROM aliases
        WHERE deployment_id=:deployment_id
          AND entity_id=ANY(CAST(:exclusive_entity_ids AS uuid[]))
        """
    ),
    text(
        """
        UPDATE entities SET canonical_name='', normalized_name='', status='retired',
            merged_into=NULL, profile_summary=NULL, embedding=NULL,
            embedding_model=NULL, embedding_input_policy_version=NULL,
            embedding_text_hash=NULL, mention_count=0, graph_degree=0,
            updated_at=now()
        WHERE deployment_id=:deployment_id
          AND entity_id=ANY(CAST(:exclusive_entity_ids AS uuid[]))
          AND (status <> 'retired' OR canonical_name <> '' OR normalized_name <> '')
        """
    ),
    # Shared entities: aliases only this document's mentions named go, and a
    # canonical name that came only from it is replaced by a surviving alias.
    text(
        """
        DELETE FROM aliases a
        WHERE a.deployment_id=:deployment_id
          AND a.entity_id=ANY(CAST(:resolved_entity_ids AS uuid[]))
          AND a.normalized_lemma=ANY(CAST(:doc_lemmas AS text[]))
          AND NOT EXISTS (
            SELECT 1 FROM mentions m
            WHERE m.deployment_id=a.deployment_id AND m.doc_id<>:doc_id
              AND NOT m.doc_id=ANY(CAST(:forgotten_doc_ids AS uuid[]))
              AND (m.normalized_lemma=a.normalized_lemma
                   OR lower(m.canonical_name_form)=a.normalized_lemma))
        """
    ),
    text(
        """
        UPDATE entities e SET (canonical_name, normalized_name, updated_at) = (
          SELECT a.alias_text, a.normalized_lemma, now() FROM aliases a
          WHERE a.deployment_id=e.deployment_id AND a.entity_id=e.entity_id
          ORDER BY (a.provenance='llm_canonical') DESC, a.confidence DESC NULLS LAST,
                   a.last_seen DESC, a.alias_id
          LIMIT 1)
        WHERE e.deployment_id=:deployment_id
          AND e.entity_id=ANY(CAST(:resolved_entity_ids AS uuid[]))
          AND e.status <> 'retired'
          AND e.normalized_name=ANY(CAST(:doc_lemmas AS text[]))
          AND EXISTS (
            SELECT 1 FROM aliases a
            WHERE a.deployment_id=e.deployment_id AND a.entity_id=e.entity_id)
          AND NOT EXISTS (
            SELECT 1 FROM aliases a
            WHERE a.deployment_id=e.deployment_id AND a.entity_id=e.entity_id
              AND a.normalized_lemma=e.normalized_name)
        """
    ),
    text(
        """
        UPDATE merge_events
        SET trigger_lemmas = '{}', evidence = NULL,
            pre_merge_membership_snapshot = '{}'::jsonb
        WHERE deployment_id=:deployment_id
          AND (survivor_id=ANY(CAST(:exclusive_entity_ids AS uuid[]))
               OR absorbed_id=ANY(CAST(:exclusive_entity_ids AS uuid[]))
               OR EXISTS (
                 SELECT 1 FROM unnest(CAST(:mention_ids AS uuid[])) AS ids(value)
                 WHERE pre_merge_membership_snapshot::text
                       LIKE '%' || CAST(ids.value AS text) || '%'))
          AND (cardinality(trigger_lemmas) > 0 OR evidence IS NOT NULL
               OR pre_merge_membership_snapshot <> '{}'::jsonb)
        """
    ),
    text(
        """
        UPDATE resolution_exclusions SET reason = NULL
        WHERE deployment_id=:deployment_id AND reason IS NOT NULL
          AND (entity_id_low=ANY(CAST(:resolved_entity_ids AS uuid[]))
               OR entity_id_high=ANY(CAST(:resolved_entity_ids AS uuid[])))
        """
    ),
)

_DELETE_COMPILED_CITATIONS = text(
    """
    DELETE FROM knowledge_artifact_evidence
    WHERE deployment_id=:deployment_id
      AND artifact_id=ANY(CAST(:compiled_artifact_ids AS uuid[]))
      AND (doc_id=:doc_id OR claim_lineage_id=:doc_id
           OR relation_id=ANY(CAST(:exclusive_fact_ids AS uuid[])))
    """
)

_COMPILED_ARTIFACT_STATEMENTS = (
    text(
        """
        DELETE FROM knowledge_compilations
        WHERE deployment_id=:deployment_id
          AND artifact_id=ANY(CAST(:compiled_artifact_ids AS uuid[]))
        """
    ),
    text(
        """
        UPDATE knowledge_artifacts
        SET page_summary=NULL, content_hash=NULL, inputs_hash=NULL,
            last_compiled_at=NULL, status='stale'
        WHERE deployment_id=:deployment_id
          AND artifact_id=ANY(CAST(:compiled_artifact_ids AS uuid[]))
        """
    ),
    text(
        """
        DELETE FROM knowledge_refresh_queue refresh
        WHERE refresh.deployment_id=:deployment_id
          AND (refresh.artifact_id=ANY(CAST(:compiled_artifact_ids AS uuid[]))
               OR refresh.payload::text LIKE '%' || CAST(:doc_id AS text) || '%'
               OR EXISTS (
                 SELECT 1 FROM unnest(CAST(:claim_ids AS uuid[])
                   || CAST(:exclusive_fact_ids AS uuid[])) AS ids(value)
                 WHERE refresh.payload::text LIKE '%' || CAST(ids.value AS text) || '%'))
        """
    ),
    text(
        """
        DELETE FROM knowledge_dispatches dispatch
        WHERE dispatch.deployment_id=:deployment_id
          AND (dispatch.payload::text LIKE '%' || CAST(:doc_id AS text) || '%'
               OR EXISTS (
                 SELECT 1 FROM unnest(CAST(:claim_ids AS uuid[])
                   || CAST(:exclusive_fact_ids AS uuid[])) AS ids(value)
                 WHERE dispatch.payload::text LIKE '%' || CAST(ids.value AS text) || '%'))
        """
    ),
)

# The document's subtree, children before parents. Foreign-key cascades that
# exist inside the subtree (representations, sections, metadata, tags, ...)
# do part of the work when the versions and the document row go.
_SUBTREE = (
    text(
        "DELETE FROM selection_results WHERE deployment_id=:deployment_id"
        " AND chunk_id=ANY(CAST(:chunk_ids AS uuid[]))"
    ),
    text(
        """
        DELETE FROM application_context_bindings
        WHERE deployment_id=:deployment_id AND application_id IN (
          SELECT application_id FROM fact_applications
          WHERE deployment_id=:deployment_id
            AND claim_id=ANY(CAST(:claim_ids AS uuid[])))
        """
    ),
    text(
        "DELETE FROM fact_applications WHERE deployment_id=:deployment_id"
        " AND claim_id=ANY(CAST(:claim_ids AS uuid[]))"
    ),
    text(
        "DELETE FROM normalization_outputs WHERE deployment_id=:deployment_id"
        " AND claim_id=ANY(CAST(:claim_ids AS uuid[]))"
    ),
    text(
        "DELETE FROM normalize_observation_staging"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        """
        DELETE FROM obs_flush_entity_units
        WHERE deployment_id=:deployment_id
          AND (doc_id=:doc_id OR version_id=ANY(CAST(:version_ids AS uuid[])))
        """
    ),
    text(
        "DELETE FROM obs_flush_version_state WHERE deployment_id=:deployment_id"
        " AND version_id=ANY(CAST(:version_ids AS uuid[]))"
    ),
    text(
        "DELETE FROM processing_layer_gaps"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        "DELETE FROM document_entity_bindings"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        "DELETE FROM resolution_decisions WHERE deployment_id=:deployment_id"
        " AND mention_id=ANY(CAST(:mention_ids AS uuid[]))"
    ),
    text("DELETE FROM mentions WHERE deployment_id=:deployment_id AND doc_id=:doc_id"),
    text(
        "DELETE FROM grounding_audits WHERE deployment_id=:deployment_id"
        " AND claim_id=ANY(CAST(:claim_ids AS uuid[]))"
    ),
    text(
        "DELETE FROM claim_extraction_decisions"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        """
        DELETE FROM chunk_claims
        WHERE deployment_id=:deployment_id
          AND (chunk_id=ANY(CAST(:chunk_ids AS uuid[]))
               OR claim_id=ANY(CAST(:claim_ids AS uuid[])))
        """
    ),
    text("DELETE FROM claims WHERE deployment_id=:deployment_id AND doc_id=:doc_id"),
    text(
        "DELETE FROM chunk_search WHERE deployment_id=:deployment_id"
        " AND chunk_id=ANY(CAST(:chunk_ids AS uuid[]))"
    ),
    text("DELETE FROM chunks WHERE deployment_id=:deployment_id AND doc_id=:doc_id"),
    text(
        """
        UPDATE document_representations r SET current_structure_generation_id = NULL
        WHERE r.deployment_id=:deployment_id
          AND r.version_id=ANY(CAST(:version_ids AS uuid[]))
          AND r.current_structure_generation_id IS NOT NULL
        """
    ),
    text(
        "DELETE FROM document_sections"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        "DELETE FROM document_structure_generations"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        "DELETE FROM document_skeleton_checks"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        "DELETE FROM document_effective_periods"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        "DELETE FROM document_effective_time_events"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        "DELETE FROM document_version_scope"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        "DELETE FROM document_crossrefs"
        " WHERE deployment_id=:deployment_id AND from_doc_id=:doc_id"
    ),
    # D140 §9: references other lineages made TO the forgotten one are their
    # sources' content; they are unbound, keeping the target as the source
    # names it.
    text(
        """
        UPDATE document_crossrefs SET to_doc_id = NULL, resolved = false
        WHERE deployment_id=:deployment_id AND to_doc_id=:doc_id
        """
    ),
    text(
        "DELETE FROM document_reference_generations"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        """
        DELETE FROM document_names WHERE deployment_id=:deployment_id
          AND version_id=ANY(CAST(:version_ids AS uuid[]))
        """
    ),
    text(
        """
        DELETE FROM document_people WHERE deployment_id=:deployment_id
          AND version_id=ANY(CAST(:version_ids AS uuid[]))
        """
    ),
    text(
        "DELETE FROM document_metadata"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        "DELETE FROM document_tags WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        """
        UPDATE documents SET current_version_id = NULL
        WHERE deployment_id=:deployment_id AND doc_id=:doc_id
          AND current_version_id IS NOT NULL
        """
    ),
    text(
        "DELETE FROM document_versions"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text("DELETE FROM documents WHERE deployment_id=:deployment_id AND doc_id=:doc_id"),
    # Content-free work-ledger rows stay; their error text and payloads go.
    text(
        """
        UPDATE processing_state SET payload = NULL, last_error = NULL
        WHERE deployment_id=:deployment_id AND stage <> 'forget'
          AND (payload IS NOT NULL OR last_error IS NOT NULL)
          AND (target_id=:doc_id
               OR target_id=ANY(CAST(:version_ids AS uuid[]))
               OR target_id=ANY(CAST(:chunk_ids AS uuid[]))
               OR target_id=ANY(CAST(:claim_ids AS uuid[]))
               OR content_hash=ANY(CAST(:content_hashes AS text[]))
               OR payload->>'doc_id'=CAST(:doc_id AS text))
        """
    ),
    text(
        """
        DELETE FROM review_queue review
        WHERE review.deployment_id=:deployment_id
          AND (review.candidate::text LIKE '%' || CAST(:doc_id AS text) || '%'
               OR EXISTS (
                 SELECT 1 FROM unnest(CAST(:claim_ids AS uuid[])
                   || CAST(:mention_ids AS uuid[])
                   || CAST(:exclusive_fact_ids AS uuid[])
                   || CAST(:exclusive_entity_ids AS uuid[])) AS ids(value)
                 WHERE review.candidate::text
                       LIKE '%' || CAST(ids.value AS text) || '%'))
        """
    ),
)

# Raw originals no surviving version uses are retired; their keys are kept on
# the record and purged in step 2. Runs after the subtree is gone.
_RETIRE_ORIGINALS = text(
    """
    UPDATE content_objects object
    SET mime = 'application/x-forgotten', byte_size = NULL,
        raw_uri = 'forgotten/' || object.content_hash,
        purged_at = COALESCE(object.purged_at, now())
    FROM (SELECT content_hash, raw_uri FROM content_objects
          WHERE deployment_id=:deployment_id
            AND content_hash=ANY(CAST(:hashes AS text[]))) original
    WHERE object.deployment_id=:deployment_id
      AND object.content_hash=original.content_hash
      AND object.purged_at IS NULL
      AND NOT EXISTS (
        SELECT 1 FROM document_versions v
        WHERE v.deployment_id=object.deployment_id
          AND v.content_hash=object.content_hash)
    RETURNING original.raw_uri
    """
)

# The eval schema has no lineage-provenance column, so a forget drops the
# deployment's free-text eval fixtures and K plan transcripts rather than guess
# which prose came from the document (as D74 did). Not counted as residue.
_DEPLOYMENT_FIXTURES = (
    text("DELETE FROM eval_runs WHERE deployment_id=:deployment_id"),
    text("DELETE FROM canary_cases WHERE deployment_id=:deployment_id"),
    text("DELETE FROM golden_claim_labels WHERE deployment_id=:deployment_id"),
    text("DELETE FROM golden_pairs WHERE deployment_id=:deployment_id"),
    text("DELETE FROM knowledge_plan_decisions WHERE deployment_id=:deployment_id"),
    text("DELETE FROM knowledge_plan_runs WHERE deployment_id=:deployment_id"),
)
