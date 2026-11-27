# Quality gate

RememberStack workflows use GitHub-hosted runners. Keep the public repository
free of self-hosted runner registrations. Jobs that use the release App key
declare the `production` Actions environment, which admits only the `main`
branch.

The required local test lane is the fast lane:

```bash
make test-fast
```

It runs tests not marked `integration` and is intended to finish in about one
minute. Modules whose fixtures require PostgreSQL/Alembic, Lance, worker-ledger
chain rigs, or byte-comparison projection builds belong to the integration lane.
Everything else stays in the fast lane.

CI continues to run the full test suite with coverage. Merge only when CI is
green; a local full-suite run is not required before opening or updating a
change.

The full lane remains available locally when wanted:

```bash
REMEMBERSTACK_DATABASE_URL=postgresql+psycopg://rememberstack:<password>@localhost:55432/rememberstack \
  make test-full
```
