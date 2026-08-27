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

from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Final
from typing import TYPE_CHECKING
from uuid import UUID
from uuid import uuid4

import psycopg

from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.grammar import validate_sql
from rememberstack.surfaces.query_sandbox.limits import ANALYTICAL_LIMITS
from rememberstack.surfaces.query_sandbox.limits import INTERACTIVE_LIMITS
from rememberstack.surfaces.query_sandbox.limits import LimitTier

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

#: The only supported query-space major for this build.
SUPPORTED_QUERY_SPACE_MAJORS: Final = frozenset({"memory_v1"})

#: JSON Schema scalar types admitted for parameter/result schemas (§5).
_SCALAR_JSON_TYPES: Final = frozenset(
    {"string", "number", "integer", "boolean", "null"}
)

#: Default-limit keys a save may declare, checked against §4.3 hard caps.
_DEFAULT_LIMIT_KEYS: Final = frozenset(
    {"max_rows", "statement_timeout_ms", "max_bytes"}
)

#: The states a version can be in, and which of them may run. A version that is
#: not exactly `active` does not execute — including `pending_revalidation`,
#: which exists precisely to stop execution.
EXECUTABLE_STATUSES: Final = frozenset({"active"})

#: Statuses from which an operator may activate with bound validation evidence.
_ACTIVATABLE_STATUSES: Final = frozenset({"draft", "pending_revalidation"})

#: Content-free governance actions retained after purge.
AUDIT_ACTIONS: Final = frozenset(
    {"activate", "disable", "purge", "publish", "revalidate", "validate", "deprecate"}
)

#: A small injected authority: True when the *bound* actor may activate/approve.
#: The host evaluates this over the registry's construction-time actor; callers
#: cannot pass a free-form identity claim on mutate methods.
ActivationAuthority = Callable[[str], bool]


def default_deny_activation(_actor: str) -> bool:
    """Default activation authority: nobody may activate until a policy is wired."""
    return False


#: Bound actor used by the self-host seed path for shipped `examples.*` install.
PLATFORM_SEED_ACTOR: Final = "platform:shipped-examples"

