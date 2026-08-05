"""The §4.1 grammar gate: default-deny validation of one parsed SQL statement.

Every request is parsed with PostgreSQL's own grammar (pglast) and walked
against the design's exact allowlists before PostgreSQL ever sees it. The
gate is deliberately a static, exhaustive allowlist — the cheap correct
posture against a hostile-infinite builtin surface — while the §4.3 caps and
the read-only role bound whatever the allowlist admits.
"""

from dataclasses import dataclass
import hashlib
from typing import Final

from pglast import parse_sql
from pglast import prettify  # noqa: F401  (re-exported for tests' readability)
from pglast.ast import A_Expr
from pglast.ast import ColumnRef
from pglast.ast import CommonTableExpr
from pglast.ast import FuncCall
from pglast.ast import Node
from pglast.ast import ParamRef
from pglast.ast import RangeFunction
from pglast.ast import RangeSubselect
from pglast.ast import RangeVar
from pglast.ast import RawStmt
from pglast.ast import SelectStmt
from pglast.ast import SortBy
from pglast.ast import String
from pglast.ast import SubLink
from pglast.ast import TypeCast
from pglast.enums import CTEMaterialize
from pglast.enums import SetOperation
from pglast.parser import ParseError
from pglast.stream import RawStream
from pglast.visitors import Skip
from pglast.visitors import Visitor

from rememberstack.spine.query_space.catalog import VIEW_CONTRACTS_BY_NAME
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection

_PUBLIC_SCHEMA: Final = "memory_v1"
MEMORY_V1_VIEW_NAMES: Final = frozenset(VIEW_CONTRACTS_BY_NAME)

# §3.4 public SRFs. Batch C/D land their SQL bodies; the grammar knows the
# names now so placement rules and the MATERIALIZED-CTE rewrite are complete
# from day one (an accepted call simply fails at execution until the function
# exists, with the store-phase taxonomy).
PUBLIC_SRF_NAMES: Final = frozenset(
    {
        "facts_as_of",
        "semantic_claims",
        "semantic_chunks",
        "semantic_facts",
        "semantic_entities",
        "lexical_claims",
        "lexical_chunks",
        "fetch_chunk_bodies",
        "graph_neighborhood",
        "graph_path",
    }
)
SRF_INVOCATIONS_MAX: Final = 3

FUNCTION_ALLOWLIST: Final = frozenset(
    {
        "count",
        "sum",
        "avg",
        "min",
        "max",
        "bool_and",
        "bool_or",
        "array_agg",
        "string_agg",
        "jsonb_agg",
        "jsonb_object_agg",
        "coalesce",
        "nullif",
        "greatest",
        "least",
        "lower",
        "upper",
        "trim",
        "btrim",
        "length",
        "octet_length",
        "substring",
        "replace",
        "regexp_replace",
        "abs",
        "ceil",
        "floor",
        "round",
        "date_trunc",
        "extract",
        "make_interval",
        "array_length",
        "cardinality",
        "jsonb_typeof",
        "jsonb_array_length",
        "jsonb_build_object",
        "row_number",
        "rank",
        "dense_rank",
        "lag",
        "lead",
        "first_value",
        "last_value",
    }
)

OPERATOR_ALLOWLIST: Final = frozenset(
    {
        "=",
        "<>",
        "!=",  # PostgreSQL normalizes != to <>; both appear in parsed text
        "<",
        "<=",
        ">",
        ">=",
        "+",
        "-",
        "*",
        "/",
        "%",
        "||",
        "@>",
        "<@",
        "&&",
        "->",
        "->>",
        "#>",
        "#>>",
        "~",
        "~*",
        "!~",
        "!~*",
        "~~",  # LIKE
        "!~~",  # NOT LIKE
        "~~*",  # ILIKE
        "!~~*",  # NOT ILIKE
        "= ANY",
        "BETWEEN",
        "NOT BETWEEN",
    }
)

CAST_TYPE_ALLOWLIST: Final = frozenset(
    {
        "uuid",
        "text",
        "varchar",
        "bpchar",
        "bool",
        "boolean",
        "int2",
        "int4",
        "int8",
        "integer",
        "bigint",
        "smallint",
        "numeric",
        "float4",
        "float8",
        "timestamptz",
        "timestamp",
        "date",
        "interval",
        "jsonb",
    }
)

RECURSION_DEPTH_MAX: Final = 6


@dataclass
class ValidatedQuery:
    """The gate's accepted output: rewritten SQL plus disclosure inputs."""

    sql: str
    query_hash: str
    referenced_views: tuple[str, ...]
    referenced_functions: tuple[str, ...]
    srf_invocations: int
    ordered_result: bool
    parameter_count: int
    #: `__srf_N` → (function name, literal/parameter argument list). The
    #: executor resolves these before planning; see `nomination.py`.
    srf_bindings: tuple[tuple[str, str, tuple[object, ...]], ...] = ()
    is_recursive: bool = False
    rewritten: bool = False


