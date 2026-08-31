# Unroutable MIME types: why ingest must refuse them at the gate

**Status:** analysis (non-binding). Supports **D104** and the `e0_files_design.md`
§3 amendment that makes routability an admission check.
**Date:** 2026-08-31. Engine evidence read at `origin/main` `9ea2953b`
(post-`v0.9.0`) and verifiable in this tree. Statements about the managed
fleet's live route table and its control-plane reservation behaviour come from
a **different repository** (`ultimate-memory-cloud` at `2db8c2c`,
`infra/prod.yaml` and `spend_safety/dp_proxy.py`) and cannot be checked here;
they are marked where they appear and none of the engine reasoning depends on
them.

---

## 1. What happened before D104

This section describes the **pre-D104 baseline** — the behaviour at parent
commit `9ea2953b`, which this analysis argued should change. It is written in
the present tense of that commit and is no longer true of `main`.

The conversion router was consulted for the first time **inside the convert
worker**, long after the upload had been accepted.

`ConversionRouter.converter_for` (`core/conversion.py`) resolves a MIME by
**exact dictionary lookup** and raises `UnroutableMimeError` when the key is
absent. There is no non-raising way to ask the router whether a type is
routable — `converter_for` is its only query.

The ingest surface never asks. `_mount_ingest` in `surfaces/http_api.py`
validates the body size, the `source_kind`/`source_ref` pairing, the principal
headers, and the timezone of `source_modified_at`. It does not look at whether
`mime` can be converted. The upload is admitted, hashed, written to immutable
raw storage, and returned as a normal accepted-not-ready receipt with a durable
`version_id`.

The failure surfaces one stage later, in `workers/e0.py::ConvertHandler.handle`:

```python
try:
    converter = self._router.converter_for(mime=source.mime)
except UnroutableMimeError as err:
    self._catalog.mark_version_failed(version_id=source.version_id, error=str(err))
    raise NonRetryableHandlerError(str(err)) from err
```

So the sequence a caller actually experiences is: **accepted → stored →
(in the managed product, a spend reservation committed) → silently dead-lettered
→ discovered only by polling readiness.**

## 2. Why this is worse than an ordinary error

Three properties compound, and the third is the one that makes it worth fixing
before anything else.

**The receipt is a lie by omission.** `accepted` means E0 stored the bytes, and
that is literally true. But the caller reads it as "this will become memory",
because for every routable type it does. Nothing in the response distinguishes
"accepted and will convert" from "accepted and cannot possibly convert".

**The cost is charged before the impossibility is discovered.** In the managed
product the control-plane proxy commits its reservation on engine acceptance.
The engine has by then written a durable raw object it will never derive
anything from. Both the money and the storage are spent on work that was
knowably impossible at the moment of submission — the route table was already
in memory.

**The caller cannot fix it themselves.** D55 makes identical bytes a no-op: a
resubmission returns the existing version rather than creating a new one, so
uploading the file again after the deployment gains a route changes nothing.
The caller has no API by which to ask for the version to be reprocessed.

Recovery exists, but only as **operator** work. The convert stage's failure
dead-letters a work-ledger row, and `WorkLedger.replay_dead_letter` — reached
through `remember ops replay` — reopens exactly one such row with a fresh
attempt allowance. Once a route exists, an operator can replay the row and the
convert handler will route the version normally; `mark_version_failed` does not
bar a later promotion.

So the honest statement is not "the damage is permanent" — it is that recovery
is **out-of-band, per-document, and unavailable to the person who caused it**.
Each mistaken upload leaves a durable raw object, a committed reservation in the
managed product, a caller with no self-service path, and one more row an
operator must be asked to replay. That burden scales with the number of wrong
guesses, and every one of them was refusable at admission for free.

That asymmetry is the argument. The check costs a set lookup; the alternative
costs storage, money, a confused caller, and an operator ticket.

## 3. This is not about any one format

The defect was found while analysing audio, but audio is only the instance that
happened to be looked at. The mechanism keys on *absence from the route table*,
so it fires identically for every type the deployment has not configured.

The stock table (`STOCK_CONVERSION_ROUTE_NAMES`) is two entries,
`text/markdown` and `text/plain`. The managed fleet's live table, set as
deployment policy, is those two plus `application/pdf` routed to `mistral_ocr`.
Everything else a caller might plausibly send — audio, video, images, office
documents, archives, JSON, CSV — takes the accepted-then-dead-letter path today,
in exactly the same way, for exactly the same reason.

