"""Pinned LoCoMo parsing and committed question-manifest validation."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Final

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import JsonValue

from benchmarks.locomo.model import Category
from benchmarks.locomo.model import LoCoMoDataset
from benchmarks.locomo.model import LoCoMoQuestion
from benchmarks.locomo.model import LoCoMoSample
from benchmarks.locomo.model import LoCoMoSession
from benchmarks.locomo.model import LoCoMoTurn
from benchmarks.locomo.model import QuestionManifest

DATASET_COMMIT: Final = "3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376"
DATASET_SHA256: Final = (
    "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4"
)
EXPECTED_COUNTS: Final = {
    "samples": 10,
    "sessions": 272,
    "turns": 5_882,
    "image_turns": 1_226,
    "questions": 1_986,
    "category_1": 282,
    "category_2": 321,
    "category_3": 96,
    "category_4": 841,
    "category_5": 446,
}
_SESSION_KEY = re.compile(r"^session_([1-9][0-9]*)$")
_SESSION_TIMESTAMP = re.compile(
    r"^(?P<hour>[1-9]|1[0-2]):(?P<minute>[0-5][0-9]) "
    r"(?P<meridiem>am|pm) on (?P<day>[1-9]|[12][0-9]|3[01]) "
    r"(?P<month>[A-Za-z]+), (?P<year>[0-9]{4})$"
)
_MONTHS: Final = {
    month: ordinal
    for ordinal, month in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        start=1,
    )
}
_MANIFEST_ROOT = Path(__file__).with_name("manifests")


class DatasetValidationError(ValueError):
    """The supplied dataset or committed manifest is not the pinned protocol."""


class _RawQuestion(BaseModel):
    """The exact QA fields used by the adapter."""

    model_config = ConfigDict(extra="forbid")

    question: str
    answer: str | int | None = None
    adversarial_answer: str | None = None
    evidence: list[str]
    category: int


class _RawSample(BaseModel):
    """The official six-field sample boundary."""

    model_config = ConfigDict(extra="forbid")

    sample_id: str
    conversation: dict[str, JsonValue]
    qa: list[_RawQuestion]
    observation: JsonValue
    session_summary: JsonValue
    event_summary: JsonValue


def load_dataset(
    path: Path,
    *,
    required_sha256: str | None = DATASET_SHA256,
    require_pinned_counts: bool = True,
) -> LoCoMoDataset:
    """Load and validate LoCoMo without downloading or modifying it."""
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if required_sha256 is not None and digest != required_sha256:
        raise DatasetValidationError(
            f"dataset SHA-256 {digest} does not match required {required_sha256}"
        )
    try:
        raw_json = json.loads(content)
    except json.JSONDecodeError as error:
        raise DatasetValidationError(f"dataset is not valid JSON: {error}") from error
    if not isinstance(raw_json, list):
        raise DatasetValidationError("dataset root must be a JSON array")
    raw_samples = tuple(_RawSample.model_validate(item) for item in raw_json)
    samples = tuple(
        _parse_sample(raw=raw, sample_position=position)
        for position, raw in enumerate(raw_samples)
    )
    dataset = LoCoMoDataset(sha256=digest, samples=samples)
    _require_unique_ids(dataset=dataset)
    if require_pinned_counts:
        _require_counts(dataset=dataset)
    return dataset


def load_manifest(tier: str) -> QuestionManifest:
    """Load one checked-in exact item selection and validate its own hash."""
    if tier not in {"smoke", "development", "publication"}:
        raise DatasetValidationError(f"unknown LoCoMo tier {tier!r}")
    manifest = QuestionManifest.model_validate_json(
        (_MANIFEST_ROOT / f"{tier}.json").read_text(encoding="utf-8")
    )
    if manifest.tier != tier:
        raise DatasetValidationError(
            f"manifest file {tier!r} declares tier {manifest.tier!r}"
        )
    if manifest.dataset_commit != DATASET_COMMIT:
        raise DatasetValidationError(
            "manifest dataset commit is not RS-LoCoMo-Full-v17"
        )
    if manifest.dataset_sha256 != DATASET_SHA256:
        raise DatasetValidationError("manifest dataset hash is not RS-LoCoMo-Full-v17")
    actual = item_ids_hash(item_ids=manifest.item_ids)
    if actual != manifest.item_ids_sha256:
        raise DatasetValidationError(
            f"manifest item hash {actual} does not match declared "
            f"{manifest.item_ids_sha256}"
        )
    if len(set(manifest.item_ids)) != len(manifest.item_ids):
        raise DatasetValidationError("manifest contains duplicate item IDs")
    return manifest


def validate_manifest(
    *, dataset: LoCoMoDataset, manifest: QuestionManifest
) -> tuple[LoCoMoQuestion, ...]:
    """Resolve every manifest ID, rejecting missing or adversarial questions."""
    questions = dataset.question_map()
    missing = tuple(
        item_id for item_id in manifest.item_ids if item_id not in questions
    )
    if missing:
        raise DatasetValidationError(
            f"manifest contains missing item IDs: {missing[:3]}"
        )
    resolved = tuple(questions[item_id] for item_id in manifest.item_ids)
    if any(question.category == 5 for question in resolved):
        raise DatasetValidationError("manifest contains excluded category 5")
    if any(question.answer is None for question in resolved):
        raise DatasetValidationError("manifest contains a retained null answer")
    return resolved


def item_ids_hash(*, item_ids: tuple[str, ...]) -> str:
    """Hash the exact ordered ID list with an unambiguous trailing newline."""
    return hashlib.sha256(("\n".join(item_ids) + "\n").encode()).hexdigest()


def parse_session_timestamp(*, timestamp: str) -> datetime:
    """Parse one unzoned LoCoMo wall time using the adapter's assumed-UTC policy."""
    match = _SESSION_TIMESTAMP.fullmatch(timestamp)
    if match is None:
        raise DatasetValidationError(
            f"LoCoMo session timestamp has unsupported format: {timestamp!r}"
        )
    month = _MONTHS.get(match.group("month"))
    if month is None:
        raise DatasetValidationError(
            f"LoCoMo session timestamp has unknown month: {timestamp!r}"
        )
    hour = int(match.group("hour"))
    if match.group("meridiem") == "am":
        hour = 0 if hour == 12 else hour
    elif hour != 12:
        hour += 12
    try:
        return datetime(
            year=int(match.group("year")),
            month=month,
            day=int(match.group("day")),
            hour=hour,
            minute=int(match.group("minute")),
            tzinfo=timezone.utc,
        )
    except ValueError as error:
        raise DatasetValidationError(
            f"LoCoMo session timestamp is not a valid date: {timestamp!r}"
        ) from error


