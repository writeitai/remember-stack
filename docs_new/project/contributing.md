---
title: Contributing, license and trademarks
description: The license, the contributor agreement, the trademark policy, how to set up a development checkout and how the repository is laid out.
applies_to: [self-hosted]
---

# Contributing, license and trademarks

RememberStack is developed in the open at
`github.com/writeitai/remember-stack`. The engine, the `remember` package,
the benchmarks and the design documents all live in that one repository.

## License

RememberStack is licensed under the Apache License, Version 2.0. The full
text is in `LICENSE` at the root of the repository, and the `remember`
package declares the same license. The copyright holder is WriteIt.ai s.r.o.

The license lets you use, modify and redistribute the code, including in
commercial products, under its conditions (keep the notices, state your
changes). It does not grant rights to the project's names and logos; see
[Trademarks](#trademarks).

## Contributor License Agreement

Contributions are accepted under the RememberStack Contributor License
Agreement, version 1.0. Its text is `CLA.md` at the root of the repository.

You accept it in the pull request itself: the pull-request template has a
checkbox for it, and a field to name a company if you sign on its behalf. A
required check called **CLA** blocks the merge until the box is ticked.

## Trademarks

The names **RememberStack** and **remember.dev** and their logos are
trademarks of WriteIt.ai s.r.o. The policy is `TRADEMARKS.md` at the root of
the repository. In short:

- You may refer to RememberStack truthfully, say that your software is
  compatible with, built with or based on it, and use the `remember` command
  name and API identifiers as needed for installation and documentation.
- You may redistribute unmodified official releases under their name.
- You need written permission to use a mark as the main name of a company,
  product, service, package or domain, to brand anything with a logo, to
  imply that something is official or endorsed, or to put a mark on
  merchandise.
- A modified version may say it is "based on RememberStack" but needs its
  own name.

Questions and permission requests go to `info@writeit.ai`.

## Set up a development checkout

You need:

- [uv](https://docs.astral.sh/uv/) version **0.12.6** exactly (the project
  pins it);
- Python 3.12 or newer (CI uses 3.13);
- Docker, for the PostgreSQL 19 database most tests need.

```bash
git clone https://github.com/writeitai/remember-stack.git
cd remember-stack
make install          # uv sync: engine, client and development tools
```

### Checks

| Command | What it runs |
|---|---|
| `make lint` | `uv run ruff check src/ benchmarks/` |
| `make format` | `uv run ruff format src/ benchmarks/` |
| `make typecheck` | `uv run pyright src/ benchmarks/` |
| `make test` | `uv run pytest src/tests` with coverage |
| `make check` | `lint`, `typecheck` and `test` |
| `uv run lint-imports` | The architecture rules in `.importlinter` (for example, workers and surfaces must not import adapters). |
| `uv run ruff format --check src/ benchmarks/` | Formatting check, as CI runs it. |

Two rules the linters enforce:

- Configuration is read through `pydantic-settings` classes only. Ruff
  rejects `os.environ`, `os.getenv` and `os.putenv` in the code; tests set
  variables with `monkeypatch.setenv`.
- Pyright runs in `standard` mode with no checks switched off for the
  library code.

### Tests and the database

Tests live in `src/tests/`. Unit tests run without a database. Integration
tests need PostgreSQL 19 with the project's extensions, built from
`Dockerfile.postgres`. The CI script starts one on port 5432:

```bash
.github/ci/start-postgres.sh
export REMEMBERSTACK_DATABASE_URL=postgresql+psycopg://rememberstack:rememberstack_test@localhost:5432/rememberstack_test
uv run pytest src/tests/spine -q
```

Every test file must be listed in exactly one of
`.github/ci/unit-paths.txt` and `.github/ci/integration-paths.txt`. CI fails
when a new test file is in neither (`.github/ci/check_test_inventory.py`).

### What CI runs

On every pull request, `.github/workflows/ci.yml` runs: the test-inventory
check, `lint-imports`, `ruff check`, `ruff format --check`, `pyright`, the
unit tests, a contract smoke pack against PostgreSQL, and the integration
tests for workers, spine, surfaces and adapters. The **CLA** check runs
separately.

### Pull requests

The pull-request template asks for a summary (what changed and why, one
coherent change per pull request), the checks you ran, and the contributor
agreement.

Design decisions are recorded in `decisions.md`, one numbered entry per
decision (for example D108, D130). A change that alters a decided behaviour
updates or supersedes its entry.

## Repository layout

| Path | What is there |
|---|---|
| `src/remember/` | The `remember` package: Python client, CLI, `remember mcp`, `remember setup`. Depends only on `httpx`, `pydantic` and `pydantic-settings`. |
| `src/rememberstack/` | The engine. |
| `src/rememberstack/model/` | Data models shared by every layer. |
| `src/rememberstack/core/` | Domain logic that depends only on `model/`. |
| `src/rememberstack/ports/` | Interfaces to storage, queues, models and other outside systems. |
| `src/rememberstack/adapters/` | Implementations of those interfaces: PostgreSQL, object storage, OpenRouter, converters, observability. |
| `src/rememberstack/spine/` | The PostgreSQL store: schema, migrations (`spine/migrations/`), catalogs, the query space manifest. |
| `src/rememberstack/workers/` | The pipeline stages (convert through label). |
| `src/rememberstack/surfaces/` | The HTTP API, the assured operations and the SQL query sandbox. |
| `src/rememberstack/profiles/` | Composition: the self-hosted profile that wires everything together and the container entry point. |
| `src/rememberstack/eval/` | Evaluation helpers. |
| `src/tests/` | The test suite, mirroring the layers above. |
| `benchmarks/` | LoCoMo and BEAM harnesses; see [Benchmarks](benchmarks.md). |
| `packages/` | The deprecated forwarding package; see the [changelog](changelog.md). |
| `Dockerfile`, `Dockerfile.postgres`, `docker/` | The engine image and the PostgreSQL 19 image with extensions. |
| `compose.yaml`, `.env.example` | The Docker Compose deployment. |
| `alembic.ini` | Database migration configuration. |
| `openapi.json` | The HTTP API schema, generated by `scripts/export_openapi.py`. |
| `scripts/` | Release and consistency checks. |
| `decisions.md`, `design/`, `plan/` | The decision log, designs and analyses. |
| `.github/` | CI workflows, CI helper scripts, the pull-request template. |
