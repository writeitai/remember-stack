"""D120/D121 projection, handle translation, and prompt-clarity proofs."""

from datetime import datetime
from datetime import timezone
from typing import Any
from uuid import UUID

from pydantic import ValidationError
import pytest

from rememberstack.core.concise_adjudication import project_concise_inputs
from rememberstack.core.concise_adjudication import translate_prompt_decision
from rememberstack.model.concise_adjudication import PromptFactDecision
from rememberstack.spine.fact_adjudication import _FACT_PROMPT
from rememberstack.workers.e2 import _CLAIMIFY_PROMPT
from rememberstack.workers.e2 import _SELECTION_PROMPT
from rememberstack.workers.e3 import _NORMALIZE_PROMPT

_APP = UUID(int=1)
_FACT = UUID(int=2)
_CLAIM = UUID(int=3)
_PRIOR = UUID(int=4)
_ROOT = UUID(int=10)
_ALIAS = UUID(int=11)
_OBJECT = UUID(int=12)
_OBJECT_ALIAS = UUID(int=13)
_DOC_A = UUID(int=20)
_DOC_B = UUID(int=21)
_WITNESS = UUID(int=99)
_AT = datetime(2022, 11, 10, tzinfo=timezone.utc)


def _snapshot(**fields: object) -> dict[str, Any]:
    """A frozen observation snapshot with overridable participants."""
    base: dict[str, object] = {
        "deployment_id": UUID(int=50),
        "root": _ROOT,
        "kind": "observation",
        "application_id": _APP,
        "normalizer_version": "test",
        "adjudicator_version": "test",
        "membership_hash": "m",
        "evidence_hash": "e",
        "support_hash": "s",
        "limits": {"facts": 20, "claims": 100, "assertions": 100},
        "potentially_truncated": False,
        "facts": [
            {
                "fact_id": _FACT,
                "subject_entity_id": _ALIAS,
                "statement": "Nate won Tournament A",
                "valid_from": datetime(2022, 11, 5, tzinfo=timezone.utc),
                "valid_until": datetime(2022, 11, 6, tzinfo=timezone.utc),
                "valid_precision": "day",
                "window_claim_ids": [_CLAIM, _WITNESS],
                "ingested_at": _AT,
                "invalidated_at": None,
                "contradiction_group": None,
                "evidence_count": 1,
                "contradict_count": 0,
            }
        ],
        "claims": [
            {
                "claim_id": _CLAIM,
                "doc_id": _DOC_A,
                "claim_text": "Nate won Tournament A",
                "source_span": "Nate won Tournament A",
                "asserted_at": _AT,
                "claim_valid_from": datetime(2022, 11, 5, tzinfo=timezone.utc),
                "claim_valid_until": datetime(2022, 11, 5, tzinfo=timezone.utc),
                "claim_valid_precision": "day",
                "claim_valid_kind": "event_time",
                "is_current_testimony": True,
                "is_attributed": False,
                "extractor_version": "test",
            }
        ],
        "assertions": [
            {
                "application_id": _APP,
                "claim_id": _CLAIM,
                "subject_entity_id": _ALIAS,
                "object_entity_id": None,
                "canonical_subject_id": _ROOT,
                "canonical_object_id": None,
                "output_kind": "observation",
                "output_ordinal": 0,
                "normalizer_version": "test",
                "adjudicator_version": "test",
                "support_relation_id": None,
                "support_observation_id": None,
                "support_stance": None,
                "assertion": {
                    "subject": {"name": "Nate"},
                    "statement": "Nate won Tournament A",
                    "uses_claim_window": True,
                },
            }
        ],
        "evidence": [{"fact_id": _FACT, "claim_id": _CLAIM, "stance": "supports"}],
    }
    base.update(fields)
    return base


