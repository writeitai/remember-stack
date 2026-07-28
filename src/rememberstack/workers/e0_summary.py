"""D79 bottom-up section summaries and root placement.

Every provider request is bounded before it crosses the port. Leaves and
oversized parent preambles shard only between canonical blocks; wide child
sets reduce through a balanced, versioned fan-in. Independent sections at one
tree depth run in parallel, while the tree itself reduces bottom-up.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
from math import ceil
from typing import Final
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.core import count_tokens
from rememberstack.model import Block
from rememberstack.model import ModelRequest
from rememberstack.model import ObjectKey
from rememberstack.model import ProviderCallError
from rememberstack.model import ProviderCallUsage
from rememberstack.model import RootSummaryPlacementResponse
from rememberstack.model import SectionSummaryResponse
from rememberstack.model import SnappedSection
from rememberstack.model import StructureSource
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.ports.object_store import ObjectStorePort
from rememberstack.spine.document_catalog import DocumentCatalog

SUMMARY_CALL_TOKEN_CEILING: Final = 2_048
"""Hard per-request ceiling under the pinned whitespace-token counter."""

SUMMARY_BALANCED_FAN_IN: Final = 8
"""Maximum ordered child/partial one-liners in one reduction call."""

SUMMARY_MAX_CHARS: Final = 512
"""Closed response-schema ceiling for every one-line summary."""

SUMMARY_PARALLELISM: Final = 8
"""Maximum independent same-depth section calls in flight."""

SUMMARY_TOKEN_COUNTER_VERSION: Final = "whitespace-token-counter-v1"
"""Names ``count_tokens`` semantics in summary-generation provenance."""

E0_SUMMARY_VERSION: Final = (
    "e0-summary-2026.07a:d79-bottom-up:block-shard-v1:"
    f"{SUMMARY_TOKEN_COUNTER_VERSION}:ceiling{SUMMARY_CALL_TOKEN_CEILING}:"
    f"balanced-fan-in{SUMMARY_BALANCED_FAN_IN}:line{SUMMARY_MAX_CHARS}"
)
"""Bottom-up summarizer algorithm and all output-affecting constants."""

E0_PLACEMENT_VERSION: Final = "e0-placement-2026.07a:d79-root-reduction-v1"
"""D39 advisory placement emitted by the document-level summary reduction."""

_SUMMARY_INSTRUCTION: Final = """Write exactly one factual orientation line \
of at most 512 characters. Use only the supplied section material. Do not add \
bullets, labels, markdown, or a newline."""

_ROOT_INSTRUCTION: Final = """Return exactly two fields: a one-line factual \
document summary of at most 512 characters, and one advisory corpus path. The \
path must begin and end with "/" and contain topical directory names only. \
Use only the supplied document material."""

_REDUCE_INSTRUCTION: Final = """Compose the ordered one-line inputs into \
exactly one factual orientation line of at most 512 characters. Do not add \
bullets, labels, markdown, or a newline."""

_SECTION_TEMPLATE: Final = """{instruction}
Call kind: section-final
Section path: {node_path}
Section title: {title}
Direct blocks:
{blocks}
Child one-liners:
{children}"""

_ROOT_TEMPLATE: Final = """{instruction}
Call kind: root-final
Document title: {title}
Source kind: {source_kind}
Direct blocks:
{blocks}
Child one-liners:
{children}"""

_SHARD_TEMPLATE: Final = """{instruction}
Call kind: block-shard
Section path: {node_path}
Section title: {title}
Shard: {shard_index}
Direct blocks:
{blocks}"""

_REDUCE_TEMPLATE: Final = """{instruction}
Call kind: {call_kind}
Section path: {node_path}
Section title: {title}
Ordered one-liners:
{lines}"""

_SUMMARY_PROMPT_HASH: Final = hashlib.sha256(
    json.dumps(
        {
            "instructions": (
                _SUMMARY_INSTRUCTION,
                _ROOT_INSTRUCTION,
                _REDUCE_INSTRUCTION,
            ),
            "templates": (
                _SECTION_TEMPLATE,
                _ROOT_TEMPLATE,
                _SHARD_TEMPLATE,
                _REDUCE_TEMPLATE,
            ),
            "section_schema": SectionSummaryResponse.model_json_schema(),
            "root_schema": RootSummaryPlacementResponse.model_json_schema(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
).hexdigest()
"""Hash of every instruction, renderer, and closed response schema."""

_GENERATION_PARAMS: Final = {
    "temperature": 0.0,
    "token_counter": SUMMARY_TOKEN_COUNTER_VERSION,
    "token_ceiling": SUMMARY_CALL_TOKEN_CEILING,
    "balanced_fan_in": SUMMARY_BALANCED_FAN_IN,
    "summary_max_chars": SUMMARY_MAX_CHARS,
}


class SummarySettings(BaseSettings):
    """The dedicated D70 flash-class D79 summary seat."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_SUMMARY_")

    model: str = Field(default="z-ai/glm-4.7-flash")