#: Platform-owned namespace for the eighteen shipped demotion examples (§2).
#: Customer drafts must copy into another namespace; only platform seed installs here.
EXAMPLES_NAMESPACE: Final = "examples"


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
    default_limits: dict[str, Any] = field(default_factory=dict)


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
    declared_result_schema: dict[str, Any]
    declared_interpretation: str | None
    query_space_major: str
    default_limits: dict[str, Any]
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

    The host constructs one registry per authenticated actor. `actor` is the
    bound identity used for authoring, activation, audit, and shipped-example
    origin checks. `can_activate` is the small injected policy decision over
    that bound actor (default-deny). Mutation methods do not accept a free-form
    principal or approver claim a caller could forge as `operator-*`.
    """

    def __init__(
        self,
        *,
        connection: psycopg.Connection,
        deployment_id: UUID,
        manifest_hash: str,
        actor: str,
        can_activate: ActivationAuthority | None = None,
    ) -> None:
        if not actor:
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message="a saved-query registry requires a bound actor",
            )
        self._connection = connection
        self._deployment_id = deployment_id
        self._manifest_hash = manifest_hash
        self._actor = actor
        self._can_activate = can_activate or default_deny_activation

    @property
    def deployment_id(self) -> UUID:
        """The one deployment this registry serves."""
        return self._deployment_id

    @property
    def actor(self) -> str:
        """The host-bound actor this registry instance acts as."""
        return self._actor

    def _authorized_to_activate(self) -> bool:
        return self._can_activate(self._actor)

    # -- authoring ---------------------------------------------------------

    def draft(
        self,
        *,
        namespace: str,
        name: str,
        sql: str,
        parameter_schema: dict[str, Any] | None = None,
        declared_result_schema: dict[str, Any] | None = None,
        default_limits: dict[str, Any] | None = None,
        query_space_major: str = "memory_v1",
        description: str | None = None,
        origin: str = "agent",
        declared_interpretation: str | None = None,
    ) -> SavedQueryVersion:
        """Write a new draft version, creating the identity if it is new.

        The bound registry actor is the author. The SQL is validated against
        the same grammar an ad-hoc statement goes through, and the version
        pins the manifest hash it was authored against. Parameter/result
        schemas and default limits are checked before write. Validation
        evidence is not accepted from the caller — only `validate_version`
        may write a bound report from the stored SQL.
        """
        principal = self._actor
        if len(sql.encode()) > SQL_BYTES_MAX:
            raise SandboxRejection(
                code=QueryErrorCode.QUOTA_EXCEEDED,
                message=f"saved SQL is limited to {SQL_BYTES_MAX} bytes",
            )
        if query_space_major not in SUPPORTED_QUERY_SPACE_MAJORS:
            raise SandboxRejection(
                code=QueryErrorCode.SCHEMA_VERSION_MISMATCH,
                message=(
                    f"only {sorted(SUPPORTED_QUERY_SPACE_MAJORS)} are supported;"
                    f" got {query_space_major!r}"
                ),
            )
        # The examples namespace is platform-owned. Customer drafting copies a
        # shipped body into another namespace; install_shipped_examples is the
        # only path that creates or refreshes examples.* identities.
        if namespace == EXAMPLES_NAMESPACE:
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message=(
                    "the examples namespace is platform-owned;"
                    " copy a shipped example into a customer namespace to edit it"
                ),
            )
        params = validate_parameter_schema(parameter_schema or {})
        results = validate_result_schema(declared_result_schema or {})
        limits = validate_default_limits(default_limits or {})
        validated = validate_sql(sql)
        if origin == "shipped_example":
            # shipped_example assurance is platform-owned. Only a registry
            # whose bound actor has activation authority may assert it.
            if not self._authorized_to_activate():
                raise SandboxRejection(
                    code=QueryErrorCode.INVALID_PARAMETER,
                    message=(
                        "origin shipped_example requires activation authority;"
                        " an agent cannot self-assert shipped-example assurance"
                    ),
                )
            assurance = "shipped_example"
        else:
            assurance = "customer_authored"

        # Serialize concurrent draft accounting against the registry-state row
        # so two principals cannot race past the 4 MiB / identity ceilings.
        # Also pins this instance to the DB-authoritative surface hash.
        current_hash = self._require_authoritative_manifest(for_update=True)
        identity = self._identity(namespace=namespace, name=name)
        if identity is not None:
            self._reject_customer_mutation_of_shipped(query_id=identity, action="draft")
        # Description lives on `saved_queries` once per identity and counts
        # toward the draft-byte ceiling once whenever this principal holds a
        # draft for that identity. New identities store the caller description
        # → count it as pending. Existing identities never store a
        # caller-supplied description, so it never counts; if this principal
        # already holds a draft, the stored EXISTS sum already includes the
        # description. If they do not yet hold a draft (e.g. after activate),
        # `_check_quotas` counts the already-stored description as pending
        # because EXISTS is still false before INSERT.
        pending_description = description if identity is None else None
        pending_meta = self._pending_draft_metadata_bytes(
            description=pending_description,
            declared_interpretation=declared_interpretation,
            parameter_schema=params,
            declared_result_schema=results,
            default_limits=limits,
            validation_report={},
        )
        self._check_quotas(
            principal=principal,
            sql=sql,
            pending_metadata_bytes=pending_meta,
            identity=identity,
        )

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
            b" sql, query_hash, parameter_schema, declared_result_schema,"
            b" declared_interpretation, query_space_major,"
            b" validated_surface_manifest_hash, default_limits, status, assurance,"
            b" author_principal, validation_report)"
            b" VALUES (%(deployment)s, %(query)s, %(version)s, %(sql)s, %(hash)s,"
            b" %(schema)s::jsonb, %(result_schema)s::jsonb, %(interpretation)s,"
            b" %(major)s, %(manifest)s, %(limits)s::jsonb, 'draft', %(assurance)s,"
            b" %(principal)s, '{}'::jsonb)",
            {
                "deployment": str(self._deployment_id),
                "query": str(query_id),
                "version": version,
                "sql": sql,
                "hash": validated.query_hash,
                "schema": _json(params),
                "result_schema": _json(results),
                "interpretation": declared_interpretation,
                "major": query_space_major,
                "manifest": current_hash,
                "limits": _json(limits),
                "assurance": assurance,
                "principal": principal,
            },
        )
        # latest_version tracks the newest *authored* version. Resolve and
        # default discovery choose the active version separately so drafting
        # v2 does not hide an active v1.
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
            parameter_schema=params,
            status="draft",
            validated_surface_manifest_hash=current_hash,
            assurance=assurance,
            default_limits=limits,
        )

    def validate_version(
        self,
        *,
        query_id: UUID,
        version: int,
        executor: QuerySandboxExecutor,
        fixtures: Mapping[str, OperatorFixture | Sequence[object]],
    ) -> ValidationReport:
        """Run §5 fixtures on a draft and persist bound validation evidence.

        Only `draft` versions accept a validation report through this path.
        Active, pending, broken, and other lifecycle states refuse before
        fixtures run so a suspended version cannot bypass revalidation's
        `minor_compatible` decision, and an active shipped report cannot be
        overwritten. Evidence is produced from the row's SQL through
        QuerySandboxExecutor, bound to that row's query_hash and the
        DB-authoritative surface hash. The executor's reported
        `surface_manifest_hash` must match that hash; this method never
        relabels a mismatched executor with the constructor value.

        The new stored report is accounted under the same per-principal 4 MiB
        draft-byte ceiling (replacing any current report) before it is written.

        Protocol matches revalidation: capture the authoritative hash without
        holding the publication lock, run EXPLAIN/fixtures unlocked, then
        lock / re-read / CAS at persistence so `publish_surface_hash` is not
        blocked and stale evidence is never written as current. Persistence
        CAS requires `status = 'draft'` so a concurrent lifecycle move cannot
        write.
        """
        # Capture-at-start: no FOR UPDATE — publication must remain unblocked.
        start_hash = self._require_authoritative_manifest(for_update=False)
        row = self._connection.execute(
            b"SELECT sql, query_hash, status"
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
        sql, query_hash, status = row
        # Refuse non-draft before expensive EXPLAIN/fixture execution.
        if status != "draft":
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_INCOMPATIBLE,
                message=(
                    f"only draft versions may be validated; this version is {status!r}"
                ),
            )
        # Unlocked execution: EXPLAIN and fixtures do not hold the state lock.
        report = validate_saved_sql(
            executor=executor,
            sql=sql,
            fixtures=fixtures,
            principal=self._actor,
            manifest_hash=start_hash,
            query_hash=query_hash,
        )
        report_json = report.as_json()

        # CAS-at-persistence: lock, re-read, fail closed if the surface moved.
        # Do not hold this lock through EXPLAIN/fixtures; only the short
        # quota + write transition is serialized here.
        state = self._connection.execute(
            b"SELECT surface_manifest_hash FROM saved_query_registry_state"
            b" WHERE deployment_id = %(deployment)s"
            b" FOR UPDATE",
            {"deployment": str(self._deployment_id)},
        ).fetchone()
        if state is None or str(state[0]) != start_hash:
            raise SurfaceMoved(
                "the surface changed while this version was being validated"
            )
        current = self._connection.execute(
            b"SELECT sql, query_hash, status, author_principal, validation_report"
            b" FROM saved_query_versions"
            b" WHERE deployment_id = %(deployment)s"
            b"   AND query_id = %(query)s AND version = %(version)s"
            b" FOR UPDATE",
            {
                "deployment": str(self._deployment_id),
                "query": str(query_id),
                "version": version,
            },
        ).fetchone()
        if current is None:
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_NOT_FOUND,
                message="no such saved-query version",
            )
        cur_sql, cur_hash, cur_status, cur_author, cur_report = current
        if cur_sql != sql or cur_hash != query_hash:
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_INCOMPATIBLE,
                message="this version changed while it was being validated",
            )
        if cur_status != "draft":
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_INCOMPATIBLE,
                message=(
                    f"only draft versions may be validated;"
                    f" this version is {cur_status!r}"
                ),
            )
        self._check_draft_report_quota(
            principal=str(cur_author),
            existing_report=cur_report if isinstance(cur_report, dict) else {},
            new_report=report_json,
        )
        # CAS specifically on draft: a concurrent lifecycle transition must not
        # write a report onto pending/active/broken rows.
        cursor = self._connection.execute(
            b"UPDATE saved_query_versions"
            b" SET validation_report = %(report)s::jsonb,"
            b"     validated_surface_manifest_hash = %(manifest)s"
            b" WHERE deployment_id = %(deployment)s"
            b"   AND query_id = %(query)s AND version = %(version)s"
            b"   AND query_hash = %(query_hash)s"
            b"   AND status = 'draft'"
            b"   AND EXISTS ("
            b"     SELECT 1 FROM saved_query_registry_state AS s"
            b"     WHERE s.deployment_id = saved_query_versions.deployment_id"
            b"       AND s.surface_manifest_hash = %(manifest)s"
            b"   )",
            {
                "report": _json(report_json),
                "manifest": start_hash,
                "deployment": str(self._deployment_id),
                "query": str(query_id),
                "version": version,
                "query_hash": query_hash,
            },
        )
        if cursor.rowcount != 1:
            raise SurfaceMoved(
                "this version or surface changed while it was being validated"
            )
        self._audit(
            action="validate",
            actor=self._actor,
            query_id=query_id,
            version=version,
            query_hash=query_hash,
            old_hash=None,
            new_hash=start_hash,
        )
        return report

    def activate(self, *, query_id: UUID, version: int) -> None:
        """Make a version live. Only an authorized bound actor may do this (§5).

        Authority is the registry's construction-time actor under the injected
        `can_activate` policy — callers cannot pass an approver string. The
        stored `author_principal` is the authority for self-approval refusal.
        Activation requires bound validation evidence for the stored SQL and a
        legal state transition (`draft` or `pending_revalidation` only).
        Activating deprecates any prior active version of the same identity
        atomically.
        """
        if not self._authorized_to_activate():
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message="only a deployment operator or explicit policy may activate",
            )
        approver = self._actor
        current_hash = self._require_authoritative_manifest(for_update=True)
        row = self._connection.execute(
            b"SELECT status, validated_surface_manifest_hash, validation_report,"
            b"       author_principal, query_hash, assurance"
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
        status, pinned_hash, raw_report, author, query_hash, assurance = row
        if approver == author:
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message="a saved query is approved by someone other than its author",
            )
        if status not in _ACTIVATABLE_STATUSES:
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_INCOMPATIBLE,
                message=(
                    f"a version in status {status!r} cannot be activated;"
                    " only draft or pending_revalidation may transition to active"
                ),
            )
        if pinned_hash != current_hash:
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_REVALIDATION_PENDING,
                message="this version was validated against a different surface",
            )
        report = raw_report if isinstance(raw_report, dict) else {}
        if not _report_authorizes_activation(
            report=report, query_hash=query_hash, manifest_hash=current_hash
        ):
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_INCOMPATIBLE,
                message=(
                    "this version has no bound passing validation report;"
                    " every §5 fixture must run against the stored SQL before"
                    " it can be activated"
                ),
            )
        new_assurance = (
            "shipped_example" if assurance == "shipped_example" else "customer_reviewed"
        )
        # Activate first and check rowcount so a concurrent status change
        # cannot silently deprecate the old active while activating none.
        cursor = self._connection.execute(
            b"UPDATE saved_query_versions"
            b" SET status = 'active',"
            b"     approver_principal = %(approver)s,"
            b"     assurance = %(assurance)s::saved_query_assurance,"
            b"     superseded_at = NULL"
            b" WHERE deployment_id = %(deployment)s"
            b"   AND query_id = %(query)s AND version = %(version)s"
            b"   AND status = ANY(%(allowed)s)",
            {
                "approver": approver,
                "assurance": new_assurance,
                "deployment": str(self._deployment_id),
                "query": str(query_id),
                "version": version,
                "allowed": list(_ACTIVATABLE_STATUSES),
            },
        )
        if cursor.rowcount != 1:
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_INCOMPATIBLE,
                message=(
                    "activation did not apply; the version status changed"
                    " concurrently or is no longer activatable"
                ),
            )
        # Deprecate any prior active version of this identity so resolve keeps
        # exactly one executable version.
        self._connection.execute(
            b"UPDATE saved_query_versions"
            b" SET status = 'deprecated', superseded_at = now()"
            b" WHERE deployment_id = %(deployment)s"
            b"   AND query_id = %(query)s"
            b"   AND status = 'active'"
            b"   AND version <> %(version)s",
            {
                "deployment": str(self._deployment_id),
                "query": str(query_id),
                "version": version,
            },
        )
        self._audit(
            action="activate",
            actor=approver,
            query_id=query_id,
            version=version,
            query_hash=query_hash,
            old_hash=pinned_hash,
            new_hash=current_hash,
        )

    # -- execution ---------------------------------------------------------

    def resolve(
        self, *, namespace: str, name: str, version: int | None = None
    ) -> SavedQueryVersion:
        """The version `run_saved_query` would execute, or a stated refusal.

        When `version` is omitted, resolve chooses the active version of the
        identity, not merely the newest authored version — drafting v2 must
        not hide an active v1. When `version` is supplied, that exact version
        must exist and be `active`; pending, draft, disabled, broken, and
        not-found refuse with the existing typed codes. Every refusal names
        its reason: not found, disabled, awaiting revalidation, or
        incompatible.
        """
        identity = self._connection.execute(
            b"SELECT query_id, disabled_at, latest_version FROM saved_queries"
            b" WHERE deployment_id = %(deployment)s"
            b"   AND namespace = %(namespace)s AND name = %(name)s",
            {
                "deployment": str(self._deployment_id),
                "namespace": namespace,
                "name": name,
            },
        ).fetchone()
        if identity is None:
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_NOT_FOUND,
                message=f"no saved query named {namespace}.{name}",
            )
        query_id, disabled_at, latest_version = identity
        if disabled_at is not None:
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_DISABLED,
                message=f"{namespace}.{name} is disabled",
            )

        if version is not None:
            return self._resolve_exact_active(
                query_id=query_id, namespace=namespace, name=name, version=version
            )

        active = self._connection.execute(
            b"SELECT v.version, v.sql, v.query_hash, v.parameter_schema, v.status,"
            b" v.validated_surface_manifest_hash, v.assurance, v.default_limits"
            b" FROM saved_query_versions AS v"
            b" WHERE v.deployment_id = %(deployment)s"
            b"   AND v.query_id = %(query)s"
            b"   AND v.status = 'active'"
            b" ORDER BY v.version DESC"
            b" LIMIT 1",
            {"deployment": str(self._deployment_id), "query": str(query_id)},
        ).fetchone()
        if active is not None:
            current_hash = self._require_authoritative_manifest()
            if active[5] != current_hash:
                raise SandboxRejection(
                    code=QueryErrorCode.SAVED_QUERY_REVALIDATION_PENDING,
                    message=(
                        f"{namespace}.{name} was validated against another surface"
                    ),
                )
            limits = active[7] if isinstance(active[7], dict) else {}
            return SavedQueryVersion(
                query_id=query_id,
                version=active[0],
                namespace=namespace,
                name=name,
                sql=active[1],
                query_hash=active[2],
                parameter_schema=active[3] if isinstance(active[3], dict) else {},
                status=active[4],
                validated_surface_manifest_hash=active[5],
                assurance=active[6],
                default_limits=limits,
            )

        # No active version: report the latest authored status so the caller
        # learns whether the work is draft, pending, broken, or deprecated.
        latest = self._connection.execute(
            b"SELECT v.status, v.validated_surface_manifest_hash"
            b" FROM saved_query_versions AS v"
            b" WHERE v.deployment_id = %(deployment)s"
            b"   AND v.query_id = %(query)s AND v.version = %(version)s",
            {
                "deployment": str(self._deployment_id),
                "query": str(query_id),
                "version": latest_version,
            },
        ).fetchone()
        status = latest[0] if latest else "draft"
        if status == "pending_revalidation":
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_REVALIDATION_PENDING,
                message=(
                    f"{namespace}.{name} is awaiting revalidation against the"
                    " current surface"
                ),
            )
        raise SandboxRejection(
            code=QueryErrorCode.SAVED_QUERY_DISABLED,
            message=f"{namespace}.{name} is {status} and does not execute",
        )

    def _resolve_exact_active(
        self, *, query_id: UUID, namespace: str, name: str, version: int
    ) -> SavedQueryVersion:
        """Resolve one exact version only when it is currently active."""
        row = self._connection.execute(
            b"SELECT v.version, v.sql, v.query_hash, v.parameter_schema, v.status,"
            b" v.validated_surface_manifest_hash, v.assurance, v.default_limits"
            b" FROM saved_query_versions AS v"
            b" WHERE v.deployment_id = %(deployment)s"
            b"   AND v.query_id = %(query)s"
            b"   AND v.version = %(version)s",
            {
                "deployment": str(self._deployment_id),
                "query": str(query_id),
                "version": version,
            },
        ).fetchone()
        if row is None:
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_NOT_FOUND,
                message=f"no saved query named {namespace}.{name} version {version}",
            )
        status = row[4]
        if status == "pending_revalidation":
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_REVALIDATION_PENDING,
                message=(
                    f"{namespace}.{name} v{version} is awaiting revalidation"
                    " against the current surface"
                ),
            )
        if status != "active":
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_DISABLED,
                message=(
                    f"{namespace}.{name} v{version} is {status} and does not execute"
                ),
            )
        current_hash = self._require_authoritative_manifest()
        if row[5] != current_hash:
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_REVALIDATION_PENDING,
                message=(
                    f"{namespace}.{name} v{version} was validated against another"
                    " surface"
                ),
            )
        limits = row[7] if isinstance(row[7], dict) else {}
        return SavedQueryVersion(
            query_id=query_id,
            version=row[0],
            namespace=namespace,
            name=name,
            sql=row[1],
            query_hash=row[2],
            parameter_schema=row[3] if isinstance(row[3], dict) else {},
            status=row[4],
            validated_surface_manifest_hash=row[5],
            assurance=row[6],
            default_limits=limits,
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
            b"v.deployment_id = q.deployment_id",
            b"v.query_id = q.query_id",
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
            if status == "draft":
                # For draft listing, show the latest draft version per identity
                # rather than every historical draft row.
                clauses.append(
                    b"v.version = ("
                    b" SELECT max(v2.version) FROM saved_query_versions AS v2"
                    b" WHERE v2.deployment_id = v.deployment_id"
                    b"   AND v2.query_id = v.query_id"
                    b"   AND v2.status = 'draft')"
                )
        where = b" AND ".join(clauses)
        rows = self._connection.execute(
            b"SELECT q.query_id, q.namespace, q.name, v.version, v.status,"
            b" q.description, q.origin, v.assurance, v.query_hash,"
            b" v.validated_surface_manifest_hash"
            b" FROM saved_queries AS q"
            b" JOIN saved_query_versions AS v ON v.query_id = q.query_id"
            b"  AND v.deployment_id = q.deployment_id"
            b" WHERE " + where + b" ORDER BY q.namespace, q.name, v.version",
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

        Omitting `version` prefers the active version when one exists, else
        the newest authored version. Drafts are describeable when asked for by
        name so an operator can inspect what an agent drafted before activating.
        """
        if version is None:
            row = self._connection.execute(
                b"SELECT q.query_id, q.namespace, q.name, v.version, v.status,"
                b" q.description, q.origin, v.assurance, v.sql, v.query_hash,"
                b" v.parameter_schema, v.declared_result_schema,"
                b" v.declared_interpretation, v.query_space_major, v.default_limits,"
                b" v.validated_surface_manifest_hash, v.validation_report,"
                b" v.author_principal, v.approver_principal"
                b" FROM saved_queries AS q"
                b" JOIN saved_query_versions AS v"
                b"   ON v.query_id = q.query_id AND v.deployment_id = q.deployment_id"
                b" WHERE q.deployment_id = %(deployment)s"
                b"   AND q.namespace = %(namespace)s AND q.name = %(name)s"
                b" ORDER BY (v.status = 'active') DESC, v.version DESC"
                b" LIMIT 1",
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
                b" v.parameter_schema, v.declared_result_schema,"
                b" v.declared_interpretation, v.query_space_major, v.default_limits,"
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
        report = row[16] if isinstance(row[16], dict) else {}
        schema = row[10] if isinstance(row[10], dict) else {}
        result_schema = row[11] if isinstance(row[11], dict) else {}
        limits = row[14] if isinstance(row[14], dict) else {}
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
            declared_result_schema=result_schema,
            declared_interpretation=row[12],
            query_space_major=row[13],
            default_limits=limits,
            validated_surface_manifest_hash=row[15],
            validation_report=report,
            author_principal=row[17],
            approver_principal=row[18],
        )

    # -- lifecycle ---------------------------------------------------------

    def disable(self, *, query_id: UUID) -> None:
        """Take an identity out of service at admission time (§5)."""
        self._reject_customer_mutation_of_shipped(query_id=query_id, action="disable")
        self._connection.execute(
            b"UPDATE saved_queries SET disabled_at = now()"
            b" WHERE deployment_id = %(deployment)s AND query_id = %(query)s",
            {"deployment": str(self._deployment_id), "query": str(query_id)},
        )
        self._connection.execute(
            b"UPDATE saved_query_versions SET status = 'disabled'"
            b" WHERE deployment_id = %(deployment)s AND query_id = %(query)s"
            b"   AND status = 'active'",
            {"deployment": str(self._deployment_id), "query": str(query_id)},
        )
        self._audit(
            action="disable",
            actor=self._actor,
            query_id=query_id,
            version=None,
            query_hash=None,
            old_hash=None,
            new_hash=None,
        )

    def purge(self, *, query_id: UUID) -> None:
        """Hard-delete an identity's text (D74).

        Registry SQL can contain customer data — a WHERE clause naming a person
        is customer data — so a deletion removes the text rather than marking
        it. What remains is the audit trail's ids, hashes, actor, and action,
        which contain none of it. Shipped `examples.*` identities are not
        purgeable through this path.
        """
        self._reject_customer_mutation_of_shipped(query_id=query_id, action="purge")
        versions = self._connection.execute(
            b"SELECT version, query_hash, validated_surface_manifest_hash"
            b" FROM saved_query_versions"
            b" WHERE deployment_id = %(deployment)s AND query_id = %(query)s",
            {"deployment": str(self._deployment_id), "query": str(query_id)},
        ).fetchall()
        for version, query_hash, manifest_hash in versions:
            self._audit(
                action="purge",
                actor=self._actor,
                query_id=query_id,
                version=version,
                query_hash=query_hash,
                old_hash=manifest_hash,
                new_hash=None,
            )
        if not versions:
            self._audit(
                action="purge",
                actor=self._actor,
                query_id=query_id,
                version=None,
                query_hash=None,
                old_hash=None,
                new_hash=None,
            )
        self._connection.execute(
            b"DELETE FROM saved_queries"
            b" WHERE deployment_id = %(deployment)s AND query_id = %(query)s",
            {"deployment": str(self._deployment_id), "query": str(query_id)},
        )

    def install_shipped_examples(self) -> int:
        """Idempotently install the eighteen `examples.*` registry identities.

        Requires activation authority on the bound actor (the self-host seed
        path binds `PLATFORM_SEED_ACTOR`). Each body is grammar-checked, stored
        under `namespace=examples` with `origin`/`assurance=shipped_example`,
        and activated as a non-default saved query. Re-running with the same
        bodies is a no-op. Examples remain editable only by copying into a
        customer namespace — this path never exposes them as top-level tools.

        Fixture-class evidence for the bodies is the focused corpus proof; the
        seed installs discoverable identities on every deployment (including
        empty ones) without requiring a live corpus at bootstrap.
        """
        if not self._authorized_to_activate():
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message=(
                    "installing shipped examples requires activation authority"
                    " on the bound registry actor"
                ),
            )
        # Import here so the registry module stays free of an examples cycle
        # at import time for pure schema helpers.
        from rememberstack.surfaces.query_sandbox.examples import EXAMPLE_QUERIES

        current_hash = self._require_authoritative_manifest(for_update=True)
        installed = 0
        for name, (purpose, sql) in EXAMPLE_QUERIES.items():
            validated = validate_sql(sql)
            identity = self._identity(namespace=EXAMPLES_NAMESPACE, name=name)
            if identity is not None:
                meta = self._connection.execute(
                    b"SELECT origin, disabled_at FROM saved_queries"
                    b" WHERE deployment_id = %(deployment)s AND query_id = %(query)s",
                    {"deployment": str(self._deployment_id), "query": str(identity)},
                ).fetchone()
                if meta is None:
                    raise SandboxRejection(
                        code=QueryErrorCode.SAVED_QUERY_NOT_FOUND,
                        message=f"examples.{name} identity disappeared during seed",
                    )
                origin, disabled_at = meta
                # Fail closed: never hijack a customer identity, never silently
                # re-enable a disabled shipped row, never treat a non-matching
                # body as an idempotent skip.
                if origin != "shipped_example":
                    raise SandboxRejection(
                        code=QueryErrorCode.INVALID_PARAMETER,
                        message=(
                            f"examples.{name} already exists with origin"
                            f" {origin!r}; seeding refuses a non-shipped collision"
                        ),
                    )
                if disabled_at is not None:
                    raise SandboxRejection(
                        code=QueryErrorCode.SAVED_QUERY_DISABLED,
                        message=(
                            f"examples.{name} is disabled;"
                            " seeding does not re-enable a shipped identity"
                        ),
                    )
                same = self._connection.execute(
                    b"SELECT 1 FROM saved_query_versions"
                    b" WHERE deployment_id = %(deployment)s"
                    b"   AND query_id = %(query)s"
                    b"   AND query_hash = %(hash)s"
                    b"   AND status = 'active'"
                    b"   AND assurance = 'shipped_example'"
                    b" LIMIT 1",
                    {
                        "deployment": str(self._deployment_id),
                        "query": str(identity),
                        "hash": validated.query_hash,
                    },
                ).fetchone()
                if same is not None:
                    continue
            if identity is None:
                query_id = uuid4()
                self._connection.execute(
                    b"INSERT INTO saved_queries (deployment_id, query_id, namespace,"
                    b" name, description, owner_principal, origin)"
                    b" VALUES (%(deployment)s, %(query)s, %(namespace)s, %(name)s,"
                    b" %(description)s, %(principal)s, 'shipped_example')",
                    {
                        "deployment": str(self._deployment_id),
                        "query": str(query_id),
                        "namespace": EXAMPLES_NAMESPACE,
                        "name": name,
                        "description": purpose,
                        "principal": self._actor,
                    },
                )
                version = 1
            else:
                query_id = identity
                version = self._next_version(query_id)
            seed_report = {
                "manifest_hash": current_hash,
                "query_hash": validated.query_hash,
                "fixtures": {kind: True for kind in VALIDATION_FIXTURES},
                "diagnostics": ["seed: shipped example installed"],
                "passed": True,
            }
            self._connection.execute(
                b"INSERT INTO saved_query_versions (deployment_id, query_id, version,"
                b" sql, query_hash, parameter_schema, declared_result_schema,"
                b" declared_interpretation, query_space_major,"
                b" validated_surface_manifest_hash, default_limits, status, assurance,"
                b" author_principal, approver_principal, validation_report)"
                b" VALUES (%(deployment)s, %(query)s, %(version)s, %(sql)s, %(hash)s,"
                b" '{}'::jsonb, '{}'::jsonb, %(interpretation)s, 'memory_v1',"
                b" %(manifest)s, '{}'::jsonb, 'active', 'shipped_example',"
                b" %(author)s, %(approver)s, %(report)s::jsonb)",
                {
                    "deployment": str(self._deployment_id),
                    "query": str(query_id),
                    "version": version,
                    "sql": sql,
                    "hash": validated.query_hash,
                    "interpretation": purpose,
                    "manifest": current_hash,
                    "author": self._actor,
                    "approver": self._actor,
                    "report": _json(seed_report),
                },
            )
            # Seed is platform-owned: the seed actor authors and installs.
            # Deprecate any prior active version of the same identity.
            self._connection.execute(
                b"UPDATE saved_query_versions"
                b" SET status = 'deprecated', superseded_at = now()"
                b" WHERE deployment_id = %(deployment)s"
                b"   AND query_id = %(query)s"
                b"   AND status = 'active'"
                b"   AND version <> %(version)s",
                {
                    "deployment": str(self._deployment_id),
                    "query": str(query_id),
                    "version": version,
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
            self._audit(
                action="activate",
                actor=self._actor,
                query_id=query_id,
                version=version,
                query_hash=validated.query_hash,
                old_hash=None,
                new_hash=current_hash,
            )
            installed += 1
        return installed

    def revalidate(
        self,
        *,
        query_id: UUID,
        version: int,
        started_against: str,
        executor: QuerySandboxExecutor,
        fixtures: Mapping[str, OperatorFixture | Sequence[object]],
        minor_compatible: bool,
    ) -> str:
        """Revalidate a pending version under this registry's bound authority.

        Restoration to `active` uses the same bound actor and `can_activate`
        policy as first activation (default-deny). For ordinary customer
        queries, marking a version `broken` does not require activation
        authority. Platform-owned `examples.*` / `origin=shipped_example`
        identities require activation authority for any revalidation
        transition (including `broken`), so a customer path cannot disable
        and silently reseed platform examples. Protocol: capture → unlocked
        fixture execution → lock / re-read / CAS at transition.
        """
        return _revalidate_version(
            connection=self._connection,
            deployment_id=self._deployment_id,
            query_id=query_id,
            version=version,
            started_against=started_against,
            executor=executor,
            fixtures=fixtures,
            minor_compatible=minor_compatible,
            actor=self._actor,
            can_activate=self._can_activate,
        )

    # -- internals ---------------------------------------------------------

    def _reject_customer_mutation_of_shipped(
        self, *, query_id: UUID, action: str
    ) -> None:
        """Refuse customer mutation of platform-owned shipped example identities."""
        row = self._connection.execute(
            b"SELECT namespace, origin FROM saved_queries"
            b" WHERE deployment_id = %(deployment)s AND query_id = %(query)s",
            {"deployment": str(self._deployment_id), "query": str(query_id)},
        ).fetchone()
        if row is None:
            return
        namespace, origin = row
        if namespace == EXAMPLES_NAMESPACE or origin == "shipped_example":
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message=(
                    f"cannot {action} a platform-owned shipped example;"
                    " copy it into a customer namespace instead"
                ),
            )

    def _pending_draft_metadata_bytes(
        self,
        *,
        description: str | None,
        declared_interpretation: str | None,
        parameter_schema: dict[str, Any],
        declared_result_schema: dict[str, Any],
        default_limits: dict[str, Any],
        validation_report: dict[str, Any],
    ) -> int:
        """Pending draft metadata size using PostgreSQL JSONB text encoding.

        Matches stored-row accounting: `octet_length(value::jsonb::text)` for
        JSONB columns and `octet_length(text)` for plain text fields. Must not
        mix Python `json.dumps` byte counts with PostgreSQL JSONB.
        """
        row = self._connection.execute(
            b"SELECT"
            b"  octet_length(coalesce(%(description)s, ''))"
            b"  + octet_length(coalesce(%(interpretation)s, ''))"
            b"  + octet_length((%(params)s::jsonb)::text)"
            b"  + octet_length((%(results)s::jsonb)::text)"
            b"  + octet_length((%(limits)s::jsonb)::text)"
            b"  + octet_length((%(report)s::jsonb)::text)",
            {
                "description": description,
                "interpretation": declared_interpretation,
                "params": _json(parameter_schema),
                "results": _json(declared_result_schema),
                "limits": _json(default_limits),
                "report": _json(validation_report),
            },
        ).fetchone()
        assert row is not None
        return int(row[0])

    def _jsonb_octet_length(self, value: dict[str, Any]) -> int:
        """Exact PostgreSQL JSONB text size of a dict about to be stored."""
        row = self._connection.execute(
            b"SELECT octet_length((%(value)s::jsonb)::text)", {"value": _json(value)}
        ).fetchone()
        assert row is not None
        return int(row[0])

    def _require_authoritative_manifest(self, *, for_update: bool = False) -> str:
        """Return the deployment's DB-authoritative surface hash.

        Initializes `saved_query_registry_state` only when absent. If a row
        already exists with a hash other than this instance's constructor
        value, fail closed — draft/validate/activate must never trust a stale
        constructor hash over the database pin.
        """
        lock = b" FOR UPDATE" if for_update else b""
        row = self._connection.execute(
            b"SELECT surface_manifest_hash FROM saved_query_registry_state"
            b" WHERE deployment_id = %(deployment)s" + lock,
            {"deployment": str(self._deployment_id)},
        ).fetchone()
        if row is None:
            self._connection.execute(
                b"INSERT INTO saved_query_registry_state"
                b" (deployment_id, surface_manifest_hash)"
                b" VALUES (%(deployment)s, %(manifest)s)"
                b" ON CONFLICT (deployment_id) DO NOTHING",
                {
                    "deployment": str(self._deployment_id),
                    "manifest": self._manifest_hash,
                },
            )
            row = self._connection.execute(
                b"SELECT surface_manifest_hash FROM saved_query_registry_state"
                b" WHERE deployment_id = %(deployment)s" + lock,
                {"deployment": str(self._deployment_id)},
            ).fetchone()
        if row is None:
            raise SandboxRejection(
                code=QueryErrorCode.EXECUTION_ERROR,
                message="saved-query registry state is unavailable",
            )
        current = str(row[0])
        if current != self._manifest_hash:
            raise SandboxRejection(
                code=QueryErrorCode.SAVED_QUERY_REVALIDATION_PENDING,
                message=(
                    "this registry instance is not bound to the deployment's"
                    " current surface manifest hash"
                ),
            )
        return current

    def _audit(
        self,
        *,
        action: str,
        actor: str,
        query_id: UUID | None,
        version: int | None,
        query_hash: str | None,
        old_hash: str | None,
        new_hash: str | None,
    ) -> None:
        """Persist non-content governance evidence for a transition."""
        self._connection.execute(
            b"INSERT INTO saved_query_audit"
            b" (deployment_id, query_id, version, query_hash, actor, action,"
            b"  old_hash, new_hash)"
            b" VALUES (%(deployment)s, %(query)s, %(version)s, %(query_hash)s,"
            b" %(actor)s, %(action)s, %(old_hash)s, %(new_hash)s)",
            {
                "deployment": str(self._deployment_id),
                "query": str(query_id) if query_id is not None else None,
                "version": version,
                "query_hash": query_hash,
                "actor": actor,
                "action": action,
                "old_hash": old_hash,
                "new_hash": new_hash,
            },
        )

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

    def _check_quotas(
        self,
        *,
        principal: str,
        sql: str,
        pending_metadata_bytes: int,
        identity: UUID | None,
    ) -> None:
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
            b" (SELECT coalesce(sum("
            b"     octet_length(v.sql)"
            b"     + octet_length(coalesce(v.parameter_schema::text, ''))"
            b"     + octet_length(coalesce(v.declared_result_schema::text, ''))"
            b"     + octet_length(coalesce(v.default_limits::text, ''))"
            b"     + octet_length(coalesce(v.validation_report::text, ''))"
            b"     + octet_length(coalesce(v.declared_interpretation, ''))"
            b"   ), 0)"
            b"   FROM saved_query_versions AS v"
            b"   WHERE v.deployment_id = %(deployment)s AND v.status = 'draft'"
            b"     AND v.author_principal = %(principal)s),"
            # Description is identity-level (saved_queries once). Count each
            # draft identity once — never once per draft version.
            b" (SELECT coalesce(sum(octet_length(coalesce(q.description, ''))), 0)"
            b"   FROM saved_queries AS q"
            b"   WHERE q.deployment_id = %(deployment)s"
            b"     AND EXISTS ("
            b"       SELECT 1 FROM saved_query_versions AS v"
            b"       WHERE v.deployment_id = q.deployment_id"
            b"         AND v.query_id = q.query_id"
            b"         AND v.status = 'draft'"
            b"         AND v.author_principal = %(principal)s"
            b"     ))",
            {"deployment": str(self._deployment_id), "principal": principal},
        ).fetchone()
        assert counts is not None
        (
            identities,
            per_hour,
            draft_identities,
            draft_versions,
            draft_version_bytes,
            draft_description_bytes,
        ) = counts
        new_identity = identity is None
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
        # Ceiling includes encoded SQL plus draft registry metadata about to
        # be written, not only what is already stored.
        pending = len(sql.encode()) + pending_metadata_bytes
        # First draft on an existing identity: the identity-level description
        # is not yet in the EXISTS sum (no draft for this principal), but it
        # will be after INSERT. Count the already-stored description once.
        # Caller-supplied description for existing identities is never stored
        # and must not be counted (pending_metadata already excludes it).
        if identity is not None and not existing_draft_identity:
            stored_desc = self._connection.execute(
                b"SELECT octet_length(coalesce(description, ''))"
                b" FROM saved_queries"
                b" WHERE deployment_id = %(deployment)s"
                b"   AND query_id = %(query)s",
                {"deployment": str(self._deployment_id), "query": str(identity)},
            ).fetchone()
            if stored_desc is not None:
                pending += int(stored_desc[0])
        already = int(draft_version_bytes) + int(draft_description_bytes)
        if already + pending > DRAFT_BYTES_MAX:
            raise SandboxRejection(
                code=QueryErrorCode.QUOTA_EXCEEDED,
                message="this principal's drafts exceed their byte ceiling",
            )

    def _check_draft_report_quota(
        self,
        *,
        principal: str,
        existing_report: dict[str, Any],
        new_report: dict[str, Any],
    ) -> None:
        """Account a draft validation-report write under the 4 MiB ceiling.

        The draft-byte sum already includes the current report; the pending
        write replaces it, so only the delta is added. Description is counted
        once per draft identity (not once per version). Must be called while
        holding the per-deployment registry-state lock (same authority as draft).
        """
        counts = self._connection.execute(
            b"SELECT"
            b" (SELECT coalesce(sum("
            b"     octet_length(v.sql)"
            b"     + octet_length(coalesce(v.parameter_schema::text, ''))"
            b"     + octet_length(coalesce(v.declared_result_schema::text, ''))"
            b"     + octet_length(coalesce(v.default_limits::text, ''))"
            b"     + octet_length(coalesce(v.validation_report::text, ''))"
            b"     + octet_length(coalesce(v.declared_interpretation, ''))"
            b"   ), 0)"
            b"   FROM saved_query_versions AS v"
            b"   WHERE v.deployment_id = %(deployment)s AND v.status = 'draft'"
            b"     AND v.author_principal = %(principal)s),"
            b" (SELECT coalesce(sum(octet_length(coalesce(q.description, ''))), 0)"
            b"   FROM saved_queries AS q"
            b"   WHERE q.deployment_id = %(deployment)s"
            b"     AND EXISTS ("
            b"       SELECT 1 FROM saved_query_versions AS v"
            b"       WHERE v.deployment_id = q.deployment_id"
            b"         AND v.query_id = q.query_id"
            b"         AND v.status = 'draft'"
            b"         AND v.author_principal = %(principal)s"
            b"     ))",
            {"deployment": str(self._deployment_id), "principal": principal},
        ).fetchone()
        assert counts is not None
        already = int(counts[0]) + int(counts[1])
        old_bytes = self._jsonb_octet_length(existing_report)
        new_bytes = self._jsonb_octet_length(new_report)
        # Replace: drop the old report contribution, add the new one.
        projected = already - old_bytes + new_bytes
        if projected > DRAFT_BYTES_MAX:
            raise SandboxRejection(
                code=QueryErrorCode.QUOTA_EXCEEDED,
                message="this principal's drafts exceed their byte ceiling",
            )


