"""Frozen reader prompts for Mem2Act tool-call generation."""

from __future__ import annotations

import json
from typing import Any
from typing import Mapping
from typing import Sequence


def build_reader_prompt(
    *,
    query: str,
    tool_schema: Mapping[str, Any],
    memory_strings: Sequence[str] = (),
    transcript: str | None = None,
) -> str:
    """Build the single-shot tool-call prompt for the frozen reader."""
    sections = [
        "You are a tool-calling assistant. Output ONE JSON object only:",
        '{"name": "<tool_name>", "arguments": { ... }}',
        "No markdown fences. No commentary.",
        "",
        "## Available tool schema",
        json.dumps(tool_schema, ensure_ascii=False, indent=2),
        "",
        "## User request",
        query.strip(),
    ]
    if memory_strings:
        sections.extend(["", "## Retrieved memory"])
        for index, item in enumerate(memory_strings, start=1):
            sections.append(f"### Memory {index}")
            sections.append(item.strip())
    if transcript:
        sections.extend(["", "## Conversation history", transcript.strip()])
    if not memory_strings and not transcript:
        sections.extend(
            [
                "",
                "## Conversation history",
                "(none provided — use only the request and schema defaults)",
            ]
        )
    return "\n".join(sections) + "\n"


def parse_tool_call_json(
    text: str,
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """Parse model output into (name, arguments, error)."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        # Take first complete JSON object
        start = cleaned.find("{")
        if start < 0:
            return None, None, "no_json_object"
        decoder = json.JSONDecoder()
        payload, _end = decoder.raw_decode(cleaned[start:])
    except json.JSONDecodeError as error:
        return None, None, f"json_error:{error}"
    if not isinstance(payload, dict):
        return None, None, "not_object"
    name = payload.get("name")
    arguments = payload.get("arguments")
    if not isinstance(name, str) or not name:
        return None, None, "missing_name"
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return name, None, "arguments_not_object"
    return name, arguments, None