def test_merged_alias_is_the_same_entity_as_canonical_root() -> None:
    """A merged subject must not look like a second person."""
    presentation, mapping = project_concise_inputs(snapshot=_snapshot())
    assert presentation["subject"] == "E1"
    entities = {row["handle"]: row for row in presentation["entities"]}
    assert entities["E1"]["name"] == "Nate"
    assert "same_as" not in entities["E1"]
    assert entities["E2"]["same_as"] == "E1"
    assert entities["E2"]["name"] == "Nate"
    fact = presentation["facts"][0]
    assert fact["subject"] == "E2"
    assert fact["canonical_subject"] == "E1"
    incoming = presentation["assertions"][0]
    assert incoming["subject"] == "E2"
    assert incoming["canonical_subject"] == "E1"
    assert mapping.entities["E1"] == _ROOT
    assert mapping.entities["E2"] == _ALIAS
    assert _ROOT.hex not in str(presentation)
    assert "deployment_id" not in presentation
    assert "membership_hash" not in presentation


def test_merged_object_alias_stays_equivalent() -> None:
    """Object redirects are identity relationships, not extra tournaments."""
    snapshot = _snapshot(
        kind="relation",
        facts=[
            {
                "fact_id": _FACT,
                "subject_entity_id": _ALIAS,
                "object_entity_id": _OBJECT_ALIAS,
                "predicate": "related_to",
                "statement": "Nate related_to Riverside Cup",
                "valid_from": None,
                "valid_until": None,
                "valid_precision": "unknown",
                "window_claim_ids": [_CLAIM],
                "ingested_at": _AT,
                "invalidated_at": None,
                "contradiction_group": None,
                "evidence_count": 1,
                "contradict_count": 0,
            }
        ],
        assertions=[
            {
                "application_id": _APP,
                "claim_id": _CLAIM,
                "subject_entity_id": _ALIAS,
                "object_entity_id": _OBJECT_ALIAS,
                "canonical_subject_id": _ROOT,
                "canonical_object_id": _OBJECT,
                "output_kind": "relation",
                "output_ordinal": 0,
                "normalizer_version": "test",
                "adjudicator_version": "test",
                "support_relation_id": _FACT,
                "support_observation_id": None,
                "support_stance": "supports",
                "assertion": {
                    "subject": {"name": "Nate"},
                    "predicate": "related_to",
                    "object": {"name": "Riverside Cup"},
                    "uses_claim_window": False,
                },
            }
        ],
    )
    presentation, mapping = project_concise_inputs(snapshot=snapshot)
    fact = presentation["facts"][0]
    assert fact["canonical_subject"] == presentation["subject"]
    assert mapping.entities[fact["object"]] == _OBJECT_ALIAS
    assert mapping.entities[fact["canonical_object"]] == _OBJECT
    assert fact["canonical_object"] != fact["object"]
    object_row = next(
        row for row in presentation["entities"] if row["handle"] == fact["object"]
    )
    assert object_row["same_as"] == fact["canonical_object"]


def test_unhydrated_window_witness_is_disclosed_and_not_citable() -> None:
    """Bounded claims may omit a window witness; that must not erase it."""
    presentation, mapping = project_concise_inputs(snapshot=_snapshot())
    fact = presentation["facts"][0]
    assert fact["window_claims"] == ["C1"]
    assert fact["window_claims_not_supplied"] == ["W1"]
    assert "W1" not in mapping.claims
    assert list(mapping.claims) == ["C1"]
    with pytest.raises(ValueError, match="not a claim name"):
        translate_prompt_decision(
            response=PromptFactDecision.model_validate(
                {
                    "target": "F1",
                    "window": {
                        "window": {"valid_precision": "unknown"},
                        "supporting_claims": ["W1"],
                    },
                    "confidence": 0.9,
                    "rationale": "Cite an unseen witness.",
                }
            ),
            mapping=mapping,
        )
    translated = translate_prompt_decision(
        response=PromptFactDecision.model_validate(
            {
                "target": "F1",
                "window": {
                    "window": {"valid_precision": "unknown"},
                    "supporting_claims": ["C1"],
                },
                "confidence": 0.9,
                "rationale": "Clear dates using a supplied claim.",
            }
        ),
        mapping=mapping,
    )
    assert translated.window is not None
    assert translated.window.supporting_claim_ids == (_CLAIM,)