class SurfaceMoved(Exception):
    """The surface changed while a validation was running.

    Raised by `revalidate` / `validate_version` when the hash it started
    against is no longer the one in force. The validation is not wrong, it is
    simply about a surface that no longer exists, and its result cannot be
    used to activate anything or be written as current evidence.
    """


def _revalidate_version(
    *,
    connection: psycopg.Connection,
    deployment_id: UUID,
    query_id: UUID,
    version: int,
    started_against: str,
    executor: QuerySandboxExecutor,
    fixtures: Mapping[str, OperatorFixture | Sequence[object]],
    minor_compatible: bool,
    actor: str,
    can_activate: ActivationAuthority,
) -> str:
    """Shared revalidation protocol with bound activation authority.

    Restoration to `active` requires `can_activate(actor)` — the same
    principal class as first activation. Default-deny when no policy is
    wired. For ordinary customer queries, marking `broken` does not require
    activation authority. Platform-owned shipped identities
    (`namespace=examples` or `origin=shipped_example`) require activation
    authority for *any* revalidation transition, including `broken`, so a
    default-denied customer path cannot disable platform examples and force
    a silent reseed.
    """
    # Capture-at-start: no FOR UPDATE — publication must remain unblocked.
    state = connection.execute(
        b"SELECT surface_manifest_hash FROM saved_query_registry_state"
        b" WHERE deployment_id = %(deployment)s",
        {"deployment": str(deployment_id)},
    ).fetchone()
    current_hash = state[0] if state is not None else None
    if current_hash is None or current_hash != started_against:
        raise SurfaceMoved(
            "the surface changed while this version was being revalidated"
        )

    row = connection.execute(
        b"SELECT status, query_hash, validated_surface_manifest_hash, sql"
        b" FROM saved_query_versions"
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
        return str(row[0])

    query_hash, old_manifest, sql = row[1], row[2], row[3]
    identity = connection.execute(
        b"SELECT namespace, origin FROM saved_queries"
        b" WHERE deployment_id = %(deployment)s AND query_id = %(query)s",
        {"deployment": str(deployment_id), "query": str(query_id)},
    ).fetchone()
    platform_owned = identity is not None and (
        identity[0] == EXAMPLES_NAMESPACE or identity[1] == "shipped_example"
    )
    # Refuse platform-owned transitions before fixtures when the bound actor
    # lacks activation authority — any outcome would mutate a shipped identity.
    if platform_owned and not can_activate(actor):
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message=(
                "revalidating a platform-owned shipped example requires"
                " activation authority; copy it into a customer namespace"
                " instead"
            ),
        )
    # Unlocked execution: EXPLAIN and fixtures do not hold the state lock.
    report = validate_saved_sql(
        executor=executor,
        sql=sql,
        fixtures=fixtures,
        principal=actor,
        manifest_hash=started_against,
        query_hash=query_hash,
    )
    if minor_compatible and report.passed:
        if not can_activate(actor):
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message=(
                    "restoring active requires activation authority"
                    " on the bound revalidation actor"
                ),
            )
        outcome = "active"
    else:
        outcome = "broken"

    # CAS-at-transition: lock, re-read, fail closed if the surface moved.
    state = connection.execute(
        b"SELECT surface_manifest_hash FROM saved_query_registry_state"
        b" WHERE deployment_id = %(deployment)s"
        b" FOR UPDATE",
        {"deployment": str(deployment_id)},
    ).fetchone()
    if state is None or state[0] != started_against:
        raise SurfaceMoved(
            "the surface changed while this version was being revalidated"
        )

    cursor = connection.execute(
        b"UPDATE saved_query_versions AS v"
        b" SET status = %(status)s::saved_query_status,"
        b"     validated_surface_manifest_hash = %(manifest)s,"
        b"     validation_report = %(report)s::jsonb,"
        b"     approver_principal = CASE"
        b"       WHEN %(status)s = 'active'"
        b"       THEN coalesce(v.approver_principal, %(actor)s)"
        b"       ELSE v.approver_principal END"
        b" WHERE v.deployment_id = %(deployment)s"
        b"   AND v.query_id = %(query)s AND v.version = %(version)s"
        b"   AND v.status = 'pending_revalidation'"
        b"   AND EXISTS ("
        b"     SELECT 1 FROM saved_query_registry_state AS s"
        b"     WHERE s.deployment_id = v.deployment_id"
        b"       AND s.surface_manifest_hash = %(manifest)s"
        b"   )",
        {
            "status": outcome,
            "manifest": started_against,
            "report": _json(report.as_json()),
            "actor": actor,
            "deployment": str(deployment_id),
            "query": str(query_id),
            "version": version,
        },
    )
    if cursor.rowcount != 1:
        raise SurfaceMoved("this version was changed while it was being revalidated")

    connection.execute(
        b"INSERT INTO saved_query_audit"
        b" (deployment_id, query_id, version, query_hash, actor, action,"
        b"  old_hash, new_hash)"
        b" VALUES (%(deployment)s, %(query)s, %(version)s, %(query_hash)s,"
        b" %(actor)s, 'revalidate', %(old_hash)s, %(new_hash)s)",
        {
            "deployment": str(deployment_id),
            "query": str(query_id),
            "version": version,
            "query_hash": query_hash,
            "actor": actor,
            "old_hash": old_manifest,
            "new_hash": started_against,
        },
    )
    return outcome


