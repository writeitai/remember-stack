"""D158: an unexpected failure is raised, or logged at ERROR with a stable code.

One AST rule over ``src/rememberstack`` (D158 design §4):

* every ``except`` that catches a failure class must re-raise (not
  ``raise HTTPException``), log at ERROR (``.error`` or ``.exception`` on a
  ``rememberstack`` module logger) with a literal or ``<name>.code``
  ``error_code``, call ``_internal_error``, or carry
  ``# error-contract: <reason>`` on its ``except`` line;
* every ERROR-or-above logging call (``.error``, ``.exception``,
  ``.critical`` on a logger, or the ``logging`` module functions) passes
  such an ``error_code``.

Code inside a nested function, lambda or class does not run when the handler
does, so it never satisfies the enclosing handler.

The conversion backlog is complete. The test requires zero violating handlers.
"""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterator
import io
from pathlib import Path
import re
import tokenize

ROOT = Path(__file__).resolve().parents[1] / "rememberstack"
CODE = re.compile(r"[a-z][a-z0-9_]{2,63}")
FAILURE_CLASSES = {
    "Exception",
    "BaseException",
    "ProviderCallError",
    "ProviderAccountingError",
    "MistralOcrProviderError",
    "DBAPIError",
    "SQLAlchemyError",
    "SQLAlchemyTimeoutError",
    "OSError",
    "TimeoutError",
    "httpx.HTTPError",
    "subprocess.TimeoutExpired",
    "RuntimeError",
}
REPORTERS = {
    "_internal_error",
    "_report_capture_scope_failure",
    "_report_query_read_failure",
}
HANDLER_LEVELS = {"error", "exception"}
ERROR_LEVELS = {"error", "exception", "critical"}
SUPPRESSION = re.compile(
    r"#\s*error-contract:\s*(raised later|expected|exempt|mixed)\b"
)
DEFERRED = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ClassDef,
    ast.GeneratorExp,
)

BASELINE: dict[str, int] = {}


def _package() -> list[tuple[str, str, ast.Module]]:
    """(relative path, source, tree) for every engine module."""
    modules = []
    for path in sorted(ROOT.rglob("*.py")):
        source = path.read_text()
        modules.append((path.relative_to(ROOT).as_posix(), source, ast.parse(source)))
    return modules


def _failure_classes(modules: list[tuple[str, str, ast.Module]]) -> set[str]:
    """The declared failure classes plus every engine subclass of one."""
    bases: dict[str, set[str]] = {}
    for _, _, tree in modules:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases.setdefault(node.name, set()).update(
                    ast.unparse(base).rsplit(".", 1)[-1] for base in node.bases
                )
    # Engine subclasses of these broad bases are mostly domain outcomes (a
    # conflict, a contended refresh, a too-broad scope), not failures; the
    # failure-to-response boundaries among them are held by the §2.1
    # boundary tests instead.
    failures = {name.rsplit(".", 1)[-1] for name in FAILURE_CLASSES} - {
        "Exception",
        "BaseException",
        "RuntimeError",
    }
    grown = True
    while grown:
        grown = False
        for name, parents in bases.items():
            if name not in failures and parents & failures:
                failures.add(name)
                grown = True
    return FAILURE_CLASSES | failures


def _aliases(tree: ast.Module) -> dict[str, str]:
    """Local name to its full dotted origin, for every import in the module."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    names[alias.asname] = alias.name
    return names


def _resolve(local: str, aliases: dict[str, str]) -> str:
    """``errors.LockNotAvailable`` → ``psycopg.errors.LockNotAvailable``."""
    head, dot, rest = local.partition(".")
    return aliases[head] + dot + rest if head in aliases else local


def _catches_failure(
    handler: ast.ExceptHandler, failures: set[str], aliases: dict[str, str]
) -> bool:
    """Whether the handler catches a failure class (analysis §5.1)."""
    if handler.type is None:
        return True
    nodes = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    for node in nodes:
        local = ast.unparse(node)
        for name in (local, _resolve(local, aliases)):
            if (
                name in failures
                or name.rsplit(".", 1)[-1] in failures
                or name.startswith("psycopg.")
                or name.endswith(("Unavailable", "UnavailableError"))
            ):
                return True
    return False


def _engine_loggers(tree: ast.Module) -> set[str]:
    """Module names bound to ``logging.getLogger(__name__ | "rememberstack...")``."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not (
            isinstance(value, ast.Call)
            and ast.unparse(value.func) in {"logging.getLogger", "getLogger"}
            and len(value.args) == 1
        ):
            continue
        argument = value.args[0]
        if (isinstance(argument, ast.Name) and argument.id == "__name__") or (
            isinstance(argument, ast.Constant)
            and isinstance(argument.value, str)
            and argument.value.split(".")[0] == "rememberstack"
        ):
            names.update(t.id for t in targets if isinstance(t, ast.Name))
    return names


