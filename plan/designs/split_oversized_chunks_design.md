# Split oversized chunks before embedding (D162)

**Status** Accepted. [Analysis and probe evidence](../analysis/split_oversized_chunks_analysis.md).

## 1. Check the complete embedding input

CHUNK and EMBED_CHUNK apply the ceiling only for the hosted E1 passage binding
`qwen/qwen3-embedding-8b` through OpenRouter with `only: ["nebius/bf16"]`,
`allow_fallbacks: false`, `zdr: true` and `data_collection: deny`. The binding also
allows `order: ["nebius/bf16"]` alongside this allowlist because it selects the
same sole endpoint. Self-host and custom bindings use their existing packing,
embedding and failure paths. Match the normalized embedding request URL
`https://openrouter.ai/api/v1/embeddings`. An explicit default HTTPS port or
upper-case hostname resolves to the same endpoint. [Adapter routing](../../src/rememberstack/adapters/openrouter.py#L1005-L1053).

Use the pinned tokenizer to count the normalized body, optional D80 header and separator, and model special tokens.
The largest header plus one nonblank Unicode scalar must fit. The **16,384-token code constant**
changes only with a release and stays outside work and embedding identities. CHUNK keeps fitting
spans and rechecks them after cuts because the final chunk count can change the D80 header mode.
EMBED_CHUNK checks before new carry-forward or dispatch. Keep ordered batching, attestations
and same-chunk replay. [Pinned Qwen tokenizer](https://huggingface.co/Qwen/Qwen3-Embedding-8B/blob/1d8ad4ca9b3dd8059ad90a75d4983776a23d44af/tokenizer.json).

## 2. Split only failing candidates

For failing base candidates, cut first at nonblank eligibility changes using E0 blocks and
D133 gap labels. Unlabelled gaps are eligible. Keep whitespace labels, attaching leading space
to the first piece and separator whitespace to the preceding piece. For size cuts, prefer block
seams, recorded turn/page/table-row seams, sentence, line, whitespace, then Unicode scalars.
Markdown table rows end at LF, CRLF or CR. Unicode line separators inside a cell
count as cell text.
In the first legal tier, cut nearest the midpoint, breaking ties earlier. Keep CRLF together
and nonblank text on both sides. Repeat until each piece fits and their exact bytes reassemble.

Assign UUIDv4 IDs, consecutive document ordinals, half-open E0 spans within the same section
and legacy whitespace token counts. Record inclusive intersected block ordinals, using the
preceding block for both fields on gap-only pieces. Clip source maps and converter origins.
Use each cited span's own chunk to decide whether its raw offsets can narrow, including
spans cited through neighbouring passages and reference cards. `linear_source_offsets`
on an E0 map entry means that each Markdown character has the same relative offset in
one exact raw source range. If the map links Markdown `[0, 40)` to raw `[100, 140)`,
a cited split span `[23, 40)` gets raw offsets `[123, 140)`. Other locators keep the
converter's precision.

Section history and resolved document references populate `first_chunk_ids` within the same
deployment, version and representation. For `span_kind = 'whole'`, including legacy rows,
match `chunk.block_start <= section.block_start <= chunk.block_end`. This covers zero-width
whole chunks. For `span_kind = 'split'`, match the first block's character start with
`chunk.char_start <= section.char_start < chunk.char_end`. Order by `(version_id, ordinal, chunk_id)`.

Use existing key builders on the final grid. Hash exact ordered block fragments and gap bytes
with a split discriminator. Selection and Claimify label a nonblank split window by its
exact span when it overlaps blocks only in whitespace or lies entirely in a gap. This
also applies beside block-backed neighbours. Ground kept propositions with exact spans and D80
typed locations. D122 reads the preceding eight Selection producers in final order, counting
empty results for ineligible pieces. [Passage labels](../../src/rememberstack/core/source_passages.py#L109-L180) · [Producer order](../../src/rememberstack/workers/extraction_references.py#L56-L76).

## 3. Version-wide reuse gate

A grid contains the chunks for one representation and chunker version. Any immutable chunk row
with `span_kind = 'split'` excludes its entire `version_id` as donor and receiver, across
all representations and chunker versions. Intact and legacy rows use `whole`. Use a partial
index on `version_id` over split rows. Check the receiver and filter donors before ranking.
For D140, also check the receiver's final grid awaiting insert.

| Reused product | Existing lookup |
|---|---|
| E1 vectors | [`ChunkCatalog.replay_or_copy_embeddings`](../../src/rememberstack/spine/chunk_catalog.py#L299) |
| E2 Selection | [`SelectionCatalog.prior`](../../src/rememberstack/spine/selection_catalog.py#L102-L138) |
| E2 Claimify | [`ClaimCatalog.prior_extracted_chunk`](../../src/rememberstack/spine/claim_catalog.py#L62-L100) |
| D140 dates | [`ChunkCatalog.text_origin_matches`](../../src/rememberstack/spine/chunk_catalog.py#L130) |

CHUNK packs outside the lock. In a `READ COMMITTED` transaction it locks the
lineage row in `documents` exclusively. Statements issued after acquiring the
lock replay a saved grid or resolve D140 dates and insert the complete grid.
The gate takes effect at grid commit, even before the representation becomes current.
E1/E2 reuse writers take a shared lock on that row, recheck both versions in a fresh
`READ COMMITTED` statement and hold the lock through commit. The recheck, vector copy
into `chunk_search` and `chunks` stamp share one transaction. A blocked donor triggers
another eligible lookup, and a blocked receiver runs fresh. Keep artifact reads and provider
calls outside the locks.

Replay committed results and dates by chunk ID under existing identity checks. E1 checks
for a matching vector and stamp after taking the lineage lock. This covers a retry that
committed after an older attempt read for replay, even if a split has since closed reuse.
Before dispatching a fresh Selection or Claimify call, read the target's committed result
again and check its input hash. This covers a retry that commits while a stale attempt
looks for a donor.
Missing results for a split version run fresh under existing skip rules. New chunks in that
version take its `source_modified_at` or `published_at` (`NULL` if undated). Eligible pairs
keep keys, ranking and remapping.

## 4. Rollout

Add `span_kind` and its partial index online under [D160](packaging_distribution_design.md#61-migration-compatibility). Pause STRUCTURE claims and let legacy
E1 attempts and handoffs settle. Retire outgoing CHUNK, EMBED_CHUNK, Selection, Claimify and both
section-entry readers before enabling split writes and fit checks together. Keep the chunker
version and work keys. Retries hand off saved grids as-is.

The online migration connection uses `READ COMMITTED` before Alembic reads the
revision. The column downgrade checks for split rows in a fresh statement after
acquiring the exclusive lock, so it sees split rows committed during the wait.
If any exist, rolling back requires restoring a backup from before the cutover.

## 5. Terminal failure

For the hosted binding, require exactly one integer response index per requested input.
Every vector arrives as a JSON array of numbers and must be finite, nonzero and of
the configured dimension.
`empty_body` is the successful skip. Isolate failures as needed. Local fit failures and
confirmed per-input size rejections dead-letter as `embedding.input_too_large`.
Other explicit input rejections dead-letter as `embedding.input_rejected`. Honor input
codes from OpenRouter or its upstream provider even when the outer status indicates
capacity or the response is an error-only success envelope. A size code in either
layer takes precedence over other input rejections. Record chunk ID, rendered hash
and binding.

Capacity errors, including error-only HTTP 200 envelopes with a request ID, take bounded
retries. Outages and unusable responses use the existing retry budget. Keep committed
batches and the saved grid. Use existing ledger operations and D159 unarchive.

A keyless D55 ingest is a no-op if its bytes match the highest-numbered version and that
version is non-deleted. Changed bytes, re-ingest after deletion of the latest version, or a
new D140 key create a successor. D159 forget and re-add with `allow_forgotten=true` starts
a fresh lineage. [Version registration](../../src/rememberstack/spine/document_catalog.py#L221-L250) · [D159 re-ingest](archive_and_forget_design.md#36-re-ingest-guard-and-adding-a-document-back).

## 6. Acceptance

Full-v45 pinned plain `nebius`. BEAM 100K/1 used a self-hosted lab stack. Both bypass the hosted
ceiling. [Published LoCoMo profile and revisions](../../benchmarks/locomo/README.md#published-results) · [Frozen bindings](../analysis/split_oversized_chunks_analysis.md).
Replay frozen LoCoMo and BEAM E0 artifacts, bindings, donors, UUIDs and clocks offline.
Clone the checkpoint after additive migration. Clear candidate target E1/E2 work, `chunks`,
`chunk_search`, `selection_results` and Claimify state (target-owned claims, occurrence links,
decisions and completion stamps). Match exact ordered requests, including routing and framing,
to saved replies. Require zero splits and zero live calls. Compare all `chunks` and `chunk_search`
columns and recomputed D122 order and stamps against the frozen baseline. Also compare
`selection_results` payloads (`output`, `cards`, `truncated`), accepted `claims` rows, and the
target `chunk_claims` links and their occurrence `evidence_spans`. Reapply the saved
downstream claim vectors through the P1 writer before the full claim-row comparison.
Record fixture digests and the replay command.

Test exact bytes, deterministic cuts, provenance, gap-only grounding, header growth and
empty Selection without a call. For both section-entry readers, test version and representation
scoping, ordered fitting-grid IDs, and the split piece containing the first block's start.
Include `Lead\u2028\n# Terms {#terms}\n` and require both readers to return the same ID for its
`[5, 5)` whole chunk as before. Verify the hosted routing payload and preserve request and
error parity on self-host and custom bindings.

In a dated unsplit → split → unsplit lineage, put an intact sibling in a different chunker
generation of the middle version. Test all four reuse paths on both sides with that sibling
still current and matching inputs, including same-date E2 matches and empty results. Race
reuse against a split on either side. Reuse that commits first must replay by chunk ID. If the
split commits first, require an eligible donor or fresh work. Interrupt a vector copy between
upsert and stamp and verify rollback of both tables. Count E1 and both E2 calls and check
pending split dates and claim IDs. Retain ordinary-edit D56 and D122 coverage.

Race first writes for one grid. Rehearse above-cap replay, CHUNK retry after insertion and EMBED_CHUNK backoff.
Test typed failures, retries and D55/D140 no-ops and successors, including a deleted latest version.

## Follow-ups (not part of D162)

Extend lossless splitting to other embedding bindings.
