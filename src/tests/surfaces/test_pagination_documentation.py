"""Published pagination examples and tables must agree with the shipped models."""

from pathlib import Path
import re
from unittest.mock import Mock

import pytest

from remember.models import DocumentPage
from remember.models import DocumentReferencesRequest
from remember.models import SectionHistoryRequest

_DOCS = Path(__file__).resolve().parents[3] / "website/src/app/docs/reference"


@pytest.mark.parametrize(
    "path", ["reference/python-sdk", "guides/keep-sources-current"]
)
def test_published_inventory_loop_consumes_next_cursor(path: str) -> None:
    page = (_DOCS.parent / path / "page.mdx").read_text()
    example = next(
        block
        for block in re.findall(r"```python\n(.*?)```", page, re.S)
        if "while True:" in block and ".list_documents(" in block
    )
    example = example[example.index("cursor = None") :]
    memory = Mock()
    memory.list_documents.side_effect = [
        DocumentPage(documents=(), next_cursor="next-position"),
        DocumentPage(documents=(), next_cursor=None),
    ]
    exec(
        compile(example, "published-inventory-loop", "exec"),
        {"memory": memory, "client": memory},
    )
    assert memory.list_documents.call_args_list[0].kwargs == {"cursor": None}
    assert memory.list_documents.call_args_list[1].kwargs == {"cursor": "next-position"}


@pytest.mark.parametrize("page", ["mcp", "http-api/sections-and-references"])
def test_reference_documentation_uses_request_model_page_size(page: str) -> None:
    text = (_DOCS / page / "page.mdx").read_text()
    # Each reference/history table uses the same page-size contract.
    sections = re.split(r"(?m)^### " if page == "mcp" else r"(?m)^## ", text)
    for name, model in [
        (
            "section_history" if page == "mcp" else "GET /documents/",
            SectionHistoryRequest,
        ),
        (
            "document_references" if page == "mcp" else "POST /documents/references",
            DocumentReferencesRequest,
        ),
    ]:
        section = next(
            part
            for part in sections
            if part.startswith(name) or part.startswith(f"`{name}`")
        )
        fields = re.findall(r"^\| `(k|limit|cursor)` \|", section, re.M)
        assert "limit" in fields
        assert set(fields) <= model.model_fields.keys()


def test_sql_documentation_describes_refusal_above_the_cap() -> None:
    text = (_DOCS / "http-api/query/page.mdx").read_text()
    rows = re.findall(r"^\| `max_rows` .*", text, re.M)
    assert len(rows) == 2
    assert all("clamp" not in row and "1,000" in row for row in rows)