@dataclass(frozen=True)
class SummaryResult:
    """One bottom-up run ready for immutable generation persistence."""

    sections: tuple[SnappedSection, ...]
    placement_path: str | None
    summary_version: str | None
    placement_version: str | None
    cache_keys: dict[str, str]


@dataclass(frozen=True)
class _CacheValue:
    summary: str
    placement_path: str | None


@dataclass(frozen=True)
class _MeterEvent:
    call_key: str
    tier: str
    usage: ProviderCallUsage


@dataclass(frozen=True)
class _CallResult:
    summary: str | None
    placement_path: str | None
    events: tuple[_MeterEvent, ...] = ()


class SectionSummarizer:
    """Bounded provider orchestration plus sidecar-backed cross-version cache."""

    def __init__(
        self,
        *,
        catalog: DocumentCatalog,
        artifact_store: ObjectStorePort,
        model_provider: ModelProviderPort | None,
        settings: SummarySettings | None = None,
    ) -> None:
        """Bind the summary seat to E0's catalog and immutable artifact store."""
        self._catalog = catalog
        self._artifact_store = artifact_store
        self._model_provider = model_provider
        self._settings = settings or SummarySettings()

    @property
    def version(self) -> str:
        """Hash-stamped summary-seat generation including its D70 model."""
        model_hash = hashlib.sha256(self._settings.model.encode("utf-8")).hexdigest()
        return (
            f"{E0_SUMMARY_VERSION}:prompt-{_SUMMARY_PROMPT_HASH[:16]}:"
            f"model-{model_hash[:16]}"
        )

    @property
    def placement_version(self) -> str:
        """Placement is inseparable from this summary root-reduction contract."""
        return f"{E0_PLACEMENT_VERSION}:summary-{hashlib.sha256(self.version.encode()).hexdigest()[:16]}"

    def summarize(
        self,
        *,
        source: StructureSource,
        sections: tuple[SnappedSection, ...],
        blocks: tuple[Block, ...],
        markdown: str,
        meter: CostMeterPort,
    ) -> SummaryResult:
        """Summarize one selected tree bottom-up; every failure is local/null.

        A missing child one-liner makes its ancestors incomplete, so those
        ancestors degrade without a partial provider call. Independent
        subtrees remain useful and are still persisted.
        """
        children = _children_by_parent(sections=sections)
        direct = {
            section.node_path: _direct_blocks(
                section=section,
                children=children.get(section.node_path, ()),
                blocks=blocks,
            )
            for section in sections
        }
        cache = self._load_cache(doc_id=source.doc_id)
        summaries: dict[str, str | None] = {}
        placements: dict[str, str | None] = {}
        cache_keys: dict[str, str] = {}

        by_depth: defaultdict[int, list[SnappedSection]] = defaultdict(list)
        for section in sections:
            by_depth[section.node_path.count(".")].append(section)
        for depth in sorted(by_depth, reverse=True):
            missing: list[tuple[SnappedSection, str, tuple[str, ...]]] = []
            for section in sorted(by_depth[depth], key=lambda item: item.ordinal):
                child_sections = children.get(section.node_path, ())
                child_lines = tuple(
                    summaries.get(child.node_path) for child in child_sections
                )
                if any(line is None for line in child_lines):
                    summaries[section.node_path] = None
                    placements[section.node_path] = None
                    continue
                complete_child_lines = tuple(
                    line for line in child_lines if line is not None
                )
                key = _summary_cache_key(
                    section=section,
                    direct_blocks=direct[section.node_path],
                    child_summaries=complete_child_lines,
                    model=self._settings.model,
                    source_kind=source.source_kind,
                )
                cached = cache.get(key)
                if cached is not None:
                    summaries[section.node_path] = cached.summary
                    placements[section.node_path] = cached.placement_path
                    cache_keys[section.node_path] = key
                    continue
                missing.append((section, key, complete_child_lines))

            if not missing:
                continue
            results: dict[str, _CallResult] = {}
            if len(missing) == 1:
                section, _, child_lines = missing[0]
                results[section.node_path] = self._summarize_uncached(
                    source=source,
                    section=section,
                    direct_blocks=direct[section.node_path],
                    child_sections=children.get(section.node_path, ()),
                    child_summaries=child_lines,
                    markdown=markdown,
                )
            else:
                with ThreadPoolExecutor(
                    max_workers=min(SUMMARY_PARALLELISM, len(missing))
                ) as executor:
                    futures = {
                        section.node_path: executor.submit(
                            self._summarize_uncached,
                            source=source,
                            section=section,
                            direct_blocks=direct[section.node_path],
                            child_sections=children.get(section.node_path, ()),
                            child_summaries=child_lines,
                            markdown=markdown,
                        )
                        for section, _, child_lines in missing
                    }
                    for path, future in futures.items():
                        results[path] = future.result()

            for section, key, _ in missing:
                result = results[section.node_path]
                for event in result.events:
                    meter.record(
                        call_key=event.call_key, tier=event.tier, usage=event.usage
                    )
                summaries[section.node_path] = result.summary
                placements[section.node_path] = result.placement_path
                if result.summary is not None:
                    cache_keys[section.node_path] = key

        summarized = tuple(
            section.model_copy(update={"summary": summaries.get(section.node_path)})
            for section in sections
        )
        root_summary = summaries.get("0")
        placement = placements.get("0") if root_summary is not None else None
        complete = all(section.summary is not None for section in summarized)
        return SummaryResult(
            sections=summarized,
            placement_path=placement,
            # Provenance convention: a non-null slot certifies a complete
            # generation. Partial useful summaries may persist, but a single
            # degraded section makes both generation slots null.
            summary_version=self.version
            if complete and placement is not None
            else None,
            placement_version=(
                self.placement_version if complete and placement is not None else None
            ),
            cache_keys=cache_keys,
        )

    def replay(
        self,
        *,
        source: StructureSource,
        sections: tuple[SnappedSection, ...],
        placement_path: str,
        blocks: tuple[Block, ...],
    ) -> SummaryResult:
        """Re-key stored successful summaries without calling the provider.

        This is the retry path when PostgreSQL committed a generation but its
        sidecar write did not complete. D7 replays persisted nondeterministic
        output; it never asks the model for fresher bytes.
        """
        children = _children_by_parent(sections=sections)
        direct = {
            section.node_path: _direct_blocks(
                section=section,
                children=children.get(section.node_path, ()),
                blocks=blocks,
            )
            for section in sections
        }
        cache_keys: dict[str, str] = {}
        summaries = {section.node_path: section.summary for section in sections}
        for section in sorted(
            sections, key=lambda item: (-item.node_path.count("."), item.ordinal)
        ):
            summary = summaries[section.node_path]
            child_summaries = tuple(
                summaries[child.node_path]
                for child in children.get(section.node_path, ())
            )
            if summary is None or any(value is None for value in child_summaries):
                raise ValueError("only complete summary generations can be replayed")
            cache_keys[section.node_path] = _summary_cache_key(
                section=section,
                direct_blocks=direct[section.node_path],
                child_summaries=tuple(
                    value for value in child_summaries if value is not None
                ),
                model=self._settings.model,
                source_kind=source.source_kind,
            )
        return SummaryResult(
            sections=sections,
            placement_path=placement_path,
            summary_version=self.version,
            placement_version=self.placement_version,
            cache_keys=cache_keys,
        )

    def _summarize_uncached(
        self,
        *,
        source: StructureSource,
        section: SnappedSection,
        direct_blocks: tuple[Block, ...],
        child_sections: tuple[SnappedSection, ...],
        child_summaries: tuple[str, ...],
        markdown: str,
    ) -> _CallResult:
        """Run one section's bounded shard/reduce/final call sequence."""
        if self._model_provider is None:
            return _CallResult(summary=None, placement_path=None)

        events: list[_MeterEvent] = []
        child_lines = tuple(
            f"{child.node_path} | {child.title} | {summary}"
            for child, summary in zip(child_sections, child_summaries, strict=True)
        )
        reduced_children = self._reduce_lines(
            section=section,
            lines=child_lines,
            call_kind="child-reduction",
            call_key_prefix=f"summary:{section.node_path}:children",
            events=events,
        )
        if reduced_children is None:
            return _CallResult(None, None, tuple(events))

        final_prompt = _render_final_prompt(
            source=source,
            section=section,
            blocks=direct_blocks,
            child_lines=reduced_children,
            markdown=markdown,
        )
        if _within_ceiling(prompt=final_prompt):
            result = self._invoke_final(
                section=section, prompt=final_prompt, events=events
            )
            return result

        shards = _block_shards(section=section, blocks=direct_blocks, markdown=markdown)
        if shards is None:
            return _CallResult(None, None, tuple(events))
        context_lines: list[str] = []
        for shard_index, shard in enumerate(shards):
            prompt = _render_shard_prompt(
                section=section,
                blocks=shard,
                markdown=markdown,
                shard_index=shard_index,
            )
            partial = self._invoke_summary(
                prompt=prompt,
                call_key=f"summary:{section.node_path}:shard:{shard_index}",
                tier="section_summary_shard",
            )
            events.extend(partial.events)
            if partial.summary is None:
                return _CallResult(None, None, tuple(events))
            context_lines.append(f"direct-shard-{shard_index} | {partial.summary}")
        context_lines.extend(reduced_children)
        reduced_context = self._reduce_lines(
            section=section,
            lines=tuple(context_lines),
            call_kind="context-reduction",
            call_key_prefix=f"summary:{section.node_path}:context",
            events=events,
        )
        if reduced_context is None:
            return _CallResult(None, None, tuple(events))
        final_prompt = _render_final_prompt(
            source=source,
            section=section,
            blocks=(),
            child_lines=reduced_context,
            markdown=markdown,
        )
        if not _within_ceiling(prompt=final_prompt):
            return _CallResult(None, None, tuple(events))
        return self._invoke_final(section=section, prompt=final_prompt, events=events)

    def _reduce_lines(
        self,
        *,
        section: SnappedSection,
        lines: tuple[str, ...],
        call_kind: str,
        call_key_prefix: str,
        events: list[_MeterEvent],
    ) -> tuple[str, ...] | None:
        """Balanced fan-in reduction; every intermediate call is bounded."""
        current = lines
        level = 0
        while len(current) > SUMMARY_BALANCED_FAN_IN:
            reduced: list[str] = []
            for group_index, group in enumerate(
                _balanced_groups(values=current, fan_in=SUMMARY_BALANCED_FAN_IN)
            ):
                prompt = _render_reduce_prompt(
                    section=section, lines=group, call_kind=call_kind
                )
                if not _within_ceiling(prompt=prompt):
                    return None
                result = self._invoke_summary(
                    prompt=prompt,
                    call_key=(f"{call_key_prefix}:level:{level}:group:{group_index}"),
                    tier="section_summary_reduction",
                )
                events.extend(result.events)
                if result.summary is None:
                    return None
                reduced.append(f"{call_kind}-{level}-{group_index} | {result.summary}")
            current = tuple(reduced)
            level += 1
        return current

    def _invoke_final(
        self, *, section: SnappedSection, prompt: str, events: list[_MeterEvent]
    ) -> _CallResult:
        """Invoke the one schema that belongs to this section level."""
        if self._model_provider is None:
            return _CallResult(None, None, tuple(events))
        call_key = f"summary:{section.node_path}:final"
        try:
            if section.node_path == "0":
                generated = self._model_provider.generate(
                    request=ModelRequest(
                        model=self._settings.model, prompt=prompt, temperature=0.0
                    ),
                    response_type=RootSummaryPlacementResponse,
                )
                events.append(
                    _MeterEvent(
                        call_key=call_key,
                        tier="document_summary_placement",
                        usage=generated.usage,
                    )
                )
                return _CallResult(
                    generated.output.summary,
                    generated.output.placement_path,
                    tuple(events),
                )
            generated_summary = self._model_provider.generate(
                request=ModelRequest(
                    model=self._settings.model, prompt=prompt, temperature=0.0
                ),
                response_type=SectionSummaryResponse,
            )
            events.append(
                _MeterEvent(
                    call_key=call_key,
                    tier="section_summary",
                    usage=generated_summary.usage,
                )
            )
            return _CallResult(generated_summary.output.summary, None, tuple(events))
        except ProviderCallError as error:
            if error.usage is not None:
                events.append(
                    _MeterEvent(
                        call_key=f"{call_key}:failed",
                        tier="section_summary_failed_response",
                        usage=error.usage,
                    )
                )
        except Exception:  # noqa: BLE001 - summary failure never fails a document
            pass
        return _CallResult(None, None, tuple(events))

    def _invoke_summary(self, *, prompt: str, call_key: str, tier: str) -> _CallResult:
        """Invoke one non-root bounded one-line call."""
        if self._model_provider is None:
            return _CallResult(None, None)
        try:
            generated = self._model_provider.generate(
                request=ModelRequest(
                    model=self._settings.model, prompt=prompt, temperature=0.0
                ),
                response_type=SectionSummaryResponse,
            )
            return _CallResult(
                generated.output.summary,
                None,
                (_MeterEvent(call_key=call_key, tier=tier, usage=generated.usage),),
            )
        except ProviderCallError as error:
            if error.usage is not None:
                return _CallResult(
                    None,
                    None,
                    (
                        _MeterEvent(
                            call_key=f"{call_key}:failed",
                            tier=f"{tier}_failed_response",
                            usage=error.usage,
                        ),
                    ),
                )
        except Exception:  # noqa: BLE001 - summary failure never fails a document
            pass
        return _CallResult(None, None)

    def _load_cache(self, *, doc_id: UUID) -> dict[str, _CacheValue]:
        """Read prior successful generation sidecars; corruption is a cache miss."""
        cache: dict[str, _CacheValue] = {}
        for uri in self._catalog.summary_cache_sidecars(
            doc_id=doc_id, summary_version=self.version
        ):
            try:
                payload = json.loads(
                    self._artifact_store.read_bytes(key=ObjectKey(uri))
                )
                placement = payload.get("placement")
                for section in payload.get("sections", ()):
                    key = section.get("summary_cache_key")
                    summary = section.get("summary")
                    if not isinstance(key, str) or not isinstance(summary, str):
                        continue
                    validated = SectionSummaryResponse(summary=summary)
                    cached_placement: str | None = None
                    if section.get("node_path") == "0":
                        validated_root = RootSummaryPlacementResponse(
                            summary=validated.summary, placement_path=placement
                        )
                        cached_placement = validated_root.placement_path
                    cache.setdefault(
                        key,
                        _CacheValue(
                            summary=validated.summary, placement_path=cached_placement
                        ),
                    )
            except Exception:  # noqa: BLE001 - cache corruption is a miss
                # Sidecars are immutable cache metadata, not an availability
                # dependency. Missing/legacy/malformed objects simply miss.
                continue
        return cache


