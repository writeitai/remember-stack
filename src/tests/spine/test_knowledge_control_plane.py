"""WP-6.1 acceptance: live K control plane, routing, and exact staleness."""

from collections.abc import Iterator
from datetime import datetime
from datetime import UTC
from decimal import Decimal
import json
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.core import knowledge_content_hash
from rememberstack.core import knowledge_inputs_hash
from rememberstack.core import knowledge_summary_hash
from rememberstack.core import validate_knowledge_page_output
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DocSetRuleParams
from rememberstack.model import EntityRuleParams
from rememberstack.model import EntitySubtreeRuleParams
from rememberstack.model import KnowledgeAdjustRuleProposal
from rememberstack.model import KnowledgeAgentSessionResult
from rememberstack.model import KnowledgeArtifactCreate
from rememberstack.model import KnowledgeAuthoredDeclaration
from rememberstack.model import KnowledgeAuthoredPageSync
from rememberstack.model import KnowledgeAuthoredReviewReason
from rememberstack.model import KnowledgeCitation
from rememberstack.model import KnowledgeCompilationFailure
from rememberstack.model import KnowledgeCompilationWrite
from rememberstack.model import KnowledgeCompileContext
from rememberstack.model import KnowledgeConvertKindProposal
from rememberstack.model import KnowledgeCreatePageProposal
from rememberstack.model import KnowledgeDispatchStatus
from rememberstack.model import KnowledgeEvidenceDelta
from rememberstack.model import KnowledgeEvidenceRole
from rememberstack.model import KnowledgeEvidenceTarget
from rememberstack.model import KnowledgeLayer
from rememberstack.model import KnowledgeMergePagesProposal
from rememberstack.model import KnowledgeOrphanAggregate
from rememberstack.model import KnowledgePageCompileRequest
from rememberstack.model import KnowledgePageKind
from rememberstack.model import KnowledgePageRuleCreate
from rememberstack.model import KnowledgePlanAction
from rememberstack.model import KnowledgePlanBand
from rememberstack.model import KnowledgePlanDecisionCreate
from rememberstack.model import KnowledgePlannedPage
from rememberstack.model import KnowledgePlannerSessionRequest
from rememberstack.model import KnowledgePlanningSnapshot
from rememberstack.model import KnowledgePlanRunKind
from rememberstack.model import KnowledgePlanRunStatus
from rememberstack.model import KnowledgePlanRunWrite
from rememberstack.model import KnowledgePlanStatus
from rememberstack.model import KnowledgePlanTrigger
from rememberstack.model import KnowledgeQuarantineStatus
from rememberstack.model import KnowledgeRetirePageProposal
from rememberstack.model import KnowledgeSplitPageProposal
from rememberstack.model import KnowledgeSubscriptionCreate
from rememberstack.model import KnowledgeWorkflowDelivery
from rememberstack.model import KnowledgeWriterSessionRequest
from rememberstack.model import KnowledgeWriterSessionResult
from rememberstack.model import KnowledgeWriterSuggestion
from rememberstack.model import ManualRuleParams
from rememberstack.model import ObjectKey
from rememberstack.model import PipelineStage
from rememberstack.model import PredicateBeatRuleParams
from rememberstack.model import PublishedMounts
from rememberstack.model import RunResultOutcome
from rememberstack.model import ScopeInterestsRuleParams
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import KnowledgeCompilationError
from rememberstack.spine import KnowledgeControlPlane
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import KNOWLEDGE_FACT_SHEET_VERSION
from rememberstack.workers import KnowledgeDispatchHandler
from rememberstack.workers import KnowledgeFactSheetCompiler
from rememberstack.workers import KnowledgePageCompilerRouter
from rememberstack.workers import KnowledgePlannerError
from rememberstack.workers import KnowledgePlannerSettings
from rememberstack.workers import KnowledgePlannerWorker
from rememberstack.workers import KnowledgeProseCompiler
from rememberstack.workers import KnowledgeRoutingDriver
from rememberstack.workers import KnowledgeWriterError
from rememberstack.workers import KnowledgeWriterSettings
from rememberstack.workers import Worker

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("61000000-0000-0000-0000-000000000001")
_NOW = datetime(2026, 7, 20, tzinfo=UTC)


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for real Plane-K proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def corpus(database_engine: Engine) -> "_Corpus":
    """Give each proof a fresh deployment and compact cross-layer corpus."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="k-control",
            name="K control proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
            knowledge_repo_uri="mem://knowledge.git",
        )
    )
    return _Corpus(engine=database_engine)


class _Corpus:
    """Small authoritative state that distinguishes every routing-rule kind."""

    def __init__(self, *, engine: Engine) -> None:
        """Seed entities, facts, stable claim coordinates, and scope."""
        self.engine = engine
        self.control = KnowledgeControlPlane(engine=engine)
        self.driver = KnowledgeRoutingDriver(control_plane=self.control)
        self.scope_id = uuid4()
        self.entities = {
            "root": uuid4(),
            "child": uuid4(),
            "acme": uuid4(),
            "outside": uuid4(),
        }
        self.docs = {"drive": uuid4(), "email": uuid4()}
        self.chunks = {"drive": uuid4(), "email": uuid4()}
        self.claims = {"drive": uuid4(), "email": uuid4()}
        self.relations = {"part_of": uuid4(), "root": uuid4(), "outside": uuid4()}
        self.observations = {"root": uuid4(), "child": uuid4()}
        with engine.begin() as connection:
            self._seed_registry(connection=connection)
            self._seed_evidence(connection=connection)

    def page(
        self,
        *,
        params: object,
        slug: str,
        page_kind: KnowledgePageKind = KnowledgePageKind.COMPILED,
        artifact_kind: str | None = None,
    ) -> UUID:
        """Register one artifact and its typed, plan-authorized rule."""
        artifact_id = uuid4()
        decision_id = uuid4()
        self.control.record_plan_decision(
            decision=KnowledgePlanDecisionCreate(
                decision_id=decision_id,
                deployment_id=_DEPLOYMENT_ID,
                action=KnowledgePlanAction.CREATE_PAGE,
                payload={"git_path": f"k/{slug}.md"},
                trigger=KnowledgePlanTrigger.HUMAN,
                planner_version="planner-test",
                status=KnowledgePlanStatus.APPLIED,
            )
        )
        self.control.create_artifact(
            artifact=KnowledgeArtifactCreate(
                artifact_id=artifact_id,
                deployment_id=_DEPLOYMENT_ID,
                layer=KnowledgeLayer.K1,
                page_kind=page_kind,
                git_path=f"k/{slug}.md",
                curation_path=(
                    f"k/{slug}.curation.md"
                    if page_kind is KnowledgePageKind.COMPILED
                    else None
                ),
                artifact_kind=artifact_kind,
                writer_version=(
                    "writer-test" if page_kind is KnowledgePageKind.COMPILED else None
                ),
            )
        )
        self.control.add_page_rule(
            rule=KnowledgePageRuleCreate.model_validate(
                {
                    "rule_id": uuid4(),
                    "deployment_id": _DEPLOYMENT_ID,
                    "artifact_id": artifact_id,
                    "plan_decision_id": decision_id,
                    "params": params,
                }
            )
        )
        return artifact_id

    def compile(self, *, artifact_id: UUID) -> KnowledgeCompileContext:
        """Record the artifact's current manifest as a successful compile."""
        context = KnowledgeCompileContext(
            curation_hash="curation-test", writer_version="writer-test"
        )
        snapshot = self.control.input_snapshot(artifact_id=artifact_id, context=context)
        compilation = KnowledgeCompilationWrite(
            compilation_id=uuid4(),
            deployment_id=_DEPLOYMENT_ID,
            artifact_id=artifact_id,
            inputs_hash=knowledge_inputs_hash(snapshot=snapshot),
            candidate_count=len(snapshot.facts) + len(snapshot.claims),
            uncited_count=len(snapshot.facts) + len(snapshot.claims),
            citations=(),
            writer_version="writer-test",
            page_summary="A compiled test page.",
            content_hash=knowledge_summary_hash(summary="content-test"),
        )
        self.control.record_pending_compilation(compilation=compilation)
        self.control.commit_compilation(
            compilation=compilation, git_commit=f"commit-{compilation.compilation_id}"
        )
        return context

    def _seed_registry(self, *, connection: Connection) -> None:
        """Seed the scope and canonical entities used by routing."""
        connection.execute(
            text(
                "INSERT INTO scopes (scope_id, deployment_id, slug, name, git_path)"
                " VALUES (:s, :d, 'work', 'Work', 'k/work')"
            ),
            {"s": self.scope_id, "d": _DEPLOYMENT_ID},
        )
        for name, entity_id, entity_type in (
            ("Root", self.entities["root"], "Project"),
            ("Child", self.entities["child"], "Project"),
            ("Acme", self.entities["acme"], "Organization"),
            ("Outside", self.entities["outside"], "Person"),
        ):
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id,"
                    " canonical_name, normalized_name)"
                    " VALUES (:e, :d, :n, lower(:n))"
                ),
                {"e": entity_id, "d": _DEPLOYMENT_ID, "t": entity_type, "n": name},
            )
        for interest_type, value in (
            ("predicate", "works_for"),
            ("keyword", "deadline"),
        ):
            connection.execute(
                text(
                    "INSERT INTO scope_interests (interest_id, deployment_id,"
                    " scope_id, interest_type, value)"
                    " VALUES (:i, :d, :s, :t, :v)"
                ),
                {
                    "i": uuid4(),
                    "d": _DEPLOYMENT_ID,
                    "s": self.scope_id,
                    "t": interest_type,
                    "v": value,
                },
            )

    def _seed_evidence(self, *, connection: Connection) -> None:
        """Seed two lineages, stable chunks/claims, facts, and entity mentions."""
        for source, source_kind in (("drive", "google_drive"), ("email", "email")):
            connection.execute(
                text(
                    "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                    " source_ref, origin, title)"
                    " VALUES (:doc, :d, :source, :ref, 'external', :title)"
                ),
                {
                    "doc": self.docs[source],
                    "d": _DEPLOYMENT_ID,
                    "source": source_kind,
                    "ref": f"{source}-ref",
                    "title": source,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO chunks (chunk_id, deployment_id, doc_id,"
                    " version_id, representation_id, ordinal, block_start, block_end,"
                    " chunk_content_hash, extraction_input_hash, char_start, char_end,"
                    " created_at) VALUES (:ch, :d, :doc, :v, :rep, 0, 0, 0,"
                    " :hash, :input, 0, 20, :at)"
                ),
                {
                    "ch": self.chunks[source],
                    "d": _DEPLOYMENT_ID,
                    "doc": self.docs[source],
                    "v": uuid4(),
                    "rep": uuid4(),
                    "hash": f"chunk-{source}",
                    "input": f"input-{source}",
                    "at": _NOW,
                },
            )
            claim_text = (
                "Root works for Acme."
                if source == "drive"
                else "Outside owns the deadline."
            )
            connection.execute(
                text(
                    "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                    " claim_text, source_span, char_start, char_end, anchor_ok,"
                    " window_membership_ok, extractor_version, ingested_at)"
                    " VALUES (:c, :d, :doc, :ch, :body, :body, 0, 20, true,"
                    " true, 'extractor-test', :at)"
                ),
                {
                    "c": self.claims[source],
                    "d": _DEPLOYMENT_ID,
                    "doc": self.docs[source],
                    "ch": self.chunks[source],
                    "body": claim_text,
                    "at": _NOW,
                },
            )
        self._seed_facts(connection=connection)
        self._seed_mention(
            connection=connection,
            claim_id=self.claims["drive"],
            doc_id=self.docs["drive"],
            entity_id=self.entities["root"],
            surface="Root",
        )
        self._seed_mention(
            connection=connection,
            claim_id=self.claims["email"],
            doc_id=self.docs["email"],
            entity_id=self.entities["outside"],
            surface="Outside",
        )

    def _seed_facts(self, *, connection: Connection) -> None:
        """Seed subtree, candidate, unrelated, and observation fact state."""
        for relation_id, subject, predicate, object_id, count in (
            (
                self.relations["part_of"],
                self.entities["child"],
                "part_of",
                self.entities["root"],
                1,
            ),
            (
                self.relations["root"],
                self.entities["root"],
                "works_for",
                self.entities["acme"],
                1,
            ),
            (
                self.relations["outside"],
                self.entities["outside"],
                "works_on",
                self.entities["acme"],
                1,
            ),
        ):
            connection.execute(
                text(
                    "INSERT INTO relations (relation_id, deployment_id,"
                    " subject_entity_id, predicate, object_entity_id,"
                    " normalizer_version, evidence_count, ingested_at)"
                    " VALUES (:r, :d, :s, :p, :o, 'normalizer-test', :count, :at)"
                ),
                {
                    "r": relation_id,
                    "d": _DEPLOYMENT_ID,
                    "s": subject,
                    "p": predicate,
                    "o": object_id,
                    "count": count,
                    "at": _NOW,
                },
            )
        for observation_id, entity_id, statement in (
            (self.observations["root"], self.entities["root"], "Root is active."),
            (self.observations["child"], self.entities["child"], "Child is active."),
        ):
            connection.execute(
                text(
                    "INSERT INTO observations (observation_id, deployment_id,"
                    " subject_entity_id, statement, normalizer_version,"
                    " evidence_count, ingested_at) VALUES (:o, :d, :e, :body,"
                    " 'normalizer-test', 1, :at)"
                ),
                {
                    "o": observation_id,
                    "d": _DEPLOYMENT_ID,
                    "e": entity_id,
                    "body": statement,
                    "at": _NOW,
                },
            )
        connection.execute(
            text(
                "INSERT INTO relation_evidence (deployment_id, relation_id, claim_id,"
                " doc_id, stance, normalizer_version) VALUES"
                " (:d, :r, :c, :doc, 'supports', 'normalizer-test')"
            ),
            {
                "d": _DEPLOYMENT_ID,
                "r": self.relations["root"],
                "c": self.claims["drive"],
                "doc": self.docs["drive"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO relation_evidence (deployment_id, relation_id, claim_id,"
                " doc_id, stance, normalizer_version) VALUES"
                " (:d, :r, :c, :doc, 'supports', 'normalizer-test')"
            ),
            {
                "d": _DEPLOYMENT_ID,
                "r": self.relations["outside"],
                "c": self.claims["email"],
                "doc": self.docs["email"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO observation_evidence (deployment_id, observation_id,"
                " claim_id, doc_id, stance, normalizer_version) VALUES"
                " (:d, :o, :c, :doc, 'supports', 'normalizer-test')"
            ),
            {
                "d": _DEPLOYMENT_ID,
                "o": self.observations["root"],
                "c": self.claims["drive"],
                "doc": self.docs["drive"],
            },
        )

    def _seed_mention(
        self,
        *,
        connection: Connection,
        claim_id: UUID,
        doc_id: UUID,
        entity_id: UUID,
        surface: str,
    ) -> None:
        """Attach one current claim mention to its resolved canonical entity."""
        mention_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO mentions (mention_id, deployment_id, surface_form,"
                " normalized_lemma, claim_id, doc_id, created_at)"
                " VALUES (:m, :d, :surface, lower(:surface), :c, :doc, :at)"
            ),
            {
                "m": mention_id,
                "d": _DEPLOYMENT_ID,
                "surface": surface,
                "c": claim_id,
                "doc": doc_id,
                "at": _NOW,
            },
        )
        connection.execute(
            text(
                "INSERT INTO resolution_decisions (decision_id, deployment_id,"
                " mention_id, entity_id, method, confidence, resolver_version,"
                " decided_at) VALUES (:x, :d, :m, :e, 'T3', 0.9,"
                " 'resolver-test', :at)"
            ),
            {
                "x": uuid4(),
                "d": _DEPLOYMENT_ID,
                "m": mention_id,
                "e": entity_id,
                "at": _NOW,
            },
        )


class _WriterSession:
    """Deterministic stock-harness fake returning only raw declared files."""

    def __init__(self, *, files: dict[str, str]) -> None:
        self.files = files
        self.requests: list[KnowledgeWriterSessionRequest] = []

    def run_session(
        self, *, request: KnowledgeWriterSessionRequest
    ) -> KnowledgeWriterSessionResult:
        """Record the sandbox request and return one complete raw transcript."""
        self.requests.append(request)
        return KnowledgeWriterSessionResult(
            session_id=request.session_id,
            exit_code=0,
            output_files=self.files,
            transcript='{"stdout":"writer trace","stderr":""}',
            tokens=42,
            cost_usd=0.125,
        )


class _PlannerSession:
    """Deterministic decisions-only stock-harness fake."""

    def __init__(self, *, decisions_json: str) -> None:
        self.decisions_json = decisions_json
        self.requests: list[KnowledgePlannerSessionRequest] = []

    def run_session(
        self, *, request: KnowledgePlannerSessionRequest
    ) -> KnowledgeAgentSessionResult:
        """Return one declared decision file and an auditable raw transcript."""
        self.requests.append(request)
        return KnowledgeAgentSessionResult(
            session_id=request.session_id,
            exit_code=0,
            output_files={"output/decisions.json": self.decisions_json},
            transcript='{"stdout":"planner trace","stderr":""}',
            tokens=23,
            cost_usd=0.25,
        )


class _TranscriptStore:
    """Immutable in-memory store proving archive-before-parse ordering."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def read_bytes(self, *, key: ObjectKey) -> bytes:
        """Return one previously archived session transcript."""
        return self.objects[key.root]

    def open_stream(
        self, *, key: ObjectKey, chunk_bytes: int = 1024 * 1024
    ) -> Iterator[bytes]:
        """Yield the stored bytes in fixed-size chunks."""
        content = self.read_bytes(key=key)
        for offset in range(0, len(content), chunk_bytes):
            yield content[offset : offset + chunk_bytes]

    def read_range(self, *, key: ObjectKey, start: int, end: int) -> bytes:
        """Return the half-open byte interval of the stored bytes."""
        return self.read_bytes(key=key)[start:end]

    def write_bytes(
        self, *, key: ObjectKey, content: bytes, storage_class: str | None = None
    ) -> None:
        """Archive one immutable transcript object."""
        if key.root in self.objects:
            raise FileExistsError(key.root)
        self.objects[key.root] = content


class _MountPublisher:
    """Return the exact four read-only memory locators for one deployment."""

    def publish(self, *, deployment_id: UUID) -> PublishedMounts:
        """Publish portable locators without granting writer-side mutations."""
        return PublishedMounts(
            deployment_id=deployment_id,
            p3="mount://p3",
            artifacts="mount://artifacts",
            raw="mount://raw",
            knowledge="mount://knowledge",
            read_only=True,
        )


def _writer_settings() -> KnowledgeWriterSettings:
    """Return explicit WP-6.4 settings with no hidden cap or model defaults."""
    return KnowledgeWriterSettings(
        model="writer-model-test",
        timeout_seconds=60,
        residue_claim_limit=5,
        evidence_claims_per_fact=3,
        transcript_prefix="k-writer-transcripts",
    )


def _planner_settings() -> KnowledgePlannerSettings:
    """Return explicit cross-family planner settings with no hidden policy defaults."""
    return KnowledgePlannerSettings(
        planner_model="producer-test",
        planner_model_family="openai",
        reflection_model="checker-test",
        reflection_model_family="xai",
        timeout_seconds=60,
        auto_apply_max_expected_impact=Decimal("0"),
        transcript_prefix="k-planner-transcripts",
    )


def _writer_files(*, lineage_id: UUID, chunk_content_hash: str) -> dict[str, str]:
    """Return a valid raw prose/citation/summary/suggestion output set."""
    return {
        "output/prose.md": "# Root\n\nRoot is an active project.\n",
        "output/citations.json": json.dumps(
            [
                {
                    "role": "supports",
                    "claim_lineage_id": str(lineage_id),
                    "claim_chunk_content_hash": chunk_content_hash,
                }
            ]
        ),
        "output/summary.md": "Root is active. It works with Acme.",
        "output/suggestions.json": json.dumps(
            [
                {
                    "action": "split_page",
                    "rationale": "A longer history may deserve its own page.",
                    "payload": {"topic": "history"},
                }
            ]
        ),
    }


def test_all_six_rules_materialize_and_evaluate(corpus: _Corpus) -> None:
    """Every closed D45 rule kind has typed params and exact candidate SQL."""
    pages = {
        "entity": corpus.page(
            params=EntityRuleParams(entity_id=corpus.entities["root"]), slug="entity"
        ),
        "subtree": corpus.page(
            params=EntitySubtreeRuleParams(root_entity_id=corpus.entities["root"]),
            slug="subtree",
        ),
        "predicate": corpus.page(
            params=PredicateBeatRuleParams(predicate="works_for"), slug="predicate"
        ),
        "doc_set": corpus.page(
            params=DocSetRuleParams(source_kind="google_drive"), slug="doc-set"
        ),
        "scope": corpus.page(
            params=ScopeInterestsRuleParams(scope_id=corpus.scope_id), slug="scope"
        ),
        "manual": corpus.page(
            params=ManualRuleParams(
                entity_ids=(corpus.entities["outside"],),
                relation_ids=(corpus.relations["outside"],),
                observation_ids=(corpus.observations["root"],),
                claim_ids=(corpus.claims["email"],),
            ),
            slug="manual",
        ),
    }
    context = KnowledgeCompileContext(writer_version="writer-test")
    snapshots = {
        name: corpus.control.input_snapshot(artifact_id=page, context=context)
        for name, page in pages.items()
    }
    assert corpus.relations["root"] in {
        fact.fact_id for fact in snapshots["entity"].facts
    }
    assert corpus.observations["child"] in {
        fact.fact_id for fact in snapshots["subtree"].facts
    }
    assert {fact.fact_id for fact in snapshots["predicate"].facts} == {
        corpus.relations["root"]
    }
    assert corpus.relations["root"] in {
        fact.fact_id for fact in snapshots["doc_set"].facts
    }
    assert corpus.relations["root"] in {
        fact.fact_id for fact in snapshots["scope"].facts
    }
    assert corpus.relations["outside"] in {
        fact.fact_id for fact in snapshots["scope"].facts
    }
    assert corpus.relations["outside"] in {
        fact.fact_id for fact in snapshots["manual"].facts
    }
    assert corpus.observations["root"] in {
        fact.fact_id for fact in snapshots["manual"].facts
    }
    with corpus.engine.connect() as connection:
        keys = {
            (row["rule_kind"], row["key_kind"], row["key_value"])
            for row in connection.execute(
                text(
                    "SELECT r.rule_kind::text, k.key_kind::text, k.key_value"
                    " FROM knowledge_rule_keys k"
                    " JOIN knowledge_page_rules r ON r.rule_id = k.rule_id"
                )
            ).mappings()
        }
    assert ("entity", "entity", str(corpus.entities["root"])) in keys
    assert ("entity_subtree", "entity", str(corpus.entities["child"])) in keys
    assert ("predicate_beat", "predicate", "works_for") in keys
    assert ("doc_set", "doc_source", "google_drive") in keys
    assert ("scope_interests", "predicate", "works_for") in keys
    assert ("manual", "entity", str(corpus.entities["outside"])) in keys


def test_scope_entity_type_interest_fails_closed(corpus: _Corpus) -> None:
    """A stale typed interest cannot silently widen a D96 scope selection."""
    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO scope_interests (interest_id, deployment_id, scope_id,"
                " interest_type, value) VALUES"
                " (:interest, :deployment, :scope, 'entity_type', 'Person')"
            ),
            {
                "interest": uuid4(),
                "deployment": _DEPLOYMENT_ID,
                "scope": corpus.scope_id,
            },
        )

    with pytest.raises(
        KnowledgeCompilationError,
        match="scope interest entity_type is unsupported after D96",
    ):
        corpus.page(
            params=ScopeInterestsRuleParams(scope_id=corpus.scope_id),
            slug="stale-typed-scope",
        )


def test_stale_set_is_exact_and_claim_reextraction_is_stable(corpus: _Corpus) -> None:
    """Only candidate-state drift stales; raw claim churn at one coordinate does not."""
    page = corpus.page(
        params=EntityRuleParams(entity_id=corpus.entities["root"]), slug="stale-exact"
    )
    doc_page = corpus.page(
        params=DocSetRuleParams(source_kind="google_drive"), slug="stale-doc-set"
    )
    authored_page = corpus.page(
        params=EntityRuleParams(entity_id=corpus.entities["root"]),
        slug="authored",
        page_kind=KnowledgePageKind.AUTHORED,
    )
    context = corpus.compile(artifact_id=page)
    doc_context = corpus.compile(artifact_id=doc_page)
    contexts = {page: context, doc_page: doc_context}

    with corpus.engine.begin() as connection:
        connection.execute(
            text("UPDATE relations SET evidence_count = 7 WHERE relation_id = :r"),
            {"r": corpus.relations["outside"]},
        )
    assert (
        corpus.driver.mark_all_manifest_drift(
            deployment_id=_DEPLOYMENT_ID, contexts=contexts
        )
        == ()
    )

    replacement_claim = uuid4()
    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, extractor_version, ingested_at)"
                " VALUES (:c, :d, :doc, :ch, 'rewritten extraction',"
                " 'rewritten extraction', 0, 20, true, true, 'extractor-new', :at)"
            ),
            {
                "c": replacement_claim,
                "d": _DEPLOYMENT_ID,
                "doc": corpus.docs["drive"],
                "ch": corpus.chunks["drive"],
                "at": _NOW,
            },
        )
        corpus._seed_mention(
            connection=connection,
            claim_id=replacement_claim,
            doc_id=corpus.docs["drive"],
            entity_id=corpus.entities["root"],
            surface="Root",
        )
    assert (
        corpus.driver.mark_all_manifest_drift(
            deployment_id=_DEPLOYMENT_ID, contexts=contexts
        )
        == ()
    )

    with corpus.engine.begin() as connection:
        connection.execute(
            text("UPDATE relations SET evidence_count = 3 WHERE relation_id = :r"),
            {"r": corpus.relations["root"]},
        )
    assert set(
        corpus.driver.route_and_mark_stale(
            deployment_id=_DEPLOYMENT_ID,
            delta=KnowledgeEvidenceDelta(relation_ids=(corpus.relations["root"],)),
            contexts=contexts,
        )
    ) == {page, doc_page}
    assert (
        corpus.control.authored_review_state(artifact_id=authored_page).open_flag_count
        == 1
    )
    context = corpus.compile(artifact_id=page)
    doc_context = corpus.compile(artifact_id=doc_page)

    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE observations SET valid_until = '2026-07-19+00'"
                " WHERE observation_id = :o"
            ),
            {"o": corpus.observations["root"]},
        )
    assert set(
        corpus.driver.route_and_mark_stale(
            deployment_id=_DEPLOYMENT_ID,
            delta=KnowledgeEvidenceDelta(
                observation_ids=(corpus.observations["root"],)
            ),
            contexts={page: context, doc_page: doc_context},
        )
    ) == {page, doc_page}


def test_subtree_membership_rematerializes_before_routing(corpus: _Corpus) -> None:
    """A new part_of edge expands keys and stales the affected subtree page."""
    page = corpus.page(
        params=EntitySubtreeRuleParams(root_entity_id=corpus.entities["root"]),
        slug="subtree-refresh",
    )
    context = corpus.compile(artifact_id=page)
    grandchild = uuid4()
    membership = uuid4()
    observation = uuid4()
    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id,"
                " canonical_name, normalized_name) VALUES"
                " (:e, :d, 'Grandchild', 'grandchild')"
            ),
            {"e": grandchild, "d": _DEPLOYMENT_ID},
        )
        connection.execute(
            text(
                "INSERT INTO relations (relation_id, deployment_id,"
                " subject_entity_id, predicate, object_entity_id,"
                " normalizer_version, evidence_count) VALUES"
                " (:r, :d, :s, 'part_of', :o, 'normalizer-test', 1)"
            ),
            {
                "r": membership,
                "d": _DEPLOYMENT_ID,
                "s": grandchild,
                "o": corpus.entities["child"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO observations (observation_id, deployment_id,"
                " subject_entity_id, statement, normalizer_version, evidence_count)"
                " VALUES (:o, :d, :e, 'Grandchild is new.', 'normalizer-test', 1)"
            ),
            {"o": observation, "d": _DEPLOYMENT_ID, "e": grandchild},
        )
    assert corpus.driver.route_and_mark_stale(
        deployment_id=_DEPLOYMENT_ID,
        delta=KnowledgeEvidenceDelta(relation_ids=(membership,)),
        contexts={page: context},
    ) == (page,)
    with corpus.engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM knowledge_rule_keys k"
                " JOIN knowledge_page_rules r ON r.rule_id = k.rule_id"
                " WHERE r.artifact_id = :a AND k.key_kind = 'entity'"
                " AND k.key_value = :e)"
            ),
            {"a": page, "e": str(grandchild)},
        ).scalar_one()


def test_new_rule_immediately_stales_a_compiled_owner(corpus: _Corpus) -> None:
    """A rule-config trigger cannot leave its previously compiled owner active."""
    page = corpus.page(
        params=EntityRuleParams(entity_id=corpus.entities["root"]),
        slug="rule-adjustment",
    )
    corpus.compile(artifact_id=page)
    decision_id = uuid4()
    corpus.control.record_plan_decision(
        decision=KnowledgePlanDecisionCreate(
            decision_id=decision_id,
            deployment_id=_DEPLOYMENT_ID,
            action=KnowledgePlanAction.ADJUST_RULE,
            payload={"artifact_id": str(page)},
            trigger=KnowledgePlanTrigger.HUMAN,
            planner_version="planner-test",
            status=KnowledgePlanStatus.APPLIED,
        )
    )
    corpus.control.add_page_rule(
        rule=KnowledgePageRuleCreate(
            rule_id=uuid4(),
            deployment_id=_DEPLOYMENT_ID,
            artifact_id=page,
            plan_decision_id=decision_id,
            params=PredicateBeatRuleParams(predicate="works_on"),
        )
    )
    with corpus.engine.connect() as connection:
        status = connection.execute(
            text("SELECT status::text FROM knowledge_artifacts WHERE artifact_id = :a"),
            {"a": page},
        ).scalar_one()
    assert status == "stale"


def test_fact_sheet_compiler_renders_exact_rule_candidates(corpus: _Corpus) -> None:
    """The zero-LLM page is the exact selected fact set with literal lifecycle."""
    page = corpus.page(
        params=EntityRuleParams(entity_id=corpus.entities["root"]),
        slug="root-facts",
        artifact_kind="fact_sheet",
    )
    contradiction_group = uuid4()
    conflicting = (uuid4(), uuid4())
    invalidated = uuid4()
    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE relations SET fact_label = 'Root formerly worked for Acme',"
                " valid_from = '2024-01-01+00', valid_until = '2025-01-01+00'"
                " WHERE relation_id = :relation_id"
            ),
            {"relation_id": corpus.relations["root"]},
        )
        connection.execute(
            text(
                "UPDATE observations SET obs_label = 'Root was active',"
                " valid_from = '2024-01-01+00', valid_until = '2025-01-01+00'"
                " WHERE observation_id = :observation_id"
            ),
            {"observation_id": corpus.observations["root"]},
        )
        for observation_id, label in zip(
            conflicting,
            ("FY2025 revenue was EUR 5M", "FY2025 revenue was EUR 7M"),
            strict=True,
        ):
            connection.execute(
                text(
                    "INSERT INTO observations (observation_id, deployment_id,"
                    " subject_entity_id, statement, obs_label, valid_from,"
                    " ingested_at, normalizer_version, evidence_count,"
                    " contradict_count, contradiction_group) VALUES"
                    " (:observation_id, :deployment_id, :entity_id, :label, :label,"
                    " '2025-01-02+00', :ingested_at, 'normalizer-test', 1, 1,"
                    " :contradiction_group)"
                ),
                {
                    "observation_id": observation_id,
                    "deployment_id": _DEPLOYMENT_ID,
                    "entity_id": corpus.entities["root"],
                    "label": label,
                    "ingested_at": _NOW,
                    "contradiction_group": contradiction_group,
                },
            )
        connection.execute(
            text(
                "INSERT INTO observations (observation_id, deployment_id,"
                " subject_entity_id, statement, ingested_at, invalidated_at,"
                " normalizer_version, evidence_count) VALUES"
                " (:observation_id, :deployment_id, :entity_id,"
                " 'Retracted root estimate', :ingested_at, :ingested_at,"
                " 'normalizer-test', 1)"
            ),
            {
                "observation_id": invalidated,
                "deployment_id": _DEPLOYMENT_ID,
                "entity_id": corpus.entities["root"],
                "ingested_at": _NOW,
            },
        )

    context = KnowledgeCompileContext(
        curation_hash="curation-fact-sheet", writer_version=KNOWLEDGE_FACT_SHEET_VERSION
    )
    snapshot = corpus.control.fact_sheet_snapshot(
        artifact_id=page, context=context, child_summary_hashes=()
    )
    selected = {(fact.kind, fact.fact_id) for fact in snapshot.input_snapshot.facts}
    hydrated = {(fact.kind, fact.fact_id) for fact in snapshot.facts}
    assert hydrated == selected

    artifact = next(
        item
        for item in corpus.control.compile_artifacts(deployment_id=_DEPLOYMENT_ID)
        if item.artifact_id == page
    )
    output = KnowledgeFactSheetCompiler(
        control_plane=corpus.control, clock=lambda: _NOW
    ).compile_page(
        request=KnowledgePageCompileRequest(
            artifact=artifact, curation_hash=context.curation_hash
        )
    )

    assert output.compilation.inputs_hash == knowledge_inputs_hash(
        snapshot=snapshot.input_snapshot
    )
    assert output.compilation.candidate_count == len(selected) + len(
        snapshot.input_snapshot.claims
    )
    assert output.compilation.uncited_count == output.compilation.candidate_count
    assert output.compilation.citations == ()
    assert output.compilation.content_hash == knowledge_content_hash(
        markdown=output.markdown
    )
    assert f"relation:{corpus.relations['part_of']}" in output.markdown
    assert f"relation:{corpus.relations['root']}" not in output.markdown
    assert "Root was active" in output.markdown
    assert "| ended |" in output.markdown
    assert "Retracted root estimate" in output.markdown
    assert "| invalidated |" in output.markdown
    assert f"`{contradiction_group}`" in output.markdown
    assert all(str(observation_id) in output.markdown for observation_id in conflicting)
    validate_knowledge_page_output(
        artifact=artifact,
        output=output,
        known_git_paths=corpus.control.artifact_git_paths(deployment_id=_DEPLOYMENT_ID),
        exclusions=(),
    )

    exclusion = KnowledgeEvidenceTarget(relation_id=corpus.relations["part_of"])
    excluded_output = KnowledgeFactSheetCompiler(
        control_plane=corpus.control, clock=lambda: _NOW
    ).compile_page(
        request=KnowledgePageCompileRequest(
            artifact=artifact,
            curation_hash=context.curation_hash,
            exclusions=(exclusion,),
        )
    )
    assert f"relation:{corpus.relations['part_of']}" not in excluded_output.markdown
    assert (
        excluded_output.compilation.candidate_count
        == output.compilation.candidate_count
    )
    validate_knowledge_page_output(
        artifact=artifact,
        output=excluded_output,
        known_git_paths=corpus.control.artifact_git_paths(deployment_id=_DEPLOYMENT_ID),
        exclusions=(exclusion,),
    )


def test_writer_bundle_hydrates_exact_d54_claims_and_selected_fact_edges(
    corpus: _Corpus,
) -> None:
    """One repeatable read joins stable coordinates to current bodies and fact support."""
    page = corpus.page(
        params=EntityRuleParams(entity_id=corpus.entities["root"]), slug="writer-bundle"
    )
    context = KnowledgeCompileContext(writer_version="writer-bundle-test")

    bundle = corpus.control.writer_bundle(
        artifact_id=page, context=context, child_summary_hashes=()
    )

    expected_coordinates = {
        (item.lineage_id, item.chunk_content_hash)
        for item in bundle.fact_sheet.input_snapshot.claims
    }
    hydrated_coordinates = {
        (item.lineage_id, item.chunk_content_hash) for item in bundle.claim_groups
    }
    assert hydrated_coordinates == expected_coordinates
    assert bundle.claim_candidate_count == len(expected_coordinates)
    assert bundle.claims_cut_count == 0
    drive_claim = next(
        claim
        for group in bundle.claim_groups
        for claim in group.claims
        if claim.claim_id == corpus.claims["drive"]
    )
    assert drive_claim.claim_text == "Root works for Acme."
    assert {
        (reference.kind, reference.fact_id, reference.stance)
        for reference in drive_claim.fact_references
    } == {
        ("relation", corpus.relations["root"], "supports"),
        ("observation", corpus.observations["root"], "supports"),
    }


def test_prose_compiler_archives_session_and_builds_mechanical_two_band_page(
    corpus: _Corpus,
) -> None:
    """Accepted prose is composed with a generated fact band and mechanical ledger."""
    page = corpus.page(
        params=EntityRuleParams(entity_id=corpus.entities["root"]),
        slug="writer-success",
    )
    artifact = next(
        item
        for item in corpus.control.compile_artifacts(deployment_id=_DEPLOYMENT_ID)
        if item.artifact_id == page
    )
    writer = _WriterSession(
        files=_writer_files(
            lineage_id=corpus.docs["drive"], chunk_content_hash="chunk-drive"
        )
    )
    transcripts = _TranscriptStore()
    compiler = KnowledgeProseCompiler(
        control_plane=corpus.control,
        writer_session=writer,
        transcript_store=transcripts,
        mount_publisher=_MountPublisher(),
        settings=_writer_settings(),
        clock=lambda: _NOW,
    )

    output = compiler.compile_page(
        request=KnowledgePageCompileRequest(
            artifact=artifact, previous_markdown="# Previous Root\n"
        )
    )

    assert output.markdown.startswith("# Root\n\nRoot is an active project.")
    assert "\n---\n## Fact sheet (generated)" in output.markdown
    assert output.markdown.count("## Fact sheet (generated)") == 1
    assert output.compilation.content_hash == knowledge_content_hash(
        markdown=output.markdown
    )
    assert output.compilation.citations == (
        KnowledgeCitation(
            role=KnowledgeEvidenceRole.SUPPORTS,
            claim_lineage_id=corpus.docs["drive"],
            claim_chunk_content_hash="chunk-drive",
        ),
    )
    assert output.compilation.candidate_count > 0
    assert 0 <= output.compilation.uncited_count < output.compilation.candidate_count
    assert f"{output.compilation.candidate_count} candidates" in output.markdown
    assert output.compilation.suggestions[0].action is KnowledgePlanAction.SPLIT_PAGE
    assert output.compilation.tokens == 42
    assert output.compilation.cost_usd == 0.125
    transcript_uri = output.compilation.session_transcript_uri
    assert transcript_uri is not None
    assert (
        transcripts.objects[transcript_uri] == b'{"stdout":"writer trace","stderr":""}'
    )
    assert len(writer.requests) == 1
    request = writer.requests[0]
    assert request.sandbox.network_access == "none"
    assert request.sandbox.memory_access == "read_only"
    assert request.sandbox.repository_write_access is False
    assert request.input_files["context/previous_page.md"] == "# Previous Root\n"
    assert set(request.mounts.model_fields_set) == {
        "deployment_id",
        "p3",
        "artifacts",
        "raw",
        "knowledge",
        "read_only",
    }
    validate_knowledge_page_output(
        artifact=artifact,
        output=output,
        known_git_paths=corpus.control.artifact_git_paths(deployment_id=_DEPLOYMENT_ID),
        exclusions=(),
    )


def test_malformed_writer_output_keeps_archived_transcript_and_failure_row(
    corpus: _Corpus,
) -> None:
    """Parsing happens after transcript archival and rejection is durable in Postgres."""
    page = corpus.page(
        params=EntityRuleParams(entity_id=corpus.entities["root"]),
        slug="writer-malformed",
    )
    artifact = next(
        item
        for item in corpus.control.compile_artifacts(deployment_id=_DEPLOYMENT_ID)
        if item.artifact_id == page
    )
    files = _writer_files(
        lineage_id=corpus.docs["drive"], chunk_content_hash="chunk-drive"
    )
    files["output/citations.json"] = "{malformed"
    writer = _WriterSession(files=files)
    transcripts = _TranscriptStore()
    compiler = KnowledgeProseCompiler(
        control_plane=corpus.control,
        writer_session=writer,
        transcript_store=transcripts,
        mount_publisher=_MountPublisher(),
        settings=_writer_settings(),
        clock=lambda: _NOW,
    )

    with pytest.raises(KnowledgeWriterError, match="JSON output"):
        compiler.compile_page(request=KnowledgePageCompileRequest(artifact=artifact))

    assert len(transcripts.objects) == 1
    transcript_uri = next(iter(transcripts.objects))
    with corpus.engine.connect() as connection:
        failure = (
            connection.execute(
                text(
                    "SELECT session_transcript_uri, failed_at, failure, git_commit"
                    " FROM knowledge_compilations WHERE artifact_id = :a"
                ),
                {"a": page},
            )
            .mappings()
            .one()
        )
    assert failure["session_transcript_uri"] == transcript_uri
    assert failure["failed_at"] is not None
    assert "KnowledgeWriterError" in failure["failure"]
    assert failure["git_commit"] is None


def test_writer_and_failure_ledger_errors_remain_visible_together(
    corpus: _Corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ledger outage cannot replace the writer error that triggered its trace."""
    page = corpus.page(
        params=EntityRuleParams(entity_id=corpus.entities["root"]),
        slug="writer-double-failure",
    )
    artifact = next(
        item
        for item in corpus.control.compile_artifacts(deployment_id=_DEPLOYMENT_ID)
        if item.artifact_id == page
    )
    files = _writer_files(
        lineage_id=corpus.docs["drive"], chunk_content_hash="chunk-drive"
    )
    files["output/citations.json"] = "{malformed"
    transcripts = _TranscriptStore()
    compiler = KnowledgeProseCompiler(
        control_plane=corpus.control,
        writer_session=_WriterSession(files=files),
        transcript_store=transcripts,
        mount_publisher=_MountPublisher(),
        settings=_writer_settings(),
        clock=lambda: _NOW,
    )

    def fail_ledger(*, failure: KnowledgeCompilationFailure) -> None:
        raise RuntimeError(f"ledger unavailable for {failure.compilation_id}")

    monkeypatch.setattr(corpus.control, "record_failed_compilation", fail_ledger)

    with pytest.raises(ExceptionGroup) as captured:
        compiler.compile_page(request=KnowledgePageCompileRequest(artifact=artifact))

    assert any(
        isinstance(error, KnowledgeWriterError) for error in captured.value.exceptions
    )
    assert any(isinstance(error, RuntimeError) for error in captured.value.exceptions)
    assert len(transcripts.objects) == 1


def test_fact_sheet_router_skips_writer_session_entirely(corpus: _Corpus) -> None:
    """Low-importance fact-sheet pages consume no writer session or transcript."""
    page = corpus.page(
        params=EntityRuleParams(entity_id=corpus.entities["root"]),
        slug="writer-skip",
        artifact_kind="fact_sheet",
    )
    artifact = next(
        item
        for item in corpus.control.compile_artifacts(deployment_id=_DEPLOYMENT_ID)
        if item.artifact_id == page
    )
    writer = _WriterSession(files={})
    transcripts = _TranscriptStore()
    prose = KnowledgeProseCompiler(
        control_plane=corpus.control,
        writer_session=writer,
        transcript_store=transcripts,
        mount_publisher=_MountPublisher(),
        settings=_writer_settings(),
        clock=lambda: _NOW,
    )
    router = KnowledgePageCompilerRouter(
        fact_sheet_compiler=KnowledgeFactSheetCompiler(
            control_plane=corpus.control, clock=lambda: _NOW
        ),
        prose_compiler=prose,
    )

    output = router.compile_page(request=KnowledgePageCompileRequest(artifact=artifact))

    assert output.markdown.startswith("## Fact sheet (generated)")
    assert writer.requests == []
    assert transcripts.objects == {}


def test_failed_compilation_is_durable_without_mutating_live_page(
    corpus: _Corpus,
) -> None:
    """Rejected output leaves a terminal trace and no pending or live replacement."""
    page = corpus.page(
        params=EntityRuleParams(entity_id=corpus.entities["root"]),
        slug="writer-failure",
    )
    context = KnowledgeCompileContext(writer_version="writer-failure-test")
    snapshot = corpus.control.input_snapshot(artifact_id=page, context=context)
    failure = KnowledgeCompilationFailure(
        compilation_id=uuid4(),
        deployment_id=_DEPLOYMENT_ID,
        artifact_id=page,
        inputs_hash=knowledge_inputs_hash(snapshot=snapshot),
        candidate_count=len(snapshot.facts) + len(snapshot.claims),
        claims_cut_count=2,
        writer_version=context.writer_version,
        failure="writer returned malformed citation JSON",
        session_transcript_uri="transcripts/session.json",
    )

    corpus.control.record_failed_compilation(failure=failure)

    with corpus.engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT cycle_id, cited_count, uncited_count, claims_cut_count,"
                    " session_transcript_uri, git_commit, failed_at, failure"
                    " FROM knowledge_compilations WHERE compilation_id = :c"
                ),
                {"c": failure.compilation_id},
            )
            .mappings()
            .one()
        )
        artifact = (
            connection.execute(
                text(
                    "SELECT status::text AS status, inputs_hash, content_hash"
                    " FROM knowledge_artifacts WHERE artifact_id = :a"
                ),
                {"a": page},
            )
            .mappings()
            .one()
        )
    assert row["cycle_id"] is None
    assert row["cited_count"] == 0
    assert row["uncited_count"] == failure.candidate_count
    assert row["claims_cut_count"] == 2
    assert row["session_transcript_uri"] == "transcripts/session.json"
    assert row["git_commit"] is None
    assert row["failed_at"] is not None
    assert row["failure"] == failure.failure
    assert artifact == {"status": "stale", "inputs_hash": None, "content_hash": None}


