"""Check the retained BEAM 100K/1 grid against its original Markdown.

The PostgreSQL lab dump predates Selection, Claimify and PostgreSQL P1. It
retains ordered chunk rows and the converted reading's source hash. This
check compares every packing field available in both generations, with the
historical E0 section grid and the non-hosted packer's original parameters.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy import text

from rememberstack.core import blockize
from rememberstack.core import ChunkerParams
from rememberstack.core import pack_blocks
from rememberstack.model import SectionSpan

_FIELDS = (
    "section_id",
    "ordinal",
    "block_start",
    "block_end",
    "char_start",
    "char_end",
    "chunk_content_hash",
    "token_count",
)


def check(*, database_url: str, markdown: Path) -> dict[str, object]:
    payload = markdown.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    body = payload.decode()
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            version = connection.execute(
                text("""
                SELECT r.representation_id, v.content_hash, r.markdown_uri
                FROM document_representations r JOIN document_versions v USING (version_id)
            """)
            ).one()
            if (
                digest != version.content_hash
                or digest not in version.markdown_uri.split("/")
            ):
                raise ValueError(
                    "Markdown does not match the frozen BEAM representation"
                )
            sections = tuple(
                SectionSpan.model_validate(dict(row))
                for row in connection.execute(
                    text("""
                    SELECT section_id,node_path,role::text,block_start,block_end,title
                    FROM document_sections WHERE representation_id=:id
                    ORDER BY string_to_array(node_path,'.')::int[]
                """),
                    {"id": version.representation_id},
                ).mappings()
            )
            frozen = connection.execute(
                text(f"""
                SELECT {", ".join(_FIELDS)} FROM chunks WHERE representation_id=:id
                ORDER BY ordinal
            """),
                {"id": version.representation_id},
            ).all()
    finally:
        engine.dispose()
    packed = pack_blocks(
        blocks=blockize(document_md=body),
        sections=sections,
        document_md=body,
        params=ChunkerParams(),
    )
    current = [tuple(getattr(chunk, name) for name in _FIELDS) for chunk in packed]
    if current != frozen:
        for index, (actual, expected) in enumerate(zip(current, frozen, strict=False)):
            if actual != expected:
                changed = [
                    name
                    for name, left, right in zip(_FIELDS, actual, expected, strict=True)
                    if left != right
                ]
                raise ValueError(
                    f"BEAM packing fields differ at ordinal {index}: {changed}"
                )
        raise ValueError(f"BEAM chunk count differs: {len(current)} vs {len(frozen)}")
    splits = sum(chunk.span_kind == "split" for chunk in packed)
    if splits:
        raise ValueError("the BEAM lab binding must keep its whole grid")
    return {
        "markdown_sha256": digest,
        "chunks": len(current),
        "splits": splits,
        "packing_fields": list(_FIELDS),
        "packing_rows_equal": True,
        "live_calls": 0,
        "full_replay_verified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--markdown", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(check(database_url=args.database_url, markdown=args.markdown)))


if __name__ == "__main__":
    main()
