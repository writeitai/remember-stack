# Image conversion: always OCR plus visual description

Status: supporting analysis for the owner-approved simplification, 2026-09-07.
Binding contract: `../../designs/media_design.md` §2, D115.

The question is whether image ingestion needs a classifier to select or weight OCR
and visual description. A photographed contract requires dedicated transcription;
a whiteboard, screenshot, or chart also needs visual relationships. The categories
overlap, and a classification mistake must not silently discard either kind of evidence.

The owner selected two independent calls for every supported image: dedicated OCR
and a vision LLM describing the original pixels. No classifier, conditional budgets,
or LLM correction of OCR. This buys predictable coverage and fewer control paths at
the cost of paying for two calls when one produces little useful information. This
is not a claim that either model is always accurate or that all evidence is captured.

Alternatives considered: exclusive routing loses evidence on mixed images; weighted
routing retains both lanes but introduces classification and budget policies without
measured benefit; one VLM producing both outputs couples transcription to generative
description and does not meet the owner's contract-transcription requirement.

Both results use the existing D65 conversion envelope and D54 source lineage. They
are two derivations of one source, not independent corroboration. A no-text OCR result
is successful and empty; provider failure is a failure, not evidence of no text.
Independent retry needs durable successful lane output, keyed by source version and
lane configuration, so a worker restart does not repeat an already completed call.
This cannot guarantee exactly-once provider execution if a process dies between the
provider response and durable persistence. Usage received before persistence is
recorded in the existing cost ledger. A crash before recording or an unaccountable
provider response can still leave a reconciliation gap; never invent zero usage or
claim exactly-once accounting across that external-call boundary.

Inspected local contracts: `../../designs/media_design.md` §§2,5,6;
`../../../src/rememberstack/workers/e0.py` ConvertHandler;
`../../../src/rememberstack/adapters/converters/mistral_ocr.py`;
`../../../src/rememberstack/model/conversion.py`. No provider pricing or quality
benchmark is asserted by this analysis. Provider/model selection must use current
primary documentation and be explicit deployment configuration.

Filesystem mounting, source_open, storage-read audit, and media-price activation
are separate changes; this decision does not require or claim their implementation.
