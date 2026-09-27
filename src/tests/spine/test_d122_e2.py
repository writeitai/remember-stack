"""Actual two-stage extraction with distant source evidence and version reuse."""

from pathlib import Path
import re

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests.workers.test_d119_versions import _bootstrap
from tests.workers.test_d119_versions import _E2_STAGES
from tests.workers.test_d119_versions import _route
from tests.workers.test_d119_versions import _VersionRig
from tests.workers.test_d119_versions import database_engine as database_engine

_INTRO = "Nate entered the Riverside Cup, also called the River Open, on 2024-05-10."
_OTHER = "Nate entered the Hill Cup on 2024-05-10."
_TARGET = "Nate said he won the River Open."
_CLAIM = "Nate said he won the Riverside Cup on 2024-05-10."


def _document(*, changed: str = "Quiet background.", prefix: str = "Preface.") -> str:
    """Put the introduction in chunk 2 and its dependent assertion in chunk 7."""
    bodies = (prefix, _INTRO, _OTHER, changed, "Local notes.", "More notes.", _TARGET)
    return (
        "\n\n".join(f"# Part {i + 1}\n\n{body}" for i, body in enumerate(bodies)) + "\n"
    )


class _ReferenceRouter:
    """Assert source-bearing inputs, then emit canned semantic decisions."""

    def __init__(self, *, cite_support: bool = True) -> None:
        """Optionally simulate an omitted evidence citation."""
        self.cite_support = cite_support
        self.claimify_calls = 0
        self.target_selection_calls = 0

    def __call__(self, prompt: str, type_name: str) -> dict[str, object]:
        """Supply source-derived labels; no paid provider or inferred test oracle."""
        if type_name == "FallbackStructureResponse":
            return {
                "sections": [
                    {"anchor": h, "occurrence_index": 0}
                    for h in re.findall(r"(?m)^# (Part \d+)\s*$", prompt)
                ]
            }
        if type_name == "SelectionResponse":
            target = prompt.split("TARGET CHUNK:\n", 1)[1]
            refs: list[dict[str, object]] = []
            for body, name, aliases in (
                (_INTRO, "Riverside Cup", ["River Open"]),
                (_OTHER, "Hill Cup", []),
            ):
                if body not in target:
                    continue
                label = re.search(r"\[(S\d+)\] TARGET:\n" + re.escape(body), prompt)
                assert label is not None
                refs.append(
                    {"name": name, "aliases": aliases, "source_refs": [label.group(1)]}
                )
            candidates = []
            if _TARGET in target:
                self.target_selection_calls += 1
                candidates = [{"source_span": _TARGET, "outcome": "keep"}]
            # No propositions may still publish a useful introduction.
            return {"candidates": candidates, "references": refs}
        if type_name == "ClaimifyResponse":
            self.claimify_calls += 1
            assert "Riverside Cup also called River Open" in prompt
            assert "Hill Cup" in prompt  # Same date does not merge the two events.
            origin = re.search(
                r"\[(S\d+)\] TARGET \(origin-eligible\):\n" + re.escape(_TARGET), prompt
            )
            support = re.search(r'(S\d+): "' + re.escape(_INTRO) + '"', prompt)
            assert origin is not None and support is not None
            assert (
                prompt.count(_INTRO) == 1
            )  # Cards do not duplicate their evidence block.
            claim_refs = [origin.group(1)]
            if self.cite_support:
                claim_refs.append(support.group(1))
            return {
                "claims": [
                    {
                        "claim_text": _CLAIM,
                        "source_refs": claim_refs,
                        "is_attributed": True,
                        "added_context": [
                            {
                                "text": "Riverside Cup on 2024-05-10",
                                "source_kind": "neighbour",
                            }
                        ],
                        "entailment_self_verdict": True,
                    }
                ]
            }
        return _route(prompt, type_name)


@pytest.mark.parametrize("cite_support", (True, False))
def test_chunk_two_reference_grounds_chunk_seven_only_when_cited(
    database_engine: Engine, tmp_path: Path, cite_support: bool
) -> None:
    """Distant aliases preserve attribution and both exact source ranges."""
    _bootstrap(database_engine)
    router = _ReferenceRouter(cite_support=cite_support)
    rig = _VersionRig(engine=database_engine, root=tmp_path, router=router)
    document = _document()
    rig.observe(extra="", markdown=document)
    rig.drain(stages=_E2_STAGES)
    assert router.claimify_calls == 1
    with database_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT c.claim_text,c.is_attributed,cc.evidence_spans FROM claims c JOIN chunk_claims cc ON cc.claim_id=c.claim_id AND cc.chunk_id=c.chunk_id"
                )
            )
            .mappings()
            .all()
        )
        cards = (
            connection.execute(
                text(
                    "SELECT jsonb_array_length(cards) FROM selection_results WHERE jsonb_array_length(cards)>0"
                )
            )
            .scalars()
            .all()
        )
        assert cards == [1, 1]
        if not cite_support:
            assert rows == []
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM claim_extraction_decisions WHERE decision_type='grounding_rejected'"
                    )
                ).scalar_one()
                == 1
            )
            return
    assert len(rows) == 1
    assert rows[0]["claim_text"] == _CLAIM
    assert rows[0]["is_attributed"] is True
    spans = rows[0]["evidence_spans"]
    assert [document[s["char_start"] : s["char_end"]] for s in spans] == [
        _TARGET,
        _INTRO,
    ]


def test_changed_zero_card_producer_rechecks_claimify_but_reuses_selection(
    database_engine: Engine, tmp_path: Path
) -> None:
    """A changed earlier input invalidates reuse even when its old card set was empty."""
    _bootstrap(database_engine)
    router = _ReferenceRouter()
    rig = _VersionRig(engine=database_engine, root=tmp_path, router=router)
    rig.observe(extra="", markdown=_document())
    rig.drain(stages=_E2_STAGES)
    rig.observe(
        extra="",
        markdown=_document(changed="A competing introduction could now appear here."),
    )
    rig.drain(stages=_E2_STAGES)
    assert router.target_selection_calls == 1
    assert router.claimify_calls == 2
    with database_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM claims WHERE claim_text=:claim"),
                {"claim": _CLAIM},
            ).scalar_one()
            == 2
        )