def _reporters(tree: ast.Module, loggers: set[str]) -> set[str]:
    """``REPORTERS`` defined in this module that themselves log a coded ERROR."""
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in REPORTERS
        and any(
            _log_level(call, loggers) in HANDLER_LEVELS and _coded(call)  # type: ignore[arg-type]
            for call in _executed(node.body)
        )
    }


def _http_exceptions(tree: ast.Module) -> set[str]:
    """Local names of ``HTTPException`` (FastAPI or Starlette), aliases included."""
    names = {"HTTPException"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {
            "fastapi",
            "fastapi.exceptions",
            "starlette.exceptions",
        }:
            names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "HTTPException"
            )
    return names


def _coded(call: ast.Call) -> bool:
    """``extra={"error_code": <literal matching the pattern> | <name>.code}``."""
    for keyword in call.keywords:
        if keyword.arg != "extra" or not isinstance(keyword.value, ast.Dict):
            continue
        if any(key is None for key in keyword.value.keys):
            return False  # a ``**mapping`` could overwrite the code
        for key, value in zip(keyword.value.keys, keyword.value.values, strict=True):
            if isinstance(key, ast.Constant) and key.value == "error_code":
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    return bool(CODE.fullmatch(value.value))
                return (
                    isinstance(value, ast.Attribute)
                    and value.attr == "code"
                    and isinstance(value.value, ast.Name)
                )
    return False


def _log_level(node: ast.AST, loggers: set[str]) -> str | None:
    """The method name, if the node calls an engine module logger."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in loggers
    ):
        return node.func.attr
    return None


def _error_call(node: ast.AST, loggers: set[str]) -> bool:
    """An ERROR-or-above call on an engine logger, the ``logging`` module, or an
    inline ``logging.getLogger(...)``."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    if node.func.attr not in ERROR_LEVELS:
        return False
    receiver = node.func.value
    if isinstance(receiver, ast.Call):
        return ast.unparse(receiver.func) in {"logging.getLogger", "getLogger"}
    if not isinstance(receiver, ast.Name):
        return False
    return receiver.id in loggers or receiver.id == "logging"


def _executed(nodes: list[ast.stmt]) -> Iterator[ast.AST]:
    """Every node that runs with these statements; deferred bodies excluded."""
    stack: list[ast.AST] = [node for node in nodes if not isinstance(node, DEFERRED)]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(
            child
            for child in ast.iter_child_nodes(node)
            if not isinstance(child, DEFERRED)
        )


def _suppressed(handler: ast.ExceptHandler, comments: dict[int, str]) -> bool:
    """A ``# error-contract: <reason>`` comment on the ``except`` header lines."""
    last = handler.type.end_lineno or handler.lineno if handler.type else handler.lineno
    return any(
        SUPPRESSION.match(comments.get(line, ""))
        for line in range(handler.lineno, last + 1)
    )


def _satisfied(
    handler: ast.ExceptHandler,
    *,
    loggers: set[str],
    http_exceptions: set[str],
    reporters: set[str],
    comments: dict[int, str],
) -> bool:
    """Raise, coded ERROR log, ``_internal_error``, or an explicit reason."""
    if _suppressed(handler, comments):
        return True
    for node in _executed(handler.body):
        if isinstance(node, ast.Raise):
            raised = node.exc
            if raised is None:
                return True
            target = raised.func if isinstance(raised, ast.Call) else raised
            converted = ast.unparse(target) in http_exceptions or (
                isinstance(target, ast.Attribute) and target.attr == "HTTPException"
            )
            if not converted:
                return True
        if _log_level(node, loggers) in HANDLER_LEVELS and _coded(node):  # type: ignore[arg-type]
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in reporters
        ):
            return True
    return False