def test_compilation_replaces_binding_citations_and_records_deltas(
    corpus: _Corpus,
) -> None:
    """Compilation, citations, counts, and artifact state commit atomically."""
    page = corpus.page(
        params=EntityRuleParams(entity_id=corpus.entities["root"]),
        slug="compile-ledger",
    )
    context = KnowledgeCompileContext(writer_version="writer-test")
    snapshot = corpus.control.input_snapshot(artifact_id=page, context=context)
    first = KnowledgeCompilationWrite(
        compilation_id=uuid4(),
        deployment_id=_DEPLOYMENT_ID,
        artifact_id=page,
        inputs_hash=knowledge_inputs_hash(snapshot=snapshot),
        candidate_count=len(snapshot.facts) + len(snapshot.claims),
        uncited_count=len(snapshot.facts) + len(snapshot.claims) - 2,
        citations=(
            KnowledgeCitation(
                role=KnowledgeEvidenceRole.SUPPORTS,
                relation_id=corpus.relations["root"],
            ),
            KnowledgeCitation(
                role=KnowledgeEvidenceRole.CITES,
                claim_lineage_id=corpus.docs["drive"],
                claim_chunk_content_hash="chunk-drive",
            ),
        ),
        writer_version="writer-test",
        page_summary="First summary.",
        content_hash=knowledge_summary_hash(summary="first-content"),
    )
    corpus.control.record_pending_compilation(compilation=first)
    corpus.control.commit_compilation(compilation=first, git_commit="commit-first")

    replacement_claim = uuid4()
    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, extractor_version, ingested_at)"
                " VALUES (:claim, :deployment, :doc, :chunk, 'replacement extraction',"
                " 'replacement extraction', 0, 20, true, true, 'extractor-new', :at)"
            ),
            {
                "claim": replacement_claim,
                "deployment": _DEPLOYMENT_ID,
                "doc": corpus.docs["drive"],
                "chunk": corpus.chunks["drive"],
                "at": _NOW,
            },
        )
    replacement_snapshot = corpus.control.input_snapshot(
        artifact_id=page, context=context
    )
    assert knowledge_inputs_hash(snapshot=replacement_snapshot) == first.inputs_hash
    assert corpus.control.route_delta(
        deployment_id=_DEPLOYMENT_ID,
        delta=KnowledgeEvidenceDelta(claim_ids=(replacement_claim,)),
    ) == (page,)

    second = first.model_copy(
        update={
            "compilation_id": uuid4(),
            "citations": (
                KnowledgeCitation(
                    role=KnowledgeEvidenceRole.CITES, doc_id=corpus.docs["drive"]
                ),
            ),
            "page_summary": "Second summary.",
            "content_hash": knowledge_summary_hash(summary="second-content"),
            "uncited_count": first.candidate_count - 1,
            "claims_cut_count": 3,
            "suggestions": (
                KnowledgeWriterSuggestion(
                    action=KnowledgePlanAction.SPLIT_PAGE,
                    rationale="The page now spans two independent topics.",
                    payload={"boundary": "timeline"},
                ),
            ),
        }
    )
    corpus.control.record_pending_compilation(compilation=second)
    with corpus.engine.connect() as connection:
        live_before_push = (
            connection.execute(
                text(
                    "SELECT claim_lineage_id, claim_chunk_content_hash,"
                    " relation_id, doc_id"
                    " FROM knowledge_artifact_evidence WHERE artifact_id = :a"
                    " ORDER BY claim_lineage_id NULLS LAST, relation_id NULLS LAST"
                ),
                {"a": page},
            )
            .mappings()
            .all()
        )
        pending_commit = connection.execute(
            text(
                "SELECT git_commit FROM knowledge_compilations"
                " WHERE compilation_id = :c"
            ),
            {"c": second.compilation_id},
        ).scalar_one()
    assert len(live_before_push) == 2
    assert all(row["doc_id"] is None for row in live_before_push)
    assert pending_commit is None
    corpus.control.commit_compilation(compilation=second, git_commit="commit-second")
    corpus.control.commit_compilation(compilation=second, git_commit="commit-second")
    with corpus.engine.connect() as connection:
        citations = (
            connection.execute(
                text(
                    "SELECT claim_lineage_id, claim_chunk_content_hash,"
                    " relation_id, doc_id"
                    " FROM knowledge_artifact_evidence WHERE artifact_id = :a"
                ),
                {"a": page},
            )
            .mappings()
            .all()
        )
        transcript = (
            connection.execute(
                text(
                    "SELECT cited_count, uncited_count, claims_cut_count, suggestions,"
                    " evidence_added, evidence_removed"
                    " FROM knowledge_compilations WHERE compilation_id = :c"
                ),
                {"c": second.compilation_id},
            )
            .mappings()
            .one()
        )
    assert citations == [
        {
            "claim_lineage_id": None,
            "claim_chunk_content_hash": None,
            "relation_id": None,
            "doc_id": corpus.docs["drive"],
        }
    ]
    assert transcript["cited_count"] == 1
    assert transcript["uncited_count"] == second.uncited_count
    assert transcript["claims_cut_count"] == 3
    assert transcript["suggestions"] == [
        {
            "action": "split_page",
            "rationale": "The page now spans two independent topics.",
            "payload": {"boundary": "timeline"},
        }
    ]
    assert transcript["evidence_added"] == 1
    assert transcript["evidence_removed"] == 2

    invalid = second.model_copy(
        update={
            "compilation_id": uuid4(),
            "citations": (
                KnowledgeCitation(role=KnowledgeEvidenceRole.CITES, doc_id=uuid4()),
            ),
        }
    )
    with pytest.raises(KnowledgeCompilationError):
        corpus.control.record_pending_compilation(compilation=invalid)