A per-format fix would therefore be four fixes that each solve a quarter of one
problem. The check belongs on the route table, once.

## 4. Where the check must live

Two placement questions, and getting the first wrong makes the second moot.

### 4.0 In E0, not on a surface

The obvious place to put an admission check is the HTTP ingest handler, and it
is the wrong one. Three ingresses reach E0, and they do not share a handler:

- HTTP `POST /ingest` (`surfaces/http_api.py`), which the SDK and CLI use;
- the local MCP `ingest` tool (`surfaces/mcp.py`), which calls the composed
  ingest port directly;
- the connector sync worker (`workers/sync.py`), which calls
  `ingest_observed` directly on the same port.

A check on the HTTP handler would leave the MCP tool and every connector still
admitting bytes the convert stage can only dead-letter — the defect would
survive on two of three paths while appearing fixed.

`UploadIngestor` is the single object all three write through, which is what
CLAUDE.md's library-boundary rule already asserts: *ingestion always writes
through E0*, and no extension point may bypass an invariant. So the check
belongs in `UploadIngestor._guard_ingest`, beside the D74 admission guard that
is there for exactly the same reason. Surfaces then only *render* the refusal
in their own idiom — HTTP as 415, a tool call as a typed error.

Two details make that placement actually hold rather than merely sound right.

The route table is a **required** constructor argument. A first draft defaulted
it to "no check", which would have made the gate exactly as strong as every
composer remembering to pass it; the shipped self-host profile would have been
fine and any other composition silently would not. Every deployment has a route
table — the settings default is the stock text table — so omission expresses
nothing but a mistake, and refusing to construct is the honest response.

And routability is decided **before** the D74 per-source admission query
rather than after. Both orders are safe, since neither writes bytes, but
deciding routability first avoids an admission query for a request that cannot
be accepted, and stops a forget-state error from masking a plain "we do not
convert that" — a caller sending an unsupported type should be told that, not
handed an unrelated failure whose cause they cannot act on.

The claim is about the gate's own two checks, not about everything that can
refuse a request. A deployment-wide availability barrier — an in-progress
forget that makes the whole deployment answer 503 — is composed above the
surfaces and still runs first. That is correct: it says the deployment cannot
serve anything right now, which is a different statement from "we do not
convert that type".

### 4.1 Against the deployment's own route table

The route table is **deployment policy** (D61): a deployment declares
`conversion_routes` as a `MIME → adapter-name` map, and
`build_conversion_routes` materializes it. Any admission check must read that
same policy rather than embed a guess about what "should" be convertible.

| Option | Verdict |
| --- | --- |
| Keep discovering it in the worker (status quo) | Rejected. The information is available at admission; spending storage and money to rediscover it is pure waste, and the caller cannot recover from it without an operator (§2). |
| Hard-code a "known media types" deny list at ingest | Rejected. It would drift from the route table immediately and would be wrong for any deployment that configures a route the list does not know about. The table is the only authority. |
| Build the `ConversionRouter` in the API process and ask it | Rejected. Building routes constructs the adapters, and a provider-backed adapter refuses composition without its API key. This would force provider credentials into a process that performs no conversion — a strictly worse secret posture for a membership test. |
| Sniff the bytes at admission and route on detected type | Rejected **as the mechanism for this check**, though valuable on its own. Sniffing is a decode-and-inspect step with its own cost and sandboxing requirements. It answers a different question ("is this really what you said it is?") and must not gate the cheap question ("is what you said routable at all?"). The two compose: the cheap check runs first and always. |
| **Compare the declared MIME against the deployment's configured route-name table (chosen)** | The table is already the authority; membership is a set lookup requiring no credentials, no decode, and no new configuration. |

### 4.2 Why the two tables agree

An obvious objection to checking the *settings* map rather than the *built*
router is that the two could drift, and then ingest and the worker would
disagree about what is routable — the worst possible outcome, because it would
reintroduce dead letters while claiming to have removed them.

They cannot drift *within one configuration*. `build_conversion_routes` raises
`UnknownConverterError` when a route names an adapter it does not know, so
composition fails at startup rather than producing a router with fewer keys than
the configuration. A process running a given configuration therefore has a
router whose key set is exactly that configuration's key set, and checking
membership in the configuration is checking membership in the router.

