"""Pure authored-frontmatter parsing and declaration-lint helpers."""

import json
from pathlib import PurePosixPath
from typing import TypeAlias
from uuid import UUID

from pydantic import TypeAdapter
from pydantic import ValidationError

from rememberstack.model import DocSetRuleParams
from rememberstack.model import EntityRuleParams
from rememberstack.model import KnowledgeAuthoredDeclaration
from rememberstack.model import KnowledgeCitation
from rememberstack.model import KnowledgeEvidenceRole
from rememberstack.model import KnowledgeRuleParams
from rememberstack.model import PredicateBeatRuleParams

_RULE_ADAPTER = TypeAdapter(KnowledgeRuleParams)
_FrontmatterItem: TypeAlias = str | dict[str, object]


class KnowledgeAuthoredDeclarationError(ValueError):
    """An authored page carries malformed ``cites`` or ``watch`` declarations."""


def parse_knowledge_authored_frontmatter(
    *, markdown: str
) -> KnowledgeAuthoredDeclaration:
    """Parse the strict JSON-compatible YAML subset used by authored pages.

    ``cites`` and ``watch`` accept an inline JSON list or an indented list whose
    items are shorthands/JSON objects. Other frontmatter keys are left to their
    owning surface and ignored here.
    """
    lines = _frontmatter_lines(markdown=markdown)
    if lines is None:
        return KnowledgeAuthoredDeclaration()
    declared = _declared_lists(lines=lines)
    citations = (
        None
        if declared["cites"] is None
        else tuple(_citation(item=item) for item in declared["cites"] or ())
    )
    watch_rules: tuple[KnowledgeRuleParams, ...] | None = None
    page_paths: tuple[str, ...] | None = None
    if declared["watch"] is not None:
        rules: list[KnowledgeRuleParams] = []
        paths: list[str] = []
        for item in declared["watch"] or ():
            rule, page_path = _watch(item=item)
            if rule is not None:
                rules.append(rule)
            if page_path is not None:
                paths.append(page_path)
        watch_rules = tuple(rules)
        page_paths = tuple(paths)
    return KnowledgeAuthoredDeclaration(
        citations=citations, watch_rules=watch_rules, watched_page_paths=page_paths
    )


def authored_declaration_is_empty(
    *, citation_count: int, watch_rule_count: int, page_watch_count: int
) -> bool:
    """Return whether an authored page has no ground and can never be alerted."""
    if min(citation_count, watch_rule_count, page_watch_count) < 0:
        raise ValueError("declaration counts must be non-negative")
    return citation_count + watch_rule_count + page_watch_count == 0


def knowledge_citation_reference(*, citation: KnowledgeCitation) -> str:
    """Render one citation as a stable delta identifier."""
    if citation.claim_lineage_id is not None:
        target = (
            f"claim:{citation.claim_lineage_id}:{citation.claim_chunk_content_hash}"
        )
    elif citation.relation_id is not None:
        target = f"relation:{citation.relation_id}"
    else:
        target = f"doc:{citation.doc_id}"
    return f"{citation.role.value}:{target}"


def _frontmatter_lines(*, markdown: str) -> tuple[str, ...] | None:
    """Return frontmatter lines, distinguishing no header from an unclosed one."""
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration as error:
        raise KnowledgeAuthoredDeclarationError(
            "authored frontmatter is missing its closing delimiter"
        ) from error
    return tuple(lines[1:end])


def _declared_lists(
    *, lines: tuple[str, ...]
) -> dict[str, tuple[_FrontmatterItem, ...] | None]:
    """Extract the two owned list keys without parsing unrelated frontmatter."""
    result: dict[str, tuple[_FrontmatterItem, ...] | None] = {
        "cites": None,
        "watch": None,
    }
    seen: set[str] = set()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line or line[0].isspace() or ":" not in line:
            index += 1
            continue
        key, inline = line.split(":", maxsplit=1)
        if key not in result:
            index += 1
            continue
        if key in seen:
            raise KnowledgeAuthoredDeclarationError(
                f"authored frontmatter repeats {key!r}"
            )
        seen.add(key)
        inline = inline.strip()
        if inline:
            result[key] = _inline_list(key=key, value=inline)
            index += 1
            continue
        values: list[_FrontmatterItem] = []
        index += 1
        while index < len(lines):
            nested = lines[index]
            if nested and not nested[0].isspace():
                break
            stripped = nested.strip()
            index += 1
            if not stripped or stripped.startswith("#"):
                continue
            if not stripped.startswith("- "):
                raise KnowledgeAuthoredDeclarationError(
                    f"{key!r} must be a flat list of shorthands or JSON objects"
                )
            values.append(_list_item(key=key, value=stripped[2:].strip()))
        result[key] = tuple(values)
    return result