def _children_by_parent(
    *, sections: tuple[SnappedSection, ...]
) -> dict[str, tuple[SnappedSection, ...]]:
    grouped: defaultdict[str, list[SnappedSection]] = defaultdict(list)
    for section in sections:
        if section.parent_path is not None:
            grouped[section.parent_path].append(section)
    return {
        path: tuple(sorted(values, key=lambda item: item.ordinal))
        for path, values in grouped.items()
    }


def _direct_blocks(
    *,
    section: SnappedSection,
    children: tuple[SnappedSection, ...],
    blocks: tuple[Block, ...],
) -> tuple[Block, ...]:
    """Blocks directly owned by a section, excluding parsed heading syntax."""
    excluded: set[int] = set()
    for child in children:
        excluded.update(range(child.block_start, child.block_end + 1))
    if section.heading_level is not None:
        excluded.add(section.block_start)
    return tuple(
        blocks[index]
        for index in range(section.block_start, section.block_end + 1)
        if 0 <= index < len(blocks) and index not in excluded
    )


def _summary_cache_key(
    *,
    section: SnappedSection,
    direct_blocks: tuple[Block, ...],
    child_summaries: tuple[str, ...],
    model: str,
    source_kind: str,
) -> str:
    """The design's exact per-section content/configuration identity."""
    child_hashes = tuple(
        hashlib.sha256(summary.encode("utf-8")).hexdigest()
        for summary in child_summaries
    )
    prompt_hash = hashlib.sha256(
        json.dumps(
            {
                "contract": _SUMMARY_PROMPT_HASH,
                "node_path": section.node_path,
                "title": section.title,
                "source_kind": source_kind if section.node_path == "0" else None,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    payload = {
        "ordered_constituent_block_hashes": tuple(
            block.block_hash for block in direct_blocks
        ),
        "child_summary_hashes": child_hashes,
        "model": model,
        "prompt_hash": prompt_hash,
        "generation_params": _GENERATION_PARAMS,
        "summarizer_version": E0_SUMMARY_VERSION,
    }
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    ).hexdigest()


def _render_blocks(*, blocks: Iterable[Block], markdown: str) -> str:
    rendered = [
        (
            f"[block ordinal={block.ordinal} type={block.type.value}"
            f" hash={block.block_hash}]\n"
            f"{markdown[block.char_start : block.char_end]}"
        )
        for block in blocks
    ]
    return "\n\n".join(rendered) if rendered else "(none)"


def _render_final_prompt(
    *,
    source: StructureSource,
    section: SnappedSection,
    blocks: tuple[Block, ...],
    child_lines: tuple[str, ...],
    markdown: str,
) -> str:
    if section.node_path == "0":
        return _ROOT_TEMPLATE.format(
            instruction=_ROOT_INSTRUCTION,
            title=source.title or "(untitled)",
            source_kind=source.source_kind,
            blocks=_render_blocks(blocks=blocks, markdown=markdown),
            children="\n".join(child_lines) if child_lines else "(none)",
        )
    return _SECTION_TEMPLATE.format(
        instruction=_SUMMARY_INSTRUCTION,
        node_path=section.node_path,
        title=section.title or "(untitled section)",
        blocks=_render_blocks(blocks=blocks, markdown=markdown),
        children="\n".join(child_lines) if child_lines else "(none)",
    )


def _render_shard_prompt(
    *,
    section: SnappedSection,
    blocks: tuple[Block, ...],
    markdown: str,
    shard_index: int,
) -> str:
    return _SHARD_TEMPLATE.format(
        instruction=_SUMMARY_INSTRUCTION,
        node_path=section.node_path,
        title=section.title or "(untitled section)",
        shard_index=shard_index,
        blocks=_render_blocks(blocks=blocks, markdown=markdown),
    )


def _render_reduce_prompt(
    *, section: SnappedSection, lines: tuple[str, ...], call_kind: str
) -> str:
    return _REDUCE_TEMPLATE.format(
        instruction=_REDUCE_INSTRUCTION,
        call_kind=call_kind,
        node_path=section.node_path,
        title=section.title or "(untitled section)",
        lines="\n".join(lines),
    )


def _block_shards(
    *, section: SnappedSection, blocks: tuple[Block, ...], markdown: str
) -> tuple[tuple[Block, ...], ...] | None:
    """Greedily shard at block grain; an indivisible oversize block degrades."""
    shards: list[tuple[Block, ...]] = []
    current: list[Block] = []
    for block in blocks:
        trial = tuple((*current, block))
        prompt = _render_shard_prompt(
            section=section, blocks=trial, markdown=markdown, shard_index=len(shards)
        )
        if _within_ceiling(prompt=prompt):
            current.append(block)
            continue
        if not current:
            return None
        shards.append(tuple(current))
        current = [block]
        if not _within_ceiling(
            prompt=_render_shard_prompt(
                section=section,
                blocks=tuple(current),
                markdown=markdown,
                shard_index=len(shards),
            )
        ):
            return None
    if current:
        shards.append(tuple(current))
    return tuple(shards)


def _balanced_groups(
    *, values: tuple[str, ...], fan_in: int
) -> tuple[tuple[str, ...], ...]:
    """Partition ordered values into balanced groups no wider than fan-in."""
    if not values:
        return ()
    group_count = ceil(len(values) / fan_in)
    small, remainder = divmod(len(values), group_count)
    groups: list[tuple[str, ...]] = []
    cursor = 0
    for index in range(group_count):
        size = small + (1 if index < remainder else 0)
        groups.append(values[cursor : cursor + size])
        cursor += size
    return tuple(groups)


def _within_ceiling(*, prompt: str) -> bool:
    return count_tokens(text=prompt) <= SUMMARY_CALL_TOKEN_CEILING
