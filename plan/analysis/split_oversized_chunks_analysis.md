# Oversized E1 chunks

Two of 40 hosted WriteIt documents reportedly failed at `EMBED_CHUNK`. One
chunk had 71,710 characters and 3,021 whitespace tokens. Shrinking the base
budget leaves oversized singletons. Cutting in E0 changes blocks and keys.
E1 checks the full D80 render because even a shorter piece can gain a header.
[Base packer](../../src/rememberstack/core/chunker.py#L173-L240) ·
[D80 rendering](../../src/rememberstack/core/embedding_input_policy.py#L188-L234).

Hosted probes reportedly accepted 19,960 input tokens and a 45,919-token
batch via OpenRouter `nebius/bf16`. Larger probes returned generic 429s.
Qualification must establish full delivery and request limits.

## Reuse and rollout

Chunk-level reuse across a split needs exact window comparisons. Block
hashes can miss a gap edit, and legacy keys leave empty slots across child
sections where E2 reads a neighbour. Inheriting those slots can propagate
a stale match after the split disappears. The owner chose a version-wide
gate. Distant intact chunks repeat paid work and take the split version's
date for relative expressions.
[E1 slots](../../src/rememberstack/workers/e1.py#L734-L765) ·
[E2 windows](../../src/rememberstack/spine/chunk_catalog.py#L373-L419).

An earlier representation can supply a donor while a new representation of
that version is packing. Selection remaps between lookup and freeze, so a
lookup filter can admit a copy after the split flag commits. All four paths
stay within one lineage. Its existing row lock can cover the final check and
publication without covering artifact or provider calls. E1 currently writes
the search row before the authority stamp in separate transactions. Both
stores use the same PostgreSQL engine, so a carried vector can commit with its
stamp and leave no partial copy for recovery.
[Selection reuse](../../src/rememberstack/workers/e2.py#L655-L695) ·
[Vector commit](../../src/rememberstack/workers/e1.py#L515-L572) ·
[Worker composition](../../src/rememberstack/profiles/selfhost.py#L1814-L1827).

Legacy CHUNK can hand off a saved over-limit grid on retry. Operator replay
can reopen a dead letter during the drain. The legacy passage builder leaves
a gap-only window unlabelled beside a block-backed neighbour.
[CHUNK replay](../../src/rememberstack/workers/e1.py#L132-L146) ·
[Ledger replay](../../src/rememberstack/spine/work_ledger.py#L2365-L2392) ·
[Passage labels](../../src/rememberstack/core/source_passages.py#L124-L148).

Unarchive resets cut-short jobs in the same transaction that restores
testimony. A check constraint alone makes one blocked reset roll back the
undo. An application filter would miss the outgoing release. A PostgreSQL
row guard skips blocked updates, and the reset's existing `RETURNING` clause
wakes only rows that moved to pending.
[Reset statement](../../src/rememberstack/spine/lifecycle.py#L2024-L2053) ·
[Unarchive transaction](../../src/rememberstack/workers/reconcile.py#L1024-L1064).

During a later switch, skipping an eligible E1 reset loses it when the same
transaction clears `archived_at`. Guarding the whole undo with the maintenance
fence preserves that marker for retry and avoids a separate backlog of deferred
resets. The connector also calls source-only unarchive on fetched same-byte
observations of live and caller-archived documents. Those calls already return
zeros without resetting work and can still do so during maintenance. A source
archive stays eligible for re-observation while it is archived.
[Archive marker check](../../src/rememberstack/workers/reconcile.py#L1024-L1049) ·
[Same-byte observation](../../src/rememberstack/workers/sync.py#L190-L197) ·
[Source polling](../../src/rememberstack/spine/sync.py#L117-L130).

A drained queue can still leave a successfully embedded 20,000-token chunk
that exceeds an incoming 16,384-token cap. Repacking would change chunk
handles and can invalidate extraction. One local pass over the persisted
inputs under the cutover fence checks fit before search goes offline.
[E1 render from saved spans](../../src/rememberstack/workers/e1.py#L262-L295).

An E1-local batch item limit changes only passage request grouping. A shared
provider limit also applies to semantic queries. Self-host uses the same
embedder for both, and P1 readiness checks model, dimension and policy. A
lower shared byte cap can therefore reject a query while every saved passage
fits and P1 reports ready. Compare the framed query limits before publishing.
Refusing a shrinking shared cap keeps queries working with the existing binding.
[E1 provider](../../src/rememberstack/profiles/selfhost.py#L1878-L1887) ·
[Query provider](../../src/rememberstack/profiles/selfhost.py#L1424-L1439) ·
[P1 readiness check](../../src/rememberstack/adapters/postgres_p1.py#L2049-L2096).

## Benchmark sizing

The author-reported Full-v45 audit found a 21,567-token OCR singleton
(`conv-41/D19`) and a 22,337-token bound with headers before route framing.
For BEAM 100K/1, render the 188 messages as `{role}: {content}` with blank
lines between them. Blockize and enumerate every singleton and every
contiguous run whose individual block whitespace-token counts sum to at
most 400. Measure normalized NFC UTF-8 bytes over the complete span from
the first block's start to the last block's end, including gaps. The largest
body is 14,187 bytes. Add 770 bytes for D80's 48 header code points (up to four
decomposition scalars and four bytes each) and two newlines, plus one end
token. The byte-level tokenizer is bounded by 14,958 tokens before framing.
[Base packer](../../src/rememberstack/core/chunker.py#L198-L238) ·
[Pinned BEAM input](https://github.com/mohammadtavakoli78/BEAM/blob/5a64c992ba05b489b891221b7bcb6b763493dde5/chats/100K/1/chat.json) ·
[Pinned tokenizer](https://huggingface.co/Qwen/Qwen3-Embedding-8B/blob/1d8ad4ca9b3dd8059ad90a75d4983776a23d44af/tokenizer.json).