def _plan_run(
    *,
    run_kind: KnowledgePlanRunKind = KnowledgePlanRunKind.PLANNER,
    trigger: KnowledgePlanTrigger = KnowledgePlanTrigger.ORPHAN_EVIDENCE,
) -> KnowledgePlanRunWrite:
    """Build one successful transcript-bearing planner ledger row."""
    run_id = uuid4()
    return KnowledgePlanRunWrite(
        run_id=run_id,
        deployment_id=_DEPLOYMENT_ID,
        run_kind=run_kind,
        trigger=trigger,
        component_version="planner-test",
        input_hash=f"snapshot-{run_id}",
        session_transcript_uri=f"mem://planner/{run_id}.json",
        status=KnowledgePlanRunStatus.SUCCEEDED,
        tokens=17,
        cost_usd=Decimal("0.01"),
    )


def test_planner_run_routes_low_impact_create_and_applies_rules(
    corpus: _Corpus,
) -> None:
    """The driver, not the proposing session, applies a low-impact page decision."""
    proposal = KnowledgeCreatePageProposal(
        page=KnowledgePlannedPage(
            layer=KnowledgeLayer.K1,
            git_path="k/planned-acme.md",
            curation_path="k/planned-acme.curation.md",
            writer_version="writer-test",
            rules=(EntityRuleParams(entity_id=corpus.entities["acme"]),),
        ),
        rationale="Acme evidence needs a stable home.",
        confidence=Decimal("1"),
    )
    results = corpus.control.record_plan_proposals(
        run=_plan_run(),
        proposals=(proposal,),
        auto_apply_max_expected_impact=Decimal("0"),
    )
    assert len(results) == 1
    assert results[0].band is KnowledgePlanBand.AUTO_APPLY
    assert results[0].status is KnowledgePlanStatus.APPLIED
    assert results[0].blast_radius == 1
    assert results[0].expected_impact == Decimal("0")

    with corpus.engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT a.page_kind::text AS page_kind,"
                    " a.status::text AS status, r.rule_kind::text AS rule_kind,"
                    " d.application_commit, d.plan_run_id"
                    " FROM knowledge_artifacts a"
                    " JOIN knowledge_page_rules r ON r.artifact_id = a.artifact_id"
                    " JOIN knowledge_plan_decisions d"
                    "   ON d.decision_id = r.plan_decision_id"
                    " WHERE a.git_path = 'k/planned-acme.md'"
                )
            )
            .mappings()
            .one()
        )
    assert row["page_kind"] == "compiled"
    assert row["status"] == "stale"
    assert row["rule_kind"] == "entity"
    assert row["application_commit"] is None
    assert row["plan_run_id"] is not None


