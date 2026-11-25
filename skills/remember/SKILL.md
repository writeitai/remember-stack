---
name: remember
description: Use self-hosted RememberStack to look up decisions, architecture, and source evidence, or to store documents and reconcile uncertain writes. Use when a user asks what was decided, what changed, when something was true, or where a remembered fact came from.
---

# Remember

Use the configured `remember` MCP server. If it is missing, follow [Connect your coding agent](https://remember.dev/docs/start/connect-your-agent) or use the [CLI](https://remember.dev/docs/reference/cli) against the same self-hosted engine. Use the deployment the user selected.

## Look up a decision

1. Call `resolve_entity` for named people, projects, or systems. If several matches could change the answer, ask which one the user meant.
2. Call `facts_context` with the question and any resolved entity IDs. Use `time.mode="current"` for what holds now, `"at"` for a specific instant, `"overlap"` for an interval, or `"history"` for changes and past events.
3. Use `claims_and_sources_context` for exact wording or when facts leave a gap. Use `combined_context` when you need both. For a question that needs structured joins or counts, call `describe_query_space` before `query_sql`.
4. Cite the supporting document and passage. Report contradictions, ambiguity, and missing evidence. `valid_from` and `valid_until` describe when a fact held. `asserted_at` tells you when the source said it. Use the source's date to interpret phrases such as “last week.”

## Store a document

When the user asks you to save a document, call `ingest` with one body. Use `text` and `filename` for a short note, or follow the [MCP ingest reference](https://remember.dev/docs/reference/mcp#ingest) for a file. Keep `source_kind` and `source_ref` stable when updating a named source. Save the `doc_id`, `version_id`, and `content_hash` from the receipt. To confirm that the document is stored, call `search_documents` with its `doc_id` in `doc_ids`. Follow `pipeline.poll_with` to check `pipeline_readiness` until it returns `ready: true` before you query the new content. Report a parked conversion or `dead_letter` stage with the affected version. See the [first workflow](https://remember.dev/docs/start/skills#store-and-find-a-note).

## Recover from a failed request

- Retry reads such as `resolve_entity`, `facts_context`, `search_documents`, and `pipeline_readiness` after a timeout or transient service error. Use bounded backoff for repeated failures. On HTTP `429` or an MCP error with `retry_after`, wait the specified time before retrying a safe operation. For `concurrency_limited`, also let in-flight work finish. See [admission limits](https://remember.dev/docs/reference/http-api#admission-limits).
- If an ingest reply is lost and the source has no intervening writer, replay the exact request with the same bytes, source identity, and metadata. Leave out the version key if the original request omitted it. A matching latest version returns `created: false` with its existing `version_id` and `content_hash`. Check the receipt and use that version for readiness. If the retry returns `created: true`, it stored a version. If another writer could have advanced the source, inspect its version history first. Replaying older bytes or adding a fresh `version_key` can create a version. See [content-hash ingest](https://remember.dev/docs/concepts/documents-and-sources#re-ingesting-the-same-bytes-createdfalse).
- If another write times out, stop and inspect its current state before making another change. For example, check the document's archive state or its assigned tags. If you cannot tell whether the write landed, report the uncertainty. `forget_document` permanently erases content and requires explicit user confirmation.
