# Byte-authoritative ingest and object classes (2026-09-23)

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
binary and an unrecognized ZIP must fail closed because treating either as text
could select a cheaper rate or a wrong route. A single magic-only `filetype`
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
code (including `application/javascript`), JSON, and plain text therefore share
one byte class, one text rate, and the passthrough converter. The declaration
may select Markdown rendering; other text hints normalize to plain text.
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

Detection runs before any raw write or catalog transaction. Contradictions and
unknown binary return a stable typed error at HTTP 422. Existing rows keep their
recorded MIME and class; changing historical evidence would require explicit
re-ingestion. No dependency or schema migration is required.