def test_driver_applies_every_planner_structure_action_transactionally(
    corpus: _Corpus,
) -> None:
    """Split, merge, retire, and rule replacement have explicit DB consequences."""
    split_source = corpus.page(
        params=EntityRuleParams(entity_id=corpus.entities["root"]), slug="split-source"
    )
    split = KnowledgeSplitPageProposal(
        source_artifact_id=split_source,
        pages=(
            KnowledgePlannedPage(
                layer=KnowledgeLayer.K1,
                git_path="k/split-a.md",
                curation_path="k/split-a.curation.md",
                writer_version="writer-test",
                parent_artifact_id=split_source,
                rules=(EntityRuleParams(entity_id=corpus.entities["root"]),),
            ),
            KnowledgePlannedPage(
                layer=KnowledgeLayer.K1,
                git_path="k/split-b.md",
                curation_path="k/split-b.curation.md",
                writer_version="writer-test",
                parent_artifact_id=split_source,
                rules=(EntityRuleParams(entity_id=corpus.entities["child"]),),
            ),
        ),
        rationale="The page has two stable evidence domains.",
        confidence=Decimal("1"),
    )
    corpus.control.record_plan_proposals(
        run=_plan_run(trigger=KnowledgePlanTrigger.SIZE_OVERFLOW),
        proposals=(split,),
        auto_apply_max_expected_impact=Decimal("0"),
    )

    merge_a = corpus.page(
        params=EntityRuleParams(entity_id=corpus.entities["outside"]), slug="merge-a"
    )
    merge_b = corpus.page(
        params=EntityRuleParams(entity_id=corpus.entities["acme"]), slug="merge-b"
    )
    merge = KnowledgeMergePagesProposal(
        source_artifact_ids=(merge_a, merge_b),
        page=KnowledgePlannedPage(
            layer=KnowledgeLayer.K1,
            git_path="k/merged.md",
            curation_path="k/merged.curation.md",
            writer_version="writer-test",
            rules=(
                EntityRuleParams(entity_id=corpus.entities["outside"]),
                EntityRuleParams(entity_id=corpus.entities["acme"]),
            ),
        ),
        rationale="The two leaves duplicate one evidence domain.",
        confidence=Decimal("1"),
    )
    corpus.control.record_plan_proposals(
        run=_plan_run(trigger=KnowledgePlanTrigger.REFLECTION),
        proposals=(merge,),
        auto_apply_max_expected_impact=Decimal("0"),
    )

    adjusted = corpus.page(
        params=EntityRuleParams(entity_id=corpus.entities["root"]), slug="adjusted"
    )
    corpus.control.record_plan_proposals(
        run=_plan_run(trigger=KnowledgePlanTrigger.WRITER_SUGGESTION),
        proposals=(
            KnowledgeAdjustRuleProposal(
                artifact_id=adjusted,
                rules=(EntityRuleParams(entity_id=corpus.entities["outside"]),),
                rationale="The existing rule owns the wrong entity.",
                confidence=Decimal("1"),
            ),
        ),
        auto_apply_max_expected_impact=Decimal("0"),
    )

    retired = corpus.page(
        params=EntityRuleParams(entity_id=corpus.entities["child"]), slug="retired"
    )
    corpus.control.record_plan_proposals(
        run=_plan_run(trigger=KnowledgePlanTrigger.REFLECTION),
        proposals=(
            KnowledgeRetirePageProposal(
                artifact_id=retired,
                rationale="No evidence or navigation path needs this leaf.",
                confidence=Decimal("1"),
            ),
        ),
        auto_apply_max_expected_impact=Decimal("0"),
    )

    with corpus.engine.connect() as connection:
        source_rule_statuses = set(
            connection.execute(
                text(
                    "SELECT status::text FROM knowledge_page_rules"
                    " WHERE artifact_id = :artifact"
                ),
                {"artifact": split_source},
            ).scalars()
        )
        split_children = connection.execute(
            text(
                "SELECT count(*) FROM knowledge_artifacts"
                " WHERE parent_artifact_id = :parent AND status = 'stale'"
            ),
            {"parent": split_source},
        ).scalar_one()
        merged_sources = set(
            connection.execute(
                text(
                    "SELECT status::text FROM knowledge_artifacts"
                    " WHERE artifact_id IN (:a, :b)"
                ),
                {"a": merge_a, "b": merge_b},
            ).scalars()
        )
        merged_target = connection.execute(
            text(
                "SELECT status::text FROM knowledge_artifacts"
                " WHERE git_path = 'k/merged.md'"
            )
        ).scalar_one()
        adjusted_params = connection.execute(
            text(
                "SELECT params FROM knowledge_page_rules"
                " WHERE artifact_id = :artifact AND status = 'active'"
            ),
            {"artifact": adjusted},
        ).scalar_one()
        retired_status = connection.execute(
            text(
                "SELECT status::text FROM knowledge_artifacts"
                " WHERE artifact_id = :artifact"
            ),
            {"artifact": retired},
        ).scalar_one()
    assert source_rule_statuses == {"deprecated"}
    assert split_children == 2
    assert merged_sources == {"tombstoned"}
    assert merged_target == "stale"
    assert adjusted_params["entity_id"] == str(corpus.entities["outside"])
    assert retired_status == "tombstoned"


