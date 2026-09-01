"""D104 holds only while E0 is the single door into a document version.

The routability gate lives in `UploadIngestor._guard_ingest` precisely because
every ingress writes through that object. Three things could quietly undo that,
and none would fail any behavioural test:

- a new caller reaching `DocumentCatalog.record_upload` directly, creating a
  version without passing the gate;
- a new module writing `document_versions` itself, going around the catalog;
- `routable_mimes` regaining a default, letting a composition opt out by
  omission.

These are structural audits, in the spirit of the repo's other inventory
proofs. They fail loudly when the shape changes, which is the point — a
behavioural test cannot notice a door that did not exist when it was written.
"""

import ast
from collections import Counter
import inspect
from pathlib import Path
import re

from rememberstack.workers.e0 import UploadIngestor

_PACKAGE = Path(inspect.getfile(UploadIngestor)).parents[1]
_CATALOG = _PACKAGE / "spine" / "document_catalog.py"
_GATED_METHODS = {"ingest", "ingest_observed"}


def _enclosing_scope(tree: ast.Module, target: ast.AST) -> tuple[str, ...]:
    """Return the nested def/class names containing `target`, outermost first."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for child in ast.walk(node):
            if child is target:
                inner = _enclosing_scope(
                    ast.Module(body=list(node.body), type_ignores=[]), target
                )
                return (node.name, *inner)
    return ()


def _record_upload_callers() -> Counter[tuple[str, tuple[str, ...]]]:
    """Count every `.record_upload(` call by (module stem, enclosing scope).

    A Counter, not a set: collapsing duplicates would hide a *second* call
    added inside an already-allowed method — including one placed before the
    guard runs, which is exactly the regression this audit exists to catch.
    """
    callers: Counter[tuple[str, tuple[str, ...]]] = Counter()
    for path in _PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "record_upload"
            ):
                callers[(path.stem, _enclosing_scope(tree, node))] += 1
    return callers


def test_only_the_gated_e0_methods_create_document_versions() -> None:
    """`record_upload` is called only from the two guarded `UploadIngestor` methods.

    Checking the enclosing scope, not just the file, is the point: a second
    call inside `e0.py` but outside `ingest`/`ingest_observed` would skip
    `_guard_ingest` and restore the accepted-then-dead-letter path D104
    removed. If this fails, route the new caller through the guard — do not
    widen this assertion.
    """
    assert _record_upload_callers() == Counter(
        {("e0", ("UploadIngestor", method)): 1 for method in _GATED_METHODS}
    )


def test_only_the_catalog_writes_document_versions() -> None:
    """No runtime module goes around `DocumentCatalog` to insert a version row.

    `record_upload` being the single gated entry point means nothing if another
    module can write `document_versions` itself. Migrations are excluded: they
    define the table rather than ingest through it, and they run under an
    operator, not a caller.

    This is a text scan, normalised for case and whitespace, so it cannot see
    SQL assembled at runtime. It is deliberately biased toward false positives
    — a comment mentioning the statement fails this test, and someone looks —
    because the failure mode worth avoiding is the silent one.
    """
    pattern = re.compile(r"insert\s+into\s+document_versions", re.IGNORECASE)
    writers = {
        path.relative_to(_PACKAGE)
        for path in _PACKAGE.rglob("*.py")
        if "migrations" not in path.parts and pattern.search(path.read_text("utf-8"))
    }
    assert writers == {_CATALOG.relative_to(_PACKAGE)}


def test_the_route_table_cannot_be_omitted_by_a_composition() -> None:
    """`routable_mimes` has no default, so no composition can skip the gate.

    An earlier draft defaulted it to None. That made D104 as strong as every
    composer remembering to pass it, which is not what an invariant means.
    """
    parameter = inspect.signature(UploadIngestor.__init__).parameters["routable_mimes"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
