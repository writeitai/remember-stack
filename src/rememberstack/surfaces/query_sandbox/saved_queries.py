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

from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from typing import Final
from typing import TYPE_CHECKING
from uuid import UUID
from uuid import uuid4

import psycopg

from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.grammar import validate_sql

if TYPE_CHECKING:
    from rememberstack.surfaces.query_sandbox.executor import QuerySandboxExecutor
    from rememberstack.surfaces.query_sandbox.result import QueryResult

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


@dataclass(frozen=True)
class SavedQuerySummary:
    """Registry metadata returned by discovery listing (§3.1, §5)."""

    query_id: UUID
    namespace: str
    name: str
    version: int
    status: str
    description: str | None
    origin: str
    assurance: str | None
    query_hash: str
    validated_surface_manifest_hash: str


@dataclass(frozen=True)
class SavedQueryDescription:
    """One immutable version as `describe_saved_query` returns it (§3.1)."""

    query_id: UUID
    namespace: str
    name: str
    version: int
    status: str
    description: str | None
    origin: str
    assurance: str | None
    sql: str
    query_hash: str
    parameter_schema: dict[str, Any]
    declared_interpretation: str | None
    validated_surface_manifest_hash: str
    validation_report: dict[str, Any]
    author_principal: str
    approver_principal: str | None