def test_repeated_statement_is_factored_inside_assertion_content() -> None:
    """Factoring must not leave a full duplicate next to statement_ref."""
    presentation, _mapping = project_concise_inputs(snapshot=_snapshot())
    assert presentation["text"]["T1"] == "Nate won Tournament A"
    fact = presentation["facts"][0]
    claim = presentation["claims"][0]
    content = presentation["assertions"][0]["content"]
    assert fact.get("statement") is None
    assert fact["statement_ref"] == "T1"
    assert claim.get("claim_text") is None
    assert claim["claim_text_ref"] == "T1"
    assert "statement" not in content
    assert content["statement_ref"] == "T1"
    assert content["uses_claim_window"] is True
    assert content["subject"] == {"name": "Nate"}


def test_identical_text_from_distinct_sources_stays_two_claims() -> None:
    """Shared wording is not shared testimony."""
    snapshot = _snapshot(
        claims=[
            {
                "claim_id": _CLAIM,
                "doc_id": _DOC_A,
                "claim_text": "Nate won Tournament A",
                "source_span": "Nate won Tournament A",
                "asserted_at": _AT,
                "claim_valid_from": None,
                "claim_valid_until": None,
                "claim_valid_precision": "unknown",
                "claim_valid_kind": None,
                "is_current_testimony": True,
                "is_attributed": False,
                "extractor_version": "test",
            },
            {
                "claim_id": UUID(int=30),
                "doc_id": _DOC_B,
                "claim_text": "Nate won Tournament A",
                "source_span": "Nate won Tournament A",
                "asserted_at": _AT,
                "claim_valid_from": None,
                "claim_valid_until": None,
                "claim_valid_precision": "unknown",
                "claim_valid_kind": None,
                "is_current_testimony": False,
                "is_attributed": True,
                "extractor_version": "test",
            },
        ]
    )
    presentation, mapping = project_concise_inputs(snapshot=snapshot)
    assert [row["handle"] for row in presentation["claims"]] == ["C1", "C2"]
    assert presentation["claims"][0]["source"] != presentation["claims"][1]["source"]
    assert presentation["claims"][0]["current_testimony"] is True
    assert presentation["claims"][1]["attributed"] is True
    assert mapping.sources["S1"] == _DOC_A
    assert mapping.sources["S2"] == _DOC_B
    assert presentation["claims"][0]["claim_text_ref"] == "T1"
    assert presentation["claims"][1]["claim_text_ref"] == "T1"


def test_missing_evidence_row_is_disclosed_not_dropped_silently() -> None:
    """Unknown references stay visible as a count, not a quiet smaller set."""
    snapshot = _snapshot(
        evidence=[
            {"fact_id": _FACT, "claim_id": _CLAIM, "stance": "supports"},
            {"fact_id": _FACT, "claim_id": _WITNESS, "stance": "supports"},
        ]
    )
    presentation, _mapping = project_concise_inputs(snapshot=snapshot)
    assert presentation["evidence"] == [
        {"fact": "F1", "claim": "C1", "stance": "supports"}
    ]
    assert presentation["evidence_not_supplied"] == 1


def test_handle_round_trip_preserves_writer_ids() -> None:
    """F/C/A names rebuild the existing UUID decision for this attempt."""
    _presentation, mapping = project_concise_inputs(snapshot=_snapshot())
    decision = translate_prompt_decision(
        response=PromptFactDecision.model_validate(
            {
                "target": "F1",
                "stance": "contradicts",
                "contradict_with": [],
                "confidence": 0.91,
                "rationale": "Same tournament, contrary result.",
            }
        ),
        mapping=mapping,
    )
    assert decision.target.fact_id == _FACT
    assert decision.stance == "contradicts"


