# PDF conversion: every page through OCR

**Decision:** D139. **Date:** 2026-09-27. This analysis records the conflict
that D139 resolves; the current contract is in
[`workspace_formats_design.md`](../designs/workspace_formats_design.md) §4 and §7.

## Problem and evidence

D38's example router extracts text from digital PDFs and OCRs scanned or
complex PDFs. D138's PDF family entry specifies `pypdfium2` text-layer
extraction and conditionally invokes OCR when pages are mostly without text.
Both allow a PDF page to bypass OCR. The owner restated on 2026-09-27:
“EVERY PDF and every page goes through OCR. No question asked.” We found no
earlier PDF-specific owner statement in this repository. D115's earlier
always-OCR decision concerns standalone images and shows the same preference
for predictable coverage, but does not itself decide PDF routing.

The converter and its page locators are part of the open engine. The managed
cloud's billing terminology is separate from the engine's opaque cost-class
labels (D60/D61). The required cloud follow-up is a `doc-scan` receipt for
each accepted PDF page, rather than a `doc-text` charge.

## Alternatives and consequences

| Route | Benefit | Why it lost or won |
|---|---|---|
| Extract a PDF text layer where present | Avoids OCR provider cost on those pages | Contradicts the owner's all-page requirement; text layers can present a different order and layout from the OCR reading. |
| Detect text per page, OCR only scan-like pages | Avoids some OCR cost in mixed PDFs | Contradicts the requirement and gives one PDF two reading paths. A threshold can also misclassify a page with a sparse or unusable text layer. |
| OCR every page | One reading and layout path for digital, scanned and mixed PDFs | Chosen by the owner. Pays OCR cost for every accepted page; converter coverage must expose failed or empty pages honestly. |

An oversized PDF could get an explicit non-reading card, as other oversized
families do. That would still mark PDF conversion as successfully handled
without reading any page. D139 instead stores the original and fails the
version with a typed limit reason, no card and no `document.md` reading. The
PDF has one effective pre-OCR byte limit: the lower of its family reading
limit and the configured provider's input ceiling. With the design's 100 MB
family starting value and a 50 MB provider ceiling, 50 MB is the effective
limit. If a provider rejects an admitted file against an unexpected smaller
limit, that is also a typed conversion failure, never a card.

For example, a PDF with selectable text on page 1 and a scanned form on page
2 sends both pages through the same OCR route. The converter records each page
in the source map and reports any unreadable page as a gap or failure. It does
not use page 1's text layer as a shortcut or a fallback after OCR failure.

## Implementation and review implications

The registry's PDF entry must require an OCR converter/provider and must not
allow a deployment overlay to substitute text-layer extraction for the PDF
family. A missing provider parks conversion under D117. The route's versioned
representation and source map follow D57/D65; changing the route causes
re-conversion rather than rewriting old coordinates. An accepted PDF page is
a source page in a valid PDF admitted under the effective pre-OCR limit. Engine
metering records only `scan_page` with the source page count, including empty
OCR results and response gaps; admission failures have zero accepted pages.
Provider `pages_processed` is diagnostic. The managed cloud follow-up maps
that same count to `doc-scan`, with no duplicate `doc-text` charge. An implementation
test should include born-digital, scanned and mixed PDFs and verify an OCR
attempt and page locator for each page, including a page whose OCR text is
empty. Provider failures must not silently invoke text extraction.

The current implementation has a `mistral_ocr` converter for PDFs but no
engine-shipped PDF route in the self-host default table; PDFs without an
explicit route park under D117. This design change does not claim that the
D133/D138 format registry is already implemented.
