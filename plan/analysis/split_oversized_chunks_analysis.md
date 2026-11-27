# Oversized E1 chunks in v0.22.4

The incident audit uses `c1f15b783`. The rescope round-three source checks
use `8bd115b7a`. The PR #681 round-three checks use `8393bdef8`. The
offline grid reviews use `11b3b805f`, `75542dc72` and `d89ac335b`.
[Proposed D162](../designs/split_oversized_chunks_design.md).

## Incident and failure path

Two of 40 documents in a hosted WriteIt batch dead-lettered at `embed_chunk`:
an indentation-heavy YAML file and a Markdown decision log. One chunk hit
71,710 characters at 3,021 whitespace tokens. The packer emits large blocks
intact. Dense text can exceed the model limit within the 400-whitespace-token
target. Counts omit parser-skipped gaps, and D80 can add a location header.
[Packer](../../src/rememberstack/core/chunker.py#L173-L240),
[blockizer](../../src/rememberstack/core/blockizer.py#L177-L193),
[D80](../../src/rememberstack/core/embedding_input_policy.py#L188-L234).

The owner-reported probe on Qwen3-Embedding-8B via OpenRouter with `nebius/bf16`
accepted 19,960 provider tokens in one input and 45,919 total in four. Isolated
60,000- and 72,000-character inputs consistently returned generic 429s. This
points to a per-input boundary near 20k tokens. The exact route boundary and
the failed rendered sizes still need measurement. The adapter retries generic
429s. Replay keeps the cuts and renders again from live source facts, so a
title rename can change the input hash when D80 includes the title.
[Retry classifier](../../src/rememberstack/adapters/openrouter.py#L1302-L1360),
[bounded retry loop](../../src/rememberstack/adapters/openrouter.py#L1079-L1124),
[E1 replay render](../../src/rememberstack/workers/e1.py#L262-L295),
[title lookup](../../src/rememberstack/spine/chunk_catalog.py#L270-L289).

Every converter reading feeds the same packer. The text route returns up to
1 MB verbatim. Larger-file profiles sample 50 head and 20 tail lines at 500
characters each. Search-only code/config/log ranges select model-free E0
structuring and affect E1 cuts and E2 scheduling. D137 coalesces single-block
leaves in fallback trees into a following leaf when available and in place
otherwise. OCR, tables, profile samples and long turns can yield large blocks.
[Text limits](../../src/rememberstack/adapters/converters/text.py#L26-L32),
[E0](../../src/rememberstack/workers/e0.py#L1429-L1438), [E2](../../src/rememberstack/workers/e2.py#L524-L553),
[fallback trees](../../src/rememberstack/core/structure_skeleton.py#L640-L703),
[profiles](../../src/rememberstack/adapters/converters/profile.py#L248-L276).

## Choice and scope

Lowering the whitespace target still admits large single blocks. A blanket
16 KiB byte cap for the pinned byte-level tokenizer would split a 46,833-byte
frozen LoCoMo OCR candidate. Re-blockizing E0 would change provenance and
reuse keys. Refine over-ceiling legacy candidates in E1 to preserve the E0
coordinates and fitting output. Count the rendered D80 input with the actual
tokenizer and qualify request-wide caps.
[Pinned Qwen tokenizer](https://huggingface.co/Qwen/Qwen3-Embedding-8B/blob/1d8ad4ca9b3dd8059ad90a75d4983776a23d44af/tokenizer.json).

The incident versions never completed E1. E0 commits the version and
representation as ready before it queues CHUNK. Public SQL can expose their
IDs as soon as CHUNK commits, and legacy E1 writes each successful batch to P1
before the job ends. Proving a legacy grid stayed private requires recorded
isolation over that entire window. For new D162 versions, record pending
passages with E0 readiness so a scoped search can disclose the delay even if
CHUNK has never written a row.
[E0 Ready update](../../src/rememberstack/spine/document_catalog.py#L827-L852),
[CHUNK follow-up](../../src/rememberstack/workers/e0.py#L2185-L2203),
[public SQL](../../src/rememberstack/spine/migrations/versions/p9_38_0059_d140_effective_time.py#L701-L719),
[batch write](../../src/rememberstack/workers/e1.py#L535-L572).

An otherwise identical embed enqueue hits the failed row because the ledger
deduplicates on stage and component version. A new delivery generation
separates work attempts from packing and paid reuse, including when the old
grid fits a wider envelope.
[Embed enqueue](../../src/rememberstack/workers/e1.py#L794-L811),
[ledger uniqueness](../../src/rememberstack/spine/work_ledger.py#L1996-L2009).

Claim embedding and reconciliation currently read a configured packing
generation. A split grid has a different stamp, so the first tail can find
no chunks to embed and treat its new claims as stale. Both tails must use the
published delivery's stamp for their lookups. The stamp identifies which
claims to consider. The claim embedding and extraction inputs still govern
paid reuse.
[Claim embedding](../../src/rememberstack/workers/p1.py#L99-L113),
[reconcile call](../../src/rememberstack/workers/reconcile.py#L153-L158),
[stale testimony query](../../src/rememberstack/spine/lifecycle.py#L1449-L1471).

A 209-token paragraph and an appended 17,000-digit block pack together under
D58. The paragraph fits alone and keeps its D80 hash, but its second sentence
qualifies as an internal anchor. Trying the whole-block seam first preserves
that hash. The pinned Qwen pre-tokenizer isolates each digit, so the appended
block alone exceeds 16,384 model tokens.
[Packer](../../src/rememberstack/core/chunker.py#L221-L240),
[pinned tokenizer](https://huggingface.co/Qwen/Qwen3-Embedding-8B/blob/1d8ad4ca9b3dd8059ad90a75d4983776a23d44af/tokenizer.json).

Appending digits to an 8,192-digit paragraph without a block break keeps
one legacy block. At a 16,384-token ceiling including the Qwen end token,
the 16,384-scalar probe overflows and the 8,192-scalar cut fits. With D80
body-only rendering, the piece has the same source bytes and embedding
input as the original block. The two content hash forms give different
lookup keys. The existing D80 hash can find the donor and the stored
spans can prove source equality without a new digest.
[Pinned tokenizer](https://huggingface.co/Qwen/Qwen3-Embedding-8B/blob/1d8ad4ca9b3dd8059ad90a75d4983776a23d44af/tokenizer.json) ·
[Carry-forward lookup](../../src/rememberstack/spine/chunk_catalog.py#L422-L439) ·
[Worker lookup](../../src/rememberstack/workers/e1.py#L338-L356).

D80 can choose a different header for a shorter prefix. With a 39-emoji
channel name, one-letter document and section titles, and a line break after
48 ASCII letters, a mock OpenRouter transport measured 343 serialized bytes
at the line seam and 293 at the 128-scalar cut. At a 332-byte cap, the scalar
probes through 128 fit while that seam overflows. Checking each proposed cut
and retaining the last passing probe allows the split to proceed.
[D80 renderer](../../src/rememberstack/core/embedding_input_policy.py#L204-L223),
[payload construction](../../src/rememberstack/adapters/openrouter.py#L1034-L1046),
[HTTPX JSON POST](../../src/rememberstack/adapters/openrouter.py#L1126-L1128).

A local probe changed `Friday` to `Monday` in a skipped HTML comment
between two paragraphs that can survive a split as a whole-block prefix.
The block hashes, relative origin hashes, date-free identity and Selection
key matched, while E2 received the edited comment in the full source slice.
Hashing the exact bytes of the three consumed slots catches this edit and
keeps invalidation local. The block hashes still describe the packing.
[Blockizer](../../src/rememberstack/core/blockizer.py#L177-L193),
[packed span](../../src/rememberstack/core/chunker.py#L198-L215),
[relative origin hash](../../src/rememberstack/core/source_origins.py#L55-L65),
[E2 bundle](../../src/rememberstack/workers/e2.py#L1596-L1619).

Removing a Markdown fence outside three fixed split slots kept their text
and relative origin hashes equal in a local probe. The blockizer changed
one clipped target range into three paragraph ranges. The old card still
remapped by substring, although no new label covered its span. Compare the
ordered label ranges relative to their slots before reusing extraction.
[Block ranges](../../src/rememberstack/core/blockizer.py#L177-L193),
[catalogue construction](../../src/rememberstack/core/source_passages.py#L124-L171),
[card remap](../../src/rememberstack/workers/extraction_references.py#L156-L218).

Renaming a heading outside those slots from `Orchard` to `Terrace` kept
the slot bytes, relative origins, ordered catalogue ranges, E1 keys and
D80 embedding hashes equal in a local probe. The bounded D80 header
omitted the section title, while the E2 prompt and grounding union consumed
it. Grounding accepted `Orchard` before the rename and rejected it after.
Compare the effective E2 location pairs for reuse, including each D122
producer whose Selection could change. D140 still dates the unchanged text
from its date-free evidence.
[D80 rendering](../../src/rememberstack/core/embedding_input_policy.py#L188-L234),
[E2 bundle and grounding](../../src/rememberstack/workers/e2.py#L1596-L1737).

Both extraction queries order by version and ordinal and return one row. A
local SQL probe with two completed same-key donors returned `Orchard` alone
for both stages, although `Terrace` also satisfied the filters. If `Orchard`
fails location verification, continuing to `Terrace` saves both calls.
Across layouts, a split fragment's block hashes can differ from those of an
identical fitting passage because its enclosing blocks differ. A shared digest
of the exact consumed slots narrows the search without scanning the E0 text
again on each lookup. Each donor still needs a completed stage and equivalent
inputs and remappable evidence.
[Selection lookup](../../src/rememberstack/spine/selection_catalog.py#L102-L138),
[Claimify lookup](../../src/rememberstack/spine/claim_catalog.py#L425-L447).

D140 records the inherited date before embedding. A fresh lookup after the
original donor is deleted can move relative dates from the inherited date to
the version date. Refining verified same-version spans preserves those dates
even when the new pieces consume different neighbors. Keeping committed cuts
also avoids assigning one date to a merged piece spanning different origins.
An earlier whole chunk can supply a fragment's date even if its paid inputs
differ or its extraction never ran.
[Origin record](../../src/rememberstack/workers/e1.py#L167-L216),
[donor lookup](../../src/rememberstack/spine/chunk_catalog.py#L332-L345),
[E2 date](../../src/rememberstack/workers/e2.py#L1794-L1801).

The lookup reads chunk rows. A narrower recovery can remove the only January
parent that covers an unchanged February piece, even when the published
January pieces carry the same date. Their new boundaries can also change
the consumed neighbours. Retaining the original date evidence once keeps
both hash and slice matches stable through publication. The immutable E0
representation supplies the text and provenance, so the retained evidence
needs only coordinates and matching metadata.
[Donor lookup](../../src/rememberstack/spine/chunk_catalog.py#L332-L345),
[consumed slots](../../src/rememberstack/spine/chunk_catalog.py#L373-L419).

The lease reaper updates the work ledger row without the lineage lock.
A checkpoint transaction must lock that row through commit to prevent a
reap and reclaim after validation, as well as holding the lineage lock
to fence delivery replacement and forget.
[Lease reaper](../../src/rememberstack/spine/work_ledger.py#L2290-L2322).

The legacy OpenRouter decoder sorts indices and checks count and dimension.
A singleton at index `1` with a valid vector passes P1 validation. A local
two-input mock also returned two distinct valid vectors, both at index `0`.
The decoder kept them in reply order and E1 stamped the expected input
hashes, so a matching hash in P1 can accompany a vector for a different
input. Validate indices for E1 on both layouts and verify the original
mapping and full delivery before trusting a donor.
[Decoder](../../src/rememberstack/adapters/openrouter.py#L1034-L1077),
[E1 assignment and stamps](../../src/rememberstack/workers/e1.py#L502-L572),
[P1 validation](../../src/rememberstack/ports/p1_index.py#L26-L49).

E1 READY records completion. The legacy decoder discards reply indices,
and the persisted vector and expected input hash cannot establish their
original mapping. Carrying that row into a new D162 grid could propagate an
incorrect vector even on a fitting edit. The first such edit pays to
embed each matching passage that lacks proof across all eligible donors.
A successful D162 call makes its vector reusable on later matching edits.

## Benchmark fixture audit

The published 94.09% LoCoMo result uses retained Full-v45 ingestion with
frozen OCR and visual readings. Those stored E0 inputs and chunk rows are
the baseline for D162's offline check.
[Published results](../../benchmarks/locomo/README.md#published-results).

Reviewers reproduced public LoCoMo text and BEAM sizing. The hosted probe and
archived LoCoMo OCR audit remained author-reported. The latter covered 272 v45
sessions (frozen manifest SHA-256
`04869f1b20ed5bf6bab6e5cd1fce639a770cc82fcbf2fc12790dcae22ad7c98c`).
The sizing bound enumerated 352,993 possible block spans. The audit reports
a 21,567-token singleton body in `conv-41/D19` including the end token and
a 22,337-token upper bound with headers. A 16,384 cap splits that singleton.
Qualify the pinned LoCoMo binding at 22,528 or higher.
[Frozen rendering](../../benchmarks/locomo/protocol.py#L581-L629),
[binding](../../benchmarks/locomo/protocol.py#L127-L134).

For BEAM 100K/1, flatten batches, turn groups and their 188 messages in file
order. Render each message as `{role}: {content}` with a blank line between
messages. Across single blocks and contiguous block runs of up to 400
whitespace tokens, the largest D80-normalized body has 14,187 NFC UTF-8 bytes.
Add up to 770 tokens for the bounded D80 header and separators and one for
the end token. **14,958 is a conservative Qwen token bound before extra
route framing.** Whole-source JSON yields a 529,540-byte normalized body.
Qualification uses the exact retained rendering.
[Pinned BEAM chat](https://github.com/mohammadtavakoli78/BEAM/blob/5a64c992ba05b489b891221b7bcb6b763493dde5/chats/100K/1/chat.json).

Both fixtures can be checked from committed Markdown, packing inputs and
D80 facts. LoCoMo's publication used v45 ingestion revision
`13ce4a5224709b3e3cda65c429d5915d47e95e89`. Restore the verified conversation
backups at their recorded revisions and use the stored BEAM 100K/1
representation. Capture and hash the committed data and expected grids before
running the new packer. The sizing-audit digest above identifies that audit's
inputs.
Rebuilding OCR or section structure would change the inputs being tested.
Legacy CHUNK assigns random UUIDv4s on first write and reuses committed IDs on
replay. Feed those saved IDs only to the allocator so the comparison can
detect missing, extra or reordered output.
[LoCoMo restore command](../../benchmarks/locomo/sharding/README.md#4-restore-one-conversation),
[CHUNK replay](../../src/rememberstack/workers/e1.py#L132-L165),
[legacy allocator](../../src/rememberstack/workers/e1.py#L683-L731).

The embedding hash covers the rendered text. E1 separately persists typed
location elements. A local probe removed only `section_title` from that
payload and changed E2's ordered pairs with the embedding text, hash and
coordinates held fixed. Compare the effective pairs from both payloads in the
same offline check.
[E1 payload](../../src/rememberstack/workers/e1.py#L551-L567),
[E2 location validation](../../src/rememberstack/workers/e2.py#L1691-L1737).

Two more probes changed deterministic fields while preserving the composed
embedding input. Renumbering three chunks from `0, 1, 2` to `0, 10, 20`
kept their row order and removed both D122 producers for the last chunk.
Moving the location header and its separator into the indexed body passed
the body fetch's hash check and returned different `source_text`. The
offline check compares the ordinal and the two text fields directly.
[D122 producer window](../../src/rememberstack/workers/extraction_references.py#L56-L63) ·
[Body fetch](../../src/rememberstack/surfaces/query_sandbox/nomination.py#L811-L832).

## Read behavior for fitting grids

Legacy SQL exposes chunk rows on E0 readiness once CHUNK writes them.
Section references and history also read the chunk rows for
`first_chunk_ids`. Legacy E1 adds P1 rows batch by batch. D162 makes
passages, including fitting grids, visible together at first E1 publication.
Document and section metadata still follow E0 readiness. Until publication,
a scoped ranked read with a passage channel can disclose a touched,
in-force version in `Freshness.scope_pending` and return `boundary` when
empty. One fitting-grid fixture with a published control covers this change.
[Chunk view](../../src/rememberstack/spine/migrations/versions/p9_38_0059_d140_effective_time.py#L701-L719),
[references](../../src/rememberstack/spine/document_references.py#L1366-L1382),
[section history](../../src/rememberstack/spine/section_history.py#L737-L752),
[publication](../designs/split_oversized_chunks_design.md#3-recover-the-unpublished-version-at-chunk).

Published grids keep their passage rows and indexes. The new SQL visibility
predicate changes the view definitions, so SQL returns a new
`surface_manifest_hash` even when its rows agree. The existing manifest
checks and saved-query revalidation cover that change.
[Manifest view member](../../src/rememberstack/spine/query_space/manifest.py#L1079-L1106),
[SQL result](../../src/rememberstack/surfaces/query_sandbox/executor.py#L1061-L1070),
[revalidation](../designs/open_query_space_design.md#5-saved-query-registry).

On fresh ingestion, the publication transaction changes when vectors
enter the approximate nearest-neighbour index (HNSW). Its bounded search
can visit different candidates when insertion order changes. The frozen
grid check compares packing and rendering output for the retained
benchmark inputs.
[Legacy batch write](../../src/rememberstack/workers/e1.py#L535-L572),
[HNSW nomination](../../src/rememberstack/adapters/postgres_p1.py#L891-L913).
