"""Optional Langfuse observer for LoCoMo answer and judge stages.

This module is imported only after all three Langfuse environment bindings are
non-empty. It never receives rendered prompts, source chunks, tool results, gold
answers, or failure messages.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from contextlib import contextmanager
from importlib import import_module
from typing import Protocol
from typing import Self

from rememberstack.model import ProviderCallUsage


class _Observation(Protocol):
    """The observation operations used by the benchmark shim."""

    def update(self, **values: object) -> Self:
        """Attach bounded metadata to the observation."""
        ...

    def end(self) -> Self:
        """End a manually created observation."""
        ...


class _LangfuseClient(Protocol):
    """The dynamically loaded Langfuse client surface used here."""

    def create_trace_id(self, *, seed: str | None = None) -> str:
        """Create a W3C trace identifier, deterministically when seeded."""
        ...

    def start_as_current_observation(
        self, **values: object
    ) -> AbstractContextManager[_Observation]:
        """Create the active root observation for one stage."""
        ...

    def start_observation(self, **values: object) -> _Observation:
        """Create one child observation under the active question root."""
        ...

    def flush(self) -> None:
        """Flush the stage's pending observations."""
        ...


class _LangfuseModule(Protocol):
    """The optional module constructor used by the shim."""

    def Langfuse(self, **values: object) -> _LangfuseClient:  # noqa: N802
        """Build one explicitly configured client."""
        ...


def create_langfuse_tracer(
    *, public_key: str, secret_key: str, host: str, run_identity: str
) -> LocomoTracer:
    """Load Langfuse lazily and create one stage-shared tracing client."""
    module = _load_langfuse()
    client = module.Langfuse(
        public_key=public_key, secret_key=secret_key, base_url=host
    )
    return LocomoTracer(client=client, run_identity=run_identity)


class LocomoTracer:
    """Create deterministic per-question traces and flush them as a batch."""

    def __init__(self, *, client: _LangfuseClient, run_identity: str) -> None:
        """Bind a client and stable prepared-run identity."""
        self._client = client
        self._run_identity = run_identity

    @contextmanager
    def question(
        self, *, item_id: str, question: str, stage: str
    ) -> Iterator[QuestionTrace]:
        """Open one answer/judge root on the question's deterministic trace."""
        trace_id = self._client.create_trace_id(seed=f"{self._run_identity}:{item_id}")
        with self._client.start_as_current_observation(
            name=f"locomo.{stage}",
            as_type="agent" if stage == "answer" else "span",
            trace_context={"trace_id": trace_id},
            input={"question": question},
            metadata={"item_id": item_id, "stage": stage},
        ) as root:
            yield QuestionTrace(client=self._client, root=root)

    def flush(self) -> None:
        """Flush all observations at the end of the answer or judge stage."""
        self._client.flush()


class QuestionTrace:
    """Bounded child-span writer for one question trace."""

    def __init__(self, *, client: _LangfuseClient, root: _Observation) -> None:
        """Bind the active question root."""
        self._client = client
        self._root = root
        self._agent_calls = 0

    def start_agent_call(self, *, model: str) -> ModelCall:
        """Start one answer-agent generation without recording its prompt."""
        self._agent_calls += 1
        observation = self._client.start_observation(
            name="locomo.answer-agent",
            as_type="generation",
            model=model,
            metadata={"call_index": self._agent_calls},
        )
        return ModelCall(observation=observation)

    def start_tool_call(self, *, name: str, arguments: dict[str, object]) -> ToolCall:
        """Start one tool span with value-free argument shape metadata."""
        observation = self._client.start_observation(
            name="locomo.tool",
            as_type="tool",
            input=_arguments_summary(arguments=arguments),
            metadata={"tool_name": name},
        )
        return ToolCall(observation=observation)

    def start_judge_call(self, *, model: str) -> ModelCall:
        """Start one judge generation without recording its rendered prompt."""
        observation = self._client.start_observation(
            name="locomo.judge", as_type="generation", model=model
        )
        return ModelCall(observation=observation)

    def finish_answer(
        self, *, final_answer: str | None, failure_kind: str | None
    ) -> None:
        """Finish the answer root with only the allowed final answer body."""
        values: dict[str, object] = {
            "metadata": {
                "outcome": "failed" if failure_kind is not None else "answered",
                "failure_kind": failure_kind,
            }
        }
        if final_answer is not None:
            values["output"] = {"final_answer": final_answer}
        self._root.update(**values)

    def finish_judge(
        self, *, final_answer: str | None, verdict: str, failure_kind: str | None
    ) -> None:
        """Finish the judge root with the persisted answer and bounded verdict."""
        values: dict[str, object] = {
            "metadata": {"verdict": verdict, "failure_kind": failure_kind}
        }
        if final_answer is not None:
            values["output"] = {"final_answer": final_answer}
        self._root.update(**values)


