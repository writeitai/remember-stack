"""Typed block records — the deterministic identity atoms of a document (D57)."""

from enum import StrEnum

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator


class BlockType(StrEnum):
    """The structural kinds a blockizer emits (e1 §2)."""

    PARAGRAPH = "paragraph"
    HEADING = "heading"
    TABLE = "table"
    LIST_ITEM = "list_item"
    CODE = "code"
    QUOTE = "quote"


class Block(BaseModel):
    """One block: a slice of document.md with its deterministic identity hash."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ordinal: int = Field(ge=0)
    type: BlockType
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    block_hash: str
    heading_level: int | None = Field(default=None, ge=1, le=6)
    heading_title: str | None = None
    normalized_title: str | None = None

    @model_validator(mode="after")
    def heading_metadata_matches_type(self) -> "Block":
        """Heading tokens alone carry the D79 parser metadata."""
        metadata = (self.heading_level, self.heading_title, self.normalized_title)
        if self.type is BlockType.HEADING and any(value is None for value in metadata):
            raise ValueError(
                "heading blocks require level, title, and normalized title"
            )
        if self.type is not BlockType.HEADING and any(
            value is not None for value in metadata
        ):
            raise ValueError("non-heading blocks cannot carry heading metadata")
        return self
