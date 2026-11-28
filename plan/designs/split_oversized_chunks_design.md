# Split oversized chunks before embedding (D162)

**Status** Proposed. [Analysis and probe evidence](../analysis/split_oversized_chunks_analysis.md).

## 1. Check the complete embedding input

CHUNK and EMBED_CHUNK apply the ceiling only for the hosted E1 passage binding
`qwen/qwen3-embedding-8b` through OpenRouter with `only: ["nebius/bf16"]`,
`allow_fallbacks: false`, `zdr: true` and `data_collection: deny`. Self-host and custom
bindings use their existing packing, embedding and failure paths. [Adapter routing](../../src/rememberstack/adapters/openrouter.py#L1001-L1046).

Use the pinned tokenizer to count the normalized body, optional D80 header and separator, and model special tokens.
The largest header plus one nonblank Unicode scalar must fit. The **16,384-token code constant**
changes only with a release and stays outside work and embedding identities. CHUNK keeps fitting
spans and rechecks after cuts because final order and count can change the header. EMBED_CHUNK
checks before new carry-forward or dispatch. Keep ordered batching, attestations and same-chunk
replay. [Pinned Qwen tokenizer](https://huggingface.co/Qwen/Qwen3-Embedding-8B/blob/1d8ad4ca9b3dd8059ad90a75d4983776a23d44af/tokenizer.json).

## 2. Split only failing candidates

For failing base candidates, cut first at nonblank eligibility changes using E0 blocks and
D133 gap labels. Unlabelled gaps are eligible. Keep whitespace labels, attaching leading space
to the first piece and separator whitespace to the preceding piece. For size cuts, prefer block
seams, recorded turn/page/table-row seams, sentence, line, whitespace, then Unicode scalars.
In the first legal tier, cut nearest the midpoint, breaking ties earlier. Keep CRLF together
and nonblank text on both sides. Repeat until each piece fits and their exact bytes reassemble.

Assign UUIDv4 IDs, consecutive document ordinals, half-open E0 spans within the same section
and legacy whitespace token counts. Record inclusive intersected block ordinals, using the
preceding block for both fields on gap-only pieces. Clip source maps and converter origins.
Section history and resolved references select the chunk containing the first block's start
within the same version and representation, preserving order.

Use existing key builders on the final grid. Hash exact ordered block fragments and gap bytes
with a split discriminator. Selection and Claimify label nonblank gap-only windows by exact
span, even beside block-backed neighbours. Ground kept propositions with exact spans and D80
typed locations. D122 reads the preceding eight Selection producers in final order, counting
empty results for ineligible pieces. [Passage labels](../../src/rememberstack/core/source_passages.py#L124-L168) · [Producer order](../../src/rememberstack/workers/extraction_references.py#L56-L76).

## 3. Version-wide reuse gate

A grid contains the chunks for one representation and chunker version. Any immutable chunk row
with `span_kind = 'split'` excludes its entire `version_id` as donor and receiver, across
all representations and chunker versions. Intact and legacy rows use `whole`. Use a partial
index on `version_id` over split rows. Check the receiver and filter donors before ranking.
For D140, also check the receiver's final grid awaiting insert.

| Reused product | Existing lookup |
|---|---|
| E1 vectors | [`ChunkCatalog.carry_forward_sources`](../../src/rememberstack/spine/chunk_catalog.py#L209-L243) |
| E2 Selection | [`SelectionCatalog.prior`](../../src/rememberstack/spine/selection_catalog.py#L102-L138) |
| E2 Claimify | [`ClaimCatalog.prior_extracted_chunk`](../../src/rememberstack/spine/claim_catalog.py#L62-L100) |
| D140 dates | [`ChunkCatalog.text_origin_matches`](../../src/rememberstack/spine/chunk_catalog.py#L82-L116) |

CHUNK packs outside the lock. It locks the lineage row in `documents` exclusively, then
replays a saved grid or resolves D140 dates and inserts the complete grid in one transaction.
The gate takes effect at grid commit, even before the representation becomes current.
E1/E2 reuse writers take a shared lock on that row, recheck both versions in a fresh
`READ COMMITTED` statement and hold the lock through commit. The recheck, vector copy
into `chunk_search` and `chunks` stamp share one transaction. A blocked donor triggers
another eligible lookup, and a blocked receiver runs fresh. Keep artifact reads and provider
calls outside the locks.

Replay committed results and dates by chunk ID under existing identity checks. Missing results
for a split version run fresh under existing skip rules. New chunks in that version take its
`source_modified_at` or `published_at` (`NULL` if undated). Eligible pairs keep keys, ranking and remapping.

## 4. Rollout

Add `span_kind` and its partial index online under [D160](packaging_distribution_design.md#61-migration-compatibility). Pause STRUCTURE claims and let legacy
E1 attempts and handoffs settle. Retire outgoing CHUNK, EMBED_CHUNK, Selection, Claimify and both
section-entry readers before enabling split writes and fit checks together. Keep the chunker
version and work keys. Retries hand off saved grids as-is.

## 5. Terminal failure

For the hosted binding, require finite, nonzero vectors of the configured dimension.
`empty_body` is the successful skip. Isolate failures as needed. Local fit failures and
confirmed per-input size rejections dead-letter as `embedding.input_too_large`, other deterministic
rejections as `embedding.input_rejected`. Record chunk ID, rendered hash and binding.
Ambiguous 429s, outages and unusable responses take bounded retries. Keep committed batches and
the saved grid. Use existing ledger operations and D159 unarchive.

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
columns and recomputed D122 order and stamps. Record fixture digests and the replay command.

Test exact bytes, deterministic cuts, provenance, gap-only grounding, header growth and
empty Selection without a call. Both section-entry readers keep fitting-grid IDs and order
and resolve the piece containing the first block's start. Verify the hosted routing payload
and preserve request and error parity on self-host and custom bindings.

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