def revalidate(
    *,
    connection: psycopg.Connection,
    deployment_id: UUID,
    query_id: UUID,
    version: int,
    started_against: str,
    executor: QuerySandboxExecutor,
    fixtures: Mapping[str, OperatorFixture | Sequence[object]],
    minor_compatible: bool,
    actor: str,
    can_activate: ActivationAuthority | None = None,
) -> str:
    """Decide what a completed revalidation may do to a suspended version (§5).

    Prefer `SavedQueryRegistry.revalidate` when a host already holds a bound
    registry. This free function is the same protocol with an explicit actor
    and the same default-deny activation policy as the registry: restoration
    to `active` requires `can_activate(actor)`. Platform-owned shipped
    identities require activation authority for any transition (including
    `broken`).

    Protocol (capture → unlocked execution → CAS at transition):

    1. Read the authoritative surface hash without holding the publication
       lock through EXPLAIN/fixtures.
    2. Load the stored SQL and run operator-owned fixtures through the real
       `QuerySandboxExecutor` while unlocked, so a concurrent
       `publish_surface_hash` is not blocked by the validator.
    3. Lock, re-read the authoritative hash, and compare-and-swap the version
       transition only when `started_against` is still current and the version
       is still `pending_revalidation`. Otherwise raise `SurfaceMoved`; the
       version remains pending for a fresh validation against the new hash.

    Fixture success comes only from that execution — callers cannot pass a
    fabricated pass flag. `minor_compatible` remains the compatibility
    decision; both it, a passing bound report, and activation authority are
    required to restore `active`.
    """
    return _revalidate_version(
        connection=connection,
        deployment_id=deployment_id,
        query_id=query_id,
        version=version,
        started_against=started_against,
        executor=executor,
        fixtures=fixtures,
        minor_compatible=minor_compatible,
        actor=actor,
        can_activate=can_activate or default_deny_activation,
    )