def _sval(node: object) -> str:
    """The text of a pglast `String` node, narrowed for the type checker."""
    value = getattr(node, "sval", None)
    return value if isinstance(value, str) else ""


def _ival(node: object) -> int | None:
    """The value of a pglast `Integer` node, narrowed for the type checker."""
    value = getattr(node, "ival", None)
    return value if isinstance(value, int) else None


def _reject(code: QueryErrorCode, message: str) -> SandboxRejection:
    return SandboxRejection(code=code, message=message)


def _func_name(call: FuncCall) -> str:
    """The (schema-stripped, lowered) function name; rejects escape schemas."""
    parts = [_sval(part) for part in call.funcname or () if _sval(part)]
    if not parts:
        raise _reject(
            QueryErrorCode.FUNCTION_NOT_ALLOWED, "unnamed function call is not allowed"
        )
    if len(parts) == 2 and parts[0] not in ("pg_catalog", _PUBLIC_SCHEMA):
        raise _reject(
            QueryErrorCode.FUNCTION_NOT_ALLOWED,
            f"schema-qualified function {'.'.join(parts)} is not allowed",
        )
    if len(parts) > 2:
        raise _reject(
            QueryErrorCode.FUNCTION_NOT_ALLOWED,
            "nested-qualified function names are not allowed",
        )
    return parts[-1].lower()


class _AllowlistVisitor(Visitor):
    """Walks the full tree enforcing relations, functions, operators, casts."""

    def __init__(self, *, cte_names: frozenset[str]) -> None:
        super().__init__()
        self.cte_names = cte_names
        self.views: set[str] = set()
        self.functions: set[str] = set()
        self.srf_calls: list[FuncCall] = []

    def visit_RangeVar(self, ancestors, node: RangeVar):  # noqa: ANN001, ANN201
        schema = node.schemaname
        name = (node.relname or "").lower()
        if schema is not None and schema != _PUBLIC_SCHEMA:
            raise _reject(
                QueryErrorCode.RELATION_NOT_ALLOWED, f"schema {schema} is not queryable"
            )
        if schema is None and name in self.cte_names:
            return None
        if name not in MEMORY_V1_VIEW_NAMES:
            raise _reject(
                QueryErrorCode.RELATION_NOT_ALLOWED,
                f"relation {name} is not part of {_PUBLIC_SCHEMA}",
            )
        self.views.add(name)
        return None

    def visit_FuncCall(self, ancestors, node: FuncCall):  # noqa: ANN001, ANN201
        name = _func_name(node)
        if node.agg_within_group:
            raise _reject(
                QueryErrorCode.STATEMENT_NOT_ALLOWED,
                "WITHIN GROUP ordered-set aggregates are not allowed",
            )
        if name in PUBLIC_SRF_NAMES:
            self.srf_calls.append(node)
            self.functions.add(name)
            return None
        if name not in FUNCTION_ALLOWLIST:
            raise _reject(
                QueryErrorCode.FUNCTION_NOT_ALLOWED, f"function {name} is not allowed"
            )
        self.functions.add(name)
        return None

    def visit_A_Expr(self, ancestors, node: A_Expr):  # noqa: ANN001, ANN201
        for piece in node.name or ():
            if isinstance(piece, String) and _sval(piece) not in OPERATOR_ALLOWLIST:
                raise _reject(
                    QueryErrorCode.OPERATOR_NOT_ALLOWED,
                    f"operator {piece.sval} is not allowed",
                )
        return None

    def visit_TypeCast(self, ancestors, node: TypeCast):  # noqa: ANN001, ANN201
        type_name = node.typeName
        names = [
            _sval(part)
            for part in ((type_name.names if type_name else None) or ())
            if _sval(part)
        ]
        bare = names[-1].lower() if names else ""
        if bare not in CAST_TYPE_ALLOWLIST:
            raise _reject(
                QueryErrorCode.OPERATOR_NOT_ALLOWED, f"cast to {bare} is not allowed"
            )
        return None

    def visit_RangeTableSample(self, ancestors, node):  # noqa: ANN001, ANN201
        raise _reject(
            QueryErrorCode.STATEMENT_NOT_ALLOWED, "TABLESAMPLE is not allowed"
        )

    def visit_RangeTableFunc(self, ancestors, node):  # noqa: ANN001, ANN201
        # XMLTABLE and JSON_TABLE produce relations without ever passing
        # through FuncCall, so the function allowlist cannot see them.
        raise _reject(
            QueryErrorCode.FUNCTION_NOT_ALLOWED,
            "table functions are not part of the public surface",
        )

    def visit_SQLValueFunction(self, ancestors, node):  # noqa: ANN001, ANN201
        # CURRENT_USER, CURRENT_SCHEMA, CURRENT_CATALOG and friends parse as
        # keywords rather than calls; they disclose session and catalog
        # identity, which the public surface never exposes.
        raise _reject(
            QueryErrorCode.FUNCTION_NOT_ALLOWED,
            "session value keywords are not allowed",
        )

    def visit_XmlExpr(self, ancestors, node):  # noqa: ANN001, ANN201
        raise _reject(
            QueryErrorCode.FUNCTION_NOT_ALLOWED, "XML expressions are not allowed"
        )

    def visit_XmlSerialize(self, ancestors, node):  # noqa: ANN001, ANN201
        raise _reject(
            QueryErrorCode.FUNCTION_NOT_ALLOWED, "XML expressions are not allowed"
        )

    def visit_LockingClause(self, ancestors, node):  # noqa: ANN001, ANN201
        raise _reject(QueryErrorCode.STATEMENT_NOT_ALLOWED, "row locks are not allowed")

    def visit_SelectStmt(self, ancestors, node: SelectStmt):  # noqa: ANN001, ANN201
        if node.intoClause is not None:
            raise _reject(
                QueryErrorCode.STATEMENT_NOT_ALLOWED, "SELECT INTO is not allowed"
            )
        return None


