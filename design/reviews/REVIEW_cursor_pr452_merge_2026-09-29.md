The byte classifier and the required storage-class label are in place, but family routing, managed metering, content-hash identity, and the PDF route still disagree with D132, D138, and D139. Re-ingest and the public docs keep the old MIME behavior.

1. **BLOCKER** — `src/rememberstack/workers/e0.py:380` and `src/rememberstack/core/content_detection.py:269`
   A verified text file is handed to the D138 registry only when `detect_content_mime` returns `text/plain` or `text/markdown`, and the registry result is kept only when it starts with `text/`. Any other recognized binary that is not PDF, image, audio, video, or an Office package is returned as `application/octet-stream` and never reaches the registry. `report.ipynb`, `thread.eml`, and `letter.rtf` are stored as `text/plain` and claim-extracted as prose. `book.epub`, `bundle.zip`, `data.parquet`, `app.db`, and `photo.psd` are stored as `application/octet-stream` and get a generic binary card, so the notebook, email, LibreOffice, ebook, archive-member, and dataset profile routes never run. There is no ingest test for these names.
   **Fix:** After the byte class is known, call `detect_mime` for every class and keep the registry family when it is compatible with that class (text families including `message/rfc822`, `application/x-ipynb+json`, and `application/rtf`; container and dataset families for non-office ZIP, tar, Parquet, and SQLite). Add ingest tests that assert those stored MIMEs.

2. **HIGH** — `src/rememberstack/workers/e0.py:355` and `src/rememberstack/core/text_metering.py:72`
   On a managed deployment, `_prepare_managed_text` replaces the detected MIME with `classify_doc_text`'s `text/plain` or `text/markdown`. A `.py` or `.csv` file that detection stored as `text/x-code` or `text/csv` is billed and converted as prose. A real PDF is classified `application/pdf` and then rejected with `rate_class_unavailable` (HTTP 409) before the raw write, so it is never stored and never reaches D139 OCR.
   **Fix:** Keep the detected MIME for the catalog row, storage class, and converter. Use the doc-text classifier only to measure or refuse the doc-text rate. Accept PDFs under the scan route instead of the doc-text refusal.

3. **HIGH** — `src/rememberstack/spine/document_catalog.py:748` and `src/rememberstack/spine/document_catalog.py:752`
   `content_objects` inserts with `ON CONFLICT DO NOTHING`, and `_ADOPT_ROUTABLE_MIME` updates the MIME only when the stored type is not already routable. D132 says an existing row keeps its MIME until re-ingest. Re-sending the same bytes still keeps a routable wrong type. A PDF first stored as `text/plain` stays on the text route after this detector would have stored `application/pdf`. The same hash shared by `notes.txt` and `data.csv` stays `text/plain` and never becomes the delimited profile.
   **Fix:** When the byte class disagrees with the stored MIME, update `content_objects.mime` on re-ingest and release or re-queue conversion. Cover that with a catalog test whose first MIME was routable and wrong.

4. **HIGH** — `src/rememberstack/adapters/converters/pdf.py:81` and `src/rememberstack/workers/e0.py:659`
   A file stored as `application/pdf` is read by `PdfConverter`, which extracts the text layer and calls OCR only when more than half the pages are empty (`policy="pdf-text-layer"`). `_converter_for` still returns `CardConverter` whenever `exceeds_reading_limit` is true, including for PDF. `test_an_oversized_file_is_never_parked` still expects that oversized PDF to be scheduled rather than failed. D139 requires OCR on every accepted page, and an over-limit PDF must fail with a typed reason and no card and no `document.md`.
   **Fix:** Send every accepted PDF page through OCR, and fail an over-limit PDF before any card or text-layer reading. Change the oversized-PDF test to expect that typed failure.

5. **MEDIUM** — `src/rememberstack/core/content_detection.py:187` and `src/rememberstack/core/text_metering.py:74`
   Storage classifies a PDF only when a header in the first 8 KiB is followed by an object token and `%%EOF`. Managed metering calls `has_pdf_body` without `%%EOF`. A note that contains `%PDF-1.4` and `1 0 obj` but not `%%EOF` is stored as text on self-host and refused with HTTP 409 on a managed deployment. A Markdown file that also contains `%%EOF` is stored as `application/pdf`, or HTTP 422 if it was declared `text/markdown`.
   **Fix:** Use one PDF predicate in detection and metering, and require enough structure that a prose quotation of those tokens stays text.

6. **HIGH** — `website/src/app/docs/guides/file-types/page.mdx:78`, `website/src/app/docs/reference/http-api/ingest/page.mdx:83`, `website/src/app/docs/guides/ingest-files/page.mdx:126`, `website/src/app/docs/self-hosting/converters/page.mdx:211`
   Public docs still say the server recognizes a file by extension first, that an explicit `mime` always wins, and that an `application/octet-stream` upload is fixed by resending it as `text/markdown`. The ingest error table does not list `content_type_mismatch` or `ambiguous_office_format`. The converters page still says the `pdf` route reads a text layer and lists `.tar.gz` as a choice inside the text class. `website/public/llms-full.txt` repeats those pages. D66 requires the shipped behavior in the same PR.
   **Fix:** Document byte class first, HTTP 422 for a contradicted declaration, and first-write content-hash MIME behavior. Regenerate `llms-full.txt` from the corrected pages.

7. **MEDIUM** — `plan/analysis/content_detection_and_object_classes_20260923.md:27` and `plan/analysis/content_detection_and_object_classes_20260923.md:62`
   The analysis is marked reconciled with D133/D138/D139, but it still says unknown binary and an unrecognized ZIP fail closed with HTTP 422, and that CSV, JSON, and code share the passthrough converter. D132 and the implementation store unknown binary as a card and let the registry assign text postures.
   **Fix:** Rewrite those paragraphs so a cold reader sees the card outcome and the registry postures.

Storage-class coverage held: every production `write_bytes` passes `hot` or `cold`, including derived objects, snapshots, checkpoints, K transcripts, and the managed raw write, and the CI smoke writes pass `storage_class`. `src/tests/core/test_content_detection.py` is on the unit inventory.

VERDICT: REQUEST_CHANGES

## Disposition (2026-09-29)

| Finding | Disposition | Reason / action |
|---|---|---|
| 1 | Adopted | Preserve compatible D138 text-bearing and binary families after byte admission; add ingest tests for eight families. |
| 2 | Not adopted in #452 | The managed doc-text profile deliberately admits only text; PDF scan billing and managed binary admission are separate from UMC D82 object-label compatibility. D138 self-host format postures do not establish managed product billing. |
| 3 | Adopted in design; runtime migration deferred | D55 content-hash first-write identity does not safely flip a routable shared MIME on a same-byte no-op. Correct the D132/analysis promise: historical wrong classes need a dedicated migration and reprocessing plan. No real customers or migration is part of this admission PR. |
| 4 | Not adopted in #452 | The stock text-layer PDF converter and oversized-PDF card come from main, not this PR. D139 implementation is a separate track. D132 always classifies a recognized PDF as PDF and introduces no text-layer shortcut. |
| 5 | Adopted | Use one complete PDF predicate in byte detection and managed metering, requiring object and end markers. |
| 6 | Partially adopted | Update file-type/ingest docs and HTTP typed errors. Keep the PDF converter description as shipped on main; public docs must not claim D139 OCR is implemented when it is not. |
| 7 | Adopted | Rewrite contradictory analysis paragraphs to distinguish D138 cards and registry text postures. |