def seed_shipped_examples(
    *, connection: psycopg.Connection, deployment_id: UUID, manifest_hash: str
) -> int:
    """Idempotently install all eighteen `examples.*` identities (bootstrap).

    Constructs a registry bound to `PLATFORM_SEED_ACTOR` with a matching
    activation policy and delegates to `install_shipped_examples`. Safe to call
    from self-host setup on both new and existing deployments; not an Alembic
    migration. Returns how many identities were newly installed (0 on a
    second seed of the same bodies).
    """
    registry = SavedQueryRegistry(
        connection=connection,
        deployment_id=deployment_id,
        manifest_hash=manifest_hash,
        actor=PLATFORM_SEED_ACTOR,
        can_activate=lambda actor: actor == PLATFORM_SEED_ACTOR,
    )
    return registry.install_shipped_examples()


def publish_surface_hash(
    *,
    connection: psycopg.Connection,
    deployment_id: UUID,
    manifest_hash: str,
    actor: str = "surface",
) -> int:
    """Publish a new surface hash and suspend active versions, atomically (§5).

    Writes the deployment's authoritative hash into registry state and moves
    every active version to `pending_revalidation` in the CALLER'S transaction.
    Between the two there must be no instant at which a version is executable
    while claiming validation against a surface that has been replaced.
    """
    old = connection.execute(
        b"SELECT surface_manifest_hash FROM saved_query_registry_state"
        b" WHERE deployment_id = %(deployment)s"
        b" FOR UPDATE",
        {"deployment": str(deployment_id)},
    ).fetchone()
    old_hash = old[0] if old is not None else None
    connection.execute(
        b"INSERT INTO saved_query_registry_state"
        b" (deployment_id, surface_manifest_hash, updated_at)"
        b" VALUES (%(deployment)s, %(manifest)s, now())"
        b" ON CONFLICT (deployment_id) DO UPDATE"
        b" SET surface_manifest_hash = EXCLUDED.surface_manifest_hash,"
        b"     updated_at = now()",
        {"deployment": str(deployment_id), "manifest": manifest_hash},
    )
    cursor = connection.execute(
        b"UPDATE saved_query_versions SET status = 'pending_revalidation'"
        b" WHERE deployment_id = %(deployment)s AND status = 'active'"
        b"   AND validated_surface_manifest_hash <> %(manifest)s",
        {"deployment": str(deployment_id), "manifest": manifest_hash},
    )
    connection.execute(
        b"INSERT INTO saved_query_audit"
        b" (deployment_id, query_id, version, query_hash, actor, action,"
        b"  old_hash, new_hash)"
        b" VALUES (%(deployment)s, NULL, NULL, NULL, %(actor)s, 'publish',"
        b" %(old_hash)s, %(new_hash)s)",
        {
            "deployment": str(deployment_id),
            "actor": actor,
            "old_hash": old_hash,
            "new_hash": manifest_hash,
        },
    )
    return cursor.rowcount


