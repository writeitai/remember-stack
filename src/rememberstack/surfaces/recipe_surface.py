"""The recipe surface (retrieval §7): one rendering of the registry for all.

The API, CLI, and MCP surfaces must expose the SAME recipes — "the MCP tool
list renders from the recipe registry; the CLI mirrors the API 1:1" — so the
rendering and dispatch live here, once, and each surface is a thin transport
over it. That is how parity is a property, not a promise: there is a single
place that turns a registry row into a callable tool.

Two responsibilities:

- **Render** each active recipe as a `ToolDescriptor` — name, description,
  and a real JSON-Schema `input_schema` built from the recipe's typed
  parameters (this is what an MCP `tools/list` returns, and what the API
  advertises at `/recipes`).
- **Run** a recipe by name: coerce the caller's string-ish arguments to the
  types the primitives need (a uuid string to a UUID, an ISO instant to a
  datetime), then hand them to the `RecipeExecutor`. Coercion is the surface's
  job precisely because every transport delivers arguments as text.
"""

from datetime import datetime
from datetime import UTC
from typing import Any
from uuid import UUID

from rememberstack.model import Envelope
from rememberstack.model import Recipe
from rememberstack.model.client import ToolDescriptor
from rememberstack.spine.recipes import RecipeRegistry
from rememberstack.surfaces.recipe_executor import RecipeExecutor


class UnknownRecipeError(Exception):
    """A recipe name the registry has no active row for."""


class MissingArgumentError(Exception):
    """A required recipe parameter the caller did not supply."""


class InvalidArgumentError(Exception):
    """An argument the caller supplied is the wrong type or not a parameter."""


# How a recipe's declared parameter type renders into JSON Schema, and how a
# transport's text argument coerces back to what the primitives expect.
_TYPE_SCHEMA: dict[str, dict[str, object]] = {
    "uuid": {"type": "string", "format": "uuid"},
    "string": {"type": "string"},
    "integer": {"type": "integer"},
    "timestamp": {"type": "string", "format": "date-time"},
}


def _coerce_uuid(value: object) -> UUID:
    """Coerce a transport argument to a UUID."""
    return value if isinstance(value, UUID) else UUID(str(value))


def _coerce_integer(value: object) -> int:
    """Coerce a transport argument to an int without a silent truncation."""
    if isinstance(value, bool):  # bool is an int subclass — never a count
        raise ValueError("expected an integer, got a boolean")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"expected an integer, got {value!r}")
        return int(value)
    return int(str(value))


def _coerce_timestamp(value: object) -> datetime:
    """Coerce a transport argument to a UTC datetime (ISO 8601).

    The envelope's timestamps are UTC-only, so a naive instant is read as
    UTC and an offset instant is normalized to UTC — never passed through
    with a stray offset that a downstream model would reject.
    """
    parsed = (
        value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    )
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


_COERCERS: dict[str, Any] = {
    "uuid": _coerce_uuid,
    "string": str,
    "integer": _coerce_integer,
    "timestamp": _coerce_timestamp,
}


class RecipeSurface:
    """Render and run the deployment's recipes — the shared surface logic."""

    def __init__(
        self, *, registry: RecipeRegistry, executor: RecipeExecutor, deployment_id: UUID
    ) -> None:
        """Bind the surface to the registry, the executor, and the deployment."""
        self._registry = registry
        self._executor = executor
        self._deployment_id = deployment_id

    @property
    def deployment_id(self) -> UUID:
        """The one deployment this surface serves (a composition guard, D50)."""
        return self._deployment_id

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        """The recipe tool list: ONE tool per name — the latest active version.

        `run` resolves a name to its latest active version, so the tool list
        advertises exactly that: a deployment with v1 and v2 both active shows
        one `relation_current`, whose schema is the one that will execute.
        """
        return recipe_descriptors(
            recipes=self._registry.active(deployment_id=self._deployment_id)
        )

    def run(self, *, name: str, arguments: dict[str, object]) -> Envelope:
        """Run one recipe by name over coerced arguments.

        Raises `UnknownRecipeError` if no active row exists,
        `MissingArgumentError` if a required parameter is absent, and
        `InvalidArgumentError` if an argument is not a declared parameter or
        will not coerce to its declared type — the surfaces map each to a
        typed failure (a 404/422, or an MCP error result), never a crash.
        """
        recipe = self._registry.by_name(deployment_id=self._deployment_id, name=name)
        if recipe is None:
            raise UnknownRecipeError(name)
        return self._executor.execute(
            deployment_id=self._deployment_id,
            recipe=recipe,
            arguments=_coerce_arguments(recipe=recipe, arguments=arguments),
        )