def test_reflection_and_authored_handover_always_require_review(
    corpus: _Corpus,
) -> None:
    """Checker proposals and ownership loss cannot cross the review gate silently."""
    compiled = corpus.page(
        params=EntityRuleParams(entity_id=corpus.entities["root"]),
        slug="reflection-target",
    )
    reflection = KnowledgeCreatePageProposal(
        page=KnowledgePlannedPage(
            layer=KnowledgeLayer.K1,
            git_path="k/reflection-page.md",
            curation_path="k/reflection-page.curation.md",
            writer_version="writer-test",
            rules=(EntityRuleParams(entity_id=corpus.entities["child"]),),
        ),
        rationale="Fresh eyes found a missing navigation leaf.",
        confidence=Decimal("1"),
    )
    reflection_result = corpus.control.record_plan_proposals(
        run=_plan_run(
            run_kind=KnowledgePlanRunKind.REFLECTION,
            trigger=KnowledgePlanTrigger.REFLECTION,
        ),
        proposals=(reflection,),
        auto_apply_max_expected_impact=Decimal("99"),
    )[0]
    assert reflection_result.band is KnowledgePlanBand.REVIEW
    assert reflection_result.status is KnowledgePlanStatus.PROPOSED
    corpus.control.accept_plan_decision(
        decision_id=reflection_result.decision_id, reviewed_by="review-agent"
    )

    authored = corpus.page(
        params=EntityRuleParams(entity_id=corpus.entities["outside"]),
        slug="authored-handover",
        page_kind=KnowledgePageKind.AUTHORED,
    )
    handover = KnowledgeConvertKindProposal(
        artifact_id=authored,
        from_kind=KnowledgePageKind.AUTHORED,
        to_kind=KnowledgePageKind.COMPILED,
        writer_version="writer-test",
        curation_path="k/authored-handover.curation.md",
        rules=(EntityRuleParams(entity_id=corpus.entities["outside"]),),
        rationale="The author confirmed the page is now wholly evidence-derived.",
        confidence=Decimal("1"),
    )
    handover_result = corpus.control.record_plan_proposals(
        run=_plan_run(trigger=KnowledgePlanTrigger.HUMAN),
        proposals=(handover,),
        auto_apply_max_expected_impact=Decimal("99"),
    )[0]
    assert handover_result.band is KnowledgePlanBand.REVIEW
    with pytest.raises(KnowledgeCompilationError, match="author confirmation"):
        corpus.control.accept_plan_decision(
            decision_id=handover_result.decision_id, reviewed_by="review-agent"
        )
    corpus.control.accept_plan_decision(
        decision_id=handover_result.decision_id,
        reviewed_by="review-agent",
        author_confirmed=True,
    )
    with corpus.engine.connect() as connection:
        ownership = connection.execute(
            text(
                "SELECT page_kind::text, status::text, curation_path, content_hash"
                " FROM knowledge_artifacts WHERE artifact_id = :a"
            ),
            {"a": authored},
        ).one()
        reflection_status = connection.execute(
            text(
                "SELECT status::text FROM knowledge_artifacts"
                " WHERE git_path = 'k/reflection-page.md'"
            )
        ).scalar_one()
    assert ownership == ("compiled", "stale", "k/authored-handover.curation.md", None)
    assert reflection_status == "stale"
    assert compiled in {
        item.artifact_id
        for item in corpus.control.compile_artifacts(deployment_id=_DEPLOYMENT_ID)
    }


