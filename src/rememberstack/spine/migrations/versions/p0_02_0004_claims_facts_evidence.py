"""Create claim, relation, observation, and evidence structures."""

from collections.abc import Sequence

from rememberstack.spine.migrations._helpers import apply_ddl
from rememberstack.spine.migrations._helpers import drop_tables

revision: str = "p0_02_0004"
down_revision: str | None = "p0_02_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DDL = r"""-- ─────────────────────────────────────────────────────────────────────────
-- claims — immutable verifiable propositions (D31/D32). THREE immutable time axes (concepts §5, D41):
-- asserted_at = assertion-EVENT time (when the source spoke, ≈ published_at); claim_valid_from/until
-- (+ precision/kind) = the world-time interval the SOURCE asserted the proposition held (valid-time
-- as EVIDENCE, not current belief); ingested_at = when WE extracted it (transaction-time). None ever
-- change, and claim validity is never superseded (D3) — adjudicated validity lives only on relations.
-- A row in claims is an ACCEPTED claim: the deterministic grounding gate (anchor + window
-- membership, D32 layers 1-2) MUST pass, enforced by the CHECK — a claim that fails the gate is
-- never produced (it becomes a ledger entry or is discarded), so the flags exist for audit and are
-- always true here. D94 keeps claims non-partitioned so current-testimony HNSW and BM25 indexes
-- have global ranking statistics rather than one index/statistics set per ingest month.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE claims (
  claim_id        uuid NOT NULL,
  deployment_id   uuid NOT NULL,               -- LOGICAL FK → deployments
  doc_id          uuid NOT NULL,               -- LOGICAL FK → documents (provenance always attached — requirements_v3)
  chunk_id        uuid NOT NULL,               -- LOGICAL FK → chunks (the target chunk of the context bundle)
  section_id      uuid,                        -- LOGICAL FK → document_sections (denormalized role/path; explains a Selection decision)
  claim_text      text NOT NULL,               -- the STANDALONE assertion — what retrieval/E3/reasoning use (decontextualized, D32)
  source_span     text NOT NULL,               -- the verbatim slice of the chunk the claim derives from (provenance/audit, D32)
  char_start      integer NOT NULL,            -- source_span offset into document.md (anchor check: a real in-bounds slice, D32 layer 1)
  char_end        integer NOT NULL,
  added_context   jsonb NOT NULL DEFAULT '[]', -- [{text, source_kind: header|neighbour|prefix|hint, source_ref}] — each substring decontextualization ADDED (D32 layer 2)
  temporal_class  claim_temporal_class,        -- static | dynamic | atemporal — the "temporally classified" requirement (see reconciliation note)
  is_attributed   boolean NOT NULL DEFAULT false, -- preserves a "X said Y" attribution (entailment rule: entails "X said Y", not "Y" — D32)
  -- grounding verdicts (D32). Deterministic layers 1-2 are an ACCEPTANCE GATE (must be true here);
  -- the LLM layers 3-4 are advisory/sampled and may be false on a kept-but-borderline claim:
  anchor_ok       boolean NOT NULL,            -- layer 1: source_span is a real in-bounds slice of the chunk (deterministic)
  window_membership_ok boolean NOT NULL,       -- layer 2: every added_context substring verbatim-exists in its declared bundle source (deterministic; rejects fabrication)
  entailment_self_verdict boolean,             -- layer 3: in-call self-assertion the bundle entails the claim (~free, optimistic)
  audit_status    grounding_audit_status NOT NULL DEFAULT 'unaudited', -- layer 4: unaudited | sampled_pass | sampled_fail | escalated (sampled, not per-claim)
  kept_flagged    boolean NOT NULL DEFAULT false, -- D35 low-confidence Selection outcome: kept but marked-for-review (mirrors a selection_keep_flagged ledger row — see invariant below)
  is_current_testimony boolean NOT NULL DEFAULT true, -- D54 CACHE of testimony currency (the ledger below is truth): false once a newer extraction generation covers this chunk, or (living mode) the chunk left the current version. Bookkeeping, NEVER validity — no adjudication, claims stay immutable in every D3 sense
  asserted_at     timestamptz,                 -- ASSERTION-EVENT time: when the source asserted this (≈ the version's source_modified_at/published_at, D55) — immutable; NOT the fact's world-time (that is claim_valid_*, D41)
  -- D41 source-asserted world-validity INTERVAL — immutable evidence about WHEN (not current belief).
  -- Overlap of these intervals across sources is EXPECTED (it is evidence), so there is deliberately
  -- NO uniqueness/EXCLUDE, NO invalidated_at, NO status here — the opposite of relations (§9):
  claim_valid_from timestamptz,                -- world-time start the SOURCE attributed (NULL = unbounded-before/unknown); immutable
  claim_valid_until timestamptz,               -- world-time end (NULL = open-per-source OR unknown; disambiguated by claim_valid_precision)
  claim_valid_precision claim_valid_precision NOT NULL DEFAULT 'unknown', -- unknown|instant|day|month|quarter|year|open — "FY2023" stores a normalized [start,end] without lying about granularity
  claim_valid_kind claim_valid_kind,           -- which world-interval this is: proposition_validity|event_time|measurement_period|effective_period (so a measurement period is never conflated with an event date or with asserted_at)
  extractor_version text NOT NULL,             -- LOGICAL FK → pipeline_component_versions (extractor); replay-on-rebuild key (D33)
  embedding_ref   text,                        -- opaque Lance key (claims are searchable in P1)
  embedding_version text,                       -- LOGICAL FK → pipeline_component_versions (embedder)
  ingested_at     timestamptz NOT NULL DEFAULT now(),  -- transaction-time; immutable
  PRIMARY KEY (claim_id),
  UNIQUE (deployment_id, claim_id),
  CHECK (char_end >= char_start),
  CHECK (anchor_ok AND window_membership_ok),  -- a claims row is an ACCEPTED claim; the deterministic grounding gate passed (D32)
  -- D41 precision/bounds coherence (claim validity carries no status/invalidated_at — it is immutable):
  CHECK (claim_valid_until IS NULL OR claim_valid_from IS NULL OR claim_valid_until >= claim_valid_from),
  CHECK (claim_valid_precision <> 'unknown' OR (claim_valid_from IS NULL AND claim_valid_until IS NULL)),
  CHECK (claim_valid_precision <> 'open'    OR (claim_valid_from IS NOT NULL AND claim_valid_until IS NULL)),
  CHECK (claim_valid_precision <> 'instant' OR (claim_valid_from IS NOT NULL AND claim_valid_until = claim_valid_from)),
  -- a bounded precision must actually carry both bounds (else it silently degrades to unknown/open):
  CHECK (claim_valid_precision NOT IN ('day','month','quarter','year') OR (claim_valid_from IS NOT NULL AND claim_valid_until IS NOT NULL))
);
COMMENT ON TABLE claims IS
  'E2 immutable verifiable propositions (D31/D32). Stores standalone claim_text + verbatim source_span + offsets + added_context for provenance-and-entailment grounding. Three immutable time axes (D41): asserted_at (assertion event), claim_valid_from/until (+precision/kind = source-asserted world-interval — evidence, not belief), ingested_at (system); never superseded (supersession is on relations, D3). A row here passed the deterministic grounding gate. Non-partitioned under D94 so current-testimony semantic and BM25 indexes rank globally.';
CREATE INDEX ix_claims_doc      ON claims (deployment_id, doc_id);
CREATE INDEX ix_claims_chunk    ON claims (chunk_id);
CREATE INDEX ix_claims_flagged  ON claims (deployment_id) WHERE kept_flagged = true;        -- review surface (D35)
CREATE INDEX ix_claims_current  ON claims (deployment_id, doc_id) WHERE is_current_testimony; -- the D54 hot filter (counts; default claim search)
CREATE INDEX ix_claims_audit    ON claims (deployment_id) WHERE audit_status = 'sampled_fail'; -- grounding regressions
-- D41 claim-validity remains ordinary authority data. D94 applies temporal predicates and semantic
-- ranking inside one PostgreSQL statement; the later ix_claims_valid_window supports bounded
-- evidence-time filtering without copying these scalars into a search mirror.

-- ─────────────────────────────────────────────────────────────────────────
-- claim_extraction_decisions — the append-only, version-stamped extraction transcript (D33). It
-- records every Selection DROP (with reason), every low-confidence KEEP-FLAG, and every
-- decontextualization EDIT. Plain keeps are NOT recorded (they ARE the claims row) — keeping the
-- table sized for drops+flags+edits, not every keep. Rebuild reads stored claims + these decisions
-- and never re-calls the model (D7). Large ⇒ monthly partition by decided_at.
-- INVARIANT: a kept_flagged claim is the pair (claims row with kept_flagged=true) + (a
-- selection_keep_flagged decision here); the ledger is the replay source from which
-- claims.kept_flagged is reconstituted on rebuild.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE claim_extraction_decisions (
  decision_id     uuid NOT NULL,
  deployment_id   uuid NOT NULL,               -- LOGICAL FK → deployments
  doc_id          uuid NOT NULL,               -- LOGICAL FK → documents
  chunk_id        uuid NOT NULL,               -- LOGICAL FK → chunks
  claim_id        uuid,                        -- LOGICAL FK → claims; set for decontext edits + selection_keep_flagged; NULL for selection_drop (no claim produced)
  decision_type   extraction_decision_type NOT NULL, -- selection_drop | selection_keep_flagged | decontext_edit
  source_span     text,                        -- the proposition/sentence the decision was about
  reason          selection_drop_reason,       -- for drops: opinion|advice|hypothetical|generic|question|intro|conclusion|no_info|ambiguous|references_boilerplate (D31)
  edit_detail     jsonb,                        -- for decontext edits: what was resolved/added and from which bundle source
  protected_class text,                        -- never-drop class checked/applied (quantity|date|named_entity_predicate|change_of_state) — D35
  extractor_version text NOT NULL,             -- LOGICAL FK → pipeline_component_versions; the version that made this decision
  decided_at      timestamptz NOT NULL DEFAULT now(),  -- partition key
  PRIMARY KEY (decision_id, decided_at)
) PARTITION BY RANGE (decided_at);
COMMENT ON TABLE claim_extraction_decisions IS
  'Append-only Selection-drop + keep-flag + decontextualization-edit ledger (D33); plain keeps are not recorded. Makes aggressive Selection auditable and recoverable: a better prompt re-examines only the drops; rebuild replays from here without re-calling the model (D7). Monthly-partitioned.';
CREATE INDEX ix_cxd_chunk ON claim_extraction_decisions (chunk_id);
CREATE INDEX ix_cxd_drops ON claim_extraction_decisions (deployment_id, reason) WHERE decision_type = 'selection_drop';

-- ─────────────────────────────────────────────────────────────────────────
-- grounding_audits — the sampled independent entailment audit (D32 layer 4). Not per-claim
-- (self-grading is optimistic; a separate judge re-checks a sample; only a borderline band
-- escalates). claims.audit_status caches the latest result.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE grounding_audits (
  audit_id        uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  claim_id        uuid NOT NULL,               -- LOGICAL FK → claims
  verdict         grounding_audit_status NOT NULL, -- sampled_pass | sampled_fail | escalated
  judge_version   text NOT NULL,               -- LOGICAL FK → pipeline_component_versions (independent judge)
  rationale       text,
  sampled_at      timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE grounding_audits IS
  'Sampled independent entailment audits of claims (D32 layer 4). Offline, not per-claim; feeds claims.audit_status and the grounding eval suite.';
CREATE INDEX ix_grounding_claim ON grounding_audits (claim_id);

-- ─────────────────────────────────────────────────────────────────────────
-- testimony_currency_events — the D54 currency ledger (append-only; the D33 pattern: this is
-- truth, claims.is_current_testimony is cache). A transition is BOOKKEEPING, never validity:
-- no adjudication, no invalidated_at, nothing about the claim changes. Timestamped events keep
-- transaction-time reconstructions exact (fact-as-of-T still sees old generations).
-- Written by the reconciliation step of the lifecycle flow (evidence_lifecycle_design §5),
-- which runs only on COMPLETED basis changes and then recounts affected facts.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE testimony_currency_events (
  event_id        uuid NOT NULL,
  deployment_id   uuid NOT NULL,               -- LOGICAL FK → deployments
  claim_id        uuid NOT NULL,               -- LOGICAL FK → claims
  doc_id          uuid NOT NULL,               -- LOGICAL FK → documents (the lineage; the recount scope)
  reconciliation_id uuid NOT NULL,             -- identifies ONE reconciliation run = one completed basis change per lineage (a new version's extraction completing, or a version-bump re-extraction completing). Minted when the run starts and stored in processing_state, so a RETRIED run reuses it — its re-emitted events hit the UNIQUE below as no-ops
  became_current  boolean NOT NULL,            -- false = lost currency; true = regained (un-delete, mode change)
  reason          currency_reason NOT NULL,    -- reextracted | version_superseded | version_deleted
  from_extractor_version text,                 -- the superseded generation (reason=reextracted)
  from_version_id uuid,                        -- the superseded/deleted document version (reason=version_*)
  occurred_at     timestamptz NOT NULL DEFAULT now(),  -- partition key
  PRIMARY KEY (event_id, occurred_at),
  UNIQUE (claim_id, reconciliation_id, reason, became_current, occurred_at)  -- a retried reconciliation re-emits as a no-op, never a duplicate (F11)
) PARTITION BY RANGE (occurred_at);
COMMENT ON TABLE testimony_currency_events IS
  'Append-only D54 testimony-currency transitions. Truth for claims.is_current_testimony (cache); replayable (D7). Reasons: reextracted (a newer extraction generation covers the chunk), version_superseded (living-mode lineage moved past the claim''s version), version_deleted. Never validity, never supersession — D3 untouched.';
CREATE INDEX ix_currency_claim ON testimony_currency_events (claim_id);
CREATE INDEX ix_currency_doc   ON testimony_currency_events (deployment_id, doc_id, occurred_at);
-- ─────────────────────────────────────────────────────────────────────────
-- relations — distinct bi-temporal facts (D2/D3). The (entity_id,predicate) blocking key for
-- supersession is the composite index below; it is small (distinct facts, not assertions) —
-- what makes supersession affordable at scale (concepts §6). The canonical fact LABEL + its
-- embedding live in Lance (D8); PG keeps the label text + version + a Lance ref. Not partitioned.
--
-- "Live belief" = invalidated_at IS NULL (transaction-time), regardless of valid_until: a
-- believed-historical fact ("Alice worked at Acme 2020-2022", valid_until set, invalidated_at NULL)
-- is still currently believed. status is a GENERATED mirror of invalidated_at so validity has
-- exactly one authoritative home (D6) and cannot drift.
--
-- Uniqueness: a GiST EXCLUSION constraint forbids two BELIEVED, non-contradictory relations with
-- the same (s,p,o) AND OVERLAPPING valid-time windows. This is more correct than a partial unique
-- index: it permits re-occurring facts with non-overlapping windows (Alice worked at Acme twice)
-- and permits contradictions (carved out via contradiction_group), while forbidding duplicate
-- overlapping beliefs. Evidence-collapse (D2) finds the believed relation whose window covers the
-- new claim's time.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE relations (
  relation_id     uuid PRIMARY KEY,            -- the fact's identity; provenance handle in the graph/Lance projections
  deployment_id   uuid NOT NULL REFERENCES deployments,
  subject_entity_id uuid NOT NULL,             -- canonical subject (composite FK below; only canonical entities enter relations/graph — p2 §2)
  predicate       text NOT NULL,               -- governed predicate; composite FK below
  object_entity_id uuid NOT NULL,              -- canonical object (entity→entity only; literals stay in claims — D2)
  -- bi-temporality (concepts §5): two clocks, different questions.
  valid_from      timestamptz,                 -- VALID-time start: when the fact began holding in the world (NULL = unknown/always)
  valid_until     timestamptz,                 -- VALID-time end: closed by supersession when the fact stops holding ("Alice left Acme")
  ingested_at     timestamptz NOT NULL DEFAULT now(), -- TRANSACTION-time: when the system first believed this fact
  invalidated_at  timestamptz,                 -- TRANSACTION-time: when the system learned it was superseded (NULL = still believed)
  evidence_count  integer NOT NULL DEFAULT 0,  -- cached count of DISTINCT DOCUMENT LINEAGES with current-testimony supporting claims (D54 — invariant under re-extraction/version churn/intra-doc repetition); confidence/salience signal (D2 refined); K3 candidate filter
  contradict_count integer NOT NULL DEFAULT 0, -- cached count of distinct current-testimony lineages contradicting (same D54 rule, stance=contradicts)
  confidence      real,                        -- aggregate confidence over evidence (not an extraction-time guess — concepts §3)
  contradiction_group uuid,                    -- shared id when two live relations contradict and can't be adjudicated — retrieval shows both sides (concepts §4)
  status          relation_status GENERATED ALWAYS AS  -- DERIVED mirror of invalidated_at (single validity home, D6): active iff invalidated_at IS NULL, else invalidated
                    (CASE WHEN invalidated_at IS NOT NULL THEN 'invalidated'::relation_status ELSE 'active'::relation_status END) STORED,
  -- fact label (D8): the human-readable sentence embedded in Lance; regenerated only on material adjudication change.
  fact_label      text,                        -- "Alice Novak works at Acme as VP of Engineering"
  fact_label_version text,                     -- LOGICAL FK → pipeline_component_versions (fact_labeler)
  fact_label_embedding_ref text,               -- opaque Lance key for the fact-label vector (P1; no vectors in PG/graph — D8)
  normalizer_version text NOT NULL,            -- LOGICAL FK → pipeline_component_versions (normalizer); replay-on-rebuild
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (deployment_id, relation_id),          -- composite-FK target (tenancy isolation, §0)
  FOREIGN KEY (deployment_id, predicate) REFERENCES predicates (deployment_id, predicate) ON UPDATE CASCADE,
  FOREIGN KEY (deployment_id, subject_entity_id) REFERENCES entities (deployment_id, entity_id),
  FOREIGN KEY (deployment_id, object_entity_id)  REFERENCES entities (deployment_id, entity_id),
  CHECK (valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from),
  CHECK (invalidated_at IS NULL OR invalidated_at >= ingested_at),  -- can't un-learn before learning
  -- At most one BELIEVED, non-contradictory relation per (s,p,o) with overlapping world-time:
  EXCLUDE USING gist (
    deployment_id WITH =, subject_entity_id WITH =, predicate WITH =, object_entity_id WITH =,
    tstzrange(valid_from, valid_until) WITH &&
  ) WHERE (invalidated_at IS NULL AND contradiction_group IS NULL)
);
COMMENT ON TABLE relations IS
  'E3 distinct bi-temporal facts (D2/D3). Identity = (subject,predicate,object) + validity interval; the unit of supersession/contradiction. evidence_count caches corpus redundancy as a confidence signal (D2). status is a generated mirror of invalidated_at (validity has one home, D6). The GiST EXCLUDE forbids overlapping duplicate beliefs while allowing re-occurring facts and carved-out contradictions. fact_label+embedding live in Lance (D8).';
-- The supersession blocking key (D4) — small, distinct facts; THE index that makes supersession
-- detection affordable (concepts §6):
CREATE INDEX ix_relations_block_subj ON relations (deployment_id, subject_entity_id, predicate, object_entity_id);
CREATE INDEX ix_relations_block_obj  ON relations (deployment_id, object_entity_id, predicate);  -- reverse blocking ("who works_at acme?")
CREATE INDEX ix_relations_predicate  ON relations (deployment_id, predicate);
CREATE INDEX ix_relations_contradiction ON relations (contradiction_group) WHERE contradiction_group IS NOT NULL;
CREATE INDEX ix_relations_live       ON relations (deployment_id, subject_entity_id) WHERE invalidated_at IS NULL;
-- ─────────────────────────────────────────────────────────────────────────
-- relation_evidence — the many-to-many join claims ⇄ relations (D2). "Where corpus redundancy goes
-- to die": 200 documents asserting the same fact = one relation + 200 rows here. ~10⁸ rows.
--
-- Partitioned by HASH(relation_id), NOT by ingest month — because every hot access is by
-- relation_id (hydration) and the evidence-once invariant is on (relation_id, claim_id). With the
-- partition key = relation_id: relation hydration prunes to ONE partition, AND a real
-- PRIMARY KEY (relation_id, claim_id) enforces "a claim evidences a relation at most once" in-DB
-- (so relations.evidence_count cannot be inflated by a retry — a re-link is an ON CONFLICT no-op).
-- This is D23's evidence-join policy. Hash partitions are STATIC (64 migration-created children;
-- a measured starting point), so no pg_partman rolling-window is needed. The claim_id reverse lookup
-- ("which relations does this claim evidence")
-- scans all partitions but is the cold path. FKs remain logical (D23 btree-only/write-amplification).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE relation_evidence (
  deployment_id   uuid NOT NULL,               -- LOGICAL FK → deployments
  relation_id     uuid NOT NULL,               -- LOGICAL FK → relations; HASH partition key
  claim_id        uuid NOT NULL,               -- LOGICAL FK → claims; the asserting claim (immutable evidence). One claim may evidence MANY relations.
  doc_id          uuid NOT NULL,               -- LOGICAL FK → documents (the claim's LINEAGE, denormalized write-once) — makes the D54 recount a single-table scan per fact (F7)
  stance          evidence_stance NOT NULL,    -- supports | contradicts (concepts §3/§4)
  normalizer_version text NOT NULL,            -- LOGICAL FK → pipeline_component_versions; which normalizer linked them
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (relation_id, claim_id)          -- evidence-once, DB-enforced (partition key relation_id is included); re-link via ON CONFLICT DO NOTHING is a no-op
) PARTITION BY HASH (relation_id);
COMMENT ON TABLE relation_evidence IS
  'Many-to-many evidence links (D2). Corpus redundancy collapses here into relations.evidence_count. Partitioned by HASH(relation_id) so relation hydration prunes to one partition and PRIMARY KEY (relation_id, claim_id) enforces evidence-once in-DB (D23; §17 item 1 resolved). claim_id reverse lookup scans all partitions (cold path). Logical FKs (D23).';
-- 64 static hash children created by the migration — required, or inserts fail (F5); mirrors observation_evidence:
DO $$ BEGIN
  FOR i IN 0..63 LOOP
    EXECUTE format('CREATE TABLE relation_evidence_p%s PARTITION OF relation_evidence '
                   'FOR VALUES WITH (MODULUS 64, REMAINDER %s);', i, i);
  END LOOP;
END $$;
CREATE INDEX ix_relevidence_claim ON relation_evidence (claim_id);  -- reverse lookup: relations a claim evidences (all-partition scan)

-- ─────────────────────────────────────────────────────────────────────────
-- relation_adjudications — append-only supersession/contradiction transcript (D3/D4). Records WHY a
-- relation's window closed, a contradiction was flagged, or a merge proposed — by which cascade
-- rung, with what confidence/evidence. Makes the non-deterministic adjudication replayable on
-- rebuild (D7) and answers "why did valid_until close on 2026-01-15?". Real composite FK; the
-- deletion GC retires (not deletes) relations referenced here so the audit trail survives (§13).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE relation_adjudications (
  adjudication_id uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  relation_id     uuid NOT NULL,               -- the relation acted upon (composite FK below)
  related_relation_id uuid,                    -- the other relation in a supersede/contradict pair, if any (composite FK below)
  outcome         adjudication_outcome NOT NULL, -- add | noop | supersede | contradict | same_as_merge_proposal (D4 write-time outcomes)
  method          adjudication_method NOT NULL,  -- novelty_gate | exact | fuzzy | embedding | small_model | frontier_llm (cheap-first cascade, D4)
  confidence      real,
  triggering_claim_id uuid,                     -- LOGICAL FK → claims; the new claim that triggered adjudication
  features        jsonb,                        -- scores/rationale the decision used (audit); scrubbed on hard-forget (§13)
  adjudicator_version text NOT NULL,            -- LOGICAL FK → pipeline_component_versions
  decided_by      decision_actor NOT NULL DEFAULT 'auto',
  decided_at      timestamptz NOT NULL DEFAULT now(),
  superseded_by   uuid REFERENCES relation_adjudications, -- a later adjudication that overrode this one
  FOREIGN KEY (deployment_id, relation_id)         REFERENCES relations (deployment_id, relation_id),
  FOREIGN KEY (deployment_id, related_relation_id) REFERENCES relations (deployment_id, relation_id)
);
COMMENT ON TABLE relation_adjudications IS
  'Append-only supersession/contradiction decision log (D3/D4). Explains every window closure / contradiction flag / merge proposal, by cascade rung + evidence; replayed on P2 rebuild and used for "what did we believe at T / why" audits.';
CREATE INDEX ix_adjud_relation ON relation_adjudications (relation_id);
CREATE INDEX ix_adjud_live     ON relation_adjudications (relation_id) WHERE superseded_by IS NULL;
-- ─────────────────────────────────────────────────────────────────────────
-- observations — non-graph facts about ONE entity (D43): entity-anchored, bi-temporal, UNTYPED. The
-- supersession blocking key is subject_entity_id alone (exact, exhaustive per entity); the same-slot /
-- supersede-vs-coexist judgment is the adjudicator's (D4 cascade), fail-safe to coexist. No governed
-- attribute vocabulary, no value_domain/cardinality, NO structured value/period columns, no typed
-- EXCLUDE — the value AND any reporting period live in `statement` and are matched SEMANTICALLY, exactly
-- like the property (consistent with the untyped design). status is a GENERATED mirror of invalidated_at
-- (one validity home, D6). The observation LABEL + its embedding live in Lance (D8).
-- THE NO-CAP RULE (D43): only a CHANGING EFFECTIVE STATE (headcount/balance/status) is capped on
-- valid-time when superseded; a MEASUREMENT / FIXED-PERIOD figure ("FY2023 revenue") is NEVER capped —
-- it doesn't stop being true at period-end, stays open, and conflicting same-period figures coexist.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE observations (
  observation_id  uuid PRIMARY KEY,            -- the observation's identity; provenance handle in Lance
  deployment_id   uuid NOT NULL REFERENCES deployments,
  subject_entity_id uuid NOT NULL,            -- the ANCHOR + supersession blocking key (a resolved canonical entity); composite FK below
  statement       text NOT NULL,              -- canonical NL form of the observed fact ("Acme's headcount is 600", "Acme's FY2023 revenue was $5M"); embedded in Lance (D8). The VALUE and any reporting period live HERE — there is no structured value/period column (D43 lean); the adjudicator reads them semantically, like the property.
  -- bi-temporality (concepts §5): two clocks, WORLD-VALIDITY OF THE BELIEF.
  valid_from      timestamptz,                 -- VALID-time start: when the belief began holding in the world (NULL = unknown/always); seeded from the claim's asserted validity (D41)
  valid_until     timestamptz,                 -- VALID-time end. NO-CAP RULE (D43): capped ONLY when a CHANGING EFFECTIVE STATE (headcount/balance/status) is superseded by a later value. A MEASUREMENT / FIXED-PERIOD figure ("FY2023 revenue") is NEVER capped here — it doesn't stop being true at period-end; it stays open and conflicting same-period figures coexist. The adjudicator decides state-vs-measurement from `statement` (semantic), not a typed column. (observations_design.md §3)
  ingested_at     timestamptz NOT NULL DEFAULT now(), -- TRANSACTION-time: when the system first believed it
  invalidated_at  timestamptz,                 -- TRANSACTION-time: when learned wrong (NULL = still believed). NOT used to "end" a fact — that's valid_until.
  evidence_count  integer NOT NULL DEFAULT 0,  -- cached count of DISTINCT current-testimony LINEAGES supporting (D54 — mirrors relations)
  contradict_count integer NOT NULL DEFAULT 0, -- cached count of distinct current-testimony lineages contradicting (D54). NB: conflicting OBSERVATIONS are tracked via contradiction_group, a different concept.
  confidence      real,                        -- aggregate confidence over evidence
  contradiction_group uuid,                    -- shared id when two live observations conflict and both must stand (concepts §4)
  status          relation_status GENERATED ALWAYS AS  -- DERIVED mirror of invalidated_at (single validity home, D6)
                    (CASE WHEN invalidated_at IS NOT NULL THEN 'invalidated'::relation_status ELSE 'active'::relation_status END) STORED,
  obs_label       text,                        -- the human-readable sentence embedded in Lance (often = statement); semantic blocking + retrieval
  obs_label_version text,                      -- LOGICAL FK → pipeline_component_versions (labeler)
  obs_label_embedding_ref text,                -- opaque Lance key for the label vector (P1; no vectors in PG — D8)
  normalizer_version text NOT NULL,            -- LOGICAL FK → pipeline_component_versions; replay-on-rebuild
  adjudicator_version text,                    -- LOGICAL FK → pipeline_component_versions (supersession/contradiction adjudicator, D4)
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (deployment_id, observation_id),       -- composite-FK target (tenancy isolation, §0)
  FOREIGN KEY (deployment_id, subject_entity_id) REFERENCES entities (deployment_id, entity_id),
  CHECK (valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from),
  CHECK (invalidated_at IS NULL OR invalidated_at >= ingested_at)  -- can't un-learn before learning
  -- NOTE: intentionally NO EXCLUDE / uniqueness constraint — there is no typed slot to key one on;
  -- supersession + evidence-collapse are adjudicated (entity-block + semantic + cascade), and
  -- "both-stand" is the safe default.
);
COMMENT ON TABLE observations IS
  'D43 non-graph fact layer: a believed value/statement about ONE entity (entity-anchored, bi-temporal, UNTYPED). Sibling of relations; never projects to the graph (D18). The value AND any reporting period live in `statement` (no structured value/period columns); the adjudicator matches same-entity + same-property + same-period + value-compatibility SEMANTICALLY. Supersession is adjudicated by entity-blocking + the D4 cascade (no typed slot, no EXCLUDE); "never silently resolve" is a binding adjudicator contract (supersede only on a positively-matched prior above margin, with a persisted reason; else coexist) + an eval gate, NOT a schema invariant. NO-CAP RULE: only a changing effective state is capped on valid-time; a measurement/fixed-period figure is never capped and conflicting same-period figures coexist. status is a generated mirror of invalidated_at (one validity home, D6); label+embedding live in Lance (D8).';
-- The supersession blocking key (D4): all live observations for an entity (exact + exhaustive per entity):
CREATE INDEX ix_observations_block ON observations (deployment_id, subject_entity_id) WHERE invalidated_at IS NULL;
CREATE INDEX ix_observations_entity ON observations (deployment_id, subject_entity_id);  -- full history incl. capped/invalidated
CREATE INDEX ix_observations_contradiction ON observations (contradiction_group) WHERE contradiction_group IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────
-- observation_evidence — many-to-many join claims ⇄ observations (D2), mirroring relation_evidence.
-- Corpus redundancy collapses here into observations.evidence_count. HASH(observation_id). Like
-- relation_evidence (§9), FKs are LOGICAL (D23 — btree-only at 10^8 scale); the integrity guarantee
-- here is the PRIMARY KEY (evidence-once), NOT referential FKs. (Evidence-collapse of the same value
-- into ONE observation is adjudicated upstream — best-effort — not enforced by this table, which only
-- dedups a given (observation, claim) pair.)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE observation_evidence (
  deployment_id   uuid NOT NULL,               -- LOGICAL FK → deployments
  observation_id  uuid NOT NULL,               -- LOGICAL FK → observations; HASH partition key
  claim_id        uuid NOT NULL,               -- LOGICAL FK → claims; the asserting claim (immutable evidence). One claim may evidence many observations.
  stance          evidence_stance NOT NULL,    -- supports | contradicts (concepts §3/§4)
  normalizer_version text NOT NULL,            -- LOGICAL FK → pipeline_component_versions
  doc_id          uuid NOT NULL,               -- LOGICAL FK → documents (the claim's lineage, write-once) — D54 recount without cross-partition claim joins (F7)
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (observation_id, claim_id)       -- evidence-once, DB-enforced; re-link via ON CONFLICT DO NOTHING is a no-op
) PARTITION BY HASH (observation_id);
-- 64 static hash children created by the migration, same as relation_evidence (§9):
DO $$ BEGIN
  FOR i IN 0..63 LOOP
    EXECUTE format('CREATE TABLE observation_evidence_p%s PARTITION OF observation_evidence '
                   'FOR VALUES WITH (MODULUS 64, REMAINDER %s);', i, i);
  END LOOP;
END $$;
COMMENT ON TABLE observation_evidence IS
  'Many-to-many evidence links claims ⇄ observations (D2/D43), mirroring relation_evidence. Corpus redundancy collapses into observations.evidence_count. HASH(observation_id), 64 static partitions; PRIMARY KEY (observation_id, claim_id) enforces evidence-once. Logical FKs (D23).';
CREATE INDEX ix_obsevidence_claim ON observation_evidence (claim_id);  -- reverse lookup (all-partition scan, cold path)

-- ─────────────────────────────────────────────────────────────────────────
-- observation_adjudications — append-only supersession/contradiction transcript (D3/D4), mirroring
-- relation_adjudications. Records WHY an observation's window closed / a contradiction was flagged, by
-- cascade rung + confidence; makes the non-deterministic adjudication replayable on rebuild (D7).
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE observation_adjudications (
  adjudication_id uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  observation_id  uuid NOT NULL,               -- the observation acted upon (composite FK below)
  related_observation_id uuid,                 -- the other observation in a supersede/contradict pair, if any (composite FK below)
  outcome         adjudication_outcome NOT NULL, -- add | noop | supersede | contradict | same_as_merge_proposal (D4)
  method          adjudication_method NOT NULL,  -- novelty_gate | exact | fuzzy | embedding | small_model | frontier_llm (cheap-first cascade, D4)
  confidence      real,
  triggering_claim_id uuid,                     -- LOGICAL FK → claims; the new claim that triggered adjudication
  features        jsonb,                        -- scores/rationale (audit); scrubbed on hard-forget (§13)
  adjudicator_version text NOT NULL,            -- LOGICAL FK → pipeline_component_versions
  decided_by      decision_actor NOT NULL DEFAULT 'auto',
  decided_at      timestamptz NOT NULL DEFAULT now(),
  superseded_by   uuid REFERENCES observation_adjudications, -- a later adjudication that overrode this one
  FOREIGN KEY (deployment_id, observation_id)         REFERENCES observations (deployment_id, observation_id),
  FOREIGN KEY (deployment_id, related_observation_id) REFERENCES observations (deployment_id, observation_id)
);
COMMENT ON TABLE observation_adjudications IS
  'Append-only supersession/contradiction decision log for observations (D3/D4/D43), mirroring relation_adjudications. Explains every window closure / contradiction flag by cascade rung + evidence; replayed on rebuild and used for "what did we believe at T / why" audits.';
CREATE INDEX ix_obsadjud_observation ON observation_adjudications (observation_id);
CREATE INDEX ix_obsadjud_live        ON observation_adjudications (observation_id) WHERE superseded_by IS NULL;
"""
_TABLES = (
    "claims",
    "claim_extraction_decisions",
    "grounding_audits",
    "testimony_currency_events",
    "relations",
    "relation_evidence",
    "relation_adjudications",
    "observations",
    "observation_evidence",
    "observation_adjudications",
)


def upgrade() -> None:
    """Apply create claim, relation, observation, and evidence structures."""
    apply_ddl(sql=_DDL)


def downgrade() -> None:
    """Revert create claim, relation, observation, and evidence structures."""
    drop_tables(table_names=reversed(_TABLES))
