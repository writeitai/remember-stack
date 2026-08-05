"""The saved-query registry: named queries with an append-only history (§5).

A saved query is something other people's work comes to depend on, so editing
one adds a version rather than changing what an earlier caller ran. Two rules
follow from that and drive everything here.

First, a version is only executable against the surface it was validated
against. Every version pins a `surface_manifest_hash`, and when that hash
changes, active versions move to `pending_revalidation` in the SAME transaction
that publishes the new hash — there is no instant at which a version claims a
validation nobody performed. That state is non-executable on purpose: a query
checked against a different surface is a query nobody has checked.

Second, authoring and activating are different acts by different people. An
agent may write drafts freely, within quotas; making one live is an operator's
decision, and the version records who made it. A registry where the author can
also approve is a registry whose approvals mean nothing.

The platform owns sandboxing, tenancy, D48 behaviour, limits, and truthful
provenance. The saved-query owner owns what the query MEANS — its filters,
joins, labels, and declared interpretation — and no amount of customer review
raises the result above `exploratory_tabular`, because review attests to intent
rather than to the platform's guarantees.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from typing import Final
from uuid import UUID
from uuid import uuid4

import psycopg

from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.grammar import validate_sql

#: §5 deployment bounds.
IDENTITIES_MAX: Final = 1_000
VERSIONS_PER_IDENTITY_MAX: Final = 50
SQL_BYTES_MAX: Final = 64 * 1024

#: §5 per-principal draft bounds. Drafts are cheap to make and an agent can
#: make them in a loop, so they are bounded separately from published work.
DRAFT_IDENTITIES_MAX: Final = 50
DRAFT_VERSIONS_MAX: Final = 200
IDENTITIES_PER_HOUR_MAX: Final = 10
DRAFT_BYTES_MAX: Final = 4 * 1024 * 1024

#: The states a version can be in, and which of them may run. A version that is
#: not exactly `active` does not execute — including `pending_revalidation`,
#: which exists precisely to stop execution.
EXECUTABLE_STATUSES: Final = frozenset({"active"})


@dataclass(frozen=True)
class SavedQueryVersion:
    """One immutable version, as the registry holds it."""

    query_id: UUID
    version: int
    namespace: str
    name: str
    sql: str
    query_hash: str
    parameter_schema: dict[str, Any]
    status: str
    validated_surface_manifest_hash: str
    assurance: str | None


class SavedQueryRegistry:
    """One deployment's saved queries.

    `connection` must already be scoped to the deployment: the connection IS
    the tenancy boundary here as everywhere else on this surface (§4.2), so
    nothing in this class takes a deployment from the caller.
    """

    def __init__(
        self, *, connection: psycopg.Connection, deployment_id: UUID, manifest_hash: str
    ) -> None:
        self._connection = connection
        self._deployment_id = deployment_id
        self._manifest_hash = manifest_hash

    # -- authoring ---------------------------------------------------------

    def draft(
        self,
        *,
        namespace: str,
        name: str,
        sql: str,
        principal: str,
        parameter_schema: dict[str, Any] | None = None,
        description: str | None = None,
        origin: str = "agent",
        declared_interpretation: str | None = None,
    ) -> SavedQueryVersion:
        """Write a new draft version, creating the identity if it is new.

        The SQL is validated against the same grammar an ad-hoc statement goes
        through, and the version pins the manifest hash it was validated
        against. Saving something that cannot be validated is refused here
        rather than discovered by whoever runs it later.
        """
        if len(sql.encode()) > SQL_BYTES_MAX:
            raise SandboxRejection(
                code=QueryErrorCode.RESOURCE_LIMIT,
                message=f"saved SQL is limited to {SQL_BYTES_MAX} bytes",
            )
        validated = validate_sql(sql)
        self._check_quotas(principal=principal, namespace=namespace, name=name)

        identity = self._identity(namespace=namespace, name=name)
        if identity is None:
            query_id = uuid4()
            self._connection.execute(
                b"INSERT INTO saved_queries (deployment_id, query_id, namespace,"
                b" name, description, owner_principal, origin)"
                b" VALUES (%(deployment)s, %(query)s, %(namespace)s, %(name)s,"
                b" %(description)s, %(principal)s, %(origin)s)",
                {
                    "deployment": str(self._deployment_id),
                    "query": str(query_id),
                    "namespace": namespace,
                    "name": name,
                    "description": description,
                    "principal": principal,
                    "origin": origin,
                },
            )
            version = 1
        else:
            query_id = identity
            version = self._next_version(query_id)

        self._connection.execute(
            b"INSERT INTO saved_query_versions (deployment_id, query_id, version,"
            b" sql, query_hash, parameter_schema, declared_interpretation,"
            b" query_space_major, validated_surface_manifest_hash, status,"
            b" author_principal)"
            b" VALUES (%(deployment)s, %(query)s, %(version)s, %(sql)s, %(hash)s,"
            b" %(schema)s::jsonb, %(interpretation)s, 'memory_v1', %(manifest)s,"
            b" 'draft', %(principal)s)",
            {
                "deployment": str(self._deployment_id),
                "query": str(query_id),
                "version": version,
                "sql": sql,
                "hash": validated.query_hash,
                "schema": _json(parameter_schema or {}),
                "interpretation": declared_interpretation,
                "manifest": self._manifest_hash,
                "principal": principal,
            },
        )
        self._connection.execute(
            b"UPDATE saved_queries SET latest_version = %(version)s"
            b" WHERE query_id = %(query)s",
            {"version": version, "query": str(query_id)},
        )
        return SavedQueryVersion(
            query_id=query_id,
            version=version,
            namespace=namespace,
            name=name,
            sql=sql,
            query_hash=validated.query_hash,
            parameter_schema=parameter_schema or {},
            status="draft",
            validated_surface_manifest_hash=self._manifest_hash,
            assurance=None,
        )

    def activate(
        self, *, query_id: UUID, version: int, approver: str, author: str
    ) -> None:
        """Make a version live. Only an operator may do this (§5).

        The approver is recorded, and may not be the author: a registry where
        the person who wrote a query can also approve it has approvals that
        attest to nothing.
        """
        if approver == author:
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message="a saved query is approved by someone other than its author",
            )
        row = self._connection.execute(
            b"SELECT status, validated_surface_manifest_hash"
            b" FROM saved_query_versions WHERE query_id = %(query)s"
            b"   AND version = %(version)s",
            {"query": str(query_id), "version": version},
        ).fetchone()
        if row is None:
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_NOT_FOUND,
                message="no such saved-query version",
            )
        if row[1] != self._manifest_hash:
            # Activating against a surface the version was not validated
            # against would publish exactly the claim this registry exists to
            # prevent.
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_REVALIDATION_PENDING,
                message="this version was validated against a different surface",
            )
        self._connection.execute(
            b"UPDATE saved_query_versions"
            b" SET status = 'active', approver_principal = %(approver)s"
            b" WHERE query_id = %(query)s AND version = %(version)s",
            {"approver": approver, "query": str(query_id), "version": version},
        )

    # -- execution ---------------------------------------------------------

    def resolve(self, *, namespace: str, name: str) -> SavedQueryVersion:
        """The version `run_saved_query` would execute, or a stated refusal.

        Every refusal names its reason: not found, disabled, awaiting
        revalidation, or incompatible. A caller who gets "no rows" from a query
        that was silently not run cannot tell that from an empty answer.
        """
        row = self._connection.execute(
            b"SELECT q.query_id, v.version, q.namespace, q.name, v.sql,"
            b" v.query_hash, v.parameter_schema, v.status,"
            b" v.validated_surface_manifest_hash, v.assurance, q.disabled_at"
            b" FROM saved_queries AS q"
            b" JOIN saved_query_versions AS v"
            b"   ON v.query_id = q.query_id AND v.version = q.latest_version"
            b" WHERE q.deployment_id = %(deployment)s"
            b"   AND q.namespace = %(namespace)s AND q.name = %(name)s",
            {
                "deployment": str(self._deployment_id),
                "namespace": namespace,
                "name": name,
            },
        ).fetchone()
        if row is None:
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_NOT_FOUND,
                message=f"no saved query named {namespace}.{name}",
            )
        if row[10] is not None:
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_DISABLED,
                message=f"{namespace}.{name} is disabled",
            )
        if row[7] == "pending_revalidation":
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_REVALIDATION_PENDING,
                message=(
                    f"{namespace}.{name} is awaiting revalidation against the"
                    " current surface"
                ),
            )
        if row[7] not in EXECUTABLE_STATUSES:
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_DISABLED,
                message=f"{namespace}.{name} is {row[7]} and does not execute",
            )
        if row[8] != self._manifest_hash:
            # Belt and braces: publication moves versions to
            # pending_revalidation, but a version that somehow reaches here
            # against another surface still does not run.
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_INCOMPATIBLE,
                message=f"{namespace}.{name} was validated against another surface",
            )
        return SavedQueryVersion(
            query_id=row[0],
            version=row[1],
            namespace=row[2],
            name=row[3],
            sql=row[4],
            query_hash=row[5],
            parameter_schema=row[6],
            status=row[7],
            validated_surface_manifest_hash=row[8],
            assurance=row[9],
        )

    # -- lifecycle ---------------------------------------------------------

    def disable(self, *, query_id: UUID) -> None:
        """Take an identity out of service at admission time (§5)."""
        self._connection.execute(
            b"UPDATE saved_queries SET disabled_at = now() WHERE query_id = %(query)s",
            {"query": str(query_id)},
        )

    def purge(self, *, query_id: UUID) -> None:
        """Hard-delete an identity's text (D74).

        Registry SQL can contain customer data — a WHERE clause naming a person
        is customer data — so a deletion removes the text rather than marking
        it. What remains is the audit trail's ids, hashes, actor, and action,
        which contain none of it.
        """
        self._connection.execute(
            b"DELETE FROM saved_queries WHERE query_id = %(query)s",
            {"query": str(query_id)},
        )

    # -- internals ---------------------------------------------------------

    def _identity(self, *, namespace: str, name: str) -> UUID | None:
        row = self._connection.execute(
            b"SELECT query_id FROM saved_queries"
            b" WHERE deployment_id = %(deployment)s AND namespace = %(namespace)s"
            b"   AND name = %(name)s",
            {
                "deployment": str(self._deployment_id),
                "namespace": namespace,
                "name": name,
            },
        ).fetchone()
        return row[0] if row else None

    def _next_version(self, query_id: UUID) -> int:
        row = self._connection.execute(
            b"SELECT coalesce(max(version), 0) + 1 FROM saved_query_versions"
            b" WHERE query_id = %(query)s",
            {"query": str(query_id)},
        ).fetchone()
        version = int(row[0]) if row else 1
        if version > VERSIONS_PER_IDENTITY_MAX:
            raise SandboxRejection(
                code=QueryErrorCode.QUOTA_EXCEEDED,
                message=(
                    f"a saved query keeps at most {VERSIONS_PER_IDENTITY_MAX} versions"
                ),
            )
        return version

    def _check_quotas(self, *, principal: str, namespace: str, name: str) -> None:
        """Every §5 registry bound, refused by name when it is reached."""
        counts = self._connection.execute(
            b"SELECT"
            b" (SELECT count(*) FROM saved_queries"
            b"   WHERE deployment_id = %(deployment)s),"
            b" (SELECT count(*) FROM saved_queries"
            b"   WHERE deployment_id = %(deployment)s"
            b"     AND owner_principal = %(principal)s"
            b"     AND created_at > now() - interval '1 hour'),"
            b" (SELECT count(DISTINCT v.query_id) FROM saved_query_versions AS v"
            b"   WHERE v.deployment_id = %(deployment)s AND v.status = 'draft'"
            b"     AND v.author_principal = %(principal)s),"
            b" (SELECT count(*) FROM saved_query_versions AS v"
            b"   WHERE v.deployment_id = %(deployment)s AND v.status = 'draft'"
            b"     AND v.author_principal = %(principal)s),"
            b" (SELECT coalesce(sum(octet_length(v.sql)), 0)"
            b"   FROM saved_query_versions AS v"
            b"   WHERE v.deployment_id = %(deployment)s AND v.status = 'draft'"
            b"     AND v.author_principal = %(principal)s)",
            {"deployment": str(self._deployment_id), "principal": principal},
        ).fetchone()
        assert counts is not None
        identities, per_hour, draft_identities, draft_versions, draft_bytes = counts
        new_identity = self._identity(namespace=namespace, name=name) is None

        for reached, limit, what in (
            (identities + (1 if new_identity else 0), IDENTITIES_MAX, "saved queries"),
            (
                per_hour + (1 if new_identity else 0),
                IDENTITIES_PER_HOUR_MAX,
                "new saved queries in an hour",
            ),
            (
                draft_identities + (1 if new_identity else 0),
                DRAFT_IDENTITIES_MAX,
                "draft saved queries",
            ),
            (draft_versions + 1, DRAFT_VERSIONS_MAX, "draft versions"),
        ):
            if reached > limit:
                raise SandboxRejection(
                    code=QueryErrorCode.QUOTA_EXCEEDED,
                    message=f"this principal may hold at most {limit} {what}",
                )
        if draft_bytes > DRAFT_BYTES_MAX:
            raise SandboxRejection(
                code=QueryErrorCode.QUOTA_EXCEEDED,
                message="this principal's drafts exceed their byte ceiling",
            )


def publish_surface_hash(
    *, connection: psycopg.Connection, deployment_id: UUID, manifest_hash: str
) -> int:
    """Move every active version to `pending_revalidation`, atomically (§5).

    This is the transition that must happen in the same transaction as making
    a new `surface_manifest_hash` visible. Between the two there must be no
    instant at which a version is executable while claiming validation against
    a surface that has been replaced — so the caller runs both inside one
    transaction and this function does not open its own.

    Returns how many versions were suspended, which the caller audits.
    """
    cursor = connection.execute(
        b"UPDATE saved_query_versions SET status = 'pending_revalidation'"
        b" WHERE deployment_id = %(deployment)s AND status = 'active'"
        b"   AND validated_surface_manifest_hash <> %(manifest)s",
        {"deployment": str(deployment_id), "manifest": manifest_hash},
    )
    return cursor.rowcount


def _json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, sort_keys=True)


def declared_examples() -> Sequence[tuple[str, str]]:
    """The `examples.*` names §5 ships, as (name, purpose) pairs.

    They are shipped_example assurance: the platform wrote them, so they are
    honest about what they compute, but they are still customer-editable
    starting points rather than platform operations. Copying one and changing
    its filters produces a customer-authored query, which is the intended use.
    """
    return (
        ("claims_verbatim", "Claims as asserted, with their source handles"),
        ("claims_hybrid_rrf", "Semantic and lexical claim channels, RRF-fused"),
        ("chunk_neighbors", "Current-section ordinal neighbours of a chunk"),
        ("facts_current", "The adjudicated current worldview"),
        ("facts_contradicted", "Facts with a live contradiction"),
        ("entity_documents", "Every live document that mentions an entity"),
        ("entity_profile", "An entity's current profile and counts"),
        ("evidence_for_fact", "The claim lineages a fact rests on"),
        ("document_timeline", "A lineage's versions in processing order"),
        ("recent_changes", "What the system learned most recently"),
    )