def _assert_single_readonly_statement(sql: str) -> SelectStmt:
    try:
        statements = parse_sql(sql)
    except ParseError as error:
        raise _reject(QueryErrorCode.PARSE_ERROR, str(error)) from error
    if len(statements) != 1:
        raise _reject(
            QueryErrorCode.MULTIPLE_STATEMENTS,
            "one request contains exactly one statement",
        )
    raw = statements[0]
    assert isinstance(raw, RawStmt)
    statement = raw.stmt
    if not isinstance(statement, SelectStmt):
        raise _reject(
            QueryErrorCode.STATEMENT_NOT_ALLOWED,
            "only a read-only SELECT/VALUES/WITH statement is allowed",
        )
    return statement


_SRF_CTE_PREFIX: Final = "__srf_"


def _collect_ctes(statement: SelectStmt) -> tuple[frozenset[str], bool]:
    """Every CTE name in the statement, plus whether the top level recurses.

    Nested `WITH` clauses (inside a CTE body or a subquery) define names the
    relation allowlist must recognize too, or legitimate composition is
    rejected as an unknown relation.
    """

    class _AllWith(Visitor):
        def __init__(self) -> None:
            super().__init__()
            self.clauses: list[object] = []

        def visit_WithClause(self, ancestors, node):  # noqa: ANN001, ANN201
            self.clauses.append(node)
            return None

    scan = _AllWith()
    scan(statement)
    names: set[str] = set()
    for nested in scan.clauses:
        for cte in getattr(nested, "ctes", None) or ():
            if isinstance(cte, CommonTableExpr):
                nested_name = str(cte.ctename or "").lower()
                if nested_name.startswith(_SRF_CTE_PREFIX):
                    raise _reject(
                        QueryErrorCode.RELATION_NOT_ALLOWED,
                        f"CTE names beginning with {_SRF_CTE_PREFIX} are reserved",
                    )
                if nested_name in MEMORY_V1_VIEW_NAMES:
                    # The shadow rule is about what a reader believes a name
                    # means, so it holds at every nesting level, not just the
                    # top one.
                    raise _reject(
                        QueryErrorCode.RELATION_NOT_ALLOWED,
                        f"CTE name {nested_name} shadows a {_PUBLIC_SCHEMA} relation",
                    )
                names.add(nested_name)

    clause = statement.withClause
    if clause is None:
        return frozenset(names), False
    for cte in clause.ctes or ():
        assert isinstance(cte, CommonTableExpr)
        name = str(cte.ctename or "").lower()
        if name in MEMORY_V1_VIEW_NAMES:
            raise _reject(
                QueryErrorCode.RELATION_NOT_ALLOWED,
                f"CTE name {name} shadows a {_PUBLIC_SCHEMA} relation",
            )
        if not isinstance(cte.ctequery, SelectStmt):
            raise _reject(
                QueryErrorCode.STATEMENT_NOT_ALLOWED,
                "data-modifying CTEs are not allowed",
            )
        names.add(name)
    return frozenset(names), bool(clause.recursive)