def _comments(source: str) -> dict[int, str]:
    """Line number to comment text, from the tokenizer (never string contents)."""
    return {
        token.start[0]: token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    }


def _scan() -> tuple[Counter[str], list[str]]:
    """(violating handlers per function, uncoded ERROR calls as path:line)."""
    modules = _package()
    failures = _failure_classes(modules)
    handlers: Counter[str] = Counter()
    calls: list[str] = []
    for relative, source, tree in modules:
        loggers = _engine_loggers(tree)
        http_exceptions = _http_exceptions(tree)
        reporters = _reporters(tree, loggers)
        aliases = _aliases(tree)
        comments = _comments(source)
        scopes: list[tuple[ast.AST, str]] = [(tree, "")]
        while scopes:
            scope, prefix = scopes.pop()
            for node in ast.iter_child_nodes(scope):
                name = prefix
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    name = f"{prefix}.{node.name}" if prefix else node.name
                if (
                    isinstance(node, ast.ExceptHandler)
                    and _catches_failure(node, failures, aliases)
                    and not _satisfied(
                        node,
                        loggers=loggers,
                        http_exceptions=http_exceptions,
                        reporters=reporters,
                        comments=comments,
                    )
                ):
                    handlers[f"{relative}::{name or '<module>'}"] += 1
                if _error_call(node, loggers) and not _coded(node):  # type: ignore[arg-type]
                    calls.append(f"{relative}:{node.lineno}")  # type: ignore[attr-defined]
                scopes.append((node, name))
    return handlers, calls


def test_failure_handlers_raise_or_report_with_a_code() -> None:
    """Require every covered handler to raise, report or explain its outcome."""
    handlers, _ = _scan()
    assert dict(handlers) == BASELINE, (
        f"handlers that neither raise nor log a coded ERROR: {dict(sorted(handlers.items()))}"
    )


def test_every_error_log_has_a_stable_code() -> None:
    """ERROR-level logging always carries a literal or declared ``error_code``."""
    _, calls = _scan()
    assert calls == [], f"ERROR logs without a stable error_code: {calls}"


def _violations(source: str) -> tuple[Counter[str], list[str]]:
    """Scan one synthetic module, for the rule's own tests."""
    tree = ast.parse(source)
    failures = _failure_classes([("m.py", source, tree)])
    loggers = _engine_loggers(tree)
    handlers: Counter[str] = Counter()
    calls = [
        str(node.lineno)  # type: ignore[attr-defined]
        for node in ast.walk(tree)
        if _error_call(node, loggers) and not _coded(node)  # type: ignore[arg-type]
    ]
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ExceptHandler)
            and _catches_failure(node, failures, _aliases(tree))
            and not _satisfied(
                node,
                loggers=loggers,
                http_exceptions=_http_exceptions(tree),
                reporters=_reporters(tree, loggers),
                comments=_comments(source),
            )
        ):
            handlers["m.py"] += 1
    return handlers, calls


_PRELUDE = "import logging\n_logger = logging.getLogger(__name__)\n"