def _json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, sort_keys=True)


def validate_parameter_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Accept only the short JSON Schema scalar/array shapes §5 allows."""
    return _validate_json_schema_shape(schema, what="parameter_schema")


def validate_result_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Accept only the short JSON Schema scalar/array shapes §5 allows."""
    return _validate_json_schema_shape(schema, what="declared_result_schema")


def validate_default_limits(limits: dict[str, Any]) -> dict[str, Any]:
    """Verify declared defaults against the authoritative §4.3 hard caps."""
    if not isinstance(limits, dict):
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message="default_limits must be an object",
        )
    unknown = set(limits) - _DEFAULT_LIMIT_KEYS
    if unknown:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message=f"default_limits has unknown keys: {sorted(unknown)}",
        )
    # Hard caps are the stricter interactive numbers for interactive defaults;
    # analytical hard caps are the absolute ceiling a save may declare.
    hard_rows = max(
        INTERACTIVE_LIMITS.returned_rows_hard, ANALYTICAL_LIMITS.returned_rows_hard
    )
    hard_timeout = max(
        INTERACTIVE_LIMITS.statement_timeout_ms_hard,
        ANALYTICAL_LIMITS.statement_timeout_ms_hard,
    )
    hard_bytes = max(
        INTERACTIVE_LIMITS.returned_bytes_hard, ANALYTICAL_LIMITS.returned_bytes_hard
    )
    cleaned: dict[str, Any] = {}
    if "max_rows" in limits:
        value = limits["max_rows"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message="default_limits.max_rows must be a positive integer",
            )
        if value > hard_rows:
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message=f"default_limits.max_rows exceeds hard cap {hard_rows}",
            )
        cleaned["max_rows"] = value
    if "statement_timeout_ms" in limits:
        value = limits["statement_timeout_ms"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message="default_limits.statement_timeout_ms must be a positive integer",
            )
        if value > hard_timeout:
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message=(
                    "default_limits.statement_timeout_ms exceeds hard cap"
                    f" {hard_timeout}"
                ),
            )
        cleaned["statement_timeout_ms"] = value
    if "max_bytes" in limits:
        value = limits["max_bytes"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message="default_limits.max_bytes must be a positive integer",
            )
        if value > hard_bytes:
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message=f"default_limits.max_bytes exceeds hard cap {hard_bytes}",
            )
        cleaned["max_bytes"] = value
    return cleaned


