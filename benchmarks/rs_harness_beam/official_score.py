"""BEAM official-style scoring: rubric nugget LLM-as-judge (+ Kendall τ-b for event order).

Faithful port of the evaluation logic in the upstream BEAM repository
(``src/evaluation/compute_metrics.py`` / ``run_evaluation.py``) and paper §2.4:

- For each probing question, gold rubrics are **atomic nuggets**.
- An LLM judge scores each nugget 0.0 / 0.5 / 1.0 against the system response
  (semantic paraphrase allowed — not string equality).
- Question score = mean of nugget scores.
- **Event ordering** also computes Kendall τ-b over LLM-aligned event lists,
  combined with an F1 over the alignment (``final_score = tau_norm * f1``),
  plus the same nugget LLM-judge mean.

This is **not** the RememberStack containment placeholder. It is the
paper/repo evaluation path, adapted to OpenRouter chat completions and our
run-dir answer layout.

Sources:
- https://github.com/mohammadtavakoli78/BEAM (evaluation/)
- Tavakoli et al., *Beyond a Million Tokens…*, §2.4
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from typing import Any
from typing import Final
from typing import Iterable
from typing import Mapping
from typing import Sequence
import urllib.error
import urllib.request

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_DEFAULT_JUDGE_PROMPT = (_FIXTURES / "unified_llm_judge_prompt.txt").read_text(
    encoding="utf-8"
)
_SMOKE_100K_1_RUBRICS = _FIXTURES / "beam_smoke_100k_1" / "probing_questions.json"

_DEFAULT_JUDGE_MODEL: Final = "openai/gpt-5.6-luna"
_OPENROUTER_URL: Final = "https://openrouter.ai/api/v1/chat/completions"

# Abilities where the primary score is mean nugget LLM-judge (paper §2.4).
_NUGGET_ABILITIES: Final = frozenset(
    {
        "abstention",
        "contradiction_resolution",
        "information_extraction",
        "instruction_following",
        "knowledge_update",
        "multi_session_reasoning",
        "preference_following",
        "summarization",
        "temporal_reasoning",
    }
)


class OfficialScoreError(RuntimeError):
    """BEAM official scoring could not complete."""


@dataclass(frozen=True)
class NuggetJudgement:
    """One LLM-judge verdict for a single rubric nugget."""

    rubric_item: str
    score: float
    reason: str


@dataclass(frozen=True)
class ItemOfficialScore:
    """Official-style scores for one probing question."""

    question_id: str
    ability: str
    question: str
    response: str
    rubric: tuple[str, ...]
    llm_judge_score: float
    nugget_judgements: tuple[NuggetJudgement, ...]
    event_ordering: Mapping[str, float] | None = None


def load_beam_rubrics(*, path: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """Load BEAM probing_questions.json (keyed by ability → list of items)."""
    source = path or _SMOKE_100K_1_RUBRICS
    if not source.is_file():
        raise OfficialScoreError(f"missing BEAM rubrics fixture: {source}")
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise OfficialScoreError(f"rubrics root must be object: {source}")
    return data


def match_rubric(
    *,
    ability: str,
    question: str,
    rubrics_by_ability: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[str, ...]:
    """Find the BEAM rubric list for a smoke question by ability + exact question text."""
    items = rubrics_by_ability.get(ability)
    if not items:
        raise OfficialScoreError(f"no BEAM rubrics for ability {ability!r}")
    q_norm = question.strip()
    for item in items:
        if str(item.get("question", "")).strip() == q_norm:
            rubric = item.get("rubric")
            if not isinstance(rubric, list) or not rubric:
                raise OfficialScoreError(
                    f"empty rubric for ability={ability!r} question={q_norm[:80]!r}"
                )
            return tuple(str(x) for x in rubric)
    raise OfficialScoreError(
        f"no BEAM rubric match for ability={ability!r} question={q_norm[:80]!r}"
    )


def parse_judge_json(text: str) -> dict[str, Any]:
    """Parse the judge's JSON object from a model response."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
        if fence:
            cleaned = fence.group(1).strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise OfficialScoreError(f"judge returned non-JSON: {text[:200]!r}")


def _clamp_score(value: object) -> float:
    """Normalize judge scores to {0.0, 0.5, 1.0} when close; else clamp [0,1]."""
    try:
        score = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise OfficialScoreError(f"invalid judge score {value!r}") from error
    for candidate in (0.0, 0.5, 1.0):
        if abs(score - candidate) < 1e-6:
            return candidate
    return max(0.0, min(1.0, score))


