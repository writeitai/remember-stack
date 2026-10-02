# @rememberdev/client

Node.js 22+ client for Remember. Its complete implementation contract is
[engine D140](../../plan/designs/typescript_client_design.md); CLI and MCP hosts
are separate packages. This implementation PR remains a draft until the full
facade, parity fixtures, distributed-package checks and independent review pass.
No npm publication or registry availability is claimed.

From this directory, `npm ci` installs locked development dependencies.
`npm run generate` exports contracts from the repository's Python source and
committed offline OpenAPI, then generates types. `npm run check:generated`
compares temporary artifacts without writing branch source. It refuses Python
public-surface drift and API/schema drift as well as stale generated files.

Runtime validation uses the original JSON Schema 2020-12 models. The type-only
OpenAPI 3.0 projection preserves nullable unions, constants and binary fields;
source-derived AST corrections preserve exact null and recursive JSON types
which the pinned generator otherwise widens or cannot compile. No generated
network transport or global token configuration is shipped.