class _DepthAssignmentScan(Visitor):
    """Rejects any reassignment-shaped use of `depth` in the recursive arm."""

    def __init__(self) -> None:
        super().__init__()
        self.increments = 0

    def visit_A_Expr(self, ancestors, node: A_Expr):  # noqa: ANN001, ANN201
        op = "".join(_sval(piece) for piece in node.name or ())
        left, right = node.lexpr, node.rexpr
        if op == "+" and (
            _is_depth_column(left)
            and _is_integer_literal(right, 1)
            or _is_depth_column(right)
            and _is_integer_literal(left, 1)
        ):
            self.increments += 1
        return None


def _is_depth_column(node: Node | None, *, qualifier: str | None = None) -> bool:
    """True for a `depth` column reference, optionally pinned to one relation.

    Pinning matters: a recursive term can cross-join a constant subquery that
    also publishes `depth`, and bound or increment the WRONG one — the bound is
    then always true, or the emitted depth never advances.
    """
    if (
        not isinstance(node, ColumnRef)
        or not node.fields
        or not isinstance(node.fields[-1], String)
        or _sval(node.fields[-1]).lower() != "depth"
    ):
        return False
    if qualifier is None:
        return True
    parts = [_sval(field) for field in node.fields]
    if len(parts) == 1:
        # Unqualified is only unambiguous when the self-reference is the sole
        # source of rows, which the caller checks separately.
        return True
    return parts[-2].lower() == qualifier.lower()


def _is_integer_literal(node: Node | None, value: int) -> bool:
    from pglast.ast import A_Const
    from pglast.ast import Integer

    return (
        isinstance(node, A_Const)
        and isinstance(node.val, Integer)
        and node.val.ival == value
    )


def _reject_self_reference_without_recursive(statement: SelectStmt) -> None:
    """A plain `WITH` name is not in scope inside its own body.

    PostgreSQL would reject it too, but as an opaque execution error; the
    public surface owes the caller a stable parse-phase code.
    """

    class _Scan(Visitor):
        def visit_WithClause(self, ancestors, node):  # noqa: ANN001, ANN201
            if getattr(node, "recursive", False):
                return None
            for cte in getattr(node, "ctes", None) or ():
                if isinstance(cte, CommonTableExpr) and _cte_is_self_referencing(cte):
                    raise _reject(
                        QueryErrorCode.RELATION_NOT_ALLOWED,
                        f"CTE {cte.ctename} is not in scope inside its own body;"
                        " WITH RECURSIVE is required to reference it",
                    )
            return None

    scan = _Scan()
    scan(statement)


def _recursive_with_clauses(statement: SelectStmt):  # noqa: ANN201
    """Every `WITH RECURSIVE` clause in the statement, at any nesting level."""
    found: list[tuple[object, object]] = []

    class _Scan(Visitor):
        def visit_WithClause(self, ancestors, node):  # noqa: ANN001, ANN201
            if getattr(node, "recursive", False):
                found.append((node, None))
            return None

    scan = _Scan()
    scan(statement)
    return found


def _validate_recursive_template(clause, owner=None) -> None:  # noqa: ANN001
    """Enforce the single §4.1 WITH RECURSIVE template, structurally.

    The template is narrow on purpose: one recursive CTE, one self-reference,
    an anchor that sets `depth` to 0, a recursive term whose OUTPUT `depth`
    column IS `depth + 1`, and a top-level `depth < N` conjunct with N <= 6.
    Checking only that the shapes appear *somewhere* is not enough — a term
    can carry a decorative `depth + 1` while emitting an unchanged or
    oscillating depth, which recurses without bound.
    """
    recursive = [
        cte
        for cte in getattr(clause, "ctes", None) or ()
        if isinstance(cte, CommonTableExpr) and _cte_is_self_referencing(cte)
    ]
    if len(getattr(clause, "ctes", None) or ()) != 1 or len(recursive) != 1:
        raise _reject(
            QueryErrorCode.UNBOUNDED_RECURSION,
            "WITH RECURSIVE allows exactly one recursive CTE",
        )
    cte = recursive[0]
    if cte.cycle_clause is not None or cte.search_clause is not None:
        raise _reject(
            QueryErrorCode.UNBOUNDED_RECURSION,
            "the recursive template has no CYCLE or SEARCH clause",
        )
    body = cte.ctequery
    assert isinstance(body, SelectStmt)
    if body.op != SetOperation.SETOP_UNION:
        raise _reject(
            QueryErrorCode.UNBOUNDED_RECURSION,
            "the recursive CTE must be anchor UNION [ALL] recursive-term",
        )
    anchor, recursive_arm = body.larg, body.rarg
    if anchor is None or recursive_arm is None:
        raise _reject(QueryErrorCode.UNBOUNDED_RECURSION, "malformed recursive CTE")
    cte_name = str(cte.ctename) if isinstance(cte.ctename, str) else ""
    anchor_position = _depth_target_position(anchor, name=cte_name)
    if anchor_position is None or not _is_integer_literal(
        _target_value(anchor, anchor_position), 0
    ):
        raise _reject(
            QueryErrorCode.UNBOUNDED_RECURSION,
            "the anchor term must initialize an integer column depth to 0",
        )
    if _self_reference_count(recursive_arm, name=cte_name) != 1:
        raise _reject(
            QueryErrorCode.UNBOUNDED_RECURSION,
            "the recursive term must reference the CTE exactly once",
        )
    if _has_subquery_from_item(recursive_arm):
        # A constant subquery in the recursive FROM multiplies the frontier
        # every round and can publish a decoy `depth` column.
        raise _reject(
            QueryErrorCode.UNBOUNDED_RECURSION,
            "the recursive term may join only public relations, not subqueries",
        )
    self_alias = _self_reference_alias(recursive_arm, name=cte_name)
    # Frontier width is bounded by the §4.3 resource controls (statement
    # timeout, work_mem, temp files), not by a relation count: a count is both
    # stricter than §4.1 — it would reject a traversal that joins an edge view
    # and a filter view — and ineffective, since a Cartesian product hidden in
    # an outer CTE reads as one relation here.
    sole_source = _from_item_count(recursive_arm) == 1
    emitted = _target_value(recursive_arm, anchor_position)
    if not _is_depth_plus_one(emitted, qualifier=None if sole_source else self_alias):
        raise _reject(
            QueryErrorCode.UNBOUNDED_RECURSION,
            "the recursive term must emit depth + 1 in the depth column",
        )
    bound = _recursive_arm_depth_bound(
        recursive_arm, qualifier=None if sole_source else self_alias
    )
    if bound is None or bound > RECURSION_DEPTH_MAX:
        raise _reject(
            QueryErrorCode.UNBOUNDED_RECURSION,
            "the recursive term must be bounded by the literal predicate"
            f" depth < N with N <= {RECURSION_DEPTH_MAX}, with no OR around it",
        )