def test_compiled_edit_quarantine_is_durable_and_has_explicit_resolutions(
    corpus: _Corpus,
) -> None:
    """A direct edit is excluded until adoption, curation, or rejection resolves it."""
    page = corpus.page(
        params=EntityRuleParams(entity_id=corpus.entities["root"]),
        slug="quarantine-adopt",
    )
    corpus.compile(artifact_id=page)
    edited = "# Human revision\n\nKeep this framing.\n"
    record = corpus.control.quarantine_compiled_edit(
        artifact_id=page,
        detected_content_hash=knowledge_content_hash(markdown=edited),
        edited_markdown=edited,
        driver_version="driver-test",
    )
    repeated = corpus.control.quarantine_compiled_edit(
        artifact_id=page,
        detected_content_hash=knowledge_content_hash(markdown=edited),
        edited_markdown=edited,
        driver_version="driver-test",
    )
    assert repeated.quarantine_id == record.quarantine_id
    with pytest.raises(KnowledgeCompilationError, match="explicit quarantine"):
        corpus.control.accept_plan_decision(
            decision_id=record.decision_id, reviewed_by="generic-reviewer"
        )
    with pytest.raises(KnowledgeCompilationError, match="not reviewable"):
        corpus.control.reject_plan_decision(
            decision_id=record.decision_id, reviewed_by="generic-reviewer"
        )
    assert page not in {
        item.artifact_id
        for item in corpus.control.compile_artifacts(deployment_id=_DEPLOYMENT_ID)
    }
    corpus.control.adopt_quarantined_page(
        quarantine_id=record.quarantine_id, reviewed_by="page-author"
    )

    curation_page = corpus.page(
        params=EntityRuleParams(entity_id=corpus.entities["child"]),
        slug="quarantine-curation",
    )
    corpus.compile(artifact_id=curation_page)
    curation_record = corpus.control.quarantine_compiled_edit(
        artifact_id=curation_page,
        detected_content_hash=knowledge_content_hash(markdown=edited),
        edited_markdown=edited,
        driver_version="driver-test",
    )
    with pytest.raises(KnowledgeCompilationError, match="does not contain"):
        corpus.control.accept_quarantine_to_curation(
            quarantine_id=curation_record.quarantine_id,
            curation_markdown="# unrelated\n",
            curation_content_hash="unrelated",
            reviewed_by="curator",
        )
    corpus.control.accept_quarantine_to_curation(
        quarantine_id=curation_record.quarantine_id,
        curation_markdown=f"# Accepted guidance\n\n{edited}",
        curation_content_hash="curation-hash",
        reviewed_by="curator",
    )
    rejected_page = corpus.page(
        params=EntityRuleParams(entity_id=corpus.entities["outside"]),
        slug="quarantine-rejected",
    )
    corpus.compile(artifact_id=rejected_page)
    rejected_record = corpus.control.quarantine_compiled_edit(
        artifact_id=rejected_page,
        detected_content_hash=knowledge_content_hash(markdown=edited),
        edited_markdown=edited,
        driver_version="driver-test",
    )
    corpus.control.reject_quarantined_edit(
        quarantine_id=rejected_record.quarantine_id, reviewed_by="curator"
    )
    with corpus.engine.connect() as connection:
        adopted = connection.execute(
            text(
                "SELECT page_kind::text, status::text, writer_version, content_hash"
                " FROM knowledge_artifacts WHERE artifact_id = :a"
            ),
            {"a": page},
        ).one()
        curation_state = connection.execute(
            text(
                "SELECT status::text, content_hash FROM knowledge_artifacts"
                " WHERE artifact_id = :a"
            ),
            {"a": curation_page},
        ).one()
        rejected_state = connection.execute(
            text(
                "SELECT status::text, content_hash FROM knowledge_artifacts"
                " WHERE artifact_id = :a"
            ),
            {"a": rejected_page},
        ).one()
        statuses = {
            quarantine_id: status
            for quarantine_id, status in connection.execute(
                text(
                    "SELECT quarantine_id, status FROM knowledge_quarantines"
                    " ORDER BY detected_at"
                )
            ).tuples()
        }
    edited_hash = knowledge_content_hash(markdown=edited)
    assert adopted == ("authored", "active", None, edited_hash)
    assert curation_state == ("stale", None)
    assert rejected_state == ("stale", None)
    assert statuses[record.quarantine_id] == KnowledgeQuarantineStatus.ADOPTED.value
    assert (
        statuses[curation_record.quarantine_id]
        == KnowledgeQuarantineStatus.CURATION_ACCEPTED.value
    )
    assert (
        statuses[rejected_record.quarantine_id]
        == KnowledgeQuarantineStatus.REJECTED.value
    )


def test_planning_snapshot_reports_only_unhoused_delta_candidates(
    corpus: _Corpus,
) -> None:
    """Planner triggers use exact rule membership and stable candidate identities."""
    page = corpus.page(
        params=EntityRuleParams(entity_id=corpus.entities["root"]), slug="root-owner"
    )
    snapshot = corpus.control.planning_snapshot(
        deployment_id=_DEPLOYMENT_ID,
        scope_id=None,
        delta=KnowledgeEvidenceDelta(
            relation_ids=(corpus.relations["root"], corpus.relations["outside"]),
            claim_ids=(corpus.claims["drive"], corpus.claims["email"]),
        ),
        page_sizes={page: 101},
        page_size_limit_bytes=100,
    )
    aggregates = {
        aggregate.entity_id: set(aggregate.candidate_keys)
        for aggregate in snapshot.orphan_aggregates
    }
    outside_fact = f"fact:relation:{corpus.relations['outside']}"
    outside_claim = f"claim:{corpus.docs['email']}:chunk-email"
    assert corpus.entities["root"] not in aggregates
    assert aggregates[corpus.entities["outside"]] == {outside_fact, outside_claim}
    assert outside_fact in aggregates[corpus.entities["acme"]]
    assert snapshot.overflow_artifact_ids == (page,)