def test_stale_attempt_cannot_reuse_another_attempts_f1() -> None:
    """F1 is rebuilt from the frozen rows of this attempt only."""
    first = _snapshot()
    second = _snapshot(
        facts=[
            {
                "fact_id": UUID(int=77),
                "subject_entity_id": _ROOT,
                "statement": "Nate enjoyed Tournament A",
                "valid_from": None,
                "valid_until": None,
                "valid_precision": "unknown",
                "window_claim_ids": [_CLAIM],
                "ingested_at": _AT,
                "invalidated_at": None,
                "contradiction_group": None,
                "evidence_count": 1,
                "contradict_count": 0,
            }
        ]
    )
    _presentation, first_mapping = project_concise_inputs(snapshot=first)
    _presentation, second_mapping = project_concise_inputs(snapshot=second)
    answer = PromptFactDecision.model_validate(
        {"target": "F1", "confidence": 0.9, "rationale": "Attach to F1."}
    )
    assert (
        translate_prompt_decision(response=answer, mapping=first_mapping).target.fact_id
        == _FACT
    )
    assert translate_prompt_decision(
        response=answer, mapping=second_mapping
    ).target.fact_id == UUID(int=77)


def test_support_move_and_contradict_round_trip() -> None:
    """Existing writer operations keep their typed handle translations."""
    snapshot = _snapshot(
        assertions=[
            {
                "application_id": _APP,
                "claim_id": _CLAIM,
                "subject_entity_id": _ALIAS,
                "object_entity_id": None,
                "canonical_subject_id": _ROOT,
                "canonical_object_id": None,
                "output_kind": "observation",
                "output_ordinal": 0,
                "normalizer_version": "test",
                "adjudicator_version": "test",
                "support_relation_id": None,
                "support_observation_id": None,
                "support_stance": None,
                "assertion": {
                    "subject": {"name": "Nate"},
                    "statement": "Nate won Tournament A",
                    "uses_claim_window": True,
                },
            },
            {
                "application_id": _PRIOR,
                "claim_id": _CLAIM,
                "subject_entity_id": _ROOT,
                "object_entity_id": None,
                "canonical_subject_id": _ROOT,
                "canonical_object_id": None,
                "output_kind": "observation",
                "output_ordinal": 0,
                "normalizer_version": "test",
                "adjudicator_version": "test",
                "support_relation_id": None,
                "support_observation_id": _FACT,
                "support_stance": "supports",
                "assertion": {
                    "subject": {"name": "Nate"},
                    "statement": "Nate participated in Tournament A",
                    "uses_claim_window": False,
                },
            },
        ]
    )
    _presentation, mapping = project_concise_inputs(snapshot=snapshot)
    decision = translate_prompt_decision(
        response=PromptFactDecision.model_validate(
            {
                "target": "fresh",
                "new_facts": [
                    {"handle": "fresh", "assertion": "A1"},
                    {"handle": "moved", "assertion": "A2"},
                ],
                "support_moves": [
                    {"assertion": "A2", "expected_fact": "F1", "target": "moved"}
                ],
                "contradict_with": ["F1"],
                "confidence": 0.9,
                "rationale": "Split the later report and keep the disagreement.",
            }
        ),
        mapping=mapping,
    )
    assert decision.support_moves[0].application_id == _PRIOR
    assert decision.support_moves[0].expected_fact_id == _FACT
    assert decision.contradict_with == (_FACT,)


def test_wrong_kind_and_unknown_handles_fail() -> None:
    """The adapter never treats a claim name as a fact or invents F99."""
    _presentation, mapping = project_concise_inputs(snapshot=_snapshot())
    with pytest.raises(ValueError, match="not a fact name"):
        translate_prompt_decision(
            response=PromptFactDecision.model_validate(
                {"target": "C1", "confidence": 0.9, "rationale": "Wrong kind."}
            ),
            mapping=mapping,
        )
    with pytest.raises(ValueError, match="unknown fact handle"):
        translate_prompt_decision(
            response=PromptFactDecision.model_validate(
                {"target": "F99", "confidence": 0.9, "rationale": "Unknown fact."}
            ),
            mapping=mapping,
        )


