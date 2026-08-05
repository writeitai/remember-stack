FROM ghcr.io/astral-sh/uv:0.11.31@sha256:ecd4de2f060c64bea0ff8ecb182ddf46ba3fcccdc8a60cfdbaf20d1a047d7437 AS uv

# The dependency set includes a SQL parser with no wheel for this interpreter,
# so it is compiled from source. The compiler lives in a build stage and never
# reaches the image that runs in production: a runtime container that can build
# C is a runtime container that can build anything.
FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6 AS build

COPY --from=uv /uv /uvx /bin/

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md LICENSE alembic.ini ./

RUN uv sync --locked --no-dev --extra observability --extra server --no-install-project

COPY src ./src

RUN uv sync --locked --no-dev --extra observability --extra server


FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6

COPY --from=uv /uv /uvx /bin/

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN addgroup --system app \
    && adduser --system --ingroup app \
        --home /var/lib/rememberstack --no-create-home app \
    && mkdir -p /var/lib/rememberstack/forget-manifests \
    && chown -R app:app /var/lib/rememberstack

# The virtualenv is built at this same path, so it is copied rather than rebuilt.
COPY --from=build /app /app

# Provenance: the exact source revision baked into this image. The benchmark
# harness compares it against the revision it prepared with, so a run can never
# attribute results to code that did not produce them. Unset means "unknown",
# which the harness treats as a hard stop for real runs rather than a warning.
ARG REMEMBERSTACK_BUILD_REVISION=""
ENV REMEMBERSTACK_BUILD_REVISION="${REMEMBERSTACK_BUILD_REVISION}"

USER app

ENTRYPOINT ["python", "-m", "rememberstack.profiles.selfhost"]
CMD ["api"]
