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

_FUNCTION_ALLOWLIST: Final = frozenset(
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

_OPERATOR_ALLOWLIST: Final = frozenset(
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

_CAST_TYPE_ALLOWLIST: Final = frozenset(
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

_RECURSION_DEPTH_MAX: Final = 6


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
        if name not in _FUNCTION_ALLOWLIST:
            raise _reject(
                QueryErrorCode.FUNCTION_NOT_ALLOWED, f"function {name} is not allowed"
            )
        self.functions.add(name)
        return None

    def visit_A_Expr(self, ancestors, node: A_Expr):  # noqa: ANN001, ANN201
        for piece in node.name or ():
            if isinstance(piece, String) and _sval(piece) not in _OPERATOR_ALLOWLIST:
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
        if bare not in _CAST_TYPE_ALLOWLIST:
            raise _reject(
                QueryErrorCode.OPERATOR_NOT_ALLOWED, f"cast to {bare} is not allowed"
            )
        return None

    def visit_RangeTableSample(self, ancestors, node):  # noqa: ANN001, ANN201
        raise _reject(
            QueryErrorCode.STATEMENT_NOT_ALLOWED, "TABLESAMPLE is not allowed"
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


def _collect_ctes(statement: SelectStmt) -> tuple[frozenset[str], bool]:
    """CTE names plus whether the statement is WITH RECURSIVE."""
    clause = statement.withClause
    if clause is None:
        return frozenset(), False
    names: set[str] = set()
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


def _is_depth_column(node: Node | None) -> bool:
    return (
        isinstance(node, ColumnRef)
        and bool(node.fields)
        and isinstance(node.fields[-1], String)
        and _sval(node.fields[-1]).lower() == "depth"
    )


def _is_integer_literal(node: Node | None, value: int) -> bool:
    from pglast.ast import A_Const
    from pglast.ast import Integer

    return (
        isinstance(node, A_Const)
        and isinstance(node.val, Integer)
        and node.val.ival == value
    )


def _validate_recursive_template(statement: SelectStmt) -> None:
    """Enforces the single §4.1 WITH RECURSIVE template, mechanically."""
    clause = statement.withClause
    assert clause is not None
    recursive = [
        cte
        for cte in clause.ctes or ()
        if isinstance(cte, CommonTableExpr) and _cte_is_self_referencing(cte)
    ]
    if len(clause.ctes or ()) != 1 or len(recursive) != 1:
        raise _reject(
            QueryErrorCode.UNBOUNDED_RECURSION,
            "WITH RECURSIVE allows exactly one recursive CTE",
        )
    cte = recursive[0]
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
    if not _anchor_initializes_depth_zero(anchor):
        raise _reject(
            QueryErrorCode.UNBOUNDED_RECURSION,
            "the anchor term must initialize an integer column depth to 0",
        )
    bound = _recursive_arm_depth_bound(recursive_arm)
    if bound is None or bound > _RECURSION_DEPTH_MAX:
        raise _reject(
            QueryErrorCode.UNBOUNDED_RECURSION,
            "the recursive term must be bounded by the literal predicate"
            f" depth < N with N <= {_RECURSION_DEPTH_MAX}, with no OR around it",
        )
    scan = _DepthAssignmentScan()
    scan(recursive_arm)
    if scan.increments != 1:
        raise _reject(
            QueryErrorCode.UNBOUNDED_RECURSION,
            "the recursive term must increment depth by exactly 1, once",
        )


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


def _recursive_arm_depth_bound(arm: SelectStmt) -> int | None:
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
                and _is_depth_column(conjunct.lexpr)
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
    from pglast.ast import A_Const

    for arg in call.args or ():
        if not isinstance(arg, (A_Const, ParamRef, TypeCast)):
            raise _reject(
                QueryErrorCode.FUNCTION_PLACEMENT_NOT_ALLOWED,
                "public function arguments must be literals or bound parameters",
            )


class _ParamScan(Visitor):
    def __init__(self) -> None:
        super().__init__()
        self.max_index = 0

    def visit_ParamRef(self, ancestors, node: ParamRef):  # noqa: ANN001, ANN201
        number = node.number if isinstance(node.number, int) else 0
        self.max_index = max(self.max_index, number)
        return None


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

    class _Rewriter(Visitor):
        def visit_RangeFunction(self, ancestors, node: RangeFunction):  # noqa: ANN001, ANN201
            nonlocal counter
            for entry in node.functions or ():
                call = entry[0] if isinstance(entry, tuple) else entry
                if isinstance(call, FuncCall) and _func_name(call) in PUBLIC_SRF_NAMES:
                    name = f"__srf_{counter}"
                    counter += 1
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
    if new_ctes:
        existing = list(rewritten.withClause.ctes) if rewritten.withClause else []
        recursive = bool(rewritten.withClause and rewritten.withClause.recursive)
        rewritten.withClause = WithClause(
            ctes=tuple(new_ctes + existing), recursive=recursive
        )
    return rewritten


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
    if is_recursive:
        _validate_recursive_template(statement)

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
    if gate.srf_calls:
        statement = _rewrite_srf_invocations(statement, gate.srf_calls)
        rewritten = True

    normalized = RawStream()(statement)
    digest = hashlib.sha256(
        f"{normalized}|params={params.max_index}".encode()
    ).hexdigest()

    return ValidatedQuery(
        sql=normalized,
        query_hash=digest,
        referenced_views=tuple(sorted(gate.views)),
        referenced_functions=tuple(sorted(gate.functions)),
        srf_invocations=len(gate.srf_calls),
        ordered_result=ordered,
        parameter_count=params.max_index,
        is_recursive=is_recursive,
        rewritten=rewritten,
    )