def test_planner_worker_archives_then_routes_typed_decisions(corpus: _Corpus) -> None:
    """The harness can only propose; its archived JSON is applied by the control plane."""
    proposal = KnowledgeCreatePageProposal(
        page=KnowledgePlannedPage(
            layer=KnowledgeLayer.K1,
            git_path="k/worker-created.md",
            curation_path="k/worker-created.curation.md",
            writer_version="writer-test",
            rules=(EntityRuleParams(entity_id=corpus.entities["outside"]),),
        ),
        rationale="The orphan aggregate needs a compiled evidence home.",
        confidence=Decimal("1"),
    )
    session = _PlannerSession(
        decisions_json=json.dumps([proposal.model_dump(mode="json")])
    )
    store = _TranscriptStore()
    worker = KnowledgePlannerWorker(
        control_plane=corpus.control,
        agent_session=session,
        transcript_store=store,
        mount_publisher=_MountPublisher(),
        settings=_planner_settings(),
        clock=lambda: _NOW,
    )
    snapshot = KnowledgePlanningSnapshot(
        deployment_id=_DEPLOYMENT_ID,
        orphan_aggregates=(
            KnowledgeOrphanAggregate(
                entity_id=corpus.entities["outside"],
                candidate_keys=(f"fact:relation:{corpus.relations['outside']}",),
            ),
        ),
        artifacts=(),
    )

    results = worker.run_planner(snapshot=snapshot)

    assert results[0].status is KnowledgePlanStatus.APPLIED
    assert len(store.objects) == 1
    assert session.requests[0].model == "producer-test"
    assert session.requests[0].sandbox.accepted_output_paths == (
        "output/decisions.json",
    )
    with corpus.engine.connect() as connection:
        run = (
            connection.execute(
                text(
                    "SELECT run_kind, trigger::text AS trigger, status, tokens, cost_usd"
                    " FROM knowledge_plan_runs"
                )
            )
            .mappings()
            .one()
        )
        created = connection.execute(
            text(
                "SELECT status::text FROM knowledge_artifacts"
                " WHERE git_path = 'k/worker-created.md'"
            )
        ).scalar_one()
    assert run == {
        "run_kind": "planner",
        "trigger": "orphan_evidence",
        "status": "succeeded",
        "tokens": 23,
        "cost_usd": Decimal("0.25"),
    }
    assert created == "stale"


def test_planner_worker_records_parse_failure_after_transcript(corpus: _Corpus) -> None:
    """Malformed agent output leaves a failed run and its original transcript."""
    session = _PlannerSession(decisions_json="not-json")
    store = _TranscriptStore()
    worker = KnowledgePlannerWorker(
        control_plane=corpus.control,
        agent_session=session,
        transcript_store=store,
        mount_publisher=_MountPublisher(),
        settings=_planner_settings(),
        clock=lambda: _NOW,
    )
    snapshot = KnowledgePlanningSnapshot(deployment_id=_DEPLOYMENT_ID, artifacts=())

    with pytest.raises(KnowledgePlannerError, match="typed contract"):
        worker.run_planner(snapshot=snapshot)

    assert list(store.objects.values()) == [b'{"stdout":"planner trace","stderr":""}']
    with corpus.engine.connect() as connection:
        failed = (
            connection.execute(
                text(
                    "SELECT status, failure, session_transcript_uri"
                    " FROM knowledge_plan_runs"
                )
            )
            .mappings()
            .one()
        )
    assert failed["status"] == "failed"
    assert "KnowledgePlannerError" in failed["failure"]
    assert failed["session_transcript_uri"].startswith("k-planner-transcripts/")


def test_reflection_uses_the_independent_model_and_review_only_band(
    corpus: _Corpus,
) -> None:
    """The checker seat is cross-family and cannot auto-apply its proposals."""
    proposal = KnowledgeCreatePageProposal(
        page=KnowledgePlannedPage(
            layer=KnowledgeLayer.K1,
            git_path="k/reflection-worker.md",
            curation_path="k/reflection-worker.curation.md",
            writer_version="writer-test",
            rules=(EntityRuleParams(entity_id=corpus.entities["root"]),),
        ),
        rationale="Reflection found a navigation dead end.",
        confidence=Decimal("1"),
    )
    session = _PlannerSession(
        decisions_json=json.dumps([proposal.model_dump(mode="json")])
    )
    worker = KnowledgePlannerWorker(
        control_plane=corpus.control,
        agent_session=session,
        transcript_store=_TranscriptStore(),
        mount_publisher=_MountPublisher(),
        settings=_planner_settings(),
        clock=lambda: _NOW,
    )

    results = worker.run_reflection(
        snapshot=KnowledgePlanningSnapshot(deployment_id=_DEPLOYMENT_ID, artifacts=())
    )

    assert session.requests[0].model == "checker-test"
    assert results[0].band is KnowledgePlanBand.REVIEW
    assert results[0].status is KnowledgePlanStatus.PROPOSED


def test_planner_settings_reject_same_family_reflection() -> None:
    """Producer/checker separation is a binding deploy-time contract."""
    with pytest.raises(ValidationError, match="different model family"):
        KnowledgePlannerSettings(
            planner_model="producer",
            planner_model_family="OpenAI",
            reflection_model="checker",
            reflection_model_family="openai",
            timeout_seconds=60,
            auto_apply_max_expected_impact=Decimal("0"),
            transcript_prefix="planner",
        )


def test_authored_sync_routes_exact_flags_and_page_compile_deltas(
    corpus: _Corpus,
) -> None:
    """Authored ground syncs atomically and only exact evidence/page changes flag it."""
    watched_id = corpus.page(
        params=PredicateBeatRuleParams(predicate="works_for"), slug="watched-record"
    )
    markdown = "# Authored decision\n\nKeep the current ordering.\n"
    citation = KnowledgeCitation(
        role=KnowledgeEvidenceRole.CITES, relation_id=corpus.relations["root"]
    )
    result = corpus.control.sync_authored_page(
        sync=KnowledgeAuthoredPageSync(
            deployment_id=_DEPLOYMENT_ID,
            git_path="decisions/ordering.md",
            markdown=markdown,
            content_hash=knowledge_content_hash(markdown=markdown),
            git_revision="authored-1",
            declaration=KnowledgeAuthoredDeclaration(
                citations=(citation,),
                watch_rules=(
                    PredicateBeatRuleParams(
                        predicate="works_for", subject_entity_id=corpus.entities["root"]
                    ),
                ),
                watched_page_paths=("k/watched-record.md",),
            ),
        )
    )
    assert result.registered
    assert not result.lint_flagged

    revised_markdown = f"{markdown}\nAn authored clarification.\n"
    preserved = corpus.control.sync_authored_page(
        sync=KnowledgeAuthoredPageSync(
            deployment_id=_DEPLOYMENT_ID,
            git_path="decisions/ordering.md",
            markdown=revised_markdown,
            content_hash=knowledge_content_hash(markdown=revised_markdown),
            git_revision="authored-2",
            declaration=KnowledgeAuthoredDeclaration(),
        )
    )
    assert preserved.artifact_id == result.artifact_id
    assert preserved.content_changed
    with corpus.engine.connect() as connection:
        counts = (
            connection.execute(
                text(
                    "SELECT"
                    " (SELECT count(*) FROM knowledge_artifact_evidence"
                    "  WHERE artifact_id = :artifact_id) AS citations,"
                    " (SELECT count(*) FROM knowledge_page_rules"
                    "  WHERE artifact_id = :artifact_id AND status = 'active') AS rules,"
                    " (SELECT count(*) FROM knowledge_page_watches"
                    "  WHERE watcher_artifact_id = :artifact_id) AS watches"
                ),
                {"artifact_id": result.artifact_id},
            )
            .mappings()
            .one()
        )
    assert counts == {"citations": 1, "rules": 1, "watches": 1}

    routed = corpus.control.route_notifications(
        deployment_id=_DEPLOYMENT_ID,
        delta=KnowledgeEvidenceDelta(relation_ids=(corpus.relations["root"],)),
    )
    assert routed.authored_artifact_ids == (result.artifact_id,)
    state = corpus.control.authored_review_state(artifact_id=result.artifact_id)
    assert state.open_flag_count == 1
    assert state.payloads[0].reasons == (
        KnowledgeAuthoredReviewReason.EVIDENCE_CHANGED,
    )

    with corpus.engine.begin() as connection:
        connection.execute(
            text("UPDATE relations SET predicate = 'works_for' WHERE relation_id = :r"),
            {"r": corpus.relations["outside"]},
        )
    filtered = corpus.control.route_notifications(
        deployment_id=_DEPLOYMENT_ID,
        delta=KnowledgeEvidenceDelta(relation_ids=(corpus.relations["outside"],)),
    )
    assert filtered.authored_artifact_ids == ()

    context = KnowledgeCompileContext(
        curation_hash="curation-test", writer_version="writer-test"
    )
    snapshot = corpus.control.input_snapshot(artifact_id=watched_id, context=context)
    compilation = KnowledgeCompilationWrite(
        compilation_id=uuid4(),
        deployment_id=_DEPLOYMENT_ID,
        artifact_id=watched_id,
        inputs_hash=knowledge_inputs_hash(snapshot=snapshot),
        candidate_count=len(snapshot.facts) + len(snapshot.claims),
        uncited_count=len(snapshot.facts) + len(snapshot.claims) - 1,
        citations=(citation,),
        writer_version="writer-test",
        page_summary="The watched record changed.",
        content_hash=knowledge_content_hash(markdown="# Watched\n"),
    )
    corpus.control.record_pending_compilation(compilation=compilation)
    corpus.control.commit_compilation(
        compilation=compilation, git_commit="watched-compile-1"
    )
    page_payload = corpus.control.authored_review_state(
        artifact_id=result.artifact_id
    ).payloads[0]
    assert KnowledgeAuthoredReviewReason.PAGE_RECOMPILED in page_payload.reasons
    assert page_payload.page_refs == ("k/watched-record.md",)
    assert page_payload.citations_added == (
        f"cites:relation:{corpus.relations['root']}",
    )
    corpus.control.route_notifications(
        deployment_id=_DEPLOYMENT_ID,
        delta=KnowledgeEvidenceDelta(relation_ids=(corpus.relations["root"],)),
        tombstone=True,
    )
    tombstone_state = corpus.control.authored_review_state(
        artifact_id=result.artifact_id
    )
    assert tombstone_state.redaction_required
    assert KnowledgeAuthoredReviewReason.TOMBSTONE in (
        tombstone_state.payloads[0].reasons
    )


def test_authored_declaration_lint_and_invalid_citation_are_atomic(
    corpus: _Corpus,
) -> None:
    """Groundless pages stay visibly flagged and invalid declarations leave no row."""
    markdown = "# Ungrounded target state\n"
    result = corpus.control.sync_authored_page(
        sync=KnowledgeAuthoredPageSync(
            deployment_id=_DEPLOYMENT_ID,
            git_path="targets/ungrounded.md",
            markdown=markdown,
            content_hash=knowledge_content_hash(markdown=markdown),
            git_revision="authored-lint",
            declaration=KnowledgeAuthoredDeclaration(
                citations=(), watch_rules=(), watched_page_paths=()
            ),
        )
    )
    assert result.lint_flagged
    assert corpus.control.authored_review_state(
        artifact_id=result.artifact_id
    ).payloads[0].reasons == (KnowledgeAuthoredReviewReason.DECLARATION_MISSING,)

    invalid_markdown = "# Invalid premise\n"
    with pytest.raises(KnowledgeCompilationError, match="citation target"):
        corpus.control.sync_authored_page(
            sync=KnowledgeAuthoredPageSync(
                deployment_id=_DEPLOYMENT_ID,
                git_path="targets/invalid.md",
                markdown=invalid_markdown,
                content_hash=knowledge_content_hash(markdown=invalid_markdown),
                git_revision="authored-invalid",
                declaration=KnowledgeAuthoredDeclaration(
                    citations=(
                        KnowledgeCitation(
                            role=KnowledgeEvidenceRole.CITES, relation_id=uuid4()
                        ),
                    )
                ),
            )
        )
    with corpus.engine.connect() as connection:
        invalid_count = connection.execute(
            text(
                "SELECT count(*) FROM knowledge_artifacts"
                " WHERE deployment_id = :d AND git_path = 'targets/invalid.md'"
            ),
            {"d": _DEPLOYMENT_ID},
        ).scalar_one()
    assert invalid_count == 0


