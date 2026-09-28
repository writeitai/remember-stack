# Format coverage — delivery order

Build order for D133 ([format conversion](../designs/format_conversion_design.md)), D134
([document metadata and search](../designs/document_metadata_and_search_design.md)) and D138
([workspace formats](../designs/workspace_formats_design.md)). The designs describe the system;
this file says what to build first and why.

D134 is built (metadata, `search_documents`, document filters, self-reference naming). D138
defines the families; they are delivered in this order, each with its implementation and tests
merged before the family counts as supported (`/docs/project/not-built-yet` stays truthful):

1. **Registry and routing.** Extension-first detection, the D138 family table, overlay route
   configuration, the extraction eligibility policy (E1 chunk cuts, E2 scheduling), the
   head/tail large-text profile, the `text` converter for prose, code, config and logs, and
   cards (image, media, archive listing, binary, oversized).
2. **Documents.** Office (docx/pptx with core-property metadata and per-slide locators;
   LibreOffice for doc, odt, rtf, ppt, odp), PDF OCR on every page (D139), HTML,
   email, notebook.
3. **Data files.** Spreadsheet, delimited and dataset profiles.

Then the Workspace-Bench smoke (task 300 and the five-task smoke) exercises all of them on the
real workspace.