def test_prompts_state_assertion_identity_in_plain_language() -> None:
    """Extractor, normalizer, and adjudicator agree on claims vs entities."""
    for prompt in (
        _SELECTION_PROMPT,
        _CLAIMIFY_PROMPT,
        _NORMALIZE_PROMPT,
        _FACT_PROMPT,
    ):
        assert "won Tournament A" in prompt
        assert "participat" in prompt
        assert "enjoy" in prompt
    assert "PURPOSE" in _FACT_PROMPT
    assert "INPUTS" in _FACT_PROMPT
    assert "DECISION RULES" in _FACT_PROMPT
    assert "EXAMPLES" in _FACT_PROMPT
    assert "OUTPUT" in _FACT_PROMPT
    assert "source_said_at" in _FACT_PROMPT
    assert (
        "never an unqualified" in _CLAIMIFY_PROMPT
        or "not automatically" in _CLAIMIFY_PROMPT
    )
    assert "uses_claim_window" in _NORMALIZE_PROMPT
    assert "THAT particular assertion" in _NORMALIZE_PROMPT
    assert "untrusted" in _FACT_PROMPT
    assert "W-names" in _FACT_PROMPT
    assert "same_as" in _FACT_PROMPT


def test_size_report_labels_bytes_and_words_not_billed_tokens() -> None:
    """Compaction evidence is byte/word (and optional tokenizer proxy), not billed tokens."""
    from rememberstack.spine.fact_applications import canonical_json

    snapshot = _snapshot()
    presentation, _mapping = project_concise_inputs(snapshot=snapshot)
    full = canonical_json(snapshot)
    compact = canonical_json(presentation)
    prompt = _FACT_PROMPT.format(inputs=compact)
    report = {
        "full_snapshot_utf8_bytes": len(full.encode()),
        "compact_input_utf8_bytes": len(compact.encode()),
        "rendered_prompt_utf8_bytes": len(prompt.encode()),
        "full_snapshot_whitespace_words": len(full.split()),
        "compact_input_whitespace_words": len(compact.split()),
    }
    try:
        import tiktoken  # type: ignore[import-not-found]

        encoder = tiktoken.get_encoding("cl100k_base")
        report["compact_input_tiktoken_cl100k_proxy"] = len(encoder.encode(compact))
        report["full_snapshot_tiktoken_cl100k_proxy"] = len(encoder.encode(full))
    except Exception:
        pass
    assert report["compact_input_utf8_bytes"] < report["full_snapshot_utf8_bytes"]
    assert "ingested_at" not in compact
    assert "membership_hash" not in compact
    assert "token" not in {key.split("_")[-1] for key in report}
    if "compact_input_tiktoken_cl100k_proxy" in report:
        assert (
            report["compact_input_tiktoken_cl100k_proxy"]
            < report["full_snapshot_tiktoken_cl100k_proxy"]
        )


def test_prompt_schema_rejects_duplicate_window_replacement() -> None:
    """Closed handle schema keeps the same consistency rules as the writer."""
    with pytest.raises(ValidationError, match="replace one window twice"):
        PromptFactDecision.model_validate(
            {
                "target": "F1",
                "window": {
                    "window": {"valid_precision": "unknown"},
                    "supporting_claims": ["C1"],
                },
                "updates": [
                    {
                        "target": "F1",
                        "window": {
                            "window": {"valid_precision": "unknown"},
                            "supporting_claims": ["C1"],
                        },
                    }
                ],
                "confidence": 0.9,
                "rationale": "Two replacements.",
            }
        )
