---
title: Hosted MCP
description: Connect a coding agent to one remember.dev project over MCP with a browser sign-in instead of an API token, and what the hosted server offers.
applies_to: [remember.dev]
---

# Hosted MCP

Hosted MCP lets a coding agent store and recall memory in one of your
projects without ever holding an API token. You add one URL to the agent,
sign in once in the browser, and pick the project. The agent gets a
short-lived token that works only through this server and only for that
project.

```text
https://remember.dev/app/api/mcp
```

If you would rather run the MCP server on your own machine with a
deployment API token, use `remember mcp` instead; see
[Connect your coding agent](../start/connect-your-agent.md) and the
[MCP tools reference](../reference/mcp.md).

## How connecting works

1. You add the URL to your agent.
2. The agent's first request is refused with `401` and a pointer to
   remember.dev's sign-in metadata.
3. The agent registers itself with remember.dev and opens your browser.
4. You sign in to remember.dev, if you are not already.
5. A page titled **Connect _agent name_** lists the projects you can
   connect. You pick one and choose **Connect**.
6. The browser hands a one-time code back to the agent, which exchanges it
   for an access token and a refresh token.

The flow is standard OAuth 2.1 with PKCE (`S256`) and dynamic client
registration. Your password and your session never reach the agent.

The page lists every project that has a deployment in each organisation you
belong to, shown as the organisation name and the project's hostname. One
connection is one project. To use two projects, add the server twice under
different names and pick a different project each time.

## Set up your agent

These are each client's usual steps for a remote MCP server that signs in
with OAuth. The exact screens depend on your client's version.

### Claude Code

```bash
claude mcp add --transport http remember https://remember.dev/app/api/mcp
```

Start Claude Code, run `/mcp`, choose `remember` and authenticate. Your
browser opens at the remember.dev connect page.

### Cursor

Add the server to `.cursor/mcp.json` in your repository, or to
`~/.cursor/mcp.json` for every project:

```json
{
  "mcpServers": {
    "remember": {
      "url": "https://remember.dev/app/api/mcp"
    }
  }
}
```

Cursor shows the server as needing sign-in; follow its prompt.

### Codex

Add the server to `~/.codex/config.toml`:

```toml
[mcp_servers.remember]
url = "https://remember.dev/app/api/mcp"
```

Then sign in:

```bash
codex mcp login remember
```

### Claude Desktop

Open **Settings → Connectors**, choose **Add custom connector**, and enter
`https://remember.dev/app/api/mcp` as the URL. Claude Desktop opens the
remember.dev sign-in when you connect.

!!! note
    Do not paste a deployment API token into an agent's settings or chat.
    Hosted MCP exists so that you do not have to.

## Tools

The hosted server offers six tools. They run against the project you
connected. The engine version remember.dev currently runs uses the
operation names below; see [What remember.dev serves](compatibility.md) for
how they map to the names used elsewhere in these docs.

| Tool | What it does | Deployment request |
|---|---|---|
| `ingest` | Stores a piece of text as a Markdown document | `POST /ingest` |
| `pipeline_readiness` | Reports whether ingested versions are processed and queryable | `POST /readiness` |
| `resolve_entity` | Finds the entity a name refers to | `POST /operations/resolve_entity` |
| `testimony_context` | Returns what sources said, with the passages they said it in | `POST /operations/testimony_context` |
| `fact_context` | Returns the facts currently held true, with their evidence | `POST /operations/fact_context` |
| `answer_context` | Returns both of the above side by side for answering a question | `POST /operations/answer_context` |

`ingest` takes `text` and an optional `filename` (default `note.md`). The
text is sent to the deployment as a Markdown document. It does not take a
file path, a MIME type or source identifiers; to ingest files, use the CLI
or the Python client.

The other five tools pass their arguments through unchanged as the JSON
body of the deployment request, so they take the same arguments as those
routes. See [Assured operations](../reference/assured-operations.md) and
[Ingest, readiness, documents](../reference/http-api/ingest.md).

The tool list advertises each tool with a one-line description and an open
argument schema; agents learn the arguments from these docs, not from the
tool list.

Each result comes back as one text item containing the deployment's JSON
response.

Hosted MCP does not yet offer the SQL query tools that `remember mcp` adds
when a deployment serves SQL queries. Use `remember mcp` with a deployment
API token, or the HTTP API, for those.

## What the agent can do

- **Read tools** work for any member of the organisation that owns the
  project.
- **`ingest`** works for owners and for members assigned to the project.
- Every call is made to your project's own deployment with a fresh
  credential that lasts 10 minutes (reads) or 3 minutes (ingest). Your
  deployment applies its usual checks, including the
  [spend controls](spend-controls.md).
- The request and the response pass through remember.dev's account API on
  the way. remember.dev does not store the text of either.

If the project's deployment is not serving, for example because it is
blocked for funding, tool calls fail.

## Token lifetimes

| Token | Lifetime | Notes |
|---|---|---|
| Access token | 1 hour | Presented on every MCP request |
| Refresh token | 30 days | Replaced on every refresh; a used refresh token stops working |
| Connect page | 15 minutes | Start again from the agent if it expires |
| One-time code | 5 minutes | Used once |

As long as the agent refreshes at least once every 30 days, it stays
connected without another browser sign-in.

## Disconnect

Remove the server from the agent's configuration, or use the agent's own
sign-out for the server. The agent's tokens then stop being used.

remember.dev has no screen yet for listing or revoking an agent's
connection. What still limits a connection you did not remove:

- each tool call checks your membership at the time of the call, so if you
  leave the organisation, every tool stops working at once;
- if the project is closed, the tools stop working;
- the refresh token expires 30 days after its last use.

Signing out of the console does not disconnect agents.

## Related

- [Tokens and sign-in](tokens-and-sign-in.md)
- [MCP tools reference](../reference/mcp.md)
- [Data handling and security](data-and-security.md)
