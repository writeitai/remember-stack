# Unselected TypeScript client alternatives

**Status:** viable, not accepted. Current authority: D140.

- A browser/edge-capable core with Node-only subpaths wins if concrete consumers
  require it and we can preserve credential isolation without weakening Node
  file/connection parity. It is not a hidden second implementation.
- Hey API or openapi-typescript wins if the pinned generator projection loses
  a current API construct or maintenance cost exceeds a reviewed migration.
  Native 3.1 support is attractive; an engine SDK migration does not silently
  change the cloud frontend convention.

Required evidence: 3.1 null/const/union/binary fixtures, consumer declarations,
runtime schema equivalence, byte-exact drift checks and source ownership.