class OpenRouterJudge:
    """Minimal OpenRouter chat client for BEAM LLM-as-judge calls."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = _DEFAULT_JUDGE_MODEL,
        timeout_s: float = 120.0,
    ) -> None:
        """Bind API key and judge model id (caller supplies the key explicitly)."""
        if not api_key:
            raise OfficialScoreError(
                "OpenRouter API key is required for BEAM official scoring"
            )
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s

    def complete(self, *, system: str | None, user: str) -> str:
        """One chat completion; returns assistant text content."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 800,
        }
        request = urllib.request.Request(
            _OPENROUTER_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://remember.dev",
                "X-Title": "BEAM-official-scorer",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise OfficialScoreError(
                f"OpenRouter judge HTTP {error.code}: {detail}"
            ) from error
        try:
            return str(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as error:
            raise OfficialScoreError(f"unexpected OpenRouter body: {body!r}") from error


def judge_nugget(
    *,
    judge: OpenRouterJudge,
    question: str,
    rubric_item: str,
    response: str,
    prompt_template: str = _DEFAULT_JUDGE_PROMPT,
) -> NuggetJudgement:
    """Score one rubric nugget with the BEAM unified LLM-judge prompt."""
    prompt = (
        prompt_template.replace("<question>", question)
        .replace("<rubric_item>", rubric_item)
        .replace("<llm_response>", response)
    )
    # Upstream prompt already embeds QUESTION; the base template in the paper
    # listing uses <question> optionally — our fixture includes it.
    raw = judge.complete(system=None, user=prompt)
    parsed = parse_judge_json(raw)
    score = _clamp_score(parsed.get("score"))
    reason = str(parsed.get("reason") or "")
    return NuggetJudgement(rubric_item=rubric_item, score=score, reason=reason)


def mean_nugget_score(*, judgements: Sequence[NuggetJudgement]) -> float:
    """Average nugget scores (paper ability metric)."""
    if not judgements:
        return 0.0
    return sum(item.score for item in judgements) / len(judgements)


def llm_equivalence(*, judge: OpenRouterJudge, left: str, right: str) -> bool:
    """BEAM event-order equivalence: same event/fact? YES/NO."""
    system = (
        "You are a binary classifier. If the TWO snippets describe the SAME "
        "event/fact, reply YES. Otherwise reply NO. No extra words. "
        "DO NOT provide any explanation."
    )
    user = f"First snippet: {left}\nSecond snippet: {right}"
    raw = judge.complete(system=system, user=user).strip().lower()
    return "yes" in raw


def align_events_with_llm(
    *, judge: OpenRouterJudge, reference: Sequence[str], system: Sequence[str]
) -> tuple[list[str], list[str]]:
    """1-to-1 map system events onto reference events via LLM equivalence."""
    used: set[int] = set()
    system_out: list[str] = []
    for system_item in system:
        matched: int | None = None
        for index, ref_item in enumerate(reference):
            if index in used:
                continue
            if llm_equivalence(judge=judge, left=ref_item, right=system_item):
                matched = index
                break
        if matched is not None:
            system_out.append(reference[matched])
            used.add(matched)
        else:
            system_out.append(system_item)
    return list(reference), system_out


def _kendall_tau_b(x: Sequence[float], y: Sequence[float]) -> float:
    """Kendall τ-b without SciPy (concordant/discordant + tie correction)."""
    n = len(x)
    if n < 2:
        return 0.0
    concordant = 0
    discordant = 0
    ties_x = 0
    ties_y = 0
    for i in range(n):
        for j in range(i + 1, n):
            dx = x[i] - x[j]
            dy = y[i] - y[j]
            if dx == 0 and dy == 0:
                continue
            if dx == 0:
                ties_x += 1
            elif dy == 0:
                ties_y += 1
            elif dx * dy > 0:
                concordant += 1
            else:
                discordant += 1
    n0 = n * (n - 1) // 2
    denominator = math.sqrt((n0 - ties_x) * (n0 - ties_y))
    if denominator <= 0:
        return 0.0
    return (concordant - discordant) / denominator


def event_ordering_score(
    *, judge: OpenRouterJudge, reference_list: Sequence[str], system_list: Sequence[str]
) -> dict[str, float]:
    """BEAM event-ordering: τ-b over LLM-aligned lists × set F1 (upstream)."""
    reference_canon, system_canon = align_events_with_llm(
        judge=judge, reference=reference_list, system=system_list
    )
    ref_set = set(reference_canon)
    sys_set = set(system_canon)
    tp = len(ref_set & sys_set)
    fp = len([item for item in system_canon if item not in ref_set])
    fn = len([item for item in reference_canon if item not in sys_set])
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    union = list(dict.fromkeys([*reference_canon, *system_canon]))
    tie_rank = len(union) + 1

    def to_rank(seq: Sequence[str]) -> list[float]:
        ranks = {item: float(index + 1) for index, item in enumerate(seq)}
        return [ranks.get(item, float(tie_rank)) for item in union]

    tau_b = _kendall_tau_b(to_rank(reference_canon), to_rank(system_canon))
    tau_b_norm = (tau_b + 1.0) / 2.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tau_norm": tau_b_norm,
        "final_score": tau_b_norm * f1,
    }


def score_item_official(
    *,
    judge: OpenRouterJudge,
    question_id: str,
    ability: str,
    question: str,
    response: str,
    rubric: Sequence[str],
) -> ItemOfficialScore:
    """Score one item with BEAM official-style metrics."""
    judgements = tuple(
        judge_nugget(
            judge=judge, question=question, rubric_item=item, response=response
        )
        for item in rubric
    )
    llm_mean = mean_nugget_score(judgements=judgements)
    event: Mapping[str, float] | None = None
    if ability == "event_ordering":
        system_list = [line.strip() for line in response.splitlines() if line.strip()]
        if not system_list:
            system_list = [response.strip()] if response.strip() else []
        event = event_ordering_score(
            judge=judge, reference_list=list(rubric), system_list=system_list
        )
    return ItemOfficialScore(
        question_id=question_id,
        ability=ability,
        question=question,
        response=response,
        rubric=tuple(rubric),
        llm_judge_score=llm_mean,
        nugget_judgements=judgements,
        event_ordering=event,
    )


def score_run_dir_official(
    *,
    run_dir: Path,
    api_key: str,
    arm: str = "rs",
    rubrics_path: Path | None = None,
    judge_model: str = _DEFAULT_JUDGE_MODEL,
) -> dict[str, Any]:
    """Score answers in a harness run directory with BEAM official metrics.

    Expects:
    - ``questions.json``: list of {question_id, question, ability, ...}
    - ``state.json``: ``answers[arm][question_id].answer``
    """
    run_dir = Path(run_dir)
    questions = json.loads((run_dir / "questions.json").read_text(encoding="utf-8"))
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    answers = (state.get("answers") or {}).get(arm) or {}
    if not answers:
        raise OfficialScoreError(f"no answers for arm={arm!r} in {run_dir}")

    rubrics_by_ability = load_beam_rubrics(path=rubrics_path)
    judge = OpenRouterJudge(api_key=api_key, model=judge_model)

    items: list[dict[str, Any]] = []
    for question in questions:
        qid = str(question.get("question_id") or question.get("item_id"))
        ability = str(question.get("ability") or "")
        q_text = str(question.get("question") or "")
        payload = answers.get(qid) or {}
        response = str(payload.get("answer") or "")
        rubric = match_rubric(
            ability=ability, question=q_text, rubrics_by_ability=rubrics_by_ability
        )
        scored = score_item_official(
            judge=judge,
            question_id=qid,
            ability=ability,
            question=q_text,
            response=response,
            rubric=rubric,
        )
        primary = scored.llm_judge_score
        if scored.event_ordering is not None:
            # Upstream reports both; paper highlights τ-b path for EO.
            primary = float(scored.event_ordering.get("final_score", primary))
        items.append(
            {
                "question_id": scored.question_id,
                "ability": scored.ability,
                "question": scored.question,
                "response": scored.response,
                "rubric": list(scored.rubric),
                "llm_judge_score": scored.llm_judge_score,
                "primary_score": primary,
                "nuggets": [
                    {
                        "rubric_item": nugget.rubric_item,
                        "score": nugget.score,
                        "reason": nugget.reason,
                    }
                    for nugget in scored.nugget_judgements
                ],
                "event_ordering": dict(scored.event_ordering)
                if scored.event_ordering
                else None,
            }
        )

    by_ability: dict[str, list[float]] = {}
    for item in items:
        by_ability.setdefault(str(item["ability"]), []).append(
            float(item["primary_score"])
        )
    ability_means = {
        ability: (sum(scores) / len(scores) if scores else 0.0)
        for ability, scores in sorted(by_ability.items())
    }
    overall = (
        sum(float(item["primary_score"]) for item in items) / len(items)
        if items
        else 0.0
    )
    report = {
        "protocol": "RS-Harness-BEAM-v1",
        "scorer": "beam_official_nugget_llm_judge",
        "source": (
            "mohammadtavakoli78/BEAM src/evaluation/compute_metrics.py "
            "+ paper §2.4 (nugget LLM-judge; Kendall τ-b for event_ordering)"
        ),
        "judge_model": judge_model,
        "arm": arm,
        "n": len(items),
        "overall_mean": overall,
        "by_ability": ability_means,
        "items": items,
    }
    out = run_dir / f"score_report_official_{arm}.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(out)
    return report


def iter_ability_scores(report: Mapping[str, Any]) -> Iterable[tuple[str, float]]:
    """Yield (ability, mean) pairs from an official report."""
    by_ability = report.get("by_ability") or {}
    if isinstance(by_ability, Mapping):
        for ability, score in by_ability.items():
            yield str(ability), float(score)
