# Oversized E1 chunks

## Incident and choice

The owner reported that two of 40 hosted WriteIt documents dead-lettered at
`embed_chunk`. One chunk had 71,710 characters and 3,021 whitespace tokens.
The packer can emit a whole oversized block. Dense YAML, OCR tables and long
turns can exceed an embedder's limit even within that whitespace budget.
[Packer](../../src/rememberstack/core/chunker.py#L173-L240) ·
[D80 renderer](../../src/rememberstack/core/embedding_input_policy.py#L188-L234).

The owner reported accepting a 19,960-token input and a 45,919-token batch
on Qwen3-Embedding-8B via OpenRouter `nebius/bf16`. Larger singleton probes
returned generic 429s. Qualification must establish full delivery and the
effective limits for the actual route. A bare 429 cannot identify a size
failure. [Adapter retries](../../src/rememberstack/adapters/openrouter.py#L1079-L1124).

A smaller whitespace budget still admits oversized singletons. A blanket
16 KiB byte limit would split the 46,833-byte LoCoMo OCR sample. Cutting in
E0 changes the block map and reuse inputs for other stages. Cutting only
failing E1 candidates preserves grounding in the same Markdown. Each cut
must be checked on its own rendered input since D80 can select a larger
header for a shorter body. [Mode selection](../../src/rememberstack/core/embedding_input_policy.py#L204-L223).

The legacy row builder allocates UUIDv4 and stores the span and ordinal as
separate fields. A split can move a following chunk's ordinal and change
the count stored in every D80 location snapshot. Full-row equality holds
with identical inputs and allocations. In mixed versions fitting spans
keep their original bytes and cuts, and fields that depend on final order,
count or changed extraction windows reflect those inputs.
[Row builder](../../src/rememberstack/workers/e1.py#L694-L731) ·
[D80 prepare](../../src/rememberstack/workers/e1.py#L290-L295) ·
[D80 snapshot write](../../src/rememberstack/workers/e1.py#L535-L572).

Selection can run fresh after an equal-input result fails to remap. The
Claimify hash includes that producer's input hash, and remapping the old
claim's citations can still succeed after its reference cards change. The
legacy lookup can select a copy whose own frozen cards differ from those
that produced the claims. The attachment stamps the same hash for copied
claims and copied zero-claim transcripts, leaving their original inputs
unidentified.
Require proof of the original inputs or a verified equivalent before reuse.
[Selection fallback](../../src/rememberstack/workers/e2.py#L655-L695) ·
[Claimify hash](../../src/rememberstack/core/selection_references.py#L70-L86) ·
[Legacy donor lookup](../../src/rememberstack/spine/claim_catalog.py#L425-L447) ·
[Legacy attachment](../../src/rememberstack/spine/claim_catalog.py#L164-L203).

Comparing every published card would repeat a paid call when, for example,
only the ninth-ranked card changes and the same eight cards still fit.
Rank and cap the full window in each occurrence so a newly competitive
card can enter the request. Compare the admitted cards and passages after
remapping. The ordered producer keys still cover the whole window, and
generated text stays out of those keys.
[Ranking and cap](../../src/rememberstack/core/selection_references.py#L277-L306) ·
[Admitted passages and request](../../src/rememberstack/workers/e2.py#L911-L960).

## Why replay is limited to unpublished versions

`embed_chunk` releases E2 only after success. The ledger commits embed
success with the claims handoff. Deferred claims admission also requires
sources completion. With a terminal embed failure, readiness diagnostics
show expected downstream stages missing and blocked by `embed_chunk`. That
ordering explains why the normal path has no claims, observations, relations or
facts for the failed version. Replay verifies the actual rows as well.
[Claims handoff](../../src/rememberstack/workers/e1.py#L422-L442) ·
[Ledger completion](../../src/rememberstack/spine/work_ledger.py#L266-L329) ·
[Sources completion](../../src/rememberstack/spine/processing_layers.py#L794-L808) ·
[Deferred admission guard](../../src/rememberstack/spine/processing_layers.py#L734-L744) ·
[Readiness layers](../../src/rememberstack/spine/readiness.py#L113-L134) ·
[Readiness blockers](../../src/rememberstack/spine/readiness.py#L42-L87).

Publication is independent evidence. E0 marks the version ready before
CHUNK and can enqueue corpus publication. SQL can expose persisted chunk
IDs at that point. E1 commits P1 vectors batch by batch. A dead letter by
itself cannot prove the grid was private. Previously served versions and
versions with uncertain exposure history are ineligible for replay.
[E0 ready and corpus enqueue](../../src/rememberstack/spine/document_catalog.py#L827-L879) ·
[SQL exposure](../../src/rememberstack/spine/migrations/versions/p9_38_0059_d140_effective_time.py#L701-L719) ·
[P1 batch writes](../../src/rememberstack/workers/e1.py#L527-L572).

D140 can use a chunk from an unpublished, embed-failed version. If a
February version packs after January's grid is removed and before its
replacement commits, unchanged text can permanently acquire February's
date. This changes its Selection key and claim assertion date. The
cleanup transaction's locks end before CHUNK rebuilds the grid. Drain and
hold other-version work for the lineage across both transactions to
preserve D140's donor rule.
[D140 lookup](../../src/rememberstack/spine/chunk_catalog.py#L332-L346) ·
[Origin assignment](../../src/rememberstack/workers/e1.py#L167-L217) ·
[Claim date](../../src/rememberstack/workers/e2.py#L1345-L1348).

A local PostgreSQL 18 probe committed one embed batch, then forced a
provider failure to dead-letter. Readiness showed every expected downstream
stage missing. Claims, occurrences, observations and fact relations were
empty. The SQL view exposed eight chunk IDs, and P1 held one partial vector.

The normal enqueue deduplicates against the succeeded CHUNK row. Normal
CHUNK delivery also replays persisted cuts. Clear the private cuts and
explicitly reschedule the existing row in one transaction. The failed
embed row must remain attached to any billable partial batches. Let CHUNK
completion reopen it so it cannot run against a missing grid.
The claim predicate requires unused attempts even when a row previously
succeeded. Generic dead-letter replay would reopen EMBED_CHUNK directly.
[Work key](../../src/rememberstack/spine/work_ledger.py#L1996-L2023) ·
[CHUNK replay](../../src/rememberstack/workers/e1.py#L132-L165) ·
[Claim predicate](../../src/rememberstack/spine/work_ledger.py#L2154-L2167) ·
[Generic replay](../../src/rememberstack/spine/work_ledger.py#L1026-L1098) ·
[Cost foreign key](../../src/rememberstack/spine/migrations/versions/p0_02_0002_infrastructure_registries.py#L139-L159).

A reaped CHUNK attempt can still reach the chunk insert after its replacement
succeeds and the failed grid is deleted. The current attempt check at completion
comes after the insert. EMBED_CHUNK also writes P1 and chunk stamps separately.
Checking the ledger under lock in each data write transaction serializes those
writes with reclaim and replay. Cost attribution already uses this pattern.
[Lease reclaim](../../src/rememberstack/spine/work_ledger.py#L2290-L2322) ·
[Chunk insert](../../src/rememberstack/spine/chunk_catalog.py#L74-L80) ·
[Embed writes](../../src/rememberstack/workers/e1.py#L569-L607) ·
[Cost attribution](../../src/rememberstack/spine/work_ledger.py#L1100-L1131).

Ordinary CHUNK claims use the same deployment, stage and lane predicate
as named deliveries. Legacy workers can claim that route during rollout
because D162 keeps the work keys. Drain legacy E1 workers before activation
and carry the qualified binding through handoff and retries so both stages
use the same fit rules.
[Claim route](../../src/rememberstack/spine/work_ledger.py#L2176-L2201) ·
[Embed handoff](../../src/rememberstack/workers/e1.py#L794-L812).

Provider-confirmed size errors can also reveal an incorrect local ceiling.
Repacking with the same fit check would repeat the cuts, discard partial
vectors and grant fresh attempts. A corrected qualification has to make the
rejected input fail locally before replay can fix that case.

## Benchmark sizing

The published LoCoMo 94.09% result uses Full-v45. The author-reported
audit of 272 frozen sessions found a 21,567-token OCR singleton in
`conv-41/D19` including the end token. The size bound with headers was
22,337 tokens before route framing. Reviewers reproduced a conservative
14,958-token bound on the public BEAM 100K/1 sample before route framing.
These are sizing estimates. The benchmark requires the exact retained
inputs and a qualified ceiling of at least 22,528 tokens.
[LoCoMo result](../../benchmarks/locomo/README.md#published-results) ·
[Frozen store restore](../../benchmarks/locomo/sharding/README.md#4-restore-one-conversation) ·
[Pinned BEAM input](https://github.com/mohammadtavakoli78/BEAM/blob/5a64c992ba05b489b891221b7bcb6b763493dde5/chats/100K/1/chat.json).

Embedding hashes alone miss changed ordinals, headers in the indexed
body and lost typed E2 location elements. The full-row check covers
these dependencies and recomputes D122 stamps and ordered producers.
[D122 window](../../src/rememberstack/workers/extraction_references.py#L56-L63) ·
[Body fetch](../../src/rememberstack/surfaces/query_sandbox/nomination.py#L811-L832) ·
[E2 location handling](../../src/rememberstack/workers/e2.py#L1691-L1737).