def _depth_target_position(select: SelectStmt, *, name: str) -> int | None:
    """The position of the column named (or aliased) `depth`."""
    from pglast.ast import ResTarget

    for position, target in enumerate(select.targetList or ()):
        if not isinstance(target, ResTarget):
            continue
        if str(target.name or "").lower() == "depth":
            return position
        if target.name is None and _is_depth_column(target.val):
            return position
    return None


def _target_value(select: SelectStmt, position: int):  # noqa: ANN202
    """The expression a select emits at one target position."""
    targets = select.targetList or ()
    if position >= len(targets):
        return None
    target = targets[position]
    return getattr(target, "val", None)


def _is_depth_plus_one(node: Node | None, *, qualifier: str | None = None) -> bool:
    """True only for the exact expression `depth + 1` (either operand order)."""
    if not isinstance(node, A_Expr):
        return False
    if "".join(_sval(part) for part in node.name or ()) != "+":
        return False
    left, right = node.lexpr, node.rexpr
    return (
        _is_depth_column(left, qualifier=qualifier) and _is_integer_literal(right, 1)
    ) or (_is_depth_column(right, qualifier=qualifier) and _is_integer_literal(left, 1))


def _self_reference_alias(select: SelectStmt, *, name: str) -> str:
    """The alias the recursive term binds to its own CTE (or the CTE name)."""
    target = name.lower()

    class _Alias(Visitor):
        def __init__(self) -> None:
            super().__init__()
            self.alias = name

        def visit_RangeVar(self, ancestors, node: RangeVar):  # noqa: ANN001, ANN201
            if node.schemaname is None and (node.relname or "").lower() == target:
                aliasname = getattr(node.alias, "aliasname", None)
                if isinstance(aliasname, str) and aliasname:
                    self.alias = aliasname
            return None

    scan = _Alias()
    scan(select)
    return scan.alias


def _has_subquery_from_item(select: SelectStmt) -> bool:
    """True when any FROM item of the term is a subquery rather than a relation."""

    class _Scan(Visitor):
        def __init__(self) -> None:
            super().__init__()
            self.found = False

        def visit_RangeSubselect(self, ancestors, node):  # noqa: ANN001, ANN201
            self.found = True
            return None

    scan = _Scan()
    for item in select.fromClause or ():
        scan(item)
    return scan.found


def _from_item_count(select: SelectStmt) -> int:
    """How many relations the recursive term draws rows from."""

    class _Count(Visitor):
        def __init__(self) -> None:
            super().__init__()
            self.total = 0

        def visit_RangeVar(self, ancestors, node: RangeVar):  # noqa: ANN001, ANN201
            self.total += 1
            return None

    counter = _Count()
    for item in select.fromClause or ():
        counter(item)
    return counter.total