def manifest_bytes_hash(*, manifest: QuestionManifest) -> str:
    """Hash one canonical manifest value for the run fingerprint."""
    return hashlib.sha256(
        manifest.model_dump_json(exclude_none=False).encode()
    ).hexdigest()


def _parse_sample(*, raw: _RawSample, sample_position: int) -> LoCoMoSample:
    """Parse nested list-valued sessions while ignoring orphan timestamps."""
    conversation = raw.conversation
    speaker_a = _required_string(conversation=conversation, key="speaker_a")
    speaker_b = _required_string(conversation=conversation, key="speaker_b")
    sessions: list[LoCoMoSession] = []
    for key, value in conversation.items():
        match = _SESSION_KEY.fullmatch(key)
        if match is None or not isinstance(value, list):
            continue
        ordinal = int(match.group(1))
        timestamp = _required_string(
            conversation=conversation, key=f"session_{ordinal}_date_time"
        )
        turns = tuple(
            _parse_turn(value=turn, sample_id=raw.sample_id, session_ordinal=ordinal)
            for turn in value
        )
        if not turns:
            raise DatasetValidationError(
                f"{raw.sample_id} session_{ordinal} has no turns"
            )
        sessions.append(
            LoCoMoSession(
                ordinal=ordinal,
                session_id=f"D{ordinal}",
                timestamp=timestamp,
                source_modified_at=parse_session_timestamp(timestamp=timestamp),
                source_timezone_basis="assumed_utc",
                turns=turns,
            )
        )
    sessions.sort(key=lambda session: session.ordinal)
    questions = tuple(
        _parse_question(raw=question, sample_id=raw.sample_id, position=position)
        for position, question in enumerate(raw.qa)
    )
    if not raw.sample_id:
        raise DatasetValidationError(f"sample at position {sample_position} has no ID")
    return LoCoMoSample(
        sample_id=raw.sample_id,
        speaker_a=speaker_a,
        speaker_b=speaker_b,
        sessions=tuple(sessions),
        questions=questions,
    )