The precise scope matters. The guarantee is per-configuration, not global: the
gate and the convert worker are separately composed processes, so a deployment
that changes its route table has a window in which one has restarted and the
other has not. That window is the same race §6 leaves `UnroutableMimeError` in
the worker to cover, and it is bounded by a deliberate operator action rather
than being a property of ordinary traffic. The claim to make is "the gate is
never looser than the worker it was composed with", not "the two can never
differ".

### 4.3 Matching is exact, deliberately

`converter_for` does an exact dictionary lookup, so the admission check performs
the same exact lookup on the same string. Normalizing at the gate — lowercasing,
stripping `; charset=utf-8` — would be an improvement to *routing*, but applying
it only at the gate would create precisely the divergence §4.1 exists to
prevent: a type the gate accepts and the worker then rejects.

If MIME normalization is wanted, it belongs in the router, where both callers
inherit it. Until then the gate is exactly as strict as the worker, which is the
property that matters.

## 5. What the caller gets instead

A refusal at admission, with the HTTP status that means this and no other thing:
**415 `unsupported_media_type`**, naming the types this deployment does convert.

Three consequences follow, and all three are improvements:

- no raw object is written, so nothing durable is created for input that can
  never produce a representation;
- no reservation is committed, because the engine never reports acceptance;
- the caller learns at submission time, synchronously, instead of by polling a
  readiness endpoint until it reports a terminal state.

It also makes the deployment's capability *discoverable by trying* — the refusal
names the supported set, so a client that guesses wrong is told what it may send.

## 5.1 One divergence the gate does not close

The gate checks the MIME the caller declared. The convert stage does not read
that value: `_SELECT_CONVERT_SOURCE` joins `content_objects` and takes `c.mime`,
and that row is written `ON CONFLICT (deployment_id, content_hash) DO NOTHING`
— so for any given bytes, the **first MIME ever seen wins permanently**.

Those two values are normally identical, and diverge only when the same bytes
are ingested a second time under a different MIME. If the first-seen MIME is
unrouted while the second is routed, the gate admits and the worker
dead-letters — the exact outcome D104 removes, reachable in that narrow case.
Getting there needs the first MIME to have become unrouted since it was stored
(a route-table change) or the row to predate this gate, because otherwise the
gate would have refused the first ingest too.

**Closing it costs a query on every ingest.** The gate would have to look up
the existing content object by hash to learn the effective MIME, since nothing
in the request reveals it. That is one indexed read added to a hot path, to
cover a case that requires byte reuse across MIMEs plus a route change. The
trade is real in both directions and is left as an explicit open question
rather than silently paid or silently skipped:

- if it is paid, the refusal must name the *effective* MIME, not the declared
  one, or the caller is told their own request is unsupported when it is the
  stored reading that cannot be routed;
- until it is, the worker's `UnroutableMimeError` still catches it, and the
  outcome for those specific ingests is exactly the pre-D104 behaviour — not a
  regression, an improvement that does not reach them.

The honest statement of the guarantee is therefore: *no upload is admitted
under a MIME this deployment cannot convert*, not *no unconvertible version is
ever created*.

## 6. What this deliberately does not do

- It does not make any format supported. Registering an adapter is a separate
  act; this only stops pretending that unregistered formats might work.
- It does not validate that the bytes match the declared type. A caller that
  labels an MP3 as `text/plain` still gets past the gate and fails in the
  converter — correctly, since that is a content error, not a routing one.
- It does not change what the worker does. `UnroutableMimeError` remains, and
  remains non-retryable: it is still reachable when a deployment's route table
  changes between admission and conversion, which is exactly the narrow race the
  worker's handling exists for, and in the content-object divergence of §5.1.

## 7. Sources

- `src/rememberstack/core/conversion.py` — `ConversionRouter.converter_for`,
  `STOCK_CONVERSION_ROUTE_NAMES`.
- `src/rememberstack/adapters/converters/__init__.py` —
  `build_conversion_routes`, `UnknownConverterError` on unknown adapter names.
- `src/rememberstack/surfaces/http_api.py` — `_mount_ingest`, the admission
  checks that exist today.
- `src/rememberstack/workers/e0.py` — `ConvertHandler.handle`, the
  `UnroutableMimeError` → `mark_version_failed` → `NonRetryableHandlerError`
  path.
- `src/rememberstack/spine/document_catalog.py` — `mark_version_failed`; no
  counterpart re-enqueues a failed version.
- `decisions.md` D55 (identical bytes are a no-op), D61 (deployment policy vs
  engine defaults), D38/D65 (the conversion router and its envelope).