def _self_reference_count(select: SelectStmt, *, name: str) -> int:
    """How many times the recursive term names its own CTE."""
    target = name.lower()

    class _Count(Visitor):
        def __init__(self) -> None:
            super().__init__()
            self.total = 0

        def visit_RangeVar(self, ancestors, node: RangeVar):  # noqa: ANN001, ANN201
            if node.schemaname is None and (node.relname or "").lower() == target:
                self.total += 1
            return None

    counter = _Count()
    counter(select)
    return counter.total


def _cte_is_self_referencing(cte: CommonTableExpr) -> bool:
    name = str(cte.ctename or "").lower()

    class _SelfRef(Visitor):
        def __init__(self) -> None:
            super().__init__()
            self.found = False

        def visit_RangeVar(self, ancestors, node: RangeVar):  # noqa: ANN001, ANN201
            if node.schemaname is None and (node.relname or "").lower() == name:
                self.found = True
            return None

    scan = _SelfRef()
    scan(cte.ctequery)
    return scan.found


def _anchor_initializes_depth_zero(anchor: SelectStmt) -> bool:
    from pglast.ast import ResTarget

    for target in anchor.targetList or ():
        if (
            isinstance(target, ResTarget)
            and str(target.name or "").lower() == "depth"
            and _is_integer_literal(target.val, 0)
        ):
            return True
    return False


def _recursive_arm_depth_bound(
    arm: SelectStmt, *, qualifier: str | None = None
) -> int | None:
    """The N of a top-level AND-conjunct `depth < N`; None when absent/OR-ed."""
    from pglast.ast import A_Const
    from pglast.ast import BoolExpr
    from pglast.ast import Integer
    from pglast.enums import BoolExprType

    def conjuncts(node: Node | None) -> list[Node]:
        if isinstance(node, BoolExpr) and node.boolop == BoolExprType.AND_EXPR:
            out: list[Node] = []
            for arg in node.args or ():
                out.extend(conjuncts(arg))
            return out
        return [node] if node is not None else []

    for conjunct in conjuncts(arm.whereClause):
        if isinstance(conjunct, A_Expr):
            op = "".join(_sval(part) for part in conjunct.name or ())
            if (
                op == "<"
                and _is_depth_column(conjunct.lexpr, qualifier=qualifier)
                and isinstance(conjunct.rexpr, A_Const)
                and isinstance(conjunct.rexpr.val, Integer)
            ):
                return _ival(conjunct.rexpr.val)
    return None


class _SrfPlacementVisitor(Visitor):
    """Confirms every SRF call sits in an accepted top-level FROM position.

    Accepted positions per §4.1: a FROM item of the top-level statement, or a
    FROM item of a top-level CTE body. Anything below a SubLink (IN/EXISTS/
    scalar subquery), inside a UNION arm, LATERAL, or a nested subquery FROM
    is rejected — the MATERIALIZED-CTE rewrite and the invocation caps are
    only real when every invocation is enumerable at the top level.
    """

    def __init__(self) -> None:
        super().__init__()
        self.accepted: list[FuncCall] = []

    def visit_RangeFunction(self, ancestors, node: RangeFunction):  # noqa: ANN001, ANN201
        public_calls = sum(
            1
            for entry in node.functions or ()
            for call in ((entry[0] if isinstance(entry, tuple) else entry),)
            if isinstance(call, FuncCall) and _func_name(call) in PUBLIC_SRF_NAMES
        )
        if public_calls > 1:
            # ROWS FROM packs several functions into ONE from-item, which the
            # rewrite would materialize as one CTE — the per-invocation caps
            # would then count one where the caller wrote several.
            raise _reject(
                QueryErrorCode.FUNCTION_PLACEMENT_NOT_ALLOWED,
                "each public function invocation needs its own FROM item",
            )
        top_level = _range_function_is_top_level(ancestors)
        if node.lateral and top_level:
            top_level = False
        for entry in node.functions or ():
            call = entry[0] if isinstance(entry, tuple) else entry
            if isinstance(call, FuncCall) and _func_name(call) in PUBLIC_SRF_NAMES:
                if not top_level:
                    raise _reject(
                        QueryErrorCode.FUNCTION_PLACEMENT_NOT_ALLOWED,
                        "public functions are callable only as top-level FROM"
                        " items or top-level CTE-body FROM items",
                    )
                _assert_srf_arguments_bindable(call)
                self.accepted.append(call)
        return Skip


def _range_function_is_top_level(ancestors) -> bool:  # noqa: ANN001
    """True when the ancestor chain is statement→[with→cte→]select→from only."""
    select_depth = 0
    through_cte = False
    for ancestor in _ancestor_nodes(ancestors):
        if isinstance(ancestor, SubLink):
            return False
        if isinstance(ancestor, RangeSubselect):
            return False
        if isinstance(ancestor, CommonTableExpr):
            through_cte = True
        if isinstance(ancestor, SelectStmt):
            select_depth += 1
            if ancestor.op != SetOperation.SETOP_NONE:
                return False
    return select_depth <= (2 if through_cte else 1)


