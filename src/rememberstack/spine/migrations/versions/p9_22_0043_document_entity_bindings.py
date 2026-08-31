"""Add the bounded document-local entity binding projection (D102).

revision: p9_22_0043

The append-only resolution decision remains identity authority. This table is
the bounded access path that lets an exact same-document repeat validate one
prior T4 match without scanning monthly mention/decision history.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "p9_22_0043"
down_revision: str | None = "p9_21_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ADD_GENERATION = """
ALTER TABLE deployments
  ADD COLUMN document_binding_generation text;
COMMENT ON COLUMN deployments.document_binding_generation IS
  'NULL disables D102 replay; document-t0-v1 means the derived binding set was
   built and verified for this deployment.';
"""

_CREATE_TABLE = """
CREATE TABLE document_entity_bindings (
  deployment_id uuid NOT NULL,
  doc_id uuid NOT NULL,
  canonical_lemma text NOT NULL CHECK (canonical_lemma <> ''),
  entity_id uuid NOT NULL,
  anchor_decision_id uuid,
  anchor_decided_at timestamptz,
  PRIMARY KEY (deployment_id, doc_id, canonical_lemma, entity_id),
  FOREIGN KEY (deployment_id, doc_id)
    REFERENCES documents (deployment_id, doc_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, entity_id)
    REFERENCES entities (deployment_id, entity_id) ON DELETE CASCADE,
  CHECK ((anchor_decision_id IS NULL) = (anchor_decided_at IS NULL))
) PARTITION BY HASH (doc_id);
COMMENT ON TABLE document_entity_bindings IS
  'D102 bounded derived membership for exact T0 replay inside one document.
   Every document-t0-v1 decision upserts membership; only T4 match stores its
   source decision and partition coordinate. Extra active rows fail closed.';
"""

_CREATE_TRIGGER_FUNCTION = """
CREATE FUNCTION maintain_document_entity_binding() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  coordinate jsonb;
  coordinate_doc_id uuid;
  mention_doc_id uuid;
  coordinate_lemma text;
  is_anchor boolean;
BEGIN
  coordinate := NEW.features -> 'document_t0';
  IF coordinate IS NULL
     OR coordinate ->> 'contract' <> 'document-t0-v1' THEN
    RETURN NEW;
  END IF;

  coordinate_doc_id := CAST(coordinate ->> 'doc_id' AS uuid);
  SELECT mention.doc_id INTO mention_doc_id
  FROM mentions mention
  WHERE mention.deployment_id = NEW.deployment_id
    AND mention.mention_id = NEW.mention_id
  ORDER BY mention.created_at DESC
  LIMIT 1;
  IF mention_doc_id IS NULL OR mention_doc_id <> coordinate_doc_id THEN
    RAISE EXCEPTION
      'document-t0-v1 decision doc_id disagrees with its mention';
  END IF;
  coordinate_lemma := coordinate ->> 'canonical_lemma';
  IF coordinate_lemma IS NULL OR coordinate_lemma = '' THEN
    RAISE EXCEPTION 'document-t0-v1 decision has no canonical lemma';
  END IF;
  is_anchor := NEW.method = 'T4_small' AND NOT NEW.is_new_entity;

  INSERT INTO document_entity_bindings (
    deployment_id, doc_id, canonical_lemma, entity_id,
    anchor_decision_id, anchor_decided_at
  ) VALUES (
    NEW.deployment_id, coordinate_doc_id, coordinate_lemma, NEW.entity_id,
    CASE WHEN is_anchor THEN NEW.decision_id ELSE NULL END,
    CASE WHEN is_anchor THEN NEW.decided_at ELSE NULL END
  )
  ON CONFLICT (deployment_id, doc_id, canonical_lemma, entity_id)
  DO UPDATE SET
    anchor_decision_id = CASE
      WHEN EXCLUDED.anchor_decision_id IS NOT NULL
      THEN EXCLUDED.anchor_decision_id
      ELSE document_entity_bindings.anchor_decision_id
    END,
    anchor_decided_at = CASE
      WHEN EXCLUDED.anchor_decision_id IS NOT NULL
      THEN EXCLUDED.anchor_decided_at
      ELSE document_entity_bindings.anchor_decided_at
    END;
  RETURN NEW;
END;
$$;

CREATE TRIGGER tr_resolution_decision_document_binding
AFTER INSERT ON resolution_decisions
FOR EACH ROW EXECUTE FUNCTION maintain_document_entity_binding();
"""


def upgrade() -> None:
    """Create D102 readiness, hash partitions, and transactional writer."""
    op.execute(_ADD_GENERATION)
    op.execute(_CREATE_TABLE)
    for remainder in range(64):
        op.execute(
            "CREATE TABLE document_entity_bindings_"
            f"p{remainder} PARTITION OF document_entity_bindings "
            f"FOR VALUES WITH (MODULUS 64, REMAINDER {remainder})"
        )
    op.execute(_CREATE_TRIGGER_FUNCTION)


def downgrade() -> None:
    """Remove D102 projection state and return to the global cascade."""
    op.execute(
        "DROP TRIGGER IF EXISTS tr_resolution_decision_document_binding "
        "ON resolution_decisions"
    )
    op.execute("DROP FUNCTION IF EXISTS maintain_document_entity_binding()")
    op.execute("DROP TABLE IF EXISTS document_entity_bindings CASCADE")
    op.execute(
        "ALTER TABLE deployments DROP COLUMN IF EXISTS document_binding_generation"
    )