def test_subscription_dispatch_coalesces_delta_and_materializes_d67_work(
    corpus: _Corpus,
) -> None:
    """One debounce window carries the exact delta into one generic worker target."""
    subscription_id = uuid4()
    corpus.control.register_subscription(
        subscription=KnowledgeSubscriptionCreate(
            subscription_id=subscription_id,
            deployment_id=_DEPLOYMENT_ID,
            name="replan-on-employment-change",
            workflow_endpoint="demo://planning/replan",
            debounce_seconds=60,
            created_by="planner-test",
            rules=(PredicateBeatRuleParams(predicate="works_for"),),
        )
    )
    first = corpus.control.route_notifications(
        deployment_id=_DEPLOYMENT_ID,
        delta=KnowledgeEvidenceDelta(relation_ids=(corpus.relations["root"],)),
    )
    second = corpus.control.route_notifications(
        deployment_id=_DEPLOYMENT_ID,
        delta=KnowledgeEvidenceDelta(
            relation_ids=(corpus.relations["root"],),
            claim_ids=(corpus.claims["drive"],),
        ),
    )
    assert first.dispatch_ids == second.dispatch_ids
    dispatch_id = first.dispatch_ids[0]
    with corpus.engine.begin() as connection:
        payload = connection.execute(
            text(
                "UPDATE knowledge_dispatches"
                " SET enqueued_at = now() - interval '2 minutes'"
                " WHERE dispatch_id = :dispatch_id RETURNING payload"
            ),
            {"dispatch_id": dispatch_id},
        ).scalar_one()
        pending_count = connection.execute(
            text(
                "SELECT count(*) FROM knowledge_dispatches"
                " WHERE subscription_id = :subscription_id AND status = 'pending'"
            ),
            {"subscription_id": subscription_id},
        ).scalar_one()
    assert pending_count == 1
    assert payload["delta"]["relation_ids"] == [str(corpus.relations["root"])]
    assert payload["delta"]["claim_ids"] == [str(corpus.claims["drive"])]

    materialized = corpus.control.materialize_due_dispatches(
        deployment_id=_DEPLOYMENT_ID, component_version="dispatch-test-v1"
    )
    assert len(materialized) == 1
    assert materialized[0].dispatch_id == dispatch_id
    with corpus.engine.connect() as connection:
        work = connection.execute(
            text(
                "SELECT target_kind::text, stage::text, lane::text"
                " FROM processing_state WHERE processing_id = :processing_id"
            ),
            {"processing_id": materialized[0].processing_id},
        ).one()
    assert work == ("knowledge_dispatch", "dispatch_knowledge", None)
    record = corpus.control.begin_dispatch(dispatch_id=dispatch_id)
    assert record.status is KnowledgeDispatchStatus.RUNNING
    assert record.workflow_endpoint == "demo://planning/replan"
    corpus.control.complete_dispatch(dispatch_id=dispatch_id)
    assert (
        corpus.control.begin_dispatch(dispatch_id=dispatch_id).status
        is KnowledgeDispatchStatus.DONE
    )


class _WorkflowDispatcher:
    """Demo subscription endpoint that can prove success or durable failure."""

    def __init__(self, *, fail: bool = False) -> None:
        """Choose whether every idempotent delivery attempt fails visibly."""
        self.fail = fail
        self.deliveries: list[KnowledgeWorkflowDelivery] = []

    def deliver(self, *, delivery: KnowledgeWorkflowDelivery) -> None:
        """Record the delivery and optionally simulate an unavailable workflow."""
        self.deliveries.append(delivery)
        if self.fail:
            raise RuntimeError("demo workflow unavailable")


def test_dispatch_worker_delivers_demo_payload_and_dead_letters_failures(
    corpus: _Corpus,
) -> None:
    """The generic worker owns retries/full tracebacks while dispatch mirrors state."""

    def materialize(*, name: str) -> UUID:
        """Register, route, mature, and materialize one independent test batch."""
        subscription_id = uuid4()
        corpus.control.register_subscription(
            subscription=KnowledgeSubscriptionCreate(
                subscription_id=subscription_id,
                deployment_id=_DEPLOYMENT_ID,
                name=name,
                workflow_endpoint=f"demo://{name}",
                debounce_seconds=60,
                created_by="worker-test",
                rules=(PredicateBeatRuleParams(predicate="works_for"),),
            )
        )
        dispatch_id = corpus.control.route_notifications(
            deployment_id=_DEPLOYMENT_ID,
            delta=KnowledgeEvidenceDelta(relation_ids=(corpus.relations["root"],)),
        ).dispatch_ids[0]
        with corpus.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE knowledge_dispatches"
                    " SET enqueued_at = now() - interval '2 minutes'"
                    " WHERE dispatch_id = :dispatch_id"
                ),
                {"dispatch_id": dispatch_id},
            )
        corpus.control.materialize_due_dispatches(
            deployment_id=_DEPLOYMENT_ID, component_version="dispatch-worker-test"
        )
        return dispatch_id

    ledger = WorkLedger(
        engine=corpus.engine,
        settings=WorkLedgerSettings(retry_backoff_base_s=0, retry_backoff_max_s=0),
    )
    success_dispatch_id = materialize(name="successful-planner")
    successful_endpoint = _WorkflowDispatcher()
    success_registry = HandlerRegistry()
    success_registry.register(
        stage=PipelineStage.DISPATCH_KNOWLEDGE,
        handler=KnowledgeDispatchHandler(
            control_plane=corpus.control, dispatcher=successful_endpoint
        ),
    )

    success = Worker(ledger=ledger, registry=success_registry).run_one(
        deployment_id=_DEPLOYMENT_ID, stage=PipelineStage.DISPATCH_KNOWLEDGE, lane=None
    )

    assert success.outcome is RunResultOutcome.SUCCEEDED
    assert len(successful_endpoint.deliveries) == 1
    assert (
        corpus.control.begin_dispatch(dispatch_id=success_dispatch_id).status
        is KnowledgeDispatchStatus.DONE
    )

    failed_dispatch_id = materialize(name="unavailable-planner")
    failing_endpoint = _WorkflowDispatcher(fail=True)
    failure_registry = HandlerRegistry()
    failure_registry.register(
        stage=PipelineStage.DISPATCH_KNOWLEDGE,
        handler=KnowledgeDispatchHandler(
            control_plane=corpus.control, dispatcher=failing_endpoint
        ),
    )
    failure_worker = Worker(ledger=ledger, registry=failure_registry)
    outcomes = tuple(
        failure_worker.run_one(
            deployment_id=_DEPLOYMENT_ID,
            stage=PipelineStage.DISPATCH_KNOWLEDGE,
            lane=None,
        ).outcome
        for _ in range(3)
    )

    assert outcomes == (
        RunResultOutcome.RETRY_SCHEDULED,
        RunResultOutcome.RETRY_SCHEDULED,
        RunResultOutcome.DEAD_LETTERED,
    )
    with corpus.engine.connect() as connection:
        failure = connection.execute(
            text(
                "SELECT p.status::text, p.last_error, d.status::text"
                " FROM processing_state p"
                " JOIN knowledge_dispatches d ON d.dispatch_id = p.target_id"
                " WHERE d.dispatch_id = :dispatch_id"
            ),
            {"dispatch_id": failed_dispatch_id},
        ).one()
    assert failure[0] == "dead_letter"
    assert "RuntimeError: demo workflow unavailable" in failure[1]
    assert failure[2] == "failed"


def test_inactive_subscription_terminates_batch_and_unblocks_reactivation(
    corpus: _Corpus,
) -> None:
    """Pause-after-materialize cannot leave a zombie pending debounce batch."""
    subscription_id = uuid4()
    corpus.control.register_subscription(
        subscription=KnowledgeSubscriptionCreate(
            subscription_id=subscription_id,
            deployment_id=_DEPLOYMENT_ID,
            name="pausable-planner",
            workflow_endpoint="demo://pausable-planner",
            debounce_seconds=60,
            created_by="worker-test",
            rules=(PredicateBeatRuleParams(predicate="works_for"),),
        )
    )
    first_dispatch_id = corpus.control.route_notifications(
        deployment_id=_DEPLOYMENT_ID,
        delta=KnowledgeEvidenceDelta(relation_ids=(corpus.relations["root"],)),
    ).dispatch_ids[0]
    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE knowledge_dispatches"
                " SET enqueued_at = now() - interval '2 minutes'"
                " WHERE dispatch_id = :dispatch_id"
            ),
            {"dispatch_id": first_dispatch_id},
        )
    corpus.control.materialize_due_dispatches(
        deployment_id=_DEPLOYMENT_ID, component_version="dispatch-pause-test"
    )
    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE knowledge_subscriptions SET status = 'paused'"
                " WHERE subscription_id = :subscription_id"
            ),
            {"subscription_id": subscription_id},
        )
    registry = HandlerRegistry()
    registry.register(
        stage=PipelineStage.DISPATCH_KNOWLEDGE,
        handler=KnowledgeDispatchHandler(
            control_plane=corpus.control, dispatcher=_WorkflowDispatcher()
        ),
    )

    rejected = Worker(
        ledger=WorkLedger(
            engine=corpus.engine,
            settings=WorkLedgerSettings(retry_backoff_base_s=0, retry_backoff_max_s=0),
        ),
        registry=registry,
    ).run_one(
        deployment_id=_DEPLOYMENT_ID, stage=PipelineStage.DISPATCH_KNOWLEDGE, lane=None
    )

    assert rejected.outcome is RunResultOutcome.DEAD_LETTERED
    with corpus.engine.begin() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT status::text FROM knowledge_dispatches"
                    " WHERE dispatch_id = :dispatch_id"
                ),
                {"dispatch_id": first_dispatch_id},
            ).scalar_one()
            == "failed"
        )
        connection.execute(
            text(
                "UPDATE knowledge_subscriptions SET status = 'active'"
                " WHERE subscription_id = :subscription_id"
            ),
            {"subscription_id": subscription_id},
        )
    second_dispatch_id = corpus.control.route_notifications(
        deployment_id=_DEPLOYMENT_ID,
        delta=KnowledgeEvidenceDelta(relation_ids=(corpus.relations["root"],)),
    ).dispatch_ids[0]
    assert second_dispatch_id != first_dispatch_id
    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE knowledge_dispatches"
                " SET enqueued_at = now() - interval '2 minutes'"
                " WHERE dispatch_id = :dispatch_id"
            ),
            {"dispatch_id": second_dispatch_id},
        )
    assert (
        corpus.control.materialize_due_dispatches(
            deployment_id=_DEPLOYMENT_ID, component_version="dispatch-pause-test"
        )[0].dispatch_id
        == second_dispatch_id
    )