class ModelCall:
    """Finish one generation with usage, cost, latency, and bounded outcome."""

    def __init__(self, *, observation: _Observation) -> None:
        """Retain the manual child observation."""
        self._observation = observation
        self._ended = False

    def finish(
        self,
        *,
        usage: ProviderCallUsage | None,
        latency_ms: int,
        outcome: str,
        final_answer: str | None = None,
        verdict: str | None = None,
    ) -> None:
        """Update and end the generation exactly once."""
        if self._ended:
            return
        metadata: dict[str, object] = {"latency_ms": latency_ms, "outcome": outcome}
        if usage is not None:
            metadata["provider_latency_ms"] = usage.latency_ms
        if verdict is not None:
            metadata["verdict"] = verdict
        values: dict[str, object] = {"metadata": metadata}
        if usage is not None:
            values.update(_usage_values(usage=usage))
        if final_answer is not None:
            values["output"] = {"final_answer": final_answer}
        self._observation.update(**values)
        self._observation.end()
        self._ended = True


class ToolCall:
    """Finish one tool span without sending its response body."""

    def __init__(self, *, observation: _Observation) -> None:
        """Retain the manual child observation."""
        self._observation = observation
        self._ended = False

    def finish(self, *, latency_ms: int, outcome: str) -> None:
        """End the span with only latency and success/failure metadata."""
        if self._ended:
            return
        self._observation.update(
            output={"outcome": outcome}, metadata={"latency_ms": latency_ms}
        )
        self._observation.end()
        self._ended = True


def _usage_values(*, usage: ProviderCallUsage) -> dict[str, object]:
    """Map provider accounting to Langfuse's generation fields."""
    values: dict[str, object] = {
        "model": usage.model_name,
        "usage_details": {
            "input": usage.tokens_in,
            "output": usage.tokens_out,
            "total": usage.tokens_in + usage.tokens_out,
        },
    }
    values["cost_details"] = {"total": float(usage.cost_usd)}
    return values


def _arguments_summary(*, arguments: dict[str, object]) -> dict[str, object]:
    """Describe argument shapes without copying keys or values."""
    shapes = [
        _value_shape(value=value)
        for _, value in sorted(arguments.items(), key=lambda item: item[0])
    ]
    return {"argument_count": len(arguments), "value_shapes": shapes}


def _value_shape(*, value: object) -> dict[str, object]:
    """Return a content-free type/size summary for one argument."""
    if isinstance(value, str):
        return {"type": "string", "length": len(value)}
    if isinstance(value, dict):
        return {"type": "object", "field_count": len(value)}
    if isinstance(value, (list, tuple)):
        return {"type": "array", "length": len(value)}
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, (int, float)):
        return {"type": "number"}
    return {"type": "other"}


def _load_langfuse() -> _LangfuseModule:
    """Resolve the optional dependency only after environment opt-in."""
    try:
        module = import_module("langfuse")
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "Langfuse tracing requires rememberstack[observability]"
        ) from error
    return module  # type: ignore[return-value]
