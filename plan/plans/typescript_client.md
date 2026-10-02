# TypeScript client delivery

1. Merge the engine D141 and cloud D95 design PRs only after Opus 5.5 approval.
2. Implement the complete normative Python inventory and safety contracts,
   updating affected Python behavior/tests/docs in the same PR. Push a draft
   checkpoint early; no npm publication is authorized by merge.
3. Add schema/generation/inventory/behavior/tarball checks and a TypeScript
   lane to the existing 0.15.0/0.16.0/candidate compatibility matrix.
4. The cloud follows with one-way provider checks against its pinned engine
   release and engine main. Missing or unreachable public contracts fail;
   providers can add fields without breaking the consumed shape. Older tags
   predating a JSON artifact are checked by deriving consumed schema from the
   pinned public issuer/connection source AST, never by running remote code.
5. Run required checks and independent Opus implementation review; fix findings
   and obtain approval before merge. Public docs describe local package usage
   until an actual npm release is verified.
6. First npm publication requires owner approval, verified org/publisher
   configuration and D5 claim evidence from the cloud product where applicable.
7. CLI and MCP: each receives its own complete binding design and Opus review,
   implementation/behavior tests and release approval. Their library boundaries
   are decided; their implementation is not part of the client PR. CLI uses
   public credential schemas/read helpers and owns locking, writes and journals;
   MCP uses public tool definitions, validators and client routing.

Contributor agreement: these changes are AI-authored under the owner instruction;
no legal assent on an individual's/entity's behalf is inferred from this file.
The authenticated contributor must supply the exact repository CLA assent if
required by the PR workflow; this remains a merge gate.
