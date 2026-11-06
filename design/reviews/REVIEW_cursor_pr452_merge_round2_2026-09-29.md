The family-routing fix is in place for the eight named formats, and storage-class labels are still required on object writes. The compatibility filter and the PDF predicate still mis-classify real uploads.

1. **HIGH** — `src/rememberstack/workers/e0.py:401` (allowlist at `388`)
   Byte class `application/octet-stream` keeps any registry family except PDF, image, media, and Office. A text extension is not in that exclusion set, so binary is stored as prose. `notes.txt` with `mime=application/octet-stream` and body `b"The secret is swordfish\x00"` is valid UTF-8, so `TextConverter` accepts it and claim extraction runs. The same upload of a non-Office ZIP (`PK\x03\x04…`) is stored as `text/plain` instead of an archive or binary card. In the other direction, `html` is missing from the text allowlist, while all of `word` is included: `index.html` whose body is `b"<!-- saved -->\n<html><p>Hi</p></html>"` (client `mime=text/html`) stays `text/plain` and is claim-extracted, and plain UTF-8 named `notes.docx` with `mime=application/octet-stream` is stored as OOXML.
   **Fix:** For `application/octet-stream`, keep only binary-compatible families (archive, dataset, ebook, binary). For text bytes, allow `html`, and allow `word` only for `application/rtf`. Add ingest tests for a NUL/`PK` payload with a `.txt` name, an HTML file with a leading comment, and UTF-8 named `.docx`.

2. **HIGH** — `src/rememberstack/core/content_detection.py:191` and `src/rememberstack/core/text_metering.py:72`
   The shared PDF predicate still treats a file that starts with the header as a PDF whenever some later line matches `N M obj` … `endobj` and `%%EOF`. `b"%PDF-1.7\n1 0 obj is the first object and endobj closes it.\n%%EOF\n"` declared `text/markdown` is HTTP 422, not a note. Managed metering still refuses on the raw prefix list before that predicate: `b"%PDF-1.4 is only a header\n"` and `b"ID3 tags are metadata\n"` are text on self-host and HTTP 409 `rate_class_unavailable` when metering is on. A CR-only PDF (`%PDF-1.7\r1 0 obj\r…\rendobj\r%%EOF`) misses the `\n` anchor; with the usual high-bit comment it becomes `application/octet-stream` and the `.pdf` name is then discarded because `pdf` is excluded, so it never enters the PDF route.
   **Fix:** Require a PDF header at a line start, a real object/endobj pair, and `%%EOF`, and do not treat a multi-word prose line as an object. Delete the `%PDF-` / `ID3` / `GIF87a` / `OggS` / `RIFF` prefixes from managed metering and call this same predicate. Accept CR, LF, and CRLF. Test the prose note, the managed/self-host pair, and a CR-delimited PDF.

3. **MEDIUM** — `src/rememberstack/core/content_detection.py:250` and `:257`
   `PAR1` at both ends and a leading `8BPS` are still enough. `b"PAR1\nmagic notes\nPAR1"` and `b"8BPS is the Photoshop marker\n"` declared `text/plain` raise `content_type_mismatch` and are not stored. Those are the same printable-prefix false positives already rejected for `BM`, `GIF`, and `ID3`.
   **Fix:** Require a Parquet footer length and a Photoshop version/reserved header, not the four-byte strings alone. Test both notes.

4. **MEDIUM** — `src/rememberstack/core/content_detection.py:325` and `:345`
   A generic declaration is treated as a different class. Recognized HTML declared `text/plain` (the registry’s non-deciding guess) is HTTP 422 even though the bytes are HTML. A real `.docx` declared `application/zip` is HTTP 422 because the class is `application` rather than `office`, so a correct container label never reaches the Word route.
   **Fix:** Treat `text/plain` like `application/octet-stream` for HTML. Accept `application/zip` for an Office package the member names already identified. Test both.

5. **MEDIUM** — `website/src/app/docs/reference/http-api/ingest/page.mdx:87`, `website/src/app/docs/self-hosting/converters/page.mdx:368`, `website/src/app/docs/reference/errors/page.mdx:45`, `src/rememberstack/spine/document_catalog.py:752`, `decisions.md:6220`
   The HTTP and converters pages still say a file first stored as `application/octet-stream` is fixed by resending it as `text/markdown`. That resend is now HTTP 422 for binary, and a no-op when the first MIME is already routable. The errors catalogue never lists `content_type_mismatch` or `ambiguous_office_format`. Troubleshooting (`website/src/app/docs/self-hosting/troubleshooting/page.mdx:252`) still says the name is chosen first. D132 says a content hash keeps its first MIME until a separate migration, but `_ADOPT_ROUTABLE_MIME` still rewrites that shared row when the stored type is unroutable, so a second lineage ingest changes the MIME and releases parked conversion for every document with those bytes.
   **Fix:** Document byte class first, HTTP 422 for a contradicted declaration, and first-write MIME. Add the two 422 codes to the errors page. Either stop the unroutable MIME adopt or state that single exception in D132. Regenerate `website/public/llms-full.txt`.

Storage-class coverage still holds: production `write_bytes` calls pass `hot` or `cold`, including the managed raw write. D139’s OCR converter and managed PDF billing stay out of this PR, matching the disposition. The family filter and the PDF predicate are not closed.

VERDICT: REQUEST_CHANGES

## Disposition (2026-09-29)

| Finding | Disposition | Reason / action |
|---|---|---|
| 1 | Adopted | Constrain registry hints to byte-compatible text and binary families; cover NUL/ZIP masquerading as text, HTML comments and false OOXML. |
| 2 | Adopted | Require line-shaped PDF object/end markers with CR/LF support; managed metering now uses the same structural detection for printable prefixes. |
| 3 | Adopted | Validate Parquet footer length and Photoshop header fields; test printable notes. |
| 4 | Adopted | Allow generic text/plain for detected HTML and ZIP container MIME for byte-verified OOXML. |
| 5 | Partially adopted | Update the errors catalogue, troubleshooting, and compatible resend examples. Preserve D117’s existing unroutable-to-routable same-byte exception and state it in D132; stopping it would strand parked content. |

No third Cursor round is permitted by the task cap. Subsequent CI fixture fixes and focused tests are recorded in the PR validation section.
