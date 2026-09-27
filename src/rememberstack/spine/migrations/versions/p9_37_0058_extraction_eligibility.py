"""D138 extraction eligibility on chunks and D138 family names in metadata.

Two changes for the workspace formats design (D138, realizing D133 §4.5):

- ``chunks.extraction_eligible`` records whether E2 runs Selection on a
  chunk, and ``chunks.extraction_eligibility_version`` the eligibility policy
  that decided it. Existing chunks were all cut from prose readings and were
  all extracted, so they are eligible; their policy version stays null.
- ``document_metadata.family`` switches to the D138 family names (``code``,
  ``word``, ``spreadsheet``, ``media``, ``binary``, …). Every existing
  version's family is re-derived from its stored MIME with the same Python
  mapping ingest uses (``family_for_mime``).

The downgrade restores the D134 family names with the p9_35 mapping and
drops the columns, refusing when any chunk is ineligible or any reading by a
D138 converter exists, even before it is chunked:
dropping the flag would make E2 extract claims from code, logs and cards.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from rememberstack.core.document_metadata import family_for_mime
from rememberstack.spine.migrations._helpers import apply_ddl

revision: str = "p9_37_0058"
down_revision: str | None = "p9_36_0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DDL = r"""
ALTER TABLE chunks ADD COLUMN extraction_eligible boolean NOT NULL DEFAULT true;
ALTER TABLE chunks ADD COLUMN extraction_eligibility_version text;
COMMENT ON COLUMN chunks.extraction_eligible IS
  'D133 section 4.5 / D138: false when every labelled range of the chunk has a derivation kind the extraction eligibility policy excludes (code, config, log, other_text, large_text, profiles, file cards). E2 completes such a chunk without a Selection call; it is still embedded and searchable.';
COMMENT ON COLUMN chunks.extraction_eligibility_version IS
  'The extraction eligibility policy version that decided extraction_eligible; null on chunks cut before the policy existed (all eligible).';
"""

_UPDATE_FAMILIES = """
UPDATE document_metadata m
SET family = f.family
FROM document_versions v
JOIN content_objects c
  ON c.deployment_id = v.deployment_id AND c.content_hash = v.content_hash
JOIN unnest(CAST(:mimes AS text[]), CAST(:families AS text[])) AS f(mime, family)
  ON f.mime = c.mime
WHERE v.deployment_id = m.deployment_id
  AND v.version_id = m.version_id
  AND m.family <> f.family
"""

_RESTORE_D134_FAMILIES = """
UPDATE document_metadata m
SET family = CASE
    WHEN c.mime IN ('text/markdown', 'text/x-markdown') THEN 'markdown'
    WHEN c.mime IN ('text/html', 'application/xhtml+xml') THEN 'html'
    WHEN c.mime = 'application/pdf' THEN 'pdf'
    WHEN c.mime LIKE 'image/%' THEN 'image'
    WHEN c.mime LIKE 'audio/%' THEN 'audio'
    WHEN c.mime LIKE 'video/%' THEN 'video'
    WHEN c.mime IN ('application/msword', 'application/rtf')
      OR c.mime LIKE 'application/vnd.openxmlformats-officedocument.%'
      OR c.mime LIKE 'application/vnd.oasis.opendocument.%'
      OR c.mime LIKE 'application/vnd.ms-excel%'
      OR c.mime LIKE 'application/vnd.ms-powerpoint%' THEN 'office'
    WHEN c.mime LIKE 'text/%' THEN 'text'
    ELSE 'other'
  END
FROM document_versions v
JOIN LATERAL (
  SELECT lower(btrim(split_part(co.mime, ';', 1))) AS mime
  FROM content_objects co
  WHERE co.deployment_id = v.deployment_id AND co.content_hash = v.content_hash
) c ON true
WHERE v.deployment_id = m.deployment_id AND v.version_id = m.version_id
"""

_D138_READINGS_EXIST = """
SELECT EXISTS (SELECT 1 FROM chunks WHERE NOT extraction_eligible)
    OR EXISTS (
      SELECT 1 FROM document_representations
      WHERE route IN ('text', 'card', 'office', 'pdf', 'email', 'notebook',
                      'spreadsheet', 'table', 'dataset')
    )
"""
"""Rows only D138 can have produced: an ineligible chunk, or any reading by a
D138 converter. The route decides, not the family: a Markdown file over
1 MB is a search-only large_text profile while its family stays prose."""


def upgrade() -> None:
    """Add the chunk eligibility columns and re-derive every version's family."""
    apply_ddl(sql=_DDL)
    connection = op.get_bind()
    mimes = tuple(
        connection.execute(text("SELECT DISTINCT mime FROM content_objects")).scalars()
    )
    connection.execute(
        text(_UPDATE_FAMILIES),
        {
            "mimes": list(mimes),
            "families": [family_for_mime(mime=mime) for mime in mimes],
        },
    )


def downgrade() -> None:
    """Restore D134 family names and drop the columns; refuse ineligible chunks."""
    connection = op.get_bind()
    if connection.execute(text(_D138_READINGS_EXIST)).scalar_one():
        raise RuntimeError(
            "D138 downgrade requires an explicitly reviewed plan: ineligible"
            " chunks or search-only, profile or card readings (code, logs, file"
            " cards) would become claim-extractable once extraction_eligible"
            " is dropped"
        )
    connection.execute(text(_RESTORE_D134_FAMILIES))
    connection.execute(
        text(
            "ALTER TABLE chunks DROP COLUMN extraction_eligibility_version,"
            " DROP COLUMN extraction_eligible"
        )
    )
