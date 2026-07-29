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
import logging
import sys
from typing import Protocol
from typing import Self

from rememberstack.model import ProviderCallUsage

_logger = logging.getLogger(__name__)


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
    ) -> Iterator[QuestionTrace | None]:
        """Open one best-effort answer/judge root on a deterministic trace."""
        try:
            trace_id = self._client.create_trace_id(
                seed=f"{self._run_identity}:{item_id}"
            )
            manager = self._client.start_as_current_observation(
                name=f"locomo.{stage}",
                as_type="agent" if stage == "answer" else "span",
                trace_context={"trace_id": trace_id},
                input={"question": question},
                metadata={"item_id": item_id, "stage": stage},
            )
            root = manager.__enter__()
        except Exception:
            _logger.warning(
                "optional Langfuse question observation start failed", exc_info=True
            )
            yield None
            return

        trace = QuestionTrace(client=self._client, root=root)
        try:
            yield trace
        finally:
            exception_details = sys.exc_info()
            try:
                trace.end_started()
            finally:
                try:
                    # Ignore a truthy result: tracing must not suppress an exception
                    # raised by the answer or judge protocol.
                    manager.__exit__(*exception_details)
                except Exception:
                    _logger.warning(
                        "optional Langfuse question observation end failed",
                        exc_info=True,
                    )

    def flush(self) -> None:
        """Best-effort flush observations at the end of a protocol stage."""
        try:
            self._client.flush()
        except Exception:
            _logger.warning("optional Langfuse flush failed", exc_info=True)


class QuestionTrace:
    """Bounded child-span writer for one question trace."""

    def __init__(self, *, client: _LangfuseClient, root: _Observation) -> None:
        """Bind the active question root."""
        self._client = client
        self._root = root
        self._agent_calls = 0
        self._started: list[ModelCall | ToolCall] = []

    def start_agent_call(self, *, model: str) -> ModelCall | None:
        """Best-effort start one generation without recording its prompt."""
        self._agent_calls += 1
        return self._start_model_call(
            name="locomo.answer-agent",
            model=model,
            metadata={"call_index": self._agent_calls},
        )

    def start_tool_call(
        self, *, name: str, arguments: dict[str, object]
    ) -> ToolCall | None:
        """Best-effort start one content-free tool observation."""
        try:
            observation = self._client.start_observation(
                name="locomo.tool",
                as_type="tool",
                input=_arguments_summary(arguments=arguments),
                metadata={"tool_name": name},
            )
        except Exception:
            _logger.warning(
                "optional Langfuse tool observation start failed", exc_info=True
            )
            return None
        call = ToolCall(observation=observation)
        self._started.append(call)
        return call

    def start_judge_call(self, *, model: str) -> ModelCall | None:
        """Best-effort start one judge generation without its prompt."""
        return self._start_model_call(name="locomo.judge", model=model)

    def _start_model_call(
        self, *, name: str, model: str, metadata: dict[str, object] | None = None
    ) -> ModelCall | None:
        """Start and retain one generation so the root can always close it."""
        try:
            values: dict[str, object] = {
                "name": name,
                "as_type": "generation",
                "model": model,
            }
            if metadata is not None:
                values["metadata"] = metadata
            observation = self._client.start_observation(**values)
        except Exception:
            _logger.warning(
                "optional Langfuse model observation start failed", exc_info=True
            )
            return None
        call = ModelCall(observation=observation)
        self._started.append(call)
        return call

    def end_started(self) -> None:
        """End every child observation, including unfinished error paths."""
        for observation in reversed(self._started):
            try:
                observation.end()
            except Exception:
                _logger.warning(
                    "optional Langfuse child observation cleanup failed", exc_info=True
                )

    def finish_answer(
        self, *, final_answer: str | None, failure_kind: str | None
    ) -> None:
        """Best-effort finish the answer root with the allowed answer body."""
        values: dict[str, object] = {
            "metadata": {
                "outcome": "failed" if failure_kind is not None else "answered",
                "failure_kind": failure_kind,
            }
        }
        if final_answer is not None:
            values["output"] = {"final_answer": final_answer}
        self._update_root(values=values)

    def finish_judge(
        self, *, final_answer: str | None, verdict: str, failure_kind: str | None
    ) -> None:
        """Best-effort finish the judge root with answer and bounded verdict."""
        values: dict[str, object] = {
            "metadata": {"verdict": verdict, "failure_kind": failure_kind}
        }
        if final_answer is not None:
            values["output"] = {"final_answer": final_answer}
        self._update_root(values=values)

    def _update_root(self, *, values: dict[str, object]) -> None:
        """Update the root without allowing the observer to affect protocol flow."""
        try:
            self._root.update(**values)
        except Exception:
            _logger.warning(
                "optional Langfuse question observation update failed", exc_info=True
            )


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
        """Best-effort update and end the generation exactly once."""
        if self._ended:
            return
        try:
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
        except Exception:
            _logger.warning(
                "optional Langfuse model observation update failed", exc_info=True
            )
        finally:
            self.end()

    def end(self) -> None:
        """Best-effort end an unfinished generation exactly once."""
        if self._ended:
            return
        self._ended = True
        try:
            self._observation.end()
        except Exception:
            _logger.warning(
                "optional Langfuse model observation end failed", exc_info=True
            )


class ToolCall:
    """Finish one tool span without sending its response body."""

    def __init__(self, *, observation: _Observation) -> None:
        """Retain the manual child observation."""
        self._observation = observation
        self._ended = False

    def finish(self, *, latency_ms: int, outcome: str) -> None:
        """Best-effort finish one tool span with bounded metadata."""
        if self._ended:
            return
        try:
            self._observation.update(
                output={"outcome": outcome}, metadata={"latency_ms": latency_ms}
            )
        except Exception:
            _logger.warning(
                "optional Langfuse tool observation update failed", exc_info=True
            )
        finally:
            self.end()

    def end(self) -> None:
        """Best-effort end an unfinished tool span exactly once."""
        if self._ended:
            return
        self._ended = True
        try:
            self._observation.end()
        except Exception:
            _logger.warning(
                "optional Langfuse tool observation end failed", exc_info=True
            )


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