def _inline_list(*, key: str, value: str) -> tuple[_FrontmatterItem, ...]:
    """Parse one inline JSON list for a declaration key."""
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise KnowledgeAuthoredDeclarationError(
            f"inline {key!r} must be valid JSON"
        ) from error
    if not isinstance(parsed, list):
        raise KnowledgeAuthoredDeclarationError(f"{key!r} must be a list")
    return tuple(_require_item(key=key, value=item) for item in parsed)


def _list_item(*, key: str, value: str) -> _FrontmatterItem:
    """Parse JSON-looking list items and retain plain shorthands verbatim."""
    if not value:
        raise KnowledgeAuthoredDeclarationError(f"{key!r} contains an empty item")
    if value[0] not in ('"', "{"):
        return value
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise KnowledgeAuthoredDeclarationError(
            f"{key!r} contains invalid JSON"
        ) from error
    return _require_item(key=key, value=parsed)


def _require_item(*, key: str, value: object) -> _FrontmatterItem:
    """Reject declaration values outside the supported scalar/object shapes."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and all(isinstance(name, str) for name in value):
        return value
    raise KnowledgeAuthoredDeclarationError(
        f"{key!r} items must be strings or JSON objects"
    )


def _citation(*, item: _FrontmatterItem) -> KnowledgeCitation:
    """Validate one citation object or compact evidence-reference string."""
    if isinstance(item, dict):
        try:
            return KnowledgeCitation.model_validate(item)
        except ValidationError as error:
            raise KnowledgeAuthoredDeclarationError(
                "invalid authored citation object"
            ) from error
    parts = item.split(":")
    role = KnowledgeEvidenceRole.CITES
    if parts[0] in {value.value for value in KnowledgeEvidenceRole}:
        role = KnowledgeEvidenceRole(parts.pop(0))
    if len(parts) == 2 and parts[0] in {"relation", "doc"}:
        try:
            target_id = UUID(parts[1])
        except ValueError as error:
            raise KnowledgeAuthoredDeclarationError(
                "citation shorthand contains an invalid UUID"
            ) from error
        if parts[0] == "relation":
            return KnowledgeCitation(role=role, relation_id=target_id)
        return KnowledgeCitation(role=role, doc_id=target_id)
    if len(parts) == 3 and parts[0] == "claim" and parts[2]:
        try:
            lineage_id = UUID(parts[1])
        except ValueError as error:
            raise KnowledgeAuthoredDeclarationError(
                "claim citation contains an invalid lineage UUID"
            ) from error
        return KnowledgeCitation(
            role=role, claim_lineage_id=lineage_id, claim_chunk_content_hash=parts[2]
        )
    raise KnowledgeAuthoredDeclarationError(
        "citation shorthand must target claim, relation, or doc evidence"
    )


def _watch(*, item: _FrontmatterItem) -> tuple[KnowledgeRuleParams | None, str | None]:
    """Validate one rich rule object or compact evidence/page watch."""
    if isinstance(item, dict):
        try:
            return _RULE_ADAPTER.validate_python(item), None
        except ValidationError as error:
            raise KnowledgeAuthoredDeclarationError(
                "invalid authored watch-rule object"
            ) from error
    prefix, separator, raw_value = item.partition(":")
    if not separator or not raw_value:
        raise KnowledgeAuthoredDeclarationError(
            "watch shorthand must contain a kind and value"
        )
    try:
        if prefix == "entity":
            return EntityRuleParams(entity_id=UUID(raw_value)), None
    except ValueError as error:
        raise KnowledgeAuthoredDeclarationError(
            "watch shorthand contains an invalid UUID"
        ) from error
    if prefix == "predicate":
        return PredicateBeatRuleParams(predicate=raw_value), None
    if prefix == "doc_source":
        return DocSetRuleParams(source_kind=raw_value), None
    if prefix == "page":
        return None, _page_watch_path(value=raw_value)
    raise KnowledgeAuthoredDeclarationError(f"unsupported watch shorthand {prefix!r}")


def _page_watch_path(*, value: str) -> str:
    """Normalize the design's extension-optional ``page:<path>`` shorthand."""
    path = PurePosixPath(value)
    if path.suffix == "":
        path = path.with_suffix(".md")
    normalized = str(path)
    if (
        path.is_absolute()
        or ".." in path.parts
        or normalized != (f"{value}.md" if PurePosixPath(value).suffix == "" else value)
        or path.suffix != ".md"
    ):
        raise KnowledgeAuthoredDeclarationError(
            "page watch must be a normalized relative Markdown path"
        )
    return normalized
