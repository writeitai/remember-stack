# Archive and forget — delivery order (D159)

Date: 2026-11-23. Implementation sequence for the accepted design; the design change itself
ships no runtime behavior. Authority: [archive and forget design](../designs/archive_and_forget_design.md),
[D159](../../decisions.md#d159-archive-and-forget-replace-delete-forget-erases-one-document-without-stopping-the-deployment).
[Analysis](../analysis/archive_and_forget.md) records the tradeoffs.

Each numbered slice is one independently mergeable pull request with the acceptance tests of
design §5 that it can already satisfy. Each slice removes the machinery it replaces in the same
change: there is no deprecation period, compatibility alias or dual path. Every PR is reviewed
by GPT 6.2 sol before merge. Each PR updates the public docs (`website/`) for exactly what it
ships (D66), regenerates the OpenAPI and client artifacts it changes, and keeps
[`typescript_client_parity.json`](../designs/typescript_client_parity.json) in step with the
Python surface (D141), because the parity check compares that inventory with the code.

## 1. The forget record, the per-document fence and the erasure job

- Migration: `forget_manifests` becomes `forgets` (design §3.5); `forget_status` replaces
  `forget_manifest_status`; the `hard_forget` stage becomes `forget`.
- The forget request (design §3.2), the per-document fence (§3.3: version share locks in every
  document-derived writer, the per-document object lock in every object writer, skipped queued
  work, input generations for profiles and K) and the background erasure job (§3.4), driven
  through the existing internal entry point so the job can be tested before any public route.
- `remember ops forget-reapply [--from]` and `remember ops forget-export` (§3.10), offline
  operator commands inside the engine container; the public `remember forget list|status`
  over HTTP lands with slice 2.
- The re-ingest gate with its default refusal (§3.6: common lock order, staged originals,
  409 `document_forgotten`) on every ingest path, so the old permanent guard is never removed
  without its replacement in place. Only the `allow_forgotten` override waits for slice 2.
- Mount invalidation (§3.7): `invalidate(doc_suffix)` on the corpus purge port, the in-repo
  test adapter, acknowledgement (or the client's cache-expiry bound) in step 6, and the S3 FUSE cache-bound documentation. The
  cloud repository's controlled-session adapter, qualified by running the warmed-handle test
  through `remember mount` with the real Mountable client (the in-repo adapter cannot exercise
  the client and kernel caches), must ship before remember.dev deploys this
  slice; the pin PR waits for it.
- Remove in the same change: `ForgetManifestPort` and both implementations,
  `forget_manifest_root`, `forget_manifest_bucket_name`, the startup readiness replay
  (API and `project --plane p3`), the deployment-wide admission barrier and drain with its
  503 `forget_in_progress`, the `preparing`/`accepted` statuses, `ForgetRedactionRequiredError`,
  the permanent ingest guard and the stop-and-remount rule.
- Tests: whole-database canary, schema coverage, every table seeded, nothing extra removed, no
  orphans, never existed, no global stop, kill/resume/late writers, concurrent forgets,
  authored pages, mounts, restore without re-adds, large document, and the refusal half of
  re-ingest (design §5 tests 1–8, 10, 12 and 16, test 9 except its re-add, testimony-episode,
  archived-owner and D135-upgrade cases, test 13 except its re-add cases, and test 11 without the flag).

## 2. Forget on every surface, the re-ingest guard and `allow_forgotten`

- `POST /documents/{doc_id}/forget`, `GET /forgets`, `GET /forgets/{forget_id}`;
  `MemoryClient.forget_document`, `list_forgets`, `get_forget`; CLI
  `remember documents forget` and `remember forget list|status`; the `forget_document` MCP tool
  with `confirm` and `destructiveHint: true`; TypeScript parity.
- The per-request `allow_forgotten` flag (HTTP, SDK, `remember ingest --allow-forgotten`, MCP
  `ingest`) releasing exactly one record, with 409 `forget_in_progress` while erasing.
- Tests: re-ingest with the flag (design §5 test 11, except its archive and unarchive cases, which move to slice 3), the restore cases that span a deliberate
  re-add (test 13) and the re-add cases of test 9, and the surface contract tests.

## 3. Archive, un-archive and the removal of the public delete

- `documents.archived_at` and `archived_by`, the per-version cutoff
  (`document_versions.archived_at`), the three lineage states and the current-testimony rule
  (design §2.1); the `version_deleted` currency reason renamed `version_archived`; testimony
  episodes with the `version_restored` transition and episode-scoped support links (§2.4).
- `POST /documents/{doc_id}/archive|unarchive`, `MemoryClient.archive_document` and
  `unarchive_document`, CLI `remember documents archive|unarchive`, MCP `archive_document` and
  `unarchive_document`, and `include_archived` on `GET /documents`, search and the context
  operations; TypeScript parity.
- A connector-observed source deletion archives instead of tombstoning.
- Remove in the same change: `DELETE /documents/{doc_id}`, `MemoryClient.delete_document`,
  `remember documents delete`, the `delete_document` MCP tool, their TypeScript counterparts
  and `DocumentDeletion`.
- The upgrade migration converting D135 tombstones to caller or source archives (design §4),
  tested by upgrading from the previous release.
- Tests: archive and Rule 4 (design §5 tests 14 and 15, test 12's restored-claim episode case, the testimony-episode, archived-owner
  and D135-upgrade cases of test 9, and test 11's archive and unarchive cases): un-archiving a
  fully processed version makes no Selection, Claimify or source-embedding call, and an edit of
  an archived document costs what the same edit of a live lineage costs.

## 4. Public documentation

Not a separate PR: each slice above updates the public docs for what it ships, in the same PR
(D66). After slice 3 the docs describe archive and forget only; delete, hard-forget manifests
and the manifest-root restore step no longer appear.
