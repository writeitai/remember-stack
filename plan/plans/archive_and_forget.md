# Archive and forget — delivery order (D159)

Build order for the [archive and forget design](../designs/archive_and_forget_design.md).
Each slice is one independently mergeable pull request that removes what it replaces in the
same change, ships the design §5 tests it can satisfy, updates the public docs for what it
ships (D66), and is reviewed by GPT 6.2 sol before merge.

## 1. Forget: record, hide, sweep until clean

- Migration: `forget_manifests` becomes `forgets` (design §3.5); unfinished D74 forgets become
  `erasing` with the hide applied; D135-deleted lineages become archives (design §4).
- The hide transaction (§3.2), the sweep (§3.3) and the sweep-until-clean job (§3.4), driven
  through the existing internal entry point first.
- The re-ingest guard with its default refusal, 409 `document_forgotten` (§3.6), so the old
  permanent guard is never removed without its replacement.
- `remember ops forget-export`, `forget-reapply` and `forget-import-d74` (§3.8, §4).
- The best-effort mount invalidation call and the S3 FUSE cache documentation (§3.7). The cloud
  repository implements the remember.dev session adapter.
- Remove in the same change: `ForgetManifestPort` and its stores and settings, the startup
  readiness replay, the global barrier, drain and its 503, the `preparing`/`accepted` statuses,
  `ForgetRedactionRequiredError`, the permanent guard and the stop-and-remount rule.
- Tests: design §5 tests 1–8, 10, 11, 13 and 14, and test 9 without the flag.

## 2. Forget on every surface and `allow_forgotten`

- `POST /documents/{doc_id}/forget`, `GET /forgets`, `GET /forgets/{forget_id}`, the SDK, CLI
  (`remember documents forget`, `remember forget list|status`) and the `forget_document` MCP
  tool with `confirm`; TypeScript parity.
- The per-request `allow_forgotten` flag on every ingest surface.
- Tests: test 9 with the flag, and the surface contract tests.

## 3. Archive, unarchive and the removal of the public delete

- `documents.archived_at` and `archived_by`, the `version_archived` reason, and the
  `reopened_unarchive` outcome (design §2).
- `POST /documents/{doc_id}/archive|unarchive`, the SDK, CLI and MCP tools, and
  `include_archived` on `GET /documents`, search and the context operations; TypeScript parity.
- A connector-observed source deletion archives.
- Remove in the same change: `DELETE /documents/{doc_id}`, `MemoryClient.delete_document`,
  `remember documents delete`, the `delete_document` MCP tool, their TypeScript counterparts and
  `DocumentDeletion`.
- Tests: design §5 test 12.
