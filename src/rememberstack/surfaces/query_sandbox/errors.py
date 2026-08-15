"""The exhaustive public error taxonomy for the open query space (design §4.1).

Raw PostgreSQL error details, object names outside the public schema, and
query fragments never reach a caller: every failure maps to one code below
plus a caller-safe message authored here.
"""

from enum import StrEnum


class QueryErrorCode(StrEnum):
    """Every public failure code, exactly as bound in design §4.1."""

    # Parse / validation
    PARSE_ERROR = "parse_error"
    MULTIPLE_STATEMENTS = "multiple_statements"
    STATEMENT_NOT_ALLOWED = "statement_not_allowed"
    RELATION_NOT_ALLOWED = "relation_not_allowed"
    FUNCTION_NOT_ALLOWED = "function_not_allowed"
    FUNCTION_PLACEMENT_NOT_ALLOWED = "function_placement_not_allowed"
    OPERATOR_NOT_ALLOWED = "operator_not_allowed"
    INVALID_PARAMETER = "invalid_parameter"
    SCHEMA_VERSION_MISMATCH = "schema_version_mismatch"
    UNBOUNDED_RECURSION = "unbounded_recursion"
    # Cypher phase (Batch D raises these; bound here so the taxonomy is one place)
    CYPHER_PARSE_ERROR = "cypher_parse_error"
    CYPHER_NOT_ALLOWED = "cypher_not_allowed"
    # Admission
    QUOTA_EXCEEDED = "quota_exceeded"
    CONCURRENCY_EXCEEDED = "concurrency_exceeded"
    SAVED_QUERY_NOT_FOUND = "saved_query_not_found"
    SAVED_QUERY_DISABLED = "saved_query_disabled"
    SAVED_QUERY_INCOMPATIBLE = "saved_query_incompatible"
    SAVED_QUERY_REVALIDATION_PENDING = "saved_query_revalidation_pending"
    # Execution
    STATEMENT_TIMEOUT = "statement_timeout"
    LOCK_TIMEOUT = "lock_timeout"
    CANCELLED = "cancelled"
    RESOURCE_LIMIT = "resource_limit"
    EXECUTION_ERROR = "execution_error"
    # Store / confirmation
    PG_UNAVAILABLE = "pg_unavailable"
    P1_UNAVAILABLE = "p1_unavailable"
    P2_UNAVAILABLE = "p2_unavailable"
    CORPUS_BODY_UNAVAILABLE = "corpus_body_unavailable"
    GENERATION_UNAVAILABLE = "generation_unavailable"
    CONFIRMATION_FAILED = "confirmation_failed"


class SandboxRejection(Exception):
    """One rejected or failed sandbox request, carrying only public content."""

    def __init__(
        self,
        *,
        code: QueryErrorCode,
        message: str,
        engine_fault_class: str | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.engine_fault_class = engine_fault_class
