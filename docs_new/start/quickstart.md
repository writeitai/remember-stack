---
title: Quickstart
description: Send a document to RememberStack and ask your first question.
applies_to: [remember.dev, self-hosted]
---

# Quickstart

By the end of this page you will have sent a document to RememberStack,
waited for it to be processed, and asked a question that it answers with a
fact and the passage behind it.

*RememberStack is the open-source memory engine. `remember` is its Python
client and CLI. remember.dev runs RememberStack for you.* This page works
with either.

## 1. Get an endpoint

=== "remember.dev"

    1. Sign in at [remember.dev/app](https://remember.dev/app).
    2. Open **Settings → API Tokens** and create a token.
    3. Copy the token (it starts with `umc_dp_` and is shown once) and the
       endpoint, `https://<deployment-id>.dp.remember.dev`.

    Only organisation owners can create tokens. See
    [Tokens and sign-in](../cloud/tokens-and-sign-in.md).

=== "Self-hosted"

    You need Docker with Compose and an
    [OpenRouter](https://openrouter.ai) API key.

    ```bash
    git clone https://github.com/writeitai/remember-stack.git
    cd remember-stack
    cp .env.example .env
    # edit .env: set REMEMBERSTACK_OPENROUTER_API_KEY
    docker compose up -d
    ```

    The first start builds the PostgreSQL image and runs migrations. When
    `docker compose ps` shows `api` as healthy, the endpoint is
    `http://localhost:8000`. No token is needed by default.

    !!! warning
        Compose publishes port 8000 on every network interface of the
        machine, and the API is open by default. On a shared network, read
        [Before you expose it](../self-hosting/install.md#before-you-expose-it)
        first.

## 2. Install the client

```bash
pip install remember
```

This installs the Python client and the `remember` command. It needs
Python 3.12 or later.

## 3. Point the client at your endpoint

=== "remember.dev"

    ```bash
    export REMEMBER_API_URL=https://<deployment-id>.dp.remember.dev
    export REMEMBER_API_KEY=umc_dp_...
    ```

    The Python client and the `remember` CLI both read these. Instead of
    exporting a token you can sign the CLI in with
    `remember login --token-host https://remember.dev/app/api`, which opens
    a browser and stores the token in `~/.config/remember/credentials.json`.

=== "Self-hosted"

    ```bash
    export REMEMBER_API_URL=http://localhost:8000
    ```

## 4. Send a document

Save this as `standup.md`:

```markdown
# Stand-up, 17 September 2026

Ravi said the billing migration moves from June to October, because the
invoice exporter needs a rewrite. Dana agreed and will tell finance.
Ravi owns the invoice exporter.
```

Then send it:

```python
from datetime import UTC, datetime

import remember

client = remember.Client.from_env()
version = client.ingest(
    "standup.md",
    mime="text/markdown",
    source_kind="file",
    source_ref="notes/standup.md",
    source_modified_at=datetime(2026, 9, 17, 9, 30, tzinfo=UTC),
)
print(version.version_id, version.created)
```

Pass `mime` explicitly. Python does not recognise `.md` files as Markdown
on every platform, and a file sent with the wrong type is not processed.

`created` is `True` the first time. Send the same bytes again and it is
`False`: nothing new is stored and nothing is charged.

## 5. Wait until it is processed

```python
client.wait_for_readiness(
    [version.version_id],
    timeout=1800,
    poll_interval=15,
)
```

Processing reads, structures and connects the document. It takes minutes.
Always pass a `timeout`: the default is 30 seconds, which is shorter than a
typical run.

## 6. Ask

```python
result = client.facts_context("Who owns the invoice exporter?")
print(result.model_dump_json(indent=2))
```

The result is an envelope. Look for:

- `facts`: each fact, such as Ravi owns the invoice exporter, with its
  `validity` (when it held) and `evidence_count`;
- `evidence`: the claims behind each fact, with the document, the passage
  and its character positions;
- `negative`: set instead of facts when the memory knows nothing about what
  you asked.

[Reading a result](../concepts/reading-results.md) explains every field.

## The same with the CLI

```bash
remember ingest standup.md --mime text/markdown \
  --source-kind file --source-ref notes/standup.md \
  --source-modified-at 2026-09-17T09:30:00+00:00

remember query "Who owns the invoice exporter?"
```

`remember query "<text>"` runs `facts_context`. Add `--combined` to
`remember query text` to get facts together with claims and source passages.

## Next

- [Connect your coding agent](connect-your-agent.md) so it can use this
  memory directly.
- [Give an agent context](../guides/agent-context.md): which operation to
  call for which kind of question.
- [Ingest files](../guides/ingest-files.md): PDFs, HTML, bulk loads.