def _validate_json_schema_shape(schema: dict[str, Any], *, what: str) -> dict[str, Any]:
    """A short typed check: object of named scalar or array-of-scalar fields."""
    if not isinstance(schema, dict):
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER, message=f"{what} must be an object"
        )
    if not schema:
        return {}
    # Two accepted shapes:
    # 1) {"type": "object", "properties": {...}, "required": [...]}?
    # 2) a bare properties map {name: {"type": ...}, ...}
    if schema.get("type") == "object" or "properties" in schema:
        if schema.get("type") not in (None, "object"):
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message=f"{what}.type must be 'object' when properties are declared",
            )
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message=f"{what}.properties must be an object",
            )
        cleaned_props = {
            name: _validate_field_schema(field, what=f"{what}.properties.{name}")
            for name, field in properties.items()
        }
        required = schema.get("required", [])
        if required is not None and not isinstance(required, list):
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message=f"{what}.required must be an array of property names",
            )
        if required:
            for name in required:
                if name not in cleaned_props:
                    raise SandboxRejection(
                        code=QueryErrorCode.INVALID_PARAMETER,
                        message=f"{what}.required names unknown property {name!r}",
                    )
        out: dict[str, Any] = {"type": "object", "properties": cleaned_props}
        if required:
            out["required"] = list(required)
        return out
    # Bare map of field schemas.
    return {
        name: _validate_field_schema(field, what=f"{what}.{name}")
        for name, field in schema.items()
    }