def recipe_descriptors(*, recipes: tuple[Recipe, ...]) -> tuple[ToolDescriptor, ...]:
    """Render the latest recipe per name into the shared public tool contract."""
    seen: set[str] = set()
    descriptors: list[ToolDescriptor] = []
    for recipe in recipes:
        if recipe.name in seen:
            continue
        seen.add(recipe.name)
        descriptors.append(_descriptor(recipe))
    return tuple(descriptors)


def _descriptor(recipe: Recipe) -> ToolDescriptor:
    """Render one recipe as a JSON-Schema-carrying tool descriptor.

    Each property carries its type plus any declared facets (`default`,
    `enum`) so a client sees the whole contract; `additionalProperties` is
    false so a mistyped argument name (`predciate`) is a schema violation a
    validating client rejects, never a silently-dropped filter.
    """
    properties: dict[str, object] = {}
    required: list[str] = []
    for name, spec in recipe.parameters.items():
        declared = spec if isinstance(spec, dict) else {}
        rendered = dict(_TYPE_SCHEMA.get(str(declared.get("type")), {"type": "string"}))
        for facet in ("default", "enum", "description", "minimum", "maximum"):
            if facet in declared:
                rendered[facet] = declared[facet]
        properties[name] = rendered
        if declared.get("required"):
            required.append(name)
    schema: dict[str, object] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        # Canonical order, not declaration order: the registry round-trips
        # `parameters` through Postgres jsonb, which normalises object key
        # order, so declaration order does not survive storage. The benchmark
        # protocol hashes descriptors exactly as served, and an order-sensitive
        # array here made every live deployment hash differently from the same
        # code's stock rendering (observed on graph_path, 2026-07-25).
        schema["required"] = sorted(required)
    return ToolDescriptor(
        name=recipe.name,
        description=recipe.description,
        input_schema=schema,
        output_grain=recipe.output_grain.value,
        answer_intent=recipe.answer_intent.value,
    )


def _coerce_arguments(
    *, recipe: Recipe, arguments: dict[str, object]
) -> dict[str, object]:
    """Coerce transport arguments to the types the primitives expect.

    Every rule fails loudly rather than silently changing the query: an
    argument that is not a declared parameter is an InvalidArgumentError (a
    typo never broadens the query); a required parameter absent (or null) is
    a MissingArgumentError; a declared optional's `default` is applied when
    the caller omits it; and a value that will not coerce to its declared
    type is an InvalidArgumentError, never an uncaught crash.
    """
    declared_names = set(recipe.parameters)
    unknown = set(arguments) - declared_names
    if unknown:
        raise InvalidArgumentError(
            f"recipe {recipe.name!r} has no parameter(s) {sorted(unknown)}"
        )
    coerced: dict[str, object] = {}
    for name, spec in recipe.parameters.items():
        declared = spec if isinstance(spec, dict) else {}
        value = arguments.get(name)
        if value is None:
            if declared.get("required"):
                raise MissingArgumentError(
                    f"recipe {recipe.name!r} requires argument {name!r}"
                )
            if "default" in declared:
                value = declared["default"]
            else:
                continue
        coerce = _COERCERS.get(str(declared.get("type")), str)
        try:
            coerced[name] = coerce(value)
        except (ValueError, TypeError) as error:
            raise InvalidArgumentError(
                f"argument {name!r} of recipe {recipe.name!r} is not a valid"
                f" {declared.get('type', 'string')}: {value!r}"
            ) from error
        coerced_value = coerced[name]
        if isinstance(coerced_value, int):
            minimum = declared.get("minimum")
            maximum = declared.get("maximum")
            if isinstance(minimum, int) and coerced_value < minimum:
                raise InvalidArgumentError(
                    f"argument {name!r} of recipe {recipe.name!r} must be at"
                    f" least {minimum}"
                )
            if isinstance(maximum, int) and coerced_value > maximum:
                raise InvalidArgumentError(
                    f"argument {name!r} of recipe {recipe.name!r} must be at"
                    f" most {maximum}"
                )
    return coerced
