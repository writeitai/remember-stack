"""D134 self-references: where a claim names its own document.

When Claimify replaces a self-reference ("this report") with the document's
own title or file name, the grounding gate records the character range of
that inserted name in ``claim_text``. E3 uses it to skip minting an entity
for the document's own name. Existing claims never had one, so the column is
added empty.

The downgrade refuses to drop recorded spans: they are part of immutable
claims and cannot be re-derived without re-extracting.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from rememberstack.spine.migrations._helpers import apply_ddl

revision: str = "p9_35_0056"
down_revision: str | None = "p9_34_0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DDL = r"""
ALTER TABLE claims ADD COLUMN own_document_name_span int4range
  CHECK (own_document_name_span IS NULL
         OR (NOT isempty(own_document_name_span)
             AND lower(own_document_name_span) >= 0));
COMMENT ON COLUMN claims.own_document_name_span IS
  'D134 [start,end) character range in claim_text of the document''s own name that Claimify wrote in place of a self-reference ("this report"); NULL for every other claim. E3 does not mint or resolve an entity for the reference at this range.';
"""


def upgrade() -> None:
    """Add the nullable own-document-name span to claims."""
    apply_ddl(sql=_DDL)


def downgrade() -> None:
    """Drop the span column, refusing when any claim recorded one."""
    connection = op.get_bind()
    if connection.execute(
        text(
            "SELECT EXISTS(SELECT 1 FROM claims WHERE own_document_name_span IS NOT NULL)"
        )
    ).scalar_one():
        raise RuntimeError(
            "D134 own_document_name_span is recorded on claims and cannot be "
            "re-derived; re-extract after downgrading instead of dropping it"
        )
    connection.execute(text("ALTER TABLE claims DROP COLUMN own_document_name_span"))
