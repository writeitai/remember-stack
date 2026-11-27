# Oversized E1 chunks in v0.22.4

The incident audit uses `c1f15b783`. The rescope round-three source checks
use `8bd115b7a`. The PR #681 round-three checks use `8393bdef8`.
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

## Benchmark fixture audit

Full-v47 is the only executable LoCoMo profile. Historical parity for the
published 94.09% uses the recorded serving and reader-prompt revisions, retained
v45 stores, frozen images and saved tool traces.
[Protocol registry](../../benchmarks/locomo/protocol.py#L513-L553),
[published results](../../benchmarks/locomo/README.md#published-results).

Reviewers reproduced public LoCoMo text and BEAM sizing. The hosted probe and
archived LoCoMo OCR audit remained author-reported. The latter covered 272 v45
sessions (frozen manifest SHA-256
`04869f1b20ed5bf6bab6e5cd1fce639a770cc82fcbf2fc12790dcae22ad7c98c`).
Across 352,993 legacy candidates it reports a 21,567-token singleton in
`conv-41/D19` before its header, including the end token, and a 22,337-token
upper bound with headers. A 16,384 cap splits that singleton. Qualify the
pinned LoCoMo binding at 22,528 or higher.
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

An offline embedding probe changed the gateway URL and then enabled zero
data retention while holding the model and provider fixed. Both changed the
wire request while the LoCoMo ingest binding guard still passed. BEAM begins
retrieval against the supplied API URL. Checking the runtime identity that
passed parity at each protected entry point keeps these changes from silently
switching a benchmark to an unqualified path.
[Gateway client](../../src/rememberstack/adapters/openrouter.py#L412-L428),
[provider payload](../../src/rememberstack/adapters/openrouter.py#L1001-L1046),
[binding report](../../src/rememberstack/profiles/selfhost.py#L2373-L2399),
[LoCoMo guard](../../benchmarks/locomo/runner.py#L1714-L1729),
[BEAM reader](../../benchmarks/rs_harness_beam/answer_agent.py#L692-L705).

An in-process HTTP probe held the API and BEAM reader fixed and disabled only
facts in a stub layer catalog. `facts_context` disappeared from discovery and
from BEAM's event-ordering calls. The live catalog also supplies backfill
state for read disclosure. Deployment deadlines and SQL caps can change read
results under the same builds. Admission needs the resolved settings for
each protected read.
[Discovery](../../src/rememberstack/surfaces/http_api.py#L2403-L2415),
[D143 admission](../../src/rememberstack/spine/processing_layers.py#L519-L614),
[BEAM event ordering](../../benchmarks/rs_harness_beam/answer_agent.py#L449-L509),
[retrieval deadline](../../src/rememberstack/profiles/selfhost.py#L1432-L1439),
[SQL limits](../../src/rememberstack/surfaces/query_sandbox/executor.py#L586-L600).

Matching chunk hashes and queries on retained vectors do not exercise ingest
delivery. The current OpenRouter decoder sorts indices and checks count and
dimension, so a singleton at index `1` with a valid vector passes P1. The
strict D162 decoder rejects it. Replay from raw transport replies can detect
that difference before a protected ingest changes worker.

A local two-input mock returned two distinct vectors, both at index `0`.
The decoder accepted both in reply order and both passed P1 validation.
E1 assigns by request position and writes the expected input hashes, so
even a matching hash in P1 can accompany a vector for a different input.
Verify the original mapping and full delivery before trusting a donor.
[Decoder](../../src/rememberstack/adapters/openrouter.py#L1034-L1077),
[E1 assignment and stamps](../../src/rememberstack/workers/e1.py#L502-L572),
[P1 validation](../../src/rememberstack/ports/p1_index.py#L26-L49).

Equal stored vectors can yield different search results. Legacy E1 inserts
each successful batch into P1. D162 inserts the grid at publication, changing
when its rows enter the approximate nearest-neighbour index (HNSW).
An insertion schedule can change which rows this bounded search visits.
Replay reads on the stores both ingests actually publish, with the indexes
they built. Reads on retained stores still test historical serving parity.
[Legacy write](../../src/rememberstack/workers/e1.py#L535-L572),
[P1 upsert](../../src/rememberstack/adapters/postgres_p1.py#L255-L285),
[HNSW nomination](../../src/rememberstack/adapters/postgres_p1.py#L891-L913),
[nomination bound](../../src/rememberstack/adapters/p1_ranked.py#L319-L322).

Matching IDs and quotes can coexist with different SQL values or fact windows.
Conversely, identical SQL runs generate fresh request IDs and timings.
Freeze evaluation instants and normalize the per-call telemetry to measure
result parity. Fixed vectors bypass query embedding, so qualify that path
before using them to isolate reader changes.
[SQL response](../../src/rememberstack/surfaces/query_sandbox/executor.py#L1061-L1085),
[BEAM SQL path](../../benchmarks/rs_harness_beam/answer_agent.py#L511-L525),
[LoCoMo tool dispatch](../../benchmarks/locomo/retrieval.py#L165-L202).

LoCoMo parses each model-generated call and applies its retrieval budget
before dispatch. In a local runner probe, two stub readers returned the
same saved-query ranking and opposite rankings for a new generated query.
The runner recorded both new responses. A saved trace proves parity only
for its calls and scopes. Keep other calls on the pinned reader until
paired replay covers them.
[Live call dispatch](../../benchmarks/locomo/runner.py#L2392-L2420).

The published LoCoMo serving revision pins PostgreSQL 19 RC 1. The physical
backup contains the stopped PostgreSQL volume. A logical copy into PostgreSQL
18 rebuilds indexes, which can change the order of retrieved candidates.
The reader's declared PostgreSQL major also contributes to the SQL surface
hash it returns. Use the recorded image for exact historical parity and
label the 18 copy as a diagnostic. Preserve the database name because
migrations and the running reader derive the query and graph roles from it.
[Published server image](https://github.com/writeitai/remember-stack/blob/78a83408011fddbd1d62ed8957b799f456b23bbd/Dockerfile.postgres#L1),
[volume backup](../../benchmarks/locomo/sharding/store_backup.py#L1248-L1263),
[manifest member](../../src/rememberstack/spine/query_space/manifest.py#L1061-L1076),
[returned hash](../../src/rememberstack/surfaces/query_sandbox/executor.py#L1061-L1065),
[retained-store restore evidence](evidence_driven_fact_eligibility_20261127.md#retained-store-oracle).