def test_rule_rejects_the_known_bypasses() -> None:
    """Deferred code, wrong loggers or levels, aliases and fake comments fail."""
    cases = {
        "deferred raise": "try:\n    f()\nexcept Exception:\n    def g():\n        raise\n",
        "deferred log": (
            "try:\n    f()\nexcept Exception:\n    h = lambda: _logger.error("
            '"x", extra={"error_code": "some_code"})\n'
        ),
        "critical": (
            'try:\n    f()\nexcept Exception:\n    _logger.critical("x", '
            'extra={"error_code": "some_code"})\n'
        ),
        "vendor logger": (
            'other = logging.getLogger("vendor")\ntry:\n    f()\n'
            'except Exception:\n    other.error("x", extra={"error_code": "some_code"})\n'
        ),
        "aliased HTTPException": (
            "from fastapi import HTTPException as HTTPError\ntry:\n    f()\n"
            "except Exception:\n    raise HTTPError(503)\n"
        ),
        "provider subclass": (
            "class ProviderCallError(Exception): ...\n"
            "class VendorError(ProviderCallError): ...\n"
            "try:\n    f()\nexcept VendorError:\n    pass\n"
        ),
        "marker in a string": (
            'try:\n    f()\nexcept Exception:  # noqa\n    s = "# error-contract: expected"\n'
        ),
        "lookalike reason": (
            "try:\n    f()\nexcept Exception:  # error-contract: expectedly fine\n    pass\n"
        ),
        "overwritable code": (
            'try:\n    f()\nexcept Exception:\n    _logger.error("x", '
            'extra={"error_code": "some_code", **details})\n'
        ),
        "aliased failure class": (
            "from sqlalchemy.exc import DBAPIError as DbError\n"
            "try:\n    f()\nexcept DbError:\n    pass\n"
        ),
        "aliased psycopg class": (
            "from psycopg import OperationalError as PgError\n"
            "try:\n    f()\nexcept PgError:\n    pass\n"
        ),
        "aliased Unavailable class": (
            "from x import SpendLeaseUnavailable as LeaseError\n"
            "try:\n    f()\nexcept LeaseError:\n    pass\n"
        ),
        "unaliased psycopg class": (
            "from psycopg import OperationalError\n"
            "try:\n    f()\nexcept OperationalError:\n    pass\n"
        ),
        "psycopg namespace import": (
            "from psycopg import errors\n"
            "try:\n    f()\nexcept errors.LockNotAvailable:\n    pass\n"
        ),
        "qualified HTTPException": (
            "import fastapi as fa\ntry:\n    f()\n"
            "except Exception:\n    raise fa.HTTPException(503)\n"
        ),
        "unverified reporter": (
            "def _internal_error():\n    logging.exception('x')\n"
            "try:\n    f()\nexcept Exception:\n    _internal_error()\n"
        ),
        "comment in the body": (
            "try:\n    f()\nexcept Exception:\n"
            "    # error-contract: expected\n    pass\n"
        ),
        "discarded generator": (
            "try:\n    f()\nexcept Exception:\n    (_logger.error("
            '"x", extra={"error_code": "some_code"}) for _ in ())\n'
        ),
        "call result code": (
            'try:\n    f()\nexcept Exception:\n    _logger.error("x", '
            'extra={"error_code": make().code})\n'
        ),
    }
    passing = [
        name for name, body in cases.items() if not _violations(_PRELUDE + body)[0]
    ]
    assert passing == []
    assert _violations(_PRELUDE + 'logging.error("x")\n')[1] == ["3"]
    annotated = (
        "import logging\n_logger: logging.Logger = logging.getLogger(__name__)\n"
        '_logger.error("x")\n'
    )
    assert _violations(annotated)[1] == ["3"]
    assert _violations(_PRELUDE + 'logging.getLogger(__name__).error("x")\n')[1] == [
        "3"
    ]


def test_rule_accepts_the_contract_forms() -> None:
    """A re-raise, a coded ERROR, ``.code``, and a multiline suppression pass."""
    cases = [
        "try:\n    f()\nexcept Exception:\n    raise\n",
        "def _internal_error():\n    _logger.exception("
        '"x", extra={"error_code": "some_code"})\n'
        "try:\n    f()\nexcept Exception:\n    _internal_error()\n",
        'try:\n    f()\nexcept Exception:\n    _logger.exception("x", '
        'extra={"error_code": "some_code"})\n',
        'try:\n    f()\nexcept Exception as error:\n    _logger.error("x", '
        'extra={"error_code": error.code})\n',
        "try:\n    f()\nexcept (\n    OSError,\n    TimeoutError,\n"
        "):  # error-contract: expected retry\n    pass\n",
    ]
    assert [body for body in cases if _violations(_PRELUDE + body)[0]] == []