def _ancestor_nodes(ancestors):  # noqa: ANN001, ANN201
    """The chain of AST nodes above the visited node, nearest last."""
    chain = []
    current = ancestors
    while current is not None:
        node = getattr(current, "node", None)
        if node is not None:
            chain.append(node)
        current = getattr(current, "parent", None)
    return chain


def _assert_srf_arguments_bindable(call: FuncCall) -> None:
    """Every argument must be a literal or a bound parameter, cast or not.

    A cast is transparent, so the operand is what matters: `lower($1)::text`
    and `($1 || 'x')::text` are computed expressions wearing a cast.
    """
    from pglast.ast import A_Const

    def bindable(node: object) -> bool:
        while isinstance(node, TypeCast):
            node = node.arg
        return isinstance(node, (A_Const, ParamRef))

    for arg in call.args or ():
        if not bindable(arg):
            raise _reject(
                QueryErrorCode.FUNCTION_PLACEMENT_NOT_ALLOWED,
                "public function arguments must be literals or bound parameters",
            )


class _ParamScan(Visitor):
    def __init__(self) -> None:
        super().__init__()
        self.max_index = 0
        self.indices: set[int] = set()

    def visit_ParamRef(self, ancestors, node: ParamRef):  # noqa: ANN001, ANN201
        number = node.number if isinstance(node.number, int) else 0
        if number > 0:
            self.indices.add(number)
        self.max_index = max(self.max_index, number)
        return None


def _to_named_placeholders(sql: str, *, count: int) -> str:
    """Rewrite PostgreSQL `$n` placeholders into psycopg named placeholders.

    psycopg binds `%(p1)s`-style names, so the deparsed statement is
    translated once, here, and the executor binds a matching mapping. Literal
    percent signs are escaped first so they survive that binding.
    """
    if count == 0:
        # With no mapping bound, psycopg performs no placeholder interpolation,
        # so a literal percent must survive untouched — escaping it here turned
        # `5 % 2` into a syntax error and `'%a%'` into `'%%a%%'`.
        return sql
    translated = sql.replace("%", "%%")
    # Longest index first, so $10 is not rewritten as $1 followed by "0".
    for index in range(count, 0, -1):
        translated = translated.replace(f"${index}", f"%(p{index})s")
    return translated


def _rewrite_srf_invocations(
    statement: SelectStmt, srf_calls: list[FuncCall]
) -> SelectStmt:
    """The §4.1 normative rewrite: each accepted SRF invocation becomes its own
    `MATERIALIZED` CTE, and its original FROM position becomes a reference.

    Materialization makes the §4.3 invocation caps real: the planner can never
    duplicate an invocation through inlining, and each invocation runs exactly
    once. Names are `__srf_<n>` — double-underscored so they can never shadow
    a `memory_v1` relation (all lowercase ASCII identifiers).
    """
    from pglast.ast import WithClause

    counter = 0
    new_ctes: list[CommonTableExpr] = []
    bindings: list[tuple[str, str, tuple[object, ...]]] = []

    class _Rewriter(Visitor):
        def visit_RangeFunction(self, ancestors, node: RangeFunction):  # noqa: ANN001, ANN201
            nonlocal counter
            for entry in node.functions or ():
                call = entry[0] if isinstance(entry, tuple) else entry
                if isinstance(call, FuncCall) and _func_name(call) in PUBLIC_SRF_NAMES:
                    name = f"__srf_{counter}"
                    counter += 1
                    bindings.append((name, _func_name(call), _literal_arguments(call)))
                    body = SelectStmt(
                        targetList=(_star_target(),),
                        fromClause=(node,),
                        op=SetOperation.SETOP_NONE,
                    )
                    new_ctes.append(
                        CommonTableExpr(
                            ctename=name,
                            ctequery=body,
                            ctematerialized=CTEMaterialize.CTEMaterializeAlways,
                        )
                    )
                    alias = node.alias
                    return RangeVar(
                        relname=name, inh=True, relpersistence="p", alias=alias
                    )
            return None

    rewritten = _Rewriter()(statement)
    assert isinstance(rewritten, SelectStmt)
    _rewrite_srf_invocations.bindings = tuple(bindings)  # type: ignore[attr-defined]
    if new_ctes:
        existing = list(rewritten.withClause.ctes) if rewritten.withClause else []
        recursive = bool(rewritten.withClause and rewritten.withClause.recursive)
        rewritten.withClause = WithClause(
            ctes=tuple(new_ctes + existing), recursive=recursive
        )
    return rewritten


