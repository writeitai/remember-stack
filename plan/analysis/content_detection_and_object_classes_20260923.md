# Byte-authoritative ingest and object classes (2026-09-23)

**Reconciled 2026-09-29 with D133/D138/D139.** D138 changed the unknown-binary
outcome from refusal to a stored binary card, and its registry gives compatible
text formats distinct postures. D132 still rejects a declaration contradicted
by recognizable bytes. D139 requires every accepted PDF page to use OCR;
byte detection never selects a PDF text-layer route.

## Problem and constraints

An uploader can call a PDF `text/plain`, causing the wrong converter, rate class,
and raw storage class. D51 routes original media by MIME but derived and private
objects currently leave the object-store port with no class. The managed gateway
requires a label for every write. D117 still requires a valid but unrouted
original to be retained and parked.

## Choices

The Python standard library can inspect fixed binary signatures, header fields,
ISO BMFF file-type brands, and ZIP package member names, then validate remaining
text as UTF-8. The detector is about 12 KiB of source and adds no
third-party dependency, dependency wheel, or separate licence beyond the
existing Python runtime. Its standard-library pieces are maintained with
Python releases. Reading ZIP names avoids decompressing
untrusted document bodies. Its limit is that a signature is a class assertion,
not full format validation; converters still validate their inputs. Unknown
binary and an unrecognized ZIP cannot be treated as text because that could
select a cheaper rate or wrong route. D138 stores them with a file card (or
uses a compatible archive/data family hint and lets its converter validate).
A single magic-only `filetype`
dependency would still need office package inspection and Unicode validation.
A libmagic binding has broad signature coverage but adds a system library and
platform-specific packaging to both OSS and managed deployments. Neither
changes the need for a typed mismatch check.

Short printable prefixes such as `BM` and `ID3` are insufficient evidence of a
binary class, and `%PDF-` mentioned inside a text document or another format
must not override its true class. Prefixed PDF headers require a PDF object body
and end marker, with the header searched in a bounded 8 KiB preamble (an
implementation starting point); trailing padding does not erase the marker.
These checks limit false refusals without decoding full media bodies.

Text flavours cannot be inferred reliably from bytes. Markdown, CSV, source
code (including `application/javascript`), JSON, and plain text share one
byte class, but D138's registry gives compatible families different postures:
prose, search-only text, or deterministic data profile. The filename and
declaration are hints within that established byte class, not proof of a
binary PDF or image.
UTF-8 BOM and CRLF remain valid. HTML markup is distinguishable from native
text and keeps its separate
`text/html` conversion route. Empty bytes are assigned the text class for
self-host pipeline compatibility, while managed text metering still refuses
an empty measured source. Existing managed text exclusions for structured or
armoured text still apply after stripping repeated or whitespace-separated
leading BOMs and after class detection.

Derived representation objects and P3 snapshots are frequently opened by
queries, mounts, and browse operations, so `hot` avoids cold retrieval charges.
Lane checkpoints and private K transcripts are retry/replay records outside the
query path, so `cold` is appropriate. Originals retain D51's media-hot and
audit/reconversion-cold split. Requiring the class on the port makes omissions
a type or runtime error, including in a new writer.

## Failure and migration behavior

Detection runs before any raw write or catalog transaction. A contradicted
declaration returns a stable typed error at HTTP 422. Unknown binary is stored
with the D138 binary card. Existing content rows keep their first recorded
MIME on a same-byte no-op when that MIME is routable. D117 retains its
unroutable-to-routable same-byte exception. Correcting a historical wrong
routable class requires a separate migration and reprocessing plan; D132 changes admission
for new content, not prior evidence. No dependency or schema migration is required.
