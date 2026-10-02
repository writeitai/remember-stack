# @rememberdev/client

The asynchronous Remember memory client for Node.js 22 or newer. It includes
memory HTTP methods, issuer/project routing, account access, typed results,
credential loading, binary/file ingest and transport-neutral tool catalogue
support. CLI commands and MCP servers are separate packages; this package has
no executable or MCP host.

This source package has not been published to npm. Build and install its local
tarball to try it:

```bash
uv sync --extra dev
cd packages/typescript-client
npm ci
npm run build
npm pack
# In your application, use the actual generated tarball path:
npm install /absolute/path/rememberdev-client-0.1.0.tgz
```

Both ESM and CommonJS are supported. TypeScript consumers should have their
usual Node types installed (`@types/node`). The package targets application
backends; browser bundling is not supported.

```ts
import { Client } from '@rememberdev/client';

const client = new Client({
  apiKey: process.env.REMEMBER_API_KEY,
  baseUrl: 'http://127.0.0.1:8000',
});
try {
  const receipt = await client.ingest({
    content: new TextEncoder().encode('A durable note.'),
    filename: 'note.md',
    sourceKind: 'agent',
    sourceRef: 'workspace/note',
  });
  const report = await client.waitForReadiness({
    versionIds: [receipt.version_id],
  });
  console.log(report.ready);
} finally {
  client.close();
}
```

Methods use named option objects and camelCase option names. Nested schema
objects and returned fields keep the engine's snake_case names. Times inside
those objects are ISO strings; top-level timestamp options also accept Date.
`timeoutMs` defaults to 30,000; readiness defaults to 1,800,000 milliseconds,
with a 15,000 millisecond polling interval. Every asynchronous call accepts
`signal`. Integer values outside JavaScript's safe range fail rather than being
silently rounded; use string values/SQL casts for larger integers.

`Client`, `MemoryClient` and `RememberClient` expose the complete reviewed Python
memory client scope. `Client` also has `fromEnv`, `ingestFile` and lazy `account`.
`QueryResultDict` is an ordinary typed object with `.rows`, `.columns` and
`.truncated`. Response model types contain validated, defaulted fields; their
`Input` counterparts describe schema-owned input objects.

Connection settings use explicit options, then REMEMBER_API_KEY/API_URL/PROJECT,
then the existing version-2 Python credential file. `resolveConnection` also
accepts issuer/mcpUrl settings. A signed key routes through its issuer; it never
falls back to a local engine. Without a configured URL or signed key, the
self-hosted default is http://127.0.0.1:8000. POSIX file reads refuse symlinks,
nonregular files and group/world-readable files; automatic file reads are
refused on Windows. Explicit key and URL settings bypass a stale credential
file. Secret wrappers redact ordinary JSON/inspection; deliberate access uses
`getSecretValue()` or `connection.authorization`.

Writes are never automatically replayed. A classified read may replay once at
a changed issuer-resolved engine URL. Caller cancellation and HTTP timeouts
never trigger host refresh/replay. The reference transport uses one low-level
Node HTTP/HTTPS request per invocation, with no automatic retry or redirect.

You can inject a caller-owned `client` implementing `HttpClient.request` instead
of connection settings, or a conforming `fetch` adapter for custom proxy/CA
requirements. Adapters must transmit once per invocation and respect manual
redirects and AbortSignal. Ordinary Node global fetch automatically repeats
requests after HTTP 421 and is unsuitable as an injection. Node HTTP/HTTPS TLS
defaults apply; httpx HTTP_PROXY/HTTPS_PROXY/NO_PROXY and SSL_CERT_FILE behavior
is not reproduced. Close cancels this client's work and destroys SDK-owned
agents, never a caller-owned transport.

Connector methods manage deployment-side configuration. They do not run npm
connector plugins. Connector adapters, an executable CLI, and MCP hosts have
separate design and release gates. Base tool validation can resolve text and
base64 bodies; a path requires an explicitly injected host-owned resolver and
never loads MCP filesystem settings.

The full method/settings/error reference is at
`website/src/app/docs/reference/typescript-sdk/page.mdx` in this repository.

## Development and drift checks

```bash
npm run generate         # offline Python/API export plus pinned type-only generation
npm run check:generated  # regenerate to temporary files and compare; never edits git
npm run typecheck
npm test
npm run check:parity     # execute current Python and compare recorded fixtures
npm run check:package    # install tarball in separate ESM/CommonJS consumers
```

CI also exports the engine OpenAPI offline, checks every reviewed public Python
method/signature/default and support export, runs shared negative fixtures,
counts requests at loopback HTTP servers, and exercises Node versions on Linux
and Windows. The live engine matrix separately checks released and candidate
engine profiles. Public issuer contracts are checked by provider repositories
in one direction; no private cloud schema is shipped here.

Generation uses pinned openapi-typescript-codegen for types only. Runtime
validation uses the original JSON Schema 2020-12 definitions, not the projected
OpenAPI 3.0 document. Python source changes, schema changes and unimplemented
cross-field validators fail checks until fixtures/code/contracts are updated
and reviewed. CI does not author committed generated artifacts.

There is no npm publication workflow. First registry publication requires a
separate owner instruction and verified namespace/publisher configuration.