def _literal_arguments(call: FuncCall) -> tuple[object, ...]:
    """The argument list as literals and parameter positions.

    A `$n` argument becomes `("$", n)` so the executor can substitute the
    bound value; a literal becomes its Python value. Nothing else can appear
    here — the placement rule already required literals or parameters.
    """
    from pglast.ast import A_Const
    from pglast.ast import Float
    from pglast.ast import Integer
    from pglast.ast import String as PgString

    arguments: list[object] = []
    for argument in call.args or ():
        node = argument
        while isinstance(node, TypeCast):
            node = node.arg
        if isinstance(node, ParamRef):
            arguments.append(("$", int(node.number or 0)))
        elif isinstance(node, A_Const):
            value = node.val
            if isinstance(value, Integer):
                arguments.append(_ival(value))
            elif isinstance(value, PgString):
                arguments.append(_sval(value))
            elif isinstance(value, Float):
                arguments.append(float(str(getattr(value, "fval", "0"))))
            else:
                arguments.append(None)
        else:  # pragma: no cover - the placement rule forbids reaching here
            arguments.append(None)
    return tuple(arguments)


def _star_target():  # noqa: ANN202
    from pglast.ast import A_Star
    from pglast.ast import ResTarget

    return ResTarget(val=ColumnRef(fields=(A_Star(),)))


def validate_sql(
    sql: str, *, expected_schema_hash: str | None = None
) -> ValidatedQuery:
    """Run the complete §4.1 gate over one SQL text; raise SandboxRejection.

    `expected_schema_hash` is threaded by the executor after comparing the
    runtime manifest — a mismatch is rejected before parsing anything.
    """
    statement = _assert_single_readonly_statement(sql)
    cte_names, is_recursive = _collect_ctes(statement)
    # Every recursive WITH is validated, wherever it sits: a recursive CTE
    # nested inside another CTE body or a subquery is exactly as capable of
    # running forever as a top-level one. The §4.3 cap is one per statement,
    # so the clauses are collected once and counted.
    _reject_self_reference_without_recursive(statement)
    recursive_clauses = _recursive_with_clauses(statement)
    if len(recursive_clauses) > 1:
        raise _reject(
            QueryErrorCode.UNBOUNDED_RECURSION,
            "one statement may contain at most one recursive CTE",
        )
    for clause, owner in recursive_clauses:
        _validate_recursive_template(clause, owner=owner)
    is_recursive = bool(recursive_clauses)

    gate = _AllowlistVisitor(cte_names=cte_names)
    gate(statement)

    placement = _SrfPlacementVisitor()
    placement(statement)
    accepted = {id(call) for call in placement.accepted}
    for call in gate.srf_calls:
        if id(call) not in accepted:
            raise _reject(
                QueryErrorCode.FUNCTION_PLACEMENT_NOT_ALLOWED,
                "public functions are callable only as top-level FROM items",
            )
    if len(gate.srf_calls) > SRF_INVOCATIONS_MAX:
        raise _reject(
            QueryErrorCode.QUOTA_EXCEEDED,
            f"at most {SRF_INVOCATIONS_MAX} public function invocations per statement",
        )

    params = _ParamScan()
    params(statement)

    ordered = bool(statement.sortClause) and all(
        isinstance(entry, SortBy) for entry in statement.sortClause
    )

    rewritten = False
    srf_bindings: tuple[tuple[str, str, tuple[object, ...]], ...] = ()
    if gate.srf_calls:
        statement = _rewrite_srf_invocations(statement, gate.srf_calls)
        srf_bindings = getattr(_rewrite_srf_invocations, "bindings", ())
        rewritten = True

    deparsed = RawStream()(statement)
    # Parameter indices must be contiguous from $1: a statement referencing
    # $1 and $3 would silently bind the wrong values.
    if params.indices and params.indices != set(range(1, params.max_index + 1)):
        raise _reject(
            QueryErrorCode.INVALID_PARAMETER,
            "parameter placeholders must be contiguous starting at $1",
        )
    # The deparser emits PostgreSQL's `$n`; psycopg binds client-side named
    # placeholders. Translating here (rather than in the executor) keeps one
    # definition of the executable text — the same text that is hashed.
    normalized = _to_named_placeholders(deparsed, count=params.max_index)
    digest = hashlib.sha256(
        f"{normalized}|params={params.max_index}".encode()
    ).hexdigest()

    return ValidatedQuery(
        sql=normalized,
        query_hash=digest,
        referenced_views=tuple(sorted(gate.views)),
        referenced_functions=tuple(sorted(gate.functions)),
        srf_invocations=len(gate.srf_calls),
        srf_bindings=srf_bindings,
        ordered_result=ordered,
        parameter_count=params.max_index,
        is_recursive=is_recursive,
        rewritten=rewritten,
    )