def _validate_field_schema(field: object, *, what: str) -> dict[str, Any]:
    if not isinstance(field, dict) or "type" not in field:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message=f"{what} must be a schema object with a type",
        )
    field_type = field["type"]
    if isinstance(field_type, list):
        if not field_type or any(t not in _SCALAR_JSON_TYPES for t in field_type):
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message=f"{what}.type must be scalar or an array of scalars",
            )
        return {"type": list(field_type)}
    if field_type in _SCALAR_JSON_TYPES:
        return {"type": field_type}
    if field_type == "array":
        items = field.get("items")
        if not isinstance(items, dict) or items.get("type") not in _SCALAR_JSON_TYPES:
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message=f"{what}.items must declare a scalar type",
            )
        return {"type": "array", "items": {"type": items["type"]}}
    raise SandboxRejection(
        code=QueryErrorCode.INVALID_PARAMETER,
        message=(
            f"{what}.type must be a JSON Schema scalar, array-of-scalar,"
            f" or object properties map; got {field_type!r}"
        ),
    )


def _report_authorizes_activation(
    *, report: dict[str, Any], query_hash: str, manifest_hash: str
) -> bool:
    """True only when the stored report is bound and fully passing."""
    if not report.get("passed"):
        return False
    if report.get("query_hash") != query_hash:
        return False
    if report.get("manifest_hash") != manifest_hash:
        return False
    fixtures = report.get("fixtures")
    if not isinstance(fixtures, dict):
        return False
    return all(fixtures.get(name) is True for name in VALIDATION_FIXTURES)


def declared_examples() -> Sequence[tuple[str, str]]:
    """The `examples.*` names this build ships, as (name, purpose) pairs.

    Derived from the single checked-in `EXAMPLE_QUERIES` table so purpose
    metadata cannot drift from the bodies the registry seeds and resolves.
    """
    from rememberstack.surfaces.query_sandbox.examples import EXAMPLE_QUERIES

    return tuple(
        (name, purpose) for name, (purpose, _sql) in sorted(EXAMPLE_QUERIES.items())
    )


def __getattr__(name: str) -> object:
    """Lazy `SHIPPED_EXAMPLES` alias so it cannot diverge from EXAMPLE_QUERIES."""
    if name == "SHIPPED_EXAMPLES":
        return tuple(declared_examples())
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    evidence. The report is bound to the SQL's query_hash and the surface
    manifest hash it ran against.
    """

    manifest_hash: str
    query_hash: str
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
            "query_hash": self.query_hash,
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

    Positive: completed with at least one row — proves the documented mapping
    returns content for the operator's live parameters (an always-empty body
    fails). Empty and tombstone: completed with no rows — the operator
    supplies parameters that must not invent content (empty) or must not
    surface deleted content (tombstone). Cap: completed under the operator's
    requested row bound with at least one row and never more than the bound
    (so an empty result cannot masquerade as a real cap).
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
        if outcome.returned_row_count == 0:
            return False, "cap: expected rows within bound, got empty"
        if outcome.returned_row_count > fixture.max_rows:
            return (
                False,
                f"cap: returned {outcome.returned_row_count} rows above"
                f" max_rows={fixture.max_rows}",
            )
        return True, f"cap: {outcome.returned_row_count} rows within bound"
    if fixture.kind == "positive":
        if outcome.empty_result:
            return False, "positive: expected at least one row"
        return True, f"positive: {outcome.returned_row_count} rows"
    return False, f"{fixture.kind}: unknown fixture class"


def validate_saved_sql(
    *,
    executor: QuerySandboxExecutor,
    sql: str,
    fixtures: Mapping[str, OperatorFixture | Sequence[object]],
    principal: str = "validator",
    manifest_hash: str | None = None,
    query_hash: str | None = None,
    tier: LimitTier = LimitTier.INTERACTIVE,
) -> ValidationReport:
    """Execute every §5 fixture through the real sandbox and record outcomes.

    This is the saving validator §5 requires: it does not invent another SQL
    path. Each operator-owned case is run with bound parameters via
    `QuerySandboxExecutor.query_sql` (and a single safe EXPLAIN for
    diagnostics). Missing a required fixture class fails that class rather
    than silently skipping it. Parameter values never enter the SQL text.

    The report is bound to the SQL's query_hash and the *authoritative*
    surface manifest hash (the `manifest_hash` argument, or the executor's
    own hash when omitted). EXPLAIN and every fixture result must report that
    same `surface_manifest_hash`; a mismatched executor fails the evidence
    rather than being relabeled.

    `tier` selects the §4.3 limit column for the fixture runs (default
    interactive). Complex multi-join bodies may need analytical entitlement
    and the analytical tier for a realistic statement budget under the same
    language and tenancy rules.
    """
    validated = validate_sql(sql)
    bound_query_hash = query_hash if query_hash is not None else validated.query_hash
    if query_hash is not None and query_hash != validated.query_hash:
        raise SandboxRejection(
            code=QueryErrorCode.SAVED_QUERY_INCOMPATIBLE,
            message="validation query_hash does not match the SQL being validated",
        )
    expected_hash = (
        manifest_hash
        if manifest_hash is not None
        else str(getattr(executor, "_manifest_hash", "") or "")
    )
    outcomes: dict[str, bool] = {}
    diagnostics: list[str] = []
    explain_matched = True

    def _manifest_matches(result: QueryResult) -> bool:
        return result.surface_manifest_hash == expected_hash

    explain_parameters: tuple[object, ...] = ()
    if "positive" in fixtures:
        explain_parameters = _as_operator_fixture(
            kind="positive", value=fixtures["positive"]
        ).parameters
    explain = executor.explain_sql(
        sql=sql, parameters=explain_parameters, principal=principal, tier=tier
    )
    if not _manifest_matches(explain):
        explain_matched = False
        diagnostics.append(
            "explain: surface_manifest_hash mismatch"
            f" (executor={explain.surface_manifest_hash!r},"
            f" expected={expected_hash!r})"
        )
    elif explain.error_code is not None:
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
            tier=tier,
        )
        if not _manifest_matches(result):
            outcomes[kind] = False
            diagnostics.append(
                f"{kind}: surface_manifest_hash mismatch"
                f" (executor={result.surface_manifest_hash!r},"
                f" expected={expected_hash!r})"
            )
            continue
        if not explain_matched:
            outcomes[kind] = False
            diagnostics.append(f"{kind}: refused; EXPLAIN surface hash mismatched")
            continue
        passed, note = _fixture_passed(fixture=fixture, outcome=result)
        outcomes[kind] = passed
        diagnostics.append(note)

    return ValidationReport(
        manifest_hash=str(expected_hash),
        query_hash=bound_query_hash,
        fixtures=outcomes,
        diagnostics=tuple(diagnostics),
    )