@dataclass(frozen=True)
class OperatorFixture:
    """One operator-owned case the saving validator must execute (§5).

    The operator owns the bound parameters (and the optional row cap for the
    cap case). The platform owns the judgment of each outcome. Parameters are
    always bound through the sandbox — never rendered into the SQL text.
    """

    kind: str
    parameters: tuple[object, ...] = ()
    max_rows: int | None = None


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
        report: "ValidationReport | None" = None,
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
        identity = self._identity(namespace=namespace, name=name)
        self._check_quotas(principal=principal, sql=sql, identity=identity)

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
            b" author_principal, validation_report)"
            b" VALUES (%(deployment)s, %(query)s, %(version)s, %(sql)s, %(hash)s,"
            b" %(schema)s::jsonb, %(interpretation)s, 'memory_v1', %(manifest)s,"
            b" 'draft', %(principal)s, %(report)s::jsonb)",
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
                "report": _json(report.as_json() if report else {}),
            },
        )
        self._connection.execute(
            b"UPDATE saved_queries SET latest_version = %(version)s"
            b" WHERE deployment_id = %(deployment)s AND query_id = %(query)s",
            {
                "version": version,
                "deployment": str(self._deployment_id),
                "query": str(query_id),
            },
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
            b"SELECT status, validated_surface_manifest_hash, validation_report"
            b" FROM saved_query_versions"
            b" WHERE deployment_id = %(deployment)s"
            b"   AND query_id = %(query)s AND version = %(version)s",
            {
                "deployment": str(self._deployment_id),
                "query": str(query_id),
                "version": version,
            },
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
        # §5: activation follows a validation that executed every fixture. A
        # version whose report is absent or partial has not been checked, and
        # activating it would publish something nobody verified while looking
        # exactly like something somebody did.
        report = row[2] if isinstance(row[2], dict) else {}
        if not report.get("passed"):
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_INCOMPATIBLE,
                message=(
                    "this version has no passing validation report; every §5"
                    " fixture must run before it can be activated"
                ),
            )
        self._connection.execute(
            b"UPDATE saved_query_versions"
            b" SET status = 'active', approver_principal = %(approver)s"
            b" WHERE deployment_id = %(deployment)s"
            b"   AND query_id = %(query)s AND version = %(version)s",
            {
                "approver": approver,
                "deployment": str(self._deployment_id),
                "query": str(query_id),
                "version": version,
            },
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

    # -- discovery ---------------------------------------------------------

    def list_saved_queries(
        self, *, namespace: str | None = None, status: str | None = None
    ) -> tuple[SavedQuerySummary, ...]:
        """Registry metadata for discoverable saved queries (§3.1, §5).

        Drafts are excluded from default discovery: when `status` is omitted,
        only `active` versions of non-disabled identities appear. Agents may
        draft freely; only an operator-activated version is discoverable by
        default. Passing `status` (including `draft`) is an explicit request
        for that state, not the default listing.
        """
        clauses = [
            b"q.deployment_id = %(deployment)s",
            b"q.disabled_at IS NULL",
            b"v.version = q.latest_version",
        ]
        params: dict[str, object] = {"deployment": str(self._deployment_id)}
        if namespace is not None:
            clauses.append(b"q.namespace = %(namespace)s")
            params["namespace"] = namespace
        if status is None:
            clauses.append(b"v.status = 'active'")
        else:
            clauses.append(b"v.status = %(status)s")
            params["status"] = status
        where = b" AND ".join(clauses)
        rows = self._connection.execute(
            b"SELECT q.query_id, q.namespace, q.name, v.version, v.status,"
            b" q.description, q.origin, v.assurance, v.query_hash,"
            b" v.validated_surface_manifest_hash"
            b" FROM saved_queries AS q"
            b" JOIN saved_query_versions AS v ON v.query_id = q.query_id"
            b"  AND v.deployment_id = q.deployment_id"
            b" WHERE " + where + b" ORDER BY q.namespace, q.name",
            params,
        ).fetchall()
        return tuple(
            SavedQuerySummary(
                query_id=row[0],
                namespace=row[1],
                name=row[2],
                version=row[3],
                status=row[4],
                description=row[5],
                origin=row[6],
                assurance=row[7],
                query_hash=row[8],
                validated_surface_manifest_hash=row[9],
            )
            for row in rows
        )

    def describe_saved_query(
        self, *, namespace: str, name: str, version: int | None = None
    ) -> SavedQueryDescription:
        """One immutable version: parameters, validation state, and hashes.

        Omitting `version` describes the identity's latest version. Drafts are
        describeable when asked for by name — description is not the same as
        default discovery listing — so an operator can inspect what an agent
        drafted before activating it.
        """
        if version is None:
            row = self._connection.execute(
                b"SELECT q.query_id, q.namespace, q.name, v.version, v.status,"
                b" q.description, q.origin, v.assurance, v.sql, v.query_hash,"
                b" v.parameter_schema, v.declared_interpretation,"
                b" v.validated_surface_manifest_hash, v.validation_report,"
                b" v.author_principal, v.approver_principal"
                b" FROM saved_queries AS q"
                b" JOIN saved_query_versions AS v"
                b"   ON v.query_id = q.query_id AND v.version = q.latest_version"
                b"  AND v.deployment_id = q.deployment_id"
                b" WHERE q.deployment_id = %(deployment)s"
                b"   AND q.namespace = %(namespace)s AND q.name = %(name)s",
                {
                    "deployment": str(self._deployment_id),
                    "namespace": namespace,
                    "name": name,
                },
            ).fetchone()
        else:
            row = self._connection.execute(
                b"SELECT q.query_id, q.namespace, q.name, v.version, v.status,"
                b" q.description, q.origin, v.assurance, v.sql, v.query_hash,"
                b" v.parameter_schema, v.declared_interpretation,"
                b" v.validated_surface_manifest_hash, v.validation_report,"
                b" v.author_principal, v.approver_principal"
                b" FROM saved_queries AS q"
                b" JOIN saved_query_versions AS v"
                b"   ON v.query_id = q.query_id AND v.deployment_id = q.deployment_id"
                b" WHERE q.deployment_id = %(deployment)s"
                b"   AND q.namespace = %(namespace)s AND q.name = %(name)s"
                b"   AND v.version = %(version)s",
                {
                    "deployment": str(self._deployment_id),
                    "namespace": namespace,
                    "name": name,
                    "version": version,
                },
            ).fetchone()
        if row is None:
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_NOT_FOUND,
                message=f"no saved query named {namespace}.{name}"
                + (f" version {version}" if version is not None else ""),
            )
        report = row[13] if isinstance(row[13], dict) else {}
        schema = row[10] if isinstance(row[10], dict) else {}
        return SavedQueryDescription(
            query_id=row[0],
            namespace=row[1],
            name=row[2],
            version=row[3],
            status=row[4],
            description=row[5],
            origin=row[6],
            assurance=row[7],
            sql=row[8],
            query_hash=row[9],
            parameter_schema=schema,
            declared_interpretation=row[11],
            validated_surface_manifest_hash=row[12],
            validation_report=report,
            author_principal=row[14],
            approver_principal=row[15],
        )

    # -- lifecycle ---------------------------------------------------------

    def disable(self, *, query_id: UUID) -> None:
        """Take an identity out of service at admission time (§5)."""
        self._connection.execute(
            b"UPDATE saved_queries SET disabled_at = now()"
            b" WHERE deployment_id = %(deployment)s AND query_id = %(query)s",
            {"deployment": str(self._deployment_id), "query": str(query_id)},
        )

    def purge(self, *, query_id: UUID) -> None:
        """Hard-delete an identity's text (D74).

        Registry SQL can contain customer data — a WHERE clause naming a person
        is customer data — so a deletion removes the text rather than marking
        it. What remains is the audit trail's ids, hashes, actor, and action,
        which contain none of it.
        """
        self._connection.execute(
            b"DELETE FROM saved_queries"
            b" WHERE deployment_id = %(deployment)s AND query_id = %(query)s",
            {"deployment": str(self._deployment_id), "query": str(query_id)},
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
            b" WHERE deployment_id = %(deployment)s AND query_id = %(query)s",
            {"deployment": str(self._deployment_id), "query": str(query_id)},
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

    def _check_quotas(self, *, principal: str, sql: str, identity: UUID | None) -> None:
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
        new_identity = identity is None
        # An identity that already has a draft version is already counted; a
        # brand-new name would add one more draft identity to the principal.
        # When the identity exists but none of its versions are draft for this
        # principal, a new draft still adds that identity to the count.
        existing_draft_identity = False
        if identity is not None:
            existing_draft_identity = (
                self._connection.execute(
                    b"SELECT 1 FROM saved_query_versions"
                    b" WHERE deployment_id = %(deployment)s"
                    b"   AND query_id = %(query)s"
                    b"   AND status = 'draft'"
                    b"   AND author_principal = %(principal)s"
                    b" LIMIT 1",
                    {
                        "deployment": str(self._deployment_id),
                        "query": str(identity),
                        "principal": principal,
                    },
                ).fetchone()
                is not None
            )
        adds_draft_identity = new_identity or not existing_draft_identity

        for reached, limit, what in (
            (identities + (1 if new_identity else 0), IDENTITIES_MAX, "saved queries"),
            (
                per_hour + (1 if new_identity else 0),
                IDENTITIES_PER_HOUR_MAX,
                "new saved queries in an hour",
            ),
            (
                draft_identities + (1 if adds_draft_identity else 0),
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
        # The ceiling includes the SQL about to be written. Checking only the
        # bytes already stored would let a principal park just under the limit
        # and then write an unbounded next draft.
        if int(draft_bytes) + len(sql.encode()) > DRAFT_BYTES_MAX:
            raise SandboxRejection(
                code=QueryErrorCode.QUOTA_EXCEEDED,
                message="this principal's drafts exceed their byte ceiling",
            )


class SurfaceMoved(Exception):
    """The surface changed while a validation was running.

    Raised by `revalidate` when the hash it started against is no longer the
    one in force. The validation is not wrong, it is simply about a surface
    that no longer exists, and its result cannot be used to activate anything.
    """


def revalidate(
    *,
    connection: psycopg.Connection,
    deployment_id: UUID,
    query_id: UUID,
    version: int,
    started_against: str,
    now_in_force: str,
    fixtures_passed: bool,
    minor_compatible: bool,
    actor: str,
) -> str:
    """Decide what a completed revalidation may do to a suspended version (§5).

    The compare-and-swap is the point. A validator reads the manifest hash when
    it starts and offers it back here; if the surface has moved again in the
    meantime, the result describes a surface nobody is running and cannot
    activate anything — the version stays suspended and waits for a fresh
    validation. Without this, a slow validator could quietly re-activate a
    version against a surface it never saw.

    Restoration to `active` is allowed ONLY when the new manifest is
    minor-compatible and every fixture passed. An incompatible major or a
    failed fixture moves the version to `broken`, which is a statement that
    someone must look at it rather than a state it can drift out of.
    """
    if started_against != now_in_force:
        raise SurfaceMoved(
            "the surface changed while this version was being revalidated"
        )
    row = connection.execute(
        b"SELECT status FROM saved_query_versions"
        b" WHERE deployment_id = %(deployment)s"
        b"   AND query_id = %(query)s AND version = %(version)s",
        {"deployment": str(deployment_id), "query": str(query_id), "version": version},
    ).fetchone()
    if row is None:
        raise SandboxRejection(
            code=QueryErrorCode.SAVED_QUERY_NOT_FOUND,
            message="no such saved-query version",
        )
    if row[0] != "pending_revalidation":
        # Only a suspended version is waiting for this answer.
        return str(row[0])

    outcome = "active" if (minor_compatible and fixtures_passed) else "broken"
    # The swap is conditional on the version still being suspended AND on the
    # hash still being the one this validation ran against, so two validators
    # racing cannot both win.
    cursor = connection.execute(
        b"UPDATE saved_query_versions"
        b" SET status = %(status)s::saved_query_status,"
        b"     validated_surface_manifest_hash = %(manifest)s,"
        b"     approver_principal = coalesce(approver_principal, %(actor)s)"
        b" WHERE deployment_id = %(deployment)s"
        b"   AND query_id = %(query)s AND version = %(version)s"
        b"   AND status = 'pending_revalidation'",
        {
            "status": outcome,
            "manifest": now_in_force,
            "actor": actor,
            "deployment": str(deployment_id),
            "query": str(query_id),
            "version": version,
        },
    )
    if cursor.rowcount != 1:
        raise SurfaceMoved("this version was changed while it was being revalidated")
    return outcome


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


#: The seventeen `examples.*` names §3.1 maps and §5 ships. They carry
#: `shipped_example` assurance: the platform wrote them, so they are honest
#: about what they compute — but they are editable starting points rather than
#: platform operations, and copying one and changing its filters produces a
#: customer-authored query, which is the intended use.
SHIPPED_EXAMPLES: Final[tuple[tuple[str, str], ...]] = (
    ("changed_since", "What the system learned after a given instant"),
    ("chunk_neighbors", "Current-section ordinal neighbours of a chunk"),
    ("chunks_hybrid_rrf", "Semantic and lexical chunk channels, RRF-fused"),
    ("claims_about", "Claims mentioning an entity, with their sources"),
    ("claims_as_of", "Claims as they stood at a given instant"),
    ("claims_hybrid_rrf", "Semantic and lexical claim channels, RRF-fused"),
    ("claims_verbatim", "Claims as asserted, with their source handles"),
    ("documents_about", "Every live document that mentions an entity"),
    ("entity_timeline", "One entity's mentions in processing order"),
    ("explain", "The plan for a statement, without running it"),
    ("graph_neighborhood", "Relations within N hops of an entity"),
    ("graph_path", "Routes between two entities"),
    ("identity_as_of", "Which entity a mention resolved to at an instant"),
    ("multi_hop_context", "Evidence along a route between two entities"),
    ("observation_current", "Current observations about an entity"),
    ("pages_about", "Compiled pages citing an entity"),
    ("relation_current", "Current relations, adjudicated"),
)


def declared_examples() -> Sequence[tuple[str, str]]:
    """The `examples.*` names this build ships, as (name, purpose) pairs."""
    return SHIPPED_EXAMPLES


#: The fixture classes §5 requires a saving validator to execute. They are
#: named here so a validator cannot quietly run fewer than the contract says.
VALIDATION_FIXTURES: Final = ("positive", "empty", "tombstone", "cap")


@dataclass(frozen=True)
class ValidationReport:
    """What a saving validation observed, fixture by fixture.

    A saved query is validated once and then trusted by everyone who runs it,
    so the report records which fixtures ran and what each concluded rather
    than a single pass/fail — "it validated" is not something a later reader
    can check, and a report that cannot be checked is a claim rather than
    evidence.
    """

    manifest_hash: str
    fixtures: dict[str, bool]
    diagnostics: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        """True only when EVERY required fixture ran and passed."""
        return all(self.fixtures.get(name, False) for name in VALIDATION_FIXTURES)

    def as_json(self) -> dict[str, Any]:
        """The shape stored in `saved_query_versions.validation_report`."""
        return {
            "manifest_hash": self.manifest_hash,
            "fixtures": {
                name: self.fixtures.get(name, False) for name in VALIDATION_FIXTURES
            },
            "diagnostics": list(self.diagnostics),
            "passed": self.passed,
        }


def _as_operator_fixture(
    *, kind: str, value: OperatorFixture | Sequence[object]
) -> OperatorFixture:
    """Normalize a mapping entry into a typed fixture."""
    if isinstance(value, OperatorFixture):
        if value.kind != kind:
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message=f"fixture {kind!r} carries kind {value.kind!r}",
            )
        return value
    return OperatorFixture(kind=kind, parameters=tuple(value))


def _fixture_passed(
    *, fixture: OperatorFixture, outcome: QueryResult
) -> tuple[bool, str]:
    """Judge one sandbox outcome against its fixture class.

    Positive: the statement completed. Empty and tombstone: completed with no
    rows — the operator supplies parameters that must not invent content
    (empty) or must not surface deleted content (tombstone). Cap: completed
    under the operator's requested row bound, and never returned more rows
    than that bound.
    """
    if outcome.error_code is not None or outcome.termination_reason != "completed":
        detail = (
            outcome.error_code.value
            if outcome.error_code is not None
            else outcome.termination_reason
        )
        return False, f"{fixture.kind}: {detail}"
    if fixture.kind in ("empty", "tombstone"):
        if not outcome.empty_result:
            return False, f"{fixture.kind}: expected no rows"
        return True, f"{fixture.kind}: empty"
    if fixture.kind == "cap":
        if fixture.max_rows is None:
            return False, "cap: max_rows is required"
        if outcome.returned_row_count > fixture.max_rows:
            return (
                False,
                f"cap: returned {outcome.returned_row_count} rows above"
                f" max_rows={fixture.max_rows}",
            )
        return True, f"cap: {outcome.returned_row_count} rows within bound"
    if fixture.kind == "positive":
        return True, "positive: completed"
    return False, f"{fixture.kind}: unknown fixture class"


def validate_saved_sql(
    *,
    executor: QuerySandboxExecutor,
    sql: str,
    fixtures: Mapping[str, OperatorFixture | Sequence[object]],
    principal: str = "validator",
    manifest_hash: str | None = None,
) -> ValidationReport:
    """Execute every §5 fixture through the real sandbox and record outcomes.

    This is the saving validator §5 requires: it does not invent another SQL
    path. Each operator-owned case is run with bound parameters via
    `QuerySandboxExecutor.query_sql` (and a single safe EXPLAIN for
    diagnostics). Missing a required fixture class fails that class rather
    than silently skipping it. Parameter values never enter the SQL text.
    """
    report_hash = (
        manifest_hash
        if manifest_hash is not None
        else getattr(executor, "_manifest_hash", "")
    )
    outcomes: dict[str, bool] = {}
    diagnostics: list[str] = []

    # Safe EXPLAIN first: diagnostics only. A plan failure does not by itself
    # fail the report — the four fixtures are the gate.
    explain_parameters: tuple[object, ...] = ()
    if "positive" in fixtures:
        explain_parameters = _as_operator_fixture(
            kind="positive", value=fixtures["positive"]
        ).parameters
    explain = executor.explain_sql(
        sql=sql, parameters=explain_parameters, principal=principal
    )
    if explain.error_code is not None:
        diagnostics.append(
            f"explain: {explain.error_code.value}: {explain.error_message}"
        )
    else:
        diagnostics.append("explain: completed")

    for kind in VALIDATION_FIXTURES:
        if kind not in fixtures:
            outcomes[kind] = False
            diagnostics.append(f"{kind}: fixture not provided")
            continue
        fixture = _as_operator_fixture(kind=kind, value=fixtures[kind])
        result = executor.query_sql(
            sql=sql,
            parameters=fixture.parameters,
            max_rows=fixture.max_rows,
            principal=principal,
        )
        passed, note = _fixture_passed(fixture=fixture, outcome=result)
        outcomes[kind] = passed
        diagnostics.append(note)

    return ValidationReport(
        manifest_hash=str(report_hash),
        fixtures=outcomes,
        diagnostics=tuple(diagnostics),
    )