def _parse_turn(
    *, value: JsonValue, sample_id: str, session_ordinal: int
) -> LoCoMoTurn:
    """Validate one official turn without fetching its image URLs."""
    if not isinstance(value, dict):
        raise DatasetValidationError(
            f"{sample_id} session_{session_ordinal} contains a non-object turn"
        )
    allowed = {
        "speaker",
        "dia_id",
        "text",
        "blip_caption",
        "img_url",
        "query",
        "re-download",
    }
    unexpected = set(value) - allowed
    if unexpected:
        raise DatasetValidationError(
            f"{sample_id} turn carries unexpected fields {sorted(unexpected)}"
        )
    dia_id = value.get("dia_id")
    expected_prefix = f"D{session_ordinal}:"
    if not isinstance(dia_id, str) or not dia_id.startswith(expected_prefix):
        raise DatasetValidationError(
            f"{sample_id} session_{session_ordinal} has invalid dia_id {dia_id!r}"
        )
    speaker = value.get("speaker")
    text = value.get("text")
    caption = value.get("blip_caption")
    query = value.get("query")
    image_urls = value.get("img_url")
    if not isinstance(speaker, str) or not speaker:
        raise DatasetValidationError(f"{dia_id} has no speaker")
    if not isinstance(text, str):
        raise DatasetValidationError(f"{dia_id} has non-string text")
    if caption is not None and not isinstance(caption, str):
        raise DatasetValidationError(f"{dia_id} has invalid blip_caption")
    if query is not None and not isinstance(query, str):
        raise DatasetValidationError(f"{dia_id} has invalid query")
    if image_urls is None:
        parsed_urls: tuple[str, ...] = ()
    elif isinstance(image_urls, list) and all(
        isinstance(item, str) for item in image_urls
    ):
        parsed_urls = tuple(item for item in image_urls if isinstance(item, str))
    else:
        raise DatasetValidationError(f"{dia_id} has invalid img_url")
    return LoCoMoTurn(
        speaker=speaker,
        dia_id=dia_id,
        text=text,
        blip_caption=caption,
        image_urls=parsed_urls,
        image_query=query,
    )


def _parse_question(
    *, raw: _RawQuestion, sample_id: str, position: int
) -> LoCoMoQuestion:
    """Assign one positional ID and canonicalize a retained answer."""
    if raw.category not in {1, 2, 3, 4, 5}:
        raise DatasetValidationError(
            f"{sample_id} question {position} has category {raw.category}"
        )
    if raw.category != 5 and raw.answer is None:
        raise DatasetValidationError(
            f"{sample_id} retained question {position} has null answer"
        )
    return LoCoMoQuestion(
        item_id=f"{sample_id}/qa/{position:04d}",
        sample_id=sample_id,
        question=raw.question,
        answer=None if raw.answer is None else str(raw.answer),
        evidence=tuple(raw.evidence),
        category=_category(raw.category),
    )


def _required_string(*, conversation: dict[str, JsonValue], key: str) -> str:
    """Read one required non-empty conversation string."""
    value = conversation.get(key)
    if not isinstance(value, str) or not value:
        raise DatasetValidationError(f"conversation field {key!r} is missing")
    return value


def _category(value: int) -> Category:
    """Narrow a validated numeric category to its literal type."""
    if value == 1:
        return 1
    if value == 2:
        return 2
    if value == 3:
        return 3
    if value == 4:
        return 4
    if value == 5:
        return 5
    raise DatasetValidationError(f"invalid category {value}")


def _require_unique_ids(*, dataset: LoCoMoDataset) -> None:
    """Reject duplicate sample, question, or within-sample dialog IDs."""
    sample_ids = [sample.sample_id for sample in dataset.samples]
    if len(sample_ids) != len(set(sample_ids)):
        raise DatasetValidationError("dataset contains duplicate sample IDs")
    item_ids = [
        question.item_id for sample in dataset.samples for question in sample.questions
    ]
    if len(item_ids) != len(set(item_ids)):
        raise DatasetValidationError("dataset contains duplicate question IDs")
    for sample in dataset.samples:
        dialog_ids = [
            turn.dia_id for session in sample.sessions for turn in session.turns
        ]
        if len(dialog_ids) != len(set(dialog_ids)):
            raise DatasetValidationError(
                f"{sample.sample_id} contains duplicate dialog IDs"
            )


def _require_counts(*, dataset: LoCoMoDataset) -> None:
    """Require every audited aggregate of the pinned conversations."""
    counts = {
        "samples": len(dataset.samples),
        "sessions": sum(len(sample.sessions) for sample in dataset.samples),
        "turns": sum(
            len(session.turns)
            for sample in dataset.samples
            for session in sample.sessions
        ),
        "image_turns": sum(
            bool(turn.blip_caption or turn.image_urls or turn.image_query)
            for sample in dataset.samples
            for session in sample.sessions
            for turn in session.turns
        ),
        "questions": sum(len(sample.questions) for sample in dataset.samples),
    }
    counts.update(
        {
            f"category_{category}": sum(
                question.category == category
                for sample in dataset.samples
                for question in sample.questions
            )
            for category in range(1, 6)
        }
    )
    if counts != EXPECTED_COUNTS:
        raise DatasetValidationError(
            f"pinned dataset aggregate mismatch: expected {EXPECTED_COUNTS}, got {counts}"
        )
