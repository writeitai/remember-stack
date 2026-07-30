"""Deterministic Mem2Act tool-call scoring."""

from __future__ import annotations

from typing import Any
from typing import Mapping


def normalize_value(value: Any) -> Any:
    """Normalize values for soft equality."""
    if isinstance(value, str):
        return value.strip().lower()
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, list):
        return [normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): normalize_value(item) for key, item in value.items()}
    return value


def arguments_match(
    *, gold: Mapping[str, Any], predicted: Mapping[str, Any] | None
) -> bool:
    """True when every gold key is present and values match under normalization."""
    if predicted is None:
        return False
    for key, gold_value in gold.items():
        if key not in predicted:
            return False
        if normalize_value(gold_value) != normalize_value(predicted[key]):
            return False
    return True


def score_tool_call(
    *,
    gold_name: str,
    gold_arguments: Mapping[str, Any],
    predicted_name: str | None,
    predicted_arguments: Mapping[str, Any] | None,
) -> tuple[bool, bool, bool]:
    """Return (tool_name_ok, args_ok, item_ok)."""
    tool_name_ok = predicted_name is not None and predicted_name == gold_name
    args_ok = arguments_match(gold=gold_arguments, predicted=predicted_arguments)
    return tool_name_ok, args_ok, tool_name_ok and args_ok
