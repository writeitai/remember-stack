# Ingestible File-Type Inventory & Ingest Posture Analysis

- **System:** RememberStack OSS (Open Memory Infrastructure for AI Agents)
- **Author:** Antigravity (AGY)
- **Status:** Non-Binding Architectural Analysis (`plan/analysis/ingest_file_type_inventory/external_agents/agy.md`)
- **Date:** 2026-08-26
- **Context:** Extension and taxonomy analysis for E0 conversion and sensory ingestion planes (`requirements_v3.md`, D38, D51, D57, D59)

---

## 1. Short Answer & Organizing Principles

### 1.1 The Multi-Layer Reality of "File Types"

When an AI agent memory system is asked to ingest files from arbitrary human and agent workflows, "file format" cannot be modeled as a single flat string. A file in modern operating systems and web transports is a multi-layered artifact:

```
+-------------------------------------------------------------------------------+
| 1. Nominal Extension  | .docx, .ts, .mp4, .dat (user hint; easily forged/ambiguous) |
+-------------------------------------------------------------------------------+
| 2. Physical Signature | Magic bytes, PRONOM ID, FileSig (e.g. PK\x03\x04, %PDF-1.7)   |
+-------------------------------------------------------------------------------+
| 3. Transport MIME     | Content-Type header (often degraded to application/octet-stream)|
+-------------------------------------------------------------------------------+
| 4. Outer Container    | ZIP, OLE2 CFBF, RIFF, ISOBMFF, Matroska, Tar, Directory Bundle |
+-------------------------------------------------------------------------------+
| 5. Inner Streams      | Elementary codecs (H.264, AV1, AAC, PCM), XML DOMs, Blobs      |
+-------------------------------------------------------------------------------+
| 6. Semantic Features  | Dynamic formulas, macros, vector paths, 3D meshes, AST symbols |
+-------------------------------------------------------------------------------+
```

An **exhaustive inventory** for an agent-memory system must account for all six layers:
1. **Nominal Extensions**: The actual filenames users and tools upload (including common collisions).
2. **Canonical MIME Types**: Standard IANA media types and widespread de facto identifiers.
3. **Compound Packaging**: Recognizing that `.docx`, `.xlsx`, `.epub`, `.jar`, and `.apk` are all ZIP archives wrapping domain-specific XML/binary schemas, while `.doc`, `.xls`, and `.msg` are OLE Structured Storage files.
4. **Container vs. Codec Decoupling**: Recognizing that `.mp4`, `.mkv`, and `.mov` are multimedia multiplexers carrying arbitrary video, audio, and timed-text streams.
5. **Epistemic Ingest Postures**: Determining how the memory plane processes the input into `document.md` + sidecars while preserving raw access.

### 1.2 Coarse Ingest Posture Taxonomy

Every format entry in this inventory is assigned one of seven operational **Ingest Postures** within the RememberStack E0 ingestion pipeline:

| Posture Label | Pipeline Processing Mechanics | Output Artifacts | Raw Handling |
| :--- | :--- | :--- | :--- |
| `text-native` | Direct UTF/ASCII decode with encoding fallback (UTF-8, UTF-16, latin1). Clean character passthrough to `document.md`. | `document.md` | Immutable raw preserved |
| `structured-parse` | Deterministic schema/AST parse (JSON, YAML, CSV, Parquet, XML). Generates structured Markdown tables or normalized text representation. | `document.md` + schema sidecar | Immutable raw preserved |
| `document-convert` | Visual/paged layout conversion (PDF, DOCX, ODT, RTF). Extracts paged text, headings, tables, and embedded raster figures into `media/`. | `document.md` + `page_map` + `media/` figures | Immutable raw preserved |
| `media-transcribe` | Sensory transcription/description pipeline. Audio/Video $\rightarrow$ ASR transcript + speaker diarization + temporal time-map `{t_start, t_end}` + extracted keyframes; Images $\rightarrow$ VLM description + OCR. | `document.md` + `time_map` + `media/` keyframes | Immutable raw preserved (`raw_uri`) |
| `archive-expand` | Container unpacking with recursion limits and bomb-safety controls (ZIP, TAR, 7Z). Emits synthetic directory index and recurses children into E0. | Synthetic index `document.md` + child document trees | Archive preserved in raw |
| `binary-opaque` | Cryptographic hashing (SHA-256), magic detection, metadata/header extraction (e.g. ELF headers, PE exports). No full text parse. | Search stub `document.md` with metadata properties | Immutable raw preserved |
| `dangerous/quarantine` | Security isolation for active executables, macro-enabled docs, polyglots, and disk images. Sandboxed passive metadata extraction only. | Quarantined metadata stub `document.md` | Air-gapped / flagged raw storage |

---

## 2. Exhaustive Format Taxonomy & Inventory

### 2.1 Documents, Office, Page Description & Publishing

This category encompasses formatted prose, word-processing files, fixed-layout page descriptions, rich notebooks, and typesetting formats.

| Extension(s) | Common MIME Type(s) | Description & Semantic Payload | Ingest Posture | Ingest Router Target & Notes |
| :--- | :--- | :--- | :--- | :--- |
| `.pdf` | `application/pdf` | Portable Document Format (ISO 32000). Vector text, fonts, images, form fields. | `document-convert` | Digital text extraction with OCR fallback for scanned pages; outputs `page_map` + bounding boxes. |
| `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | Microsoft Word (Office OpenXML). ZIP container with `word/document.xml`, styles, and media. | `document-convert` | MarkItDown / Pandoc / OOXML parser $\rightarrow$ Markdown headings, lists, tables; figures to `media/`. |
| `.doc` | `application/msword` | Microsoft Word 97-2003. OLE2 Compound Document Binary Format (CFBF). | `document-convert` | LibreOffice filter / Antiword / Tika $\rightarrow$ clean Markdown + extracted figures. |
| `.docm` | `application/vnd.ms-word.document.macroEnabled.12` | Word OpenXML with embedded VBA macro binaries (`word/vbaProject.bin`). | `dangerous/quarantine` | Strip VBA macro execution; convert text payload via `document-convert` in isolated sandbox. |
| `.dotx`, `.dotm`, `.dot` | `application/vnd.openxmlformats-officedocument.wordprocessingml.template`, `application/msword` | Microsoft Word document templates. | `document-convert` | Converted identically to DOCX/DOC. |
| `.odt`, `.ott` | `application/vnd.oasis.opendocument.text`, `application/vnd.oasis.opendocument.text-template` | OpenDocument Text (ISO/IEC 26300). ZIP container with `content.xml`. | `document-convert` | ODF parser / Pandoc $\rightarrow$ Markdown headings, formatting, and tables. |
| `.rtf` | `application/rtf`, `text/rtf` | Rich Text Format. ASCII/7-bit control words for formatting and embedded objects. | `document-convert` | RTF parser / unrtf $\rightarrow$ clean Markdown. |
| `.pages` | `application/vnd.apple.pages`, `application/x-iwork-pages-sffpages` | Apple Pages Document. macOS bundle directory or single-file ZIP (IWork archive with Protobuf/Snappy index). | `document-convert` | Bundle/ZIP extraction $\rightarrow$ preview PDF or Protobuf text extraction $\rightarrow$ Markdown. |
| `.wpd` | `application/wordperfect` | Corel WordPerfect Document. Legacy binary word processor format. | `document-convert` | `libwpd` / Tika filter $\rightarrow$ Markdown. |
| `.wps` | `application/vnd.ms-works` | Microsoft Works Document. Legacy consumer office format. | `document-convert` | `libwps` filter $\rightarrow$ Markdown. |
| `.abw`, `.zabw` | `application/x-abiword` | AbiWord Document. XML-based word processor format (ZABW is gzipped). | `document-convert` | XML parse / Pandoc $\rightarrow$ Markdown. |
| `.sxw`, `.sdw` | `application/vnd.sun.xml.writer` | StarOffice / OpenOffice.org 1.x legacy XML in ZIP. | `document-convert` | ODF legacy filter $\rightarrow$ Markdown. |
| `.lwp` | `application/vnd.lotus-wordpro` | Lotus Word Pro Document. Legacy binary word processing format. | `document-convert` | `libmwaw` filter $\rightarrow$ Markdown. |
| `.hwp`, `.hwpx` | `application/x-hwp`, `application/vnd.hancom.hwpx` | Hancom Hangul Word Processor (standard Korean document format). OLE CFBF (HWP) or OpenXML ZIP (HWPX). | `document-convert` | `pyhwp` / Hancom filter $\rightarrow$ Markdown. |
| `.xps`, `.oxps` | `application/vnd.ms-xpsdocument`, `application/oxps` | XML Paper Specification (Microsoft fixed page format). ZIP container with XAML pages. | `document-convert` | XPS parser / MuPDF $\rightarrow$ paged Markdown with `page_map`. |
| `.ps`, `.eps` | `application/postscript`, `image/x-eps` | PostScript / Encapsulated PostScript. Turing-complete page description programming language. | `document-convert` | Ghostscript in hardened sandbox $\rightarrow$ PDF/raster $\rightarrow$ Markdown; treat active PS code with care. |
| `.prn` | `application/octet-stream` | Printer output spool file (PCL, PostScript, or plain ASCII fixed-width). | `binary-opaque` | Sniff inner language (PCL vs PostScript vs text); convert or retain as opaque printer dump. |
| `.dvi` | `application/x-dvi` | TeX Device Independent file format. Binary typesetting output before PostScript/PDF. | `document-convert` | `dvitype` / `dvipdf` conversion $\rightarrow$ Markdown. |
| `.djvu`, `.djv` | `image/vnd.djvu`, `image/x-djvu` | DjVu scanned document format. High-compression multi-layer raster (text mask + background). | `document-convert` | `djvutxt` layer extraction for embedded OCR; fallback to image OCR pipeline. |
| `.txt`, `.text` | `text/plain` | Plain unformatted text (UTF-8, ASCII, UTF-16, ISO-8859-1). | `text-native` | Direct character passthrough to `document.md` with BOM/encoding detection. |
| `.rtfd` | `application/x-rtfd` | Apple Rich Text Format Directory. macOS directory bundle with `TXT.rtf` + image attachments. | `document-convert` | Normalize directory bundle $\rightarrow$ parse `TXT.rtf`, copy images to `media/`. |
| `.enex` | `application/xml` | Evernote XML Export. XML envelope containing XHTML note bodies and base64 attachments. | `structured-parse` | Extract note metadata, convert body HTML to Markdown, decode attachments to `media/`. |
| `.one`, `.onetoc2` | `application/onenote` | Microsoft OneNote notebook and section binary storage files. | `document-convert` | OneNote parser / MS-ONE binary parser $\rightarrow$ Markdown pages + embedded media. |
| `.org` | `text/x-org` | Emacs Org-mode document. Plain text with structured outline syntax, tags, and code blocks. | `text-native` | Passthrough or Org $\rightarrow$ CommonMark AST normalization. |
| `.opml` | `text/x-opml`, `application/xml` | Outline Processor Markup Language. XML format for hierarchical outlines and RSS feeds. | `structured-parse` | XML parse $\rightarrow$ nested markdown outline list. |
| `.fdx`, `.fdr` | `application/xml`, `application/octet-stream` | Final Draft Screenplay Format. XML structure with scene headings, character cues, dialogue. | `structured-parse` | Final Draft XML parser $\rightarrow$ structured screenplay Markdown. |
| `.fountain` | `text/x-fountain` | Fountain plain-text screenplay markup format. | `text-native` | Direct passthrough / Fountain AST $\rightarrow$ Markdown dialogue blocks. |
| `.tex`, `.latex`, `.ltx` | `application/x-tex`, `text/x-tex` | LaTeX source documents. LaTeX macro typesetting markup. | `text-native` | Direct passthrough with math preserved (`$...$`, `$$...$$`) or Pandoc AST normalization. |
| `.bib` | `text/x-bibtex` | BibTeX bibliography database. Plain text key-value citation format. | `structured-parse` | BibTeX parser $\rightarrow$ structured citation catalog / Markdown reference section. |
| `.cls`, `.sty`, `.dtx`, `.ins` | `text/x-tex` | LaTeX document class definitions, style packages, and documented source archives. | `text-native` | Plain text code ingestion. |
| `.typ` | `text/x-typst` | Typst modern typesetting source file. | `text-native` | Plain text markup passthrough. |

---

### 2.2 Spreadsheets, Tabular Data, Numerical & Data Interchange

This category covers spreadsheets, delimited text, columnar analytical stores, array containers, and statistical software datasets.

| Extension(s) | Common MIME Type(s) | Description & Semantic Payload | Ingest Posture | Ingest Router Target & Notes |
| :--- | :--- | :--- | :--- | :--- |
| `.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | Excel OpenXML Workbook. ZIP containing `xl/worksheets/sheetN.xml`, shared strings, and styles. | `structured-parse` | OpenPyXL / Calamine parse $\rightarrow$ multi-sheet Markdown tables + formula metadata; raw preserved. |
| `.xls` | `application/vnd.ms-excel` | Excel 97-2003 Workbook. OLE2 BIFF8 binary spreadsheet stream. | `structured-parse` | `xlrd` / Calamine parse $\rightarrow$ Markdown tables. |
| `.xlsm` | `application/vnd.ms-excel.sheet.macroEnabled.12` | Excel OpenXML Workbook with embedded VBA macros. | `dangerous/quarantine` | Quarantine/strip VBA; parse tabular sheets via `structured-parse`. |
| `.xlsb` | `application/vnd.ms-excel.sheet.binary.macroEnabled.12` | Excel Binary Workbook. ZIP container wrapping binary BIFF12 records instead of XML. | `structured-parse` | `pyxlsb` / Calamine $\rightarrow$ fast tabular extraction to Markdown. |
| `.xltx`, `.xltm`, `.xlt` | `application/vnd.openxmlformats-officedocument.spreadsheetml.template`, `application/vnd.ms-excel` | Microsoft Excel templates. | `structured-parse` | Tabular schema extraction. |
| `.ods`, `.ots` | `application/vnd.oasis.opendocument.spreadsheet`, `application/vnd.oasis.opendocument.spreadsheet-template` | OpenDocument Spreadsheet (ISO/IEC 26300). ZIP with `content.xml`. | `structured-parse` | ODF table parser $\rightarrow$ Markdown tables. |
| `.numbers` | `application/vnd.apple.numbers`, `application/x-iwork-numbers-sffnumbers` | Apple Numbers Spreadsheet. macOS bundle / ZIP with Protobuf Snappy tables. | `structured-parse` | Extract sheet grids $\rightarrow$ Markdown tables. |
| `.sxc`, `.sdc` | `application/vnd.sun.xml.calc` | StarOffice / OpenOffice.org 1.x Calc legacy XML. | `structured-parse` | Legacy ODF filter $\rightarrow$ Markdown tables. |
| `.123`, `.wk1`..`.wk4` | `application/vnd.lotus-1-2-3` | Lotus 1-2-3 legacy spreadsheet files. | `structured-parse` | `libetonyek` / Tika filter $\rightarrow$ tabular text. |
| `.qpw`, `.wb1`, `.wb3` | `application/x-quattropro` | Corel Quattro Pro spreadsheet files. | `structured-parse` | `libqxp` filter $\rightarrow$ tabular text. |
| `.csv` | `text/csv` | Comma-Separated Values (RFC 4180). Delimited tabular text with quoting. | `structured-parse` | Delimiter detection (sniff `,`, `;`, `\t`, `|`) $\rightarrow$ Markdown table (with row truncation for large files). |
| `.tsv`, `.tab` | `text/tab-separated-values` | Tab-Separated Values. Tab-delimited tabular plain text. | `structured-parse` | Structured TSV parser $\rightarrow$ Markdown table. |
| `.psv` | `text/plain` | Pipe-Separated Values (`|` delimiter). | `structured-parse` | Delimited parser $\rightarrow$ Markdown table. |
| `.dif` | `application/x-dif` | Data Interchange Format. ASCII format for spreadsheet data exchange. | `structured-parse` | DIF parser $\rightarrow$ tabular Markdown. |
| `.slk`, `.sylk` | `application/x-sylk` | Symbolic Link (SYLK) spreadsheet format. | `structured-parse` | SYLK parser $\rightarrow$ tabular Markdown. |
| `.parquet` | `application/vnd.apache.parquet` | Apache Parquet. Columnar binary format with snappy/gzip/zstd compression and metadata. | `structured-parse` | PyArrow/DuckDB $\rightarrow$ schema extraction, column summaries, sample top-50 rows to Markdown; raw retained. |
| `.feather`, `.arrow` | `application/vnd.apache.arrow.file`, `application/vnd.apache.arrow.stream` | Apache Arrow IPC / Feather binary columnar storage format. | `structured-parse` | Arrow metadata extraction + schema + top-k table slice. |
| `.orc` | `application/x-orc` | Apache ORC (Optimized Row Columnar) format. | `structured-parse` | ORC reader $\rightarrow$ schema + row summary to Markdown. |
| `.avro` | `application/vnd.apache.avro` | Apache Avro binary serialization with JSON schema header. | `structured-parse` | Extract Avro JSON schema + sample records to Markdown. |
| `.hdf5`, `.h5`, `.he5` | `application/x-hdf5` | Hierarchical Data Format 5. Multi-dimensional array and scientific dataset container. | `structured-parse` | `h5py` $\rightarrow$ inspect dataset tree hierarchy, attributes, shapes, dtypes; summarize to `document.md`. |
| `.nc`, `.netcdf`, `.cdf` | `application/x-netcdf` | NetCDF (Network Common Data Form) for array-oriented scientific data. | `structured-parse` | `netCDF4` $\rightarrow$ extract variable dimensions, units, coordinate bounds into Markdown. |
| `.zarr` | `application/x-zarr` | Zarr chunked, compressed N-dimensional array store (directory hierarchy). | `structured-parse` | Read `.zarray` / `.zgroup` metadata $\rightarrow$ dimensional summary in Markdown. |
| `.npy`, `.npz` | `application/x-numpy-data` | NumPy single array binary (`.npy`) or zipped multi-array archive (`.npz`). | `structured-parse` | Extract shape, dtype, statistical moments (min, max, mean) $\rightarrow$ Markdown summary. |
| `.mat` | `application/x-matlab-data` | MATLAB Workspace (v4/v5 binary or v7.3 HDF5 container). | `structured-parse` | `scipy.io.loadmat` / `h5py` $\rightarrow$ list variable names, types, matrix dimensions. |
| `.sav`, `.zsav` | `application/x-spss-sav` | IBM SPSS Statistics dataset file (ZSAV is compressed). | `structured-parse` | `pyreadstat` $\rightarrow$ extract variable labels, value labels, sample data table. |
| `.dta` | `application/x-stata-dta` | Stata dataset binary format. | `structured-parse` | `pyreadstat` $\rightarrow$ extract Stata variables, data dictionary, sample rows. |
| `.sas7bdat`, `.xpt` | `application/x-sas-data`, `application/x-sas-xport` | SAS dataset binary and SAS XPORT transport format. | `structured-parse` | `pyreadstat` $\rightarrow$ extract column definitions and data summary. |
| `.rds`, `.rdata`, `.rda` | `application/x-r-data` | R serialized object store (RDS = single object; RData = environment). | `structured-parse` | Read R header/metadata $\rightarrow$ object structure representation. |

---

### 2.3 Presentations & Slide Decks

This category covers slide decks, master templates, and presentation software bundles.

| Extension(s) | Common MIME Type(s) | Description & Semantic Payload | Ingest Posture | Ingest Router Target & Notes |
| :--- | :--- | :--- | :--- | :--- |
| `.pptx` | `application/vnd.openxmlformats-officedocument.presentationml.presentation` | PowerPoint OpenXML Presentation. ZIP containing `ppt/slides/slideN.xml`, notes, and media. | `document-convert` | Extract slide text, presenter notes, slide titles, shapes, and figures to `media/`; per-slide `page_map`. |
| `.ppt` | `application/vnd.ms-powerpoint` | PowerPoint 97-2003 Presentation. OLE2 Compound Document Binary Format. | `document-convert` | LibreOffice / Tika filter $\rightarrow$ per-slide Markdown + extracted slide images. |
| `.pptm` | `application/vnd.ms-powerpoint.presentation.macroEnabled.12` | PowerPoint OpenXML Presentation with embedded VBA macros. | `dangerous/quarantine` | Quarantine/strip VBA; extract slide text and media via `document-convert`. |
| `.potx`, `.potm`, `.pot` | `application/vnd.openxmlformats-officedocument.presentationml.template`, `application/vnd.ms-powerpoint` | Microsoft PowerPoint presentation templates. | `document-convert` | Master slide layout and text extraction. |
| `.ppsx`, `.ppsm`, `.pps` | `application/vnd.openxmlformats-officedocument.presentationml.slideshow`, `application/vnd.ms-powerpoint` | Microsoft PowerPoint auto-running slide shows. | `document-convert` | Converted identically to PPTX/PPT. |
| `.odp`, `.otp` | `application/vnd.oasis.opendocument.presentation`, `application/vnd.oasis.opendocument.presentation-template` | OpenDocument Presentation (ISO/IEC 26300). ZIP with `content.xml`. | `document-convert` | ODF presentation parser $\rightarrow$ slide text + presenter notes to Markdown. |
| `.key`, `.keynote` | `application/vnd.apple.keynote`, `application/x-iwork-keynote-sffkey` | Apple Keynote Presentation. macOS bundle / ZIP with Protobuf Snappy slides. | `document-convert` | Keynote extraction $\rightarrow$ slide text + notes + embedded graphics to Markdown/media. |
| `.sxi`, `.sdd` | `application/vnd.sun.xml.impress` | StarOffice / OpenOffice.org 1.x Impress legacy presentation files. | `document-convert` | Legacy ODF filter $\rightarrow$ Markdown. |
| `.show` | `application/x-hshow` | Hancom Show Presentation (Korean presentation format). | `document-convert` | Hancom filter $\rightarrow$ Markdown slides. |
| `.marp` | `text/markdown` | Marp Markdown Presentation ecosystem file (`marp: true`). | `text-native` | Direct Markdown passthrough with slide delimiter `---` tracking. |

---

### 2.4 Email, Messaging, Calendars, Contacts & Chat Exports

This category covers asynchronous and synchronous human communication formats, mailboxes, calendar events, contact cards, and chat platform exports.

| Extension(s) | Common MIME Type(s) | Description & Semantic Payload | Ingest Posture | Ingest Router Target & Notes |
| :--- | :--- | :--- | :--- | :--- |
| `.eml` | `message/rfc822` | Internet Message Format (RFC 5322). Headers (From, To, Subject, Date) + MIME body + attachments. | `document-convert` | Parse headers to frontmatter; convert HTML/text body to `document.md`; save attachments to `media/`. |
| `.msg` | `application/vnd.ms-outlook` | Microsoft Outlook Item. OLE2 Compound Document storing MAPI properties, recipients, and attachments. | `document-convert` | `msg-extractor` / `oxmsg` $\rightarrow$ parse MAPI headers, body text, HTML, and extract attachment streams. |
| `.emlx` | `message/x-emlx` | Apple Mail individual message format (byte count header + RFC 822 text + XML plist metadata). | `document-convert` | Strip byte count prefix $\rightarrow$ parse RFC 822 body + plist tags. |
| `.mbox`, `.mbx` | `application/mbox` | MBOX Email Mailbox file. Concatenated plaintext RFC 822 messages delimited by `From ` lines. | `archive-expand` | Split on `From ` boundaries $\rightarrow$ emit child `.eml` documents into E0 recursion. |
| `.pst`, `.ost` | `application/vnd.ms-outlook-pst`, `application/vnd.ms-outlook-ost` | Outlook Personal Storage Table / Offline Storage Table (NDB database format). | `archive-expand` | `libpff` / `pypff` $\rightarrow$ traverse folder hierarchy, extract individual messages and attachments. |
| `.dbx` | `application/x-ms-dbx` | Outlook Express folder database format. | `archive-expand` | `libdbx` $\rightarrow$ extract individual mail items. |
| `.ics`, `.ical` | `text/calendar` | iCalendar (RFC 5545). Plaintext scheduling events (`VEVENT`), tasks (`VTODO`), alarms. | `structured-parse` | iCalendar parser $\rightarrow$ structured Markdown agenda (Summary, Organizer, DTSTART, DTEND, RRULE). |
| `.ifb` | `text/calendar` | iCalendar Free/Busy time description format. | `structured-parse` | Extract available/busy time slots $\rightarrow$ Markdown summary. |
| `.vcs` | `text/x-vcalendar` | vCalendar 1.0 legacy calendar format. | `structured-parse` | vCalendar parser $\rightarrow$ Markdown event table. |
| `.vcf`, `.vcard` | `text/vcard` | vCard electronic business card (RFC 6350). Contact name, email, telephone, address, org. | `structured-parse` | vCard parser $\rightarrow$ structured Markdown contact profile. |
| `.pab` | `application/octet-stream` | Microsoft Personal Address Book binary storage. | `binary-opaque` | Opaque metadata extraction unless `libpab` available. |
| `.json` (Slack Export) | `application/json` | Slack Workspace export archive (channels, users, message threads). | `structured-parse` | Normalize user IDs, map timestamped message threads into conversational dialog blocks. |
| `.json` (Discord Export) | `application/json` | Discord chat export JSON (guilds, channels, reactions, embeds). | `structured-parse` | Format chronological chat dialogue with author tags and reply references. |
| `.txt` (WhatsApp Export) | `text/plain` | WhatsApp `_chat.txt` plaintext transcript with bracketed timestamps and phone/name headers. | `structured-parse` | Regex pattern matching for `[DD/MM/YY, HH:MM:SS] Name: Text` $\rightarrow$ structured turns. |
| `.html` (Telegram Export) | `text/html` | Telegram Desktop chat export (messages.html with CSS styles). | `document-convert` | Telegram HTML parser $\rightarrow$ clean Markdown dialogue turns. |

---

### 2.5 Markup, Web, Config, Code, Notebooks & Build Manifests

This category covers human-readable markup, hypermedia, structured configuration languages, source code across compiled and interpreted languages, interactive notebooks, and build definitions.

| Extension(s) | Common MIME Type(s) | Description & Semantic Payload | Ingest Posture | Ingest Router Target & Notes |
| :--- | :--- | :--- | :--- | :--- |
| `.html`, `.htm` | `text/html` | HyperText Markup Language (HTML5). Web pages with DOM elements, forms, scripts, links. | `document-convert` | Readability / Trafilatura / BeautifulSoup $\rightarrow$ clean article Markdown; strip `<script>`, `<style>`. |
| `.xhtml` | `application/xhtml+xml` | Extensible HyperText Markup Language (XML-compliant HTML). | `document-convert` | XML parse / Readability $\rightarrow$ clean Markdown. |
| `.mhtml`, `.mht` | `multipart/related`, `message/rfc822` | MIME Encapsulation of Aggregate HTML Documents (web archive with embedded images/CSS). | `document-convert` | RFC 822 unpacker $\rightarrow$ convert main HTML to Markdown, unpack assets to `media/`. |
| `.warc`, `.arc` | `application/warc` | Web ARChive standard format (ISO 28500). Raw HTTP request/response headers and payloads. | `archive-expand` | `warcio` $\rightarrow$ iterate HTTP responses, extract HTML bodies to child documents. |
| `.md`, `.markdown`, `.mdown`, `.mkdn` | `text/markdown` | CommonMark / GitHub Flavored Markdown (GFM). | `text-native` | Direct passthrough; verified as canonical `document.md` coordinate system. |
| `.mdx` | `text/mdx` | Component-based Markdown with embedded JSX components. | `text-native` | Markdown passthrough; optional JSX component tag normalization. |
| `.rst` | `text/x-rst` | reStructuredText (Docutils / Sphinx documentation format). | `text-native` | Docutils / Pandoc $\rightarrow$ CommonMark AST normalization. |
| `.asciidoc`, `.adoc`, `.asc` | `text/asciidoc` | AsciiDoc technical documentation format. | `text-native` | Asciidoctor / Pandoc $\rightarrow$ CommonMark AST normalization. |
| `.textile` | `text/x-textile` | Textile lightweight markup language. | `text-native` | Textile parser $\rightarrow$ CommonMark Markdown. |
| `.wiki`, `.creole`, `.mediawiki` | `text/x-wiki` | Wiki markup formats (MediaWiki wikitext, Creole standard). | `text-native` | `mwparserfromhell` / Pandoc $\rightarrow$ CommonMark. |
| `.json`, `.json5` | `application/json`, `application/json5` | JavaScript Object Notation / JSON5 (with comments and trailing commas). | `structured-parse` | Syntax validation + pretty-printed JSON code block or schema summary in Markdown. |
| `.jsonl`, `.ndjson` | `application/x-ndjson` | JSON Lines / Newline-Delimited JSON (one JSON object per line). | `structured-parse` | Record stream inspection $\rightarrow$ schema discovery + top-k records formatted in Markdown. |
| `.yaml`, `.yml` | `application/yaml`, `text/yaml` | YAML Ain't Markup Language (human-friendly data serialization). | `structured-parse` | PyYAML/ruamel $\rightarrow$ structured code block or key-value summary. |
| `.toml` | `application/toml`, `text/toml` | Tom's Obvious Minimal Language (standard for Python/Rust package config). | `structured-parse` | TOML parser $\rightarrow$ structured code block in Markdown. |
| `.xml` | `application/xml`, `text/xml` | Extensible Markup Language. Hierarchical tagged data. | `structured-parse` | DefusedXML parse (XXE protected) $\rightarrow$ formatted XML code block or tree summary. |
| `.xsd`, `.xsl`, `.xslt` | `application/xml` | XML Schema Definition and XML Stylesheet Transformations. | `structured-parse` | XML parse $\rightarrow$ schema definition in Markdown. |
| `.kml` | `application/vnd.google-earth.kml+xml` | Keyhole Markup Language (XML for geographic annotations and placemarks). | `structured-parse` | KML parser $\rightarrow$ list placemarks, coordinates, descriptions in Markdown. |
| `.gpx` | `application/gpx+xml` | GPS Exchange Format (XML format for waypoints, tracks, and routes). | `structured-parse` | GPX parser $\rightarrow$ waypoint list, track statistics, elevation profile in Markdown. |
| `.plist` | `application/x-plist` | Apple Property List (XML format or binary format `bplist00`). | `structured-parse` | `plistlib` $\rightarrow$ normalize binary/XML plist to structured YAML/JSON in Markdown. |
| `.ini`, `.cfg`, `.conf`, `.properties`, `.env` | `text/plain` | Standard INI-style configuration files, key-value pairs, and Java properties. | `text-native` | Plain text passthrough with language syntax hints. |
| `.hcl`, `.tf`, `.tfvars` | `text/x-hcl` | HashiCorp Configuration Language (Terraform infrastructure-as-code). | `text-native` | HCL / Terraform code passthrough with block tracking. |
| `.cue`, `.dhall`, `.ron` | `text/plain` | Modern typed configuration languages (CUE, Dhall, Rusty Object Notation). | `text-native` | Plain text code passthrough. |
| `.cbor` | `application/cbor` | Concise Binary Object Representation (RFC 8949 binary JSON equivalent). | `structured-parse` | Decode CBOR $\rightarrow$ formatted JSON in Markdown; raw preserved. |
| `.msgpack` | `application/msgpack` | MessagePack binary serialization format. | `structured-parse` | Decode MessagePack $\rightarrow$ formatted JSON in Markdown. |
| `.bson` | `application/bson` | Binary JSON (MongoDB document storage format). | `structured-parse` | `bson` decode $\rightarrow$ formatted JSON in Markdown. |
| `.proto` | `text/x-protobuf` | Protocol Buffers interface definition schema file. | `text-native` | Protobuf syntax code passthrough. |
| `.pb`, `.binpb` | `application/octet-stream` | Compiled Protobuf binary message payload (without embedded schema). | `binary-opaque` | Opaque payload; requires external `.proto` descriptor to decode. |
| `.ipynb` | `application/x-ipynb+json` | Jupyter Notebook. JSON structure containing code cells, markdown cells, and outputs. | `structured-parse` | Separate Markdown explanations, Python code cells, stdout, and extract output images to `media/`. |
| `.qmd` | `text/x-quarto` | Quarto scientific document (multi-language markdown notebook). | `text-native` | Markdown passthrough with code chunk headers. |
| `.rmd` | `text/x-r-markdown` | R Markdown document (knitr / Sweave). | `text-native` | Markdown passthrough with R code blocks. |
| `.c`, `.h` | `text/x-c` | C source and header files. | `text-native` | Tree-sitter C grammar chunking $\rightarrow$ syntax-highlighted code blocks in `document.md`. |
| `.cpp`, `.hpp`, `.cc`, `.hh`, `.cxx`, `.hxx` | `text/x-c++` | C++ source and header files. | `text-native` | Tree-sitter C++ grammar chunking $\rightarrow$ code in `document.md`. |
| `.rs` | `text/x-rust` | Rust source code. | `text-native` | Tree-sitter Rust grammar chunking $\rightarrow$ functions, traits, modules in `document.md`. |
| `.go` | `text/x-go` | Go source code. | `text-native` | Tree-sitter Go grammar chunking $\rightarrow$ structs, methods, interfaces. |
| `.zig` | `text/x-zig` | Zig systems programming source code. | `text-native` | Plain text code passthrough. |
| `.d` | `text/x-dsrc` | D programming language source code. | `text-native` | Plain text code passthrough (disambiguate from DTrace/Make). |
| `.nim` | `text/x-nim` | Nim programming language source code. | `text-native` | Plain text code passthrough. |
| `.pas`, `.pp`, `.dpr` | `text/x-pascal` | Pascal / Object Pascal / Delphi source files. | `text-native` | Plain text code passthrough. |
| `.f`, `.for`, `.f90`, `.f95` | `text/x-fortran` | Fortran (Fixed form and Free form) scientific source code. | `text-native` | Plain text code passthrough. |
| `.cob`, `.cbl` | `text/x-cobol` | COBOL enterprise legacy source code. | `text-native` | Plain text code passthrough. |
| `.asm`, `.s` | `text/x-asm` | Assembly source files (x86, ARM, RISC-V, MIPS). | `text-native` | Plain text code passthrough. |
| `.java` | `text/x-java-source` | Java source code. | `text-native` | Tree-sitter Java grammar chunking $\rightarrow$ classes, methods, annotations. |
| `.kt`, `.kts` | `text/x-kotlin` | Kotlin source code and Kotlin scripts. | `text-native` | Tree-sitter Kotlin grammar chunking $\rightarrow$ code in `document.md`. |
| `.scala`, `.sc` | `text/x-scala` | Scala source code and worksheets. | `text-native` | Tree-sitter Scala grammar chunking $\rightarrow$ code in `document.md`. |
| `.groovy` | `text/x-groovy` | Groovy source code (Jenkins pipelines, Gradle builds). | `text-native` | Plain text code passthrough. |
| `.clj`, `.cljs`, `.cljc`, `.edn` | `text/x-clojure` | Clojure, ClojureScript, and EDN data notation files. | `text-native` | Plain text code passthrough. |
| `.cs` | `text/x-csharp` | C# (.NET) source code. | `text-native` | Tree-sitter C# grammar chunking $\rightarrow$ classes, methods, LINQ. |
| `.fs`, `.fsx` | `text/x-fsharp` | F# functional programming source and script files. | `text-native` | Plain text code passthrough. |
| `.vb` | `text/x-vb` | Visual Basic / VB.NET source code. | `text-native` | Plain text code passthrough. |
| `.js`, `.mjs`, `.cjs` | `text/javascript` | JavaScript source files (ECMAScript, ES Modules, CommonJS). | `text-native` | Tree-sitter JS grammar chunking $\rightarrow$ functions, classes, exports. |
| `.jsx` | `text/jsx` | React JSX component files. | `text-native` | Tree-sitter JSX grammar chunking $\rightarrow$ code in `document.md`. |
| `.ts`, `.mts`, `.cts` | `text/typescript` | TypeScript source files (disambiguate from MPEG-TS video!). | `text-native` | Tree-sitter TS grammar chunking $\rightarrow$ interfaces, types, functions. |
| `.tsx` | `text/tsx` | TypeScript with React JSX. | `text-native` | Tree-sitter TSX grammar chunking $\rightarrow$ code in `document.md`. |
| `.py`, `.pyi`, `.pyw` | `text/x-python` | Python source files, type stub interfaces, and windowed scripts. | `text-native` | Tree-sitter Python grammar chunking $\rightarrow$ defs, classes, docstrings. |
| `.rb`, `.rake`, `.gemspec` | `text/x-ruby` | Ruby source files, Rakefiles, and Gem specifications. | `text-native` | Tree-sitter Ruby grammar chunking $\rightarrow$ classes, modules, methods. |
| `.php`, `.phtml` | `text/x-php` | PHP hypertext preprocessor source files. | `text-native` | Tree-sitter PHP grammar chunking $\rightarrow$ functions, classes. |
| `.lua` | `text/x-lua` | Lua scripting language source files. | `text-native` | Plain text code passthrough. |
| `.pl`, `.pm`, `.t` | `text/x-perl` | Perl source scripts, modules, and test files. | `text-native` | Plain text code passthrough (disambiguate from Prolog!). |
| `.raku`, `.rakumod` | `text/x-raku` | Raku (formerly Perl 6) source files. | `text-native` | Plain text code passthrough. |
| `.sh`, `.bash`, `.zsh`, `.fish`, `.ksh`, `.csh` | `text/x-shellscript` | POSIX, Bash, Zsh, and Fish shell scripts. | `text-native` | Plain text code passthrough with syntax fence. |
| `.bat`, `.cmd` | `text/x-bat` | Windows Command / Batch scripts. | `text-native` | Plain text code passthrough. |
| `.ps1`, `.psm1`, `.psd1` | `text/x-powershell` | Microsoft PowerShell scripts, modules, and data files. | `text-native` | Plain text code passthrough. |
| `.hs`, `.lhs` | `text/x-haskell` | Haskell source and Literate Haskell files. | `text-native` | Plain text code passthrough. |
| `.erl`, `.hrl` | `text/x-erlang` | Erlang source code and header definitions. | `text-native` | Plain text code passthrough. |
| `.ex`, `.exs` | `text/x-elixir` | Elixir source code and script files. | `text-native` | Tree-sitter Elixir grammar chunking $\rightarrow$ code in `document.md`. |
| `.ml`, `.mli` | `text/x-ocaml` | OCaml source code and interface files. | `text-native` | Plain text code passthrough. |
| `.elm` | `text/x-elm` | Elm functional frontend architecture files. | `text-native` | Plain text code passthrough. |
| `.purs` | `text/x-purescript` | PureScript source files. | `text-native` | Plain text code passthrough. |
| `.lisp`, `.lsp`, `.cl`, `.el`, `.scm`, `.ss`, `.rkt` | `text/x-lisp`, `text/x-scheme` | Lisp dialects (Common Lisp, Emacs Lisp, Scheme, Racket). | `text-native` | Plain text code passthrough. |
| `.prolog` | `text/x-prolog` | Prolog logic programming source files. | `text-native` | Plain text code passthrough. |
| `.coq`, `.v` | `text/x-coq` | Coq interactive theorem prover proof files. | `text-native` | Plain text code passthrough (disambiguate `.v` from Verilog). |
| `.sv`, `.svh`, `.vhd`, `.vhdl` | `text/x-systemverilog`, `text/x-vhdl` | SystemVerilog and VHDL hardware description source files. | `text-native` | Plain text code passthrough. |
| `.sol`, `.vy` | `text/x-solidity` | Solidity and Vyper smart contract source code. | `text-native` | Plain text code passthrough. |
| `.glsl`, `.vert`, `.frag`, `.geom`, `.comp`, `.hlsl`, `.metal`, `.wgsl` | `text/x-glsl` | GPU Shader source files (OpenGL, Direct3D, Apple Metal, WebGPU). | `text-native` | Plain text code passthrough. |
| `Makefile`, `GNUmakefile`, `.mk` | `text/x-makefile` | Make build automation files. | `text-native` | Plain text code passthrough. |
| `Dockerfile`, `Containerfile` | `text/x-dockerfile` | Container build instruction files. | `text-native` | Plain text code passthrough. |
| `Vagrantfile`, `Justfile`, `Procfile` | `text/plain` | Modern developer task and environment automation definitions. | `text-native` | Plain text code passthrough. |
| `CMakeLists.txt`, `.cmake` | `text/x-cmake` | CMake build system configuration files. | `text-native` | Plain text code passthrough. |
| `meson.build` | `text/x-meson` | Meson build system definitions. | `text-native` | Plain text code passthrough. |
| `BUILD`, `WORKSPACE`, `BUILD.bazel` | `text/x-starlark` | Bazel / Starlark build definition files. | `text-native` | Plain text code passthrough. |
| `flake.nix`, `.nix` | `text/x-nix` | Nix package manager expressions and flakes. | `text-native` | Plain text code passthrough. |
| `Jenkinsfile` | `text/x-groovy` | Jenkins CI/CD pipeline definition. | `text-native` | Plain text code passthrough. |

---

### 2.6 Images (Raster, Vector, Camera RAW, Medical, GIS)

This category covers 2D bitmap images, compressed photographic formats, raw camera sensor captures, scalable vector drawings, medical scans, and geospatial imagery.

| Extension(s) | Common MIME Type(s) | Description & Semantic Payload | Ingest Posture | Ingest Router Target & Notes |
| :--- | :--- | :--- | :--- | :--- |
| `.png` | `image/png` | Portable Network Graphics (ISO/IEC 15948). Lossless raster bitmap with alpha channel. | `media-transcribe` | Document OCR (if text detected) OR VLM visual description $\rightarrow$ `document.md`; raw in storage. |
| `.jpg`, `.jpeg`, `.jpe`, `.jfif` | `image/jpeg` | JPEG Lossy Photographic Image (ISO/IEC 10918-1). EXIF metadata + DCT image data. | `media-transcribe` | Extract EXIF camera metadata + VLM description / OCR $\rightarrow$ `document.md`. |
| `.webp` | `image/webp` | WebP Image format (lossy VP8 / lossless VP8L container with RIFF header). | `media-transcribe` | VLM visual description + OCR $\rightarrow$ `document.md`. |
| `.gif` | `image/gif` | Graphics Interchange Format (GIF87a / GIF89a). Palette raster with optional multi-frame animation. | `media-transcribe` | Multi-frame keyframe extraction to `media/` + VLM description. |
| `.bmp`, `.dib` | `image/bmp` | Windows Bitmap Format / Device Independent Bitmap. Uncompressed raster. | `media-transcribe` | OCR / VLM description $\rightarrow$ `document.md`. |
| `.ico`, `.cur` | `image/x-icon`, `image/vnd.microsoft.icon` | Windows Icon and Cursor file (contains multiple resolutions). | `media-transcribe` | Metadata summary + thumbnail generation. |
| `.avif` | `image/avif` | AV1 Image File Format (HEIF container with AV1 compression). | `media-transcribe` | VLM description + OCR $\rightarrow$ `document.md`. |
| `.heic`, `.heif`, `.hif` | `image/heic`, `image/heif` | High Efficiency Image Container (ISOBMFF with HEVC compression). | `media-transcribe` | `libheif` decode $\rightarrow$ VLM description + EXIF extraction. |
| `.tiff`, `.tif` | `image/tiff` | Tagged Image File Format. Uncompressed/compressed multi-page raster with extensive tags. | `document-convert` / `media-transcribe` | If multi-page scanned document $\rightarrow$ paged OCR with `page_map`; if photo $\rightarrow$ VLM description. |
| `.psd`, `.psb` | `image/vnd.adobe.photoshop` | Adobe Photoshop Document (PSB is Large Document Format >2GB). Multi-layer image data. | `media-transcribe` | `psd-tools` $\rightarrow$ extract flattened composite image to `media/` + layer list summary. |
| `.exr` | `image/x-exr` | OpenEXR (ILM high dynamic range 16/32-bit floating point image format). | `media-transcribe` | Tone-map to sRGB preview in `media/` + EXR channel header extraction. |
| `.hdr` | `image/vnd.radiance` | Radiance RGBE High Dynamic Range format. | `media-transcribe` | Tone-map preview + metadata extraction. |
| `.jxl` | `image/jxl` | JPEG XL next-generation image format. | `media-transcribe` | `libjxl` decode $\rightarrow$ VLM description. |
| `.jp2`, `.j2k`, `.jpf`, `.jpx` | `image/jp2` | JPEG 2000 Wavelet-compressed Image. | `media-transcribe` | Wavelet decode $\rightarrow$ VLM description + OCR. |
| `.tga` | `image/x-targa` | Truevision TGA (Targa) raster graphics file. | `media-transcribe` | Raster decode $\rightarrow$ VLM description. |
| `.ppm`, `.pgm`, `.pbm`, `.pnm` | `image/x-portable-anymap` | Netpbm Portable Anymap format family (ASCII and binary pixel maps). | `media-transcribe` | Netpbm parser $\rightarrow$ VLM description. |
| `.dds`, `.astc`, `.ktx`, `.ktx2` | `image/vnd-ms.dds`, `image/ktx2` | GPU Texture Compression containers (DirectDraw Surface, ASTC, Khronos Texture). | `binary-opaque` | Extract texture header (format, mipmap count, resolution); raw retained. |
| `.dng` | `image/x-adobe-dng` | Adobe Digital Negative (standardized TIFF-based camera RAW format). | `media-transcribe` | `rawpy` / `libraw` $\rightarrow$ extract embedded JPEG preview + EXIF metadata + VLM description. |
| `.cr2`, `.cr3` | `image/x-canon-cr2`, `image/x-canon-cr3` | Canon Raw 2 (TIFF-based) and Canon Raw 3 (ISOBMFF-based) camera formats. | `media-transcribe` | `libraw` $\rightarrow$ extract embedded preview, camera settings, lens info, VLM description. |
| `.nef`, `.nrw` | `image/x-nikon-nef` | Nikon Electronic Format camera RAW. | `media-transcribe` | `libraw` $\rightarrow$ extract preview and metadata. |
| `.arw`, `.srf`, `.sr2` | `image/x-sony-arw` | Sony Alpha camera RAW formats. | `media-transcribe` | `libraw` $\rightarrow$ extract preview and metadata. |
| `.orf` | `image/x-olympus-orf` | Olympus Raw Format. | `media-transcribe` | `libraw` $\rightarrow$ extract preview and metadata. |
| `.rw2` | `image/x-panasonic-rw2` | Panasonic Lumix camera RAW format. | `media-transcribe` | `libraw` $\rightarrow$ extract preview and metadata. |
| `.pef`, `.ptx` | `image/x-pentax-pef` | Pentax Electronic File camera RAW format. | `media-transcribe` | `libraw` $\rightarrow$ extract preview and metadata. |
| `.raf` | `image/x-fuji-raf` | Fujifilm camera RAW format. | `media-transcribe` | `libraw` $\rightarrow$ extract preview and metadata. |
| `.rwl` | `image/x-leica-rwl` | Leica camera RAW format. | `media-transcribe` | `libraw` $\rightarrow$ extract preview and metadata. |
| `.x3f` | `image/x-sigma-x3f` | Sigma Foveon direct image sensor RAW format. | `media-transcribe` | `libraw` $\rightarrow$ extract preview and metadata. |
| `.3fr`, `.fff` | `image/x-hasselblad` | Hasselblad digital camera RAW formats. | `media-transcribe` | `libraw` $\rightarrow$ extract preview and metadata. |
| `.svg`, `.svgz` | `image/svg+xml` | Scalable Vector Graphics (W3C XML vector standard; SVGZ is gzipped). | `structured-parse` / `media-transcribe` | XML parse (strip `<script>` tags!) $\rightarrow$ extract text/labels + render raster preview for VLM. |
| `.ai` | `application/illustrator`, `application/pdf` | Adobe Illustrator Artwork (modern versions are PDF with Private Illustrator Data). | `document-convert` / `media-transcribe` | Treat as PDF $\rightarrow$ extract vector graphics and rasterize for VLM. |
| `.cdr` | `application/coreldraw` | CorelDRAW Vector Drawing binary format. | `binary-opaque` / `document-convert` | `libcdr` $\rightarrow$ extract vector paths and text. |
| `.wmf`, `.emf` | `image/wmf`, `image/emf` | Windows Metafile and Enhanced Metafile (vector/raster hybrid). | `media-transcribe` | `libwmf` $\rightarrow$ render preview + extract text strings. |
| `.vsdx`, `.vsd`, `.vssx`, `.vstx` | `application/vnd.visio` | Microsoft Visio Diagram (VSDX is OpenXML ZIP; VSD is OLE2 CFBF). | `document-convert` / `structured-parse` | Extract diagram shape text, connections, and export page previews to `media/`. |
| `.drawio` | `application/xml` | Diagrams.net / Draw.io diagram format (XML with compressed diagram data). | `structured-parse` | Decompress XML model $\rightarrow$ extract shape text, labels, and topology to Markdown. |
| `.excalidraw` | `application/json` | Excalidraw whiteboard drawing format (JSON array of shape objects). | `structured-parse` | Parse elements $\rightarrow$ extract text notes, arrow bindings, group hierarchy into Markdown. |
| `.fig` | `application/octet-stream` | Figma binary design archive. | `binary-opaque` | Figma REST API / parser $\rightarrow$ extract canvas node hierarchy. |
| `.sketch` | `application/zip` | Sketch UI design file (ZIP archive containing JSON canvas models and images). | `structured-parse` | Extract JSON layer models $\rightarrow$ UI component hierarchy and text layers to Markdown. |
| `.dcm`, `.dicom` | `application/dicom` | DICOM (Digital Imaging and Communications in Medicine - ISO 12052). | `media-transcribe` / `structured-parse` | `pydicom` $\rightarrow$ extract Patient/Study/Series metadata + render windowed CT/MRI slice to `media/`. |
| `.nii`, `.nii.gz` | `application/x-nifti` | NIfTI (Neuroimaging Informatics Technology Initiative) 3D/4D volumetric MRI/fMRI scans. | `structured-parse` / `binary-opaque` | `nibabel` $\rightarrow$ extract 3D dimensions, voxel spacing, affine matrix + slice montage to `media/`. |
| `.mha`, `.mhd` | `application/x-metaimage` | MetaImage medical 3D volume header and raw pixel data. | `structured-parse` | Extract header attributes + dimensional bounds. |
| `.nrrd`, `.nhdr` | `application/x-nrrd` | Nearly Raw Raster Data format for N-dimensional medical rasters. | `structured-parse` | Extract NRRD field tags and dimensions. |
| `.svs`, `.ndpi`, `.mrxs` | `image/x-svs` | Whole Slide Digital Pathology multi-resolution pyramidal TIFF scans. | `media-transcribe` / `binary-opaque` | `OpenSlide` $\rightarrow$ extract magnification metadata, scan properties, and low-res thumbnail. |
| `.geotiff`, `.tif` (+ `.tfw`) | `image/tiff` | GeoTIFF (TIFF with embedded cartographic projection and coordinate metadata). | `structured-parse` / `media-transcribe` | `rasterio` / GDAL $\rightarrow$ extract CRS, bounding box, band statistics + preview thumbnail. |
| `.shp`, `.shx`, `.dbf`, `.prj` | `application/x-qgis` | ESRI Shapefile geospatial vector bundle (geometry, index, attribute table, projection). | `structured-parse` | `geopandas` / Fiona $\rightarrow$ parse attribute schema, bounding envelope, feature count to Markdown. |
| `.geojson` | `application/geo+json` | GeoJSON (RFC 7946 geographic features encoded in JSON). | `structured-parse` | Extract FeatureCollection properties, geometry types, and geographic bounds. |
| `.gml` | `application/gml+xml` | Geography Markup Language (OGC XML standard). | `structured-parse` | Parse XML feature definitions $\rightarrow$ Markdown summary. |
| `.kmz` | `application/vnd.google-earth.kmz` | Keyhole Markup Language Zipped (ZIP containing `doc.kml` and embedded images). | `archive-expand` | Unpack KMZ $\rightarrow$ parse KML placemarks + extract raster overlays to `media/`. |
| `.gdb` | `application/x-esri-filegdb` | ESRI File Geodatabase (directory format containing binary spatial tables). | `structured-parse` | GDAL/OGR FileGDB driver $\rightarrow$ list spatial layers, attribute schemas, geometry counts. |
| `.dem`, `.asc` | `application/octet-stream` | Digital Elevation Model / ESRI ASCII Grid raster elevation matrix. | `structured-parse` | Extract grid resolution, coordinate origin, min/max elevation values. |
| `.mbtiles` | `application/vnd.mapbox-vector-tile` | Mapbox MBTiles (SQLite database container storing vector or raster map tiles). | `structured-parse` | SQLite inspect $\rightarrow$ extract tilejson metadata, zoom ranges, layer schemas. |

---

### 2.7 Audio (Containers, Codecs, Voice Recordings, Production)

This category covers compressed consumer audio, lossless formats, broadcast audio with metadata, voice notes, telephony codecs, multichannel spatial sound, and MIDI/tracker files.

| Extension(s) | Common MIME Type(s) | Description & Semantic Payload | Ingest Posture | Ingest Router Target & Notes |
| :--- | :--- | :--- | :--- | :--- |
| `.mp3` | `audio/mpeg` | MPEG-1/2 Audio Layer III. Compressed audio stream with ID3v1/ID3v2 metadata tags. | `media-transcribe` | Extract ID3 tags $\rightarrow$ Whisper-class ASR + diarization $\rightarrow$ `document.md` with `time_map`. |
| `.m4a`, `.aac` | `audio/mp4`, `audio/aac` | Advanced Audio Coding in ISOBMFF container or raw ADTS stream. | `media-transcribe` | ASR transcription + diarization $\rightarrow$ timed transcript in `document.md`; raw preserved. |
| `.ogg`, `.oga` | `audio/ogg` | Ogg container wrapping Vorbis audio codec and Vorbis comments. | `media-transcribe` | Extract Vorbis tags $\rightarrow$ ASR transcription $\rightarrow$ `document.md` with `time_map`. |
| `.opus` | `audio/opus` | Opus Interactive Audio Codec in Ogg container (RFC 7845). Optimized for voice and speech. | `media-transcribe` | ASR transcription + diarization $\rightarrow$ `document.md` with `time_map`. |
| `.wma` | `audio/x-ms-wma` | Windows Media Audio in ASF container. | `media-transcribe` | FFmpeg decode $\rightarrow$ ASR transcription $\rightarrow$ `document.md`. |
| `.flac` | `audio/flac` | Free Lossless Audio Codec. Lossless compressed PCM audio with metadata block. | `media-transcribe` | Extract FLAC metadata $\rightarrow$ ASR transcription $\rightarrow$ `document.md`. |
| `.alac` | `audio/mp4` | Apple Lossless Audio Codec inside M4A container. | `media-transcribe` | ASR transcription $\rightarrow$ `document.md`. |
| `.wv` | `audio/x-wavpack` | WavPack lossless/hybrid audio compression. | `media-transcribe` | ASR transcription $\rightarrow$ `document.md`. |
| `.ape` | `audio/x-ape` | Monkey's Audio lossless compression format. | `media-transcribe` | ASR transcription $\rightarrow$ `document.md`. |
| `.mpc` | `audio/x-musepack` | Musepack audio compression format. | `media-transcribe` | ASR transcription $\rightarrow$ `document.md`. |
| `.weba` | `audio/webm` | WebM Audio (Matroska container carrying Opus or Vorbis audio). | `media-transcribe` | ASR transcription + diarization $\rightarrow$ `document.md`. |
| `.wav`, `.wave` | `audio/wav`, `audio/x-wav` | Waveform Audio File Format (RIFF container with uncompressed LPCM audio). | `media-transcribe` | Extract RIFF chunks $\rightarrow$ ASR transcription + diarization $\rightarrow$ `document.md`. |
| `.aiff`, `.aif`, `.aifc` | `audio/aiff`, `audio/x-aiff` | Audio Interchange File Format (IFF container developed by Apple; AIFC is compressed). | `media-transcribe` | Extract IFF tags $\rightarrow$ ASR transcription $\rightarrow$ `document.md`. |
| `.au`, `.snd` | `audio/basic` | Sun/NeXT audio format ($\mu569Xlaw / A-law / linear PCM). | `media-transcribe` | ASR transcription $\rightarrow$ `document.md`. |
| `.caf` | `audio/x-caf` | Apple Core Audio Format. Container supporting 64-bit file sizes and arbitrary codecs. | `media-transcribe` | ASR transcription $\rightarrow$ `document.md`. |
| `.bwf` | `audio/wav` | Broadcast Wave Format (EBU standard WAV with `bext` and `iXML` production metadata). | `media-transcribe` | Extract `bext` timecode and take metadata $\rightarrow$ ASR transcription $\rightarrow$ `document.md`. |
| `.rf64` | `audio/wav` | European Broadcasting Union RF64 format (RIFF 64-bit extension for files >4GB). | `media-transcribe` | ASR transcription $\rightarrow$ `document.md`. |
| `.amr` | `audio/amr` | Adaptive Multi-Rate speech codec (AMR-NB / AMR-WB for mobile voice recordings). | `media-transcribe` | ASR transcription + diarization $\rightarrow$ `document.md`. |
| `.3ga` | `audio/3gpp` | 3GPP audio container (Samsung voice recorder format). | `media-transcribe` | ASR transcription $\rightarrow$ `document.md`. |
| `.qcp` | `audio/qcelp` | Qualcomm PureVoice QCELP / EVRC mobile audio format. | `media-transcribe` | FFmpeg decode $\rightarrow$ ASR transcription $\rightarrow$ `document.md`. |
| `.gsm` | `audio/x-gsm` | GSM 06.10 Full Rate telephony audio format. | `media-transcribe` | ASR transcription $\rightarrow$ `document.md`. |
| `.vox` | `audio/voxware` | Dialogic ADPCM telephony voice file (headerless 8kHz 4-bit audio). | `media-transcribe` | Decode raw ADPCM $\rightarrow$ ASR transcription $\rightarrow$ `document.md`. |
| `.ac3`, `.eac3` | `audio/ac3`, `audio/eac3` | Dolby Digital / Dolby Digital Plus multi-channel audio streams. | `media-transcribe` | Downmix to stereo $\rightarrow$ ASR transcription $\rightarrow$ `document.md`. |
| `.dts`, `.dtshd` | `audio/vnd.dts` | DTS Digital Surround audio streams. | `media-transcribe` | FFmpeg decode $\rightarrow$ ASR transcription $\rightarrow$ `document.md`. |
| `.amb` | `audio/x-ambisonic` | B-Format Ambisonic spatial audio file (WAV container with 4-channel WXYZ spatial audio). | `media-transcribe` | Downmix omnidirectional W-channel $\rightarrow$ ASR transcription $\rightarrow$ `document.md`. |
| `.mid`, `.midi`, `.kar` | `audio/midi` | Standard MIDI File (SMF Type 0/1/2) and Karaoke MIDI. Note events, tempo, tracks. | `structured-parse` / `media-transcribe` | `mido` $\rightarrow$ extract track names, instruments, tempo map, lyrics track to Markdown. |
| `.mod`, `.xm`, `.it`, `.s3m` | `audio/x-mod` | Tracker Music Modules (ProTracker, FastTracker II, Impulse Tracker, Scream Tracker 3). | `structured-parse` | Extract song title, sample names, instrument credits, pattern count to Markdown. |
| `.sf2`, `.sf3` | `audio/x-soundfont` | SoundFont 2.0 / 3.0 instrument sound bank. | `binary-opaque` | Extract preset names and instrument bank metadata. |

---

### 2.8 Video (Containers, Broadcast Dumps) & Captions / Subtitles

This category covers modern multimedia containers, legacy video streams, professional broadcast wrappers, screen recordings, and timed subtitle/caption files.

| Extension(s) | Common MIME Type(s) | Description & Semantic Payload | Ingest Posture | Ingest Router Target & Notes |
| :--- | :--- | :--- | :--- | :--- |
| `.mp4`, `.m4v` | `video/mp4` | MPEG-4 Part 14 (ISOBMFF container carrying H.264/H.265/AV1 video + AAC audio). | `media-transcribe` | Demux audio $\rightarrow$ ASR transcript spine + extract scene keyframes to `media/` + VLM notes. |
| `.mkv` | `video/x-matroska` | Matroska Multimedia Container. Highly extensible wrapper for arbitrary video/audio/subtitles. | `media-transcribe` | Extract embedded subtitle tracks; ASR audio spine; extract keyframes to `media/`. |
| `.webm` | `video/webm` | WebM open media format (Matroska subset with VP8/VP9/AV1 video + Opus audio). | `media-transcribe` | ASR transcription + keyframe visual description $\rightarrow$ `document.md` with `time_map`. |
| `.mov`, `.qt` | `video/quicktime` | Apple QuickTime Movie container (supports ProRes, H.264, uncompressed tracks). | `media-transcribe` | ASR transcription + keyframe extraction to `media/` $\rightarrow$ `document.md`. |
| `.avi` | `video/x-msvideo` | Audio Video Interleave (Microsoft RIFF multi-stream video container). | `media-transcribe` | FFmpeg demux $\rightarrow$ ASR audio spine + keyframe snapshots to `media/`. |
| `.wmv`, `.asf` | `video/x-ms-wmv`, `video/x-ms-asf` | Windows Media Video in Advanced Systems Format container. | `media-transcribe` | ASR transcription + keyframe extraction to `media/`. |
| `.ts`, `.m2ts`, `.mts` | `video/mp2t` | MPEG-2 Transport Stream (broadcast TV, DVB, Blu-ray AVCHD video stream). | `media-transcribe` | Disambiguate from TypeScript! Demux $\rightarrow$ ASR spine + keyframes. |
| `.vob`, `.ifo`, `.bup` | `video/dvd` | DVD-Video Object stream (MPEG-2 Program Stream) and navigation tables. | `media-transcribe` | VOB stream extraction $\rightarrow$ ASR transcription + keyframes. |
| `.flv`, `.f4v` | `video/x-flv` | Flash Video container (Sorenson Spark, VP6, or AVC video). | `media-transcribe` | FFmpeg decode $\rightarrow$ ASR transcription + keyframes. |
| `.ogv` | `video/ogg` | Ogg Video container carrying Theora video and Vorbis audio. | `media-transcribe` | ASR transcription + keyframe extraction. |
| `.3gp`, `.3g2` | `video/3gpp`, `video/3gpp2` | 3GPP / 3GPP2 mobile multimedia containers (H.263/MPEG-4 video + AMR audio). | `media-transcribe` | ASR transcription + keyframes. |
| `.rm`, `.rmvb` | `application/vnd.rn-realmedia` | RealMedia / RealMedia Variable Bitrate container. | `media-transcribe` | FFmpeg decode $\rightarrow$ ASR transcription + keyframes. |
| `.mxf` | `application/mxf` | Material Exchange Format (SMPTE standard for professional broadcast production). | `media-transcribe` | Extract SMPTE timecode + audio tracks $\rightarrow$ ASR transcript + scene keyframes. |
| `.braw`, `.r3d` | `application/octet-stream` | Blackmagic RAW and REDCODE RAW professional cinema camera video files. | `media-transcribe` / `binary-opaque` | Extract embedded audio and proxy preview track for ASR/VLM; retain raw master. |
| `.srt` | `application/x-subrip`, `text/plain` | SubRip Subtitle Format. Numeric sequential counter + start/end timestamps + text lines. | `structured-parse` | Parse timestamp spans $\rightarrow$ `document.md` dialogue turns with exact time-map ranges. |
| `.vtt` | `text/vtt` | Web Video Text Tracks (W3C WebVTT standard for HTML5 `<track>`). | `structured-parse` | Parse cue timings, voice identifiers (`<v Speaker>`) $\rightarrow$ structured Markdown turns. |
| `.ass`, `.ssa` | `text/x-ssa` | Advanced SubStation Alpha / SubStation Alpha timed styling subtitle format. | `structured-parse` | Parse `[Events]` table $\rightarrow$ extract speaker names, timecodes, dialogue to Markdown. |
| `.smi`, `.sami` | `application/x-sami` | Synchronized Accessible Media Interchange (Microsoft HTML-like caption format). | `structured-parse` | SAMI parser $\rightarrow$ extract timed `<SYNC Start=...>` text spans. |
| `.ttml`, `.dfxp` | `application/ttml+xml` | Timed Text Markup Language (W3C standard XML for broadcast caption interchange). | `structured-parse` | XML parse $\rightarrow$ extract timed paragraph spans (`<p begin=... end=...>`). |
| `.sbv` | `text/plain` | YouTube Subtitle Format (`0:00:01.000,0:00:04.000\nText`). | `structured-parse` | Parse timestamp ranges $\rightarrow$ structured Markdown turns. |
| `.sub`, `.idx` | `text/plain`, `application/octet-stream` | MicroDVD text subtitles (`.sub`) OR VobSub DVD bitmap subtitle stream pair (`.sub` + `.idx`). | `structured-parse` / `media-transcribe` | If MicroDVD $\rightarrow$ text parse; if VobSub bitmap $\rightarrow$ run OCR on subpicture bitmaps. |
| `.lrc` | `text/plain` | Synchronized Lyrics format (`[mm:ss.xx] Lyric text`). | `structured-parse` | Parse lyric timestamps $\rightarrow$ structured timed Markdown lyrics. |

---

### 2.9 Archives, Compressed Streams, Software Packages & Disk Images

This category covers file compression archives, software installation packages, container bundles, and virtual machine/forensic disk images.

| Extension(s) | Common MIME Type(s) | Description & Semantic Payload | Ingest Posture | Ingest Router Target & Notes |
| :--- | :--- | :--- | :--- | :--- |
| `.zip`, `.zipx` | `application/zip`, `application/x-zip-compressed` | ZIP archive format (Phil Katz PKZIP standard; ZIPX uses advanced compression). | `archive-expand` | Bomb-safe extraction with path-traversal check $\rightarrow$ emit synthetic directory index + recurse. |
| `.tar` | `application/x-tar` | Tape Archive standard format (POSIX ustar/pax). Concatenated file stream. | `archive-expand` | Tar unpacker with depth/size quotas $\rightarrow$ recurse child files into E0. |
| `.gz`, `.tgz` | `application/gzip` | GNU Zip compression (DEFLATE algorithm; `.tgz` is `.tar.gz`). | `archive-expand` | Decompress stream; if tarball $\rightarrow$ expand; if single file $\rightarrow$ route inner payload. |
| `.bz2`, `.tbz2` | `application/x-bzip2` | Bzip2 compression format (Burrows-Wheeler transform). | `archive-expand` | Decompress and route inner payload to E0. |
| `.xz`, `.txz` | `application/x-xz` | XZ compression format (LZMA2 algorithm; `.txz` is `.tar.xz`). | `archive-expand` | Decompress and recurse into child files. |
| `.zst`, `.zstd` | `application/zstd` | Zstandard real-time compression format (RFC 8878). | `archive-expand` | Decompress and route child files into E0. |
| `.7z` | `application/x-7z-compressed` | 7-Zip compressed archive (LZMA/LZMA2/PPMd algorithms, AES encryption). | `archive-expand` | `py7zr` extraction (verify not encrypted) $\rightarrow$ recurse child files into E0. |
| `.rar` | `application/vnd.rar`, `application/x-rar-compressed` | RAR compressed archive format (RAR4 / RAR5 format). | `archive-expand` | `unrar` / `rarfile` extraction $\rightarrow$ recurse child files. |
| `.lz`, `.lz4`, `.lzma` | `application/x-lzip`, `application/x-lz4` | Lzip, LZ4, and raw LZMA compressed streams. | `archive-expand` | Decompress stream and route to E0. |
| `.br` | `application/x-brotli` | Brotli compressed data stream (RFC 7932). | `archive-expand` | Decompress and route inner payload. |
| `.sz` | `application/x-snappy-framed` | Snappy framed compression stream. | `archive-expand` | Decompress and route inner payload. |
| `.cab` | `application/vnd.ms-cab-compressed` | Microsoft Cabinet archive format (MSZIP, LZX, Quantum compression). | `archive-expand` | `cabextract` $\rightarrow$ unpack and route children. |
| `.arj`, `.lzh`, `.lha`, `.ace`, `.zoo`, `.arc` | `application/octet-stream` | Legacy archive formats (ARJ, LHA/LZH, WinACE, Zoo, SEA ARC). | `archive-expand` | Dedicated legacy unpackers $\rightarrow$ recurse into E0. |
| `.deb` | `application/vnd.debian.binary-package` | Debian Software Package (ar archive containing `control.tar.gz` and `data.tar.xz`). | `archive-expand` / `dangerous/quarantine` | Unpack `control` package metadata (Package, Version, Depends, Description) to Markdown. |
| `.rpm` | `application/x-rpm` | Red Hat Package Manager format (cpio archive with Gzip/XZ payload). | `archive-expand` / `dangerous/quarantine` | Extract RPM header tags (Summary, Description, Changelog, File list) to Markdown. |
| `.apk`, `.aab` | `application/vnd.android.package-archive` | Android Application Package (ZIP containing `AndroidManifest.xml`, classes.dex, resources). | `archive-expand` / `dangerous/quarantine` | Parse binary XML manifest $\rightarrow$ extract package name, permissions, activities to Markdown. |
| `.ipa` | `application/x-itunes-ipa` | iOS Application Package (ZIP containing `Payload/App.app/Info.plist`). | `archive-expand` / `dangerous/quarantine` | Extract `Info.plist` bundle metadata and app entitlements to Markdown. |
| `.jar`, `.war`, `.ear` | `application/java-archive` | Java Archive (ZIP containing compiled `.class` files, resources, and `META-INF/MANIFEST.MF`). | `archive-expand` / `dangerous/quarantine` | Extract `MANIFEST.MF` + list package hierarchy and public class signatures. |
| `.whl` | `application/x-wheel+zip` | Python Wheel binary package distribution (ZIP with `METADATA`, `entry_points.txt`). | `archive-expand` | Extract `METADATA` file $\rightarrow$ package summary, dependencies, docstrings in Markdown. |
| `.egg` | `application/x-python-egg` | Python Egg legacy package distribution format. | `archive-expand` | Extract `PKG-INFO` metadata to Markdown. |
| `.gem` | `application/x-ruby-gem` | RubyGem package format (tar archive wrapping `metadata.gz` and `data.tar.gz`). | `archive-expand` | Extract YAML gemspec metadata (summary, authors, dependencies) to Markdown. |
| `.nupkg` | `application/zip` | NuGet package format for .NET (ZIP containing `.nuspec` XML manifest and DLLs). | `archive-expand` | Parse `.nuspec` metadata $\rightarrow$ package description and dependencies. |
| `.crx`, `.xpi` | `application/x-chrome-extension`, `application/x-xpinstall` | Chromium and Firefox WebExtension packages (ZIP with `manifest.json`). | `archive-expand` | Extract `manifest.json` $\rightarrow$ extension name, permissions, background scripts to Markdown. |
| `.flatpak`, `.snap` | `application/octet-stream` | Flatpak and Ubuntu Snap containerized application packages. | `archive-expand` | Extract app manifest metadata to Markdown. |
| `.appx`, `.msix` | `application/zip` | Windows Universal App Package (ZIP with `AppxManifest.xml`). | `archive-expand` | Parse manifest $\rightarrow$ app capabilities and dependencies. |
| `.iso` | `application/x-iso9660-image` | ISO 9660 / UDF Optical Disc Image. Sector-level filesystem dump. | `archive-expand` / `binary-opaque` | `pycdlib` $\rightarrow$ list directory volume structure, extract files up to quota limit. |
| `.img` | `application/octet-stream` | Raw sector-by-sector disk image (FAT, ext4, NTFS, raw). | `archive-expand` / `binary-opaque` | Inspect partition table $\rightarrow$ extract root files or retain as opaque image. |
| `.dmg` | `application/x-apple-diskimage` | Apple Disk Image (zlib/bzip2 compressed HFS+/APFS disk image). | `archive-expand` / `dangerous/quarantine` | `dmg2img` / 7-Zip $\rightarrow$ inspect volume files, extract installer metadata. |
| `.vmdk` | `application/x-vmdk` | VMware Virtual Disk Image format (monolithic sparse or descriptor). | `binary-opaque` | Extract VMDK descriptor header (geometry, adapter type, parent CID); raw preserved. |
| `.vhd`, `.vhdx` | `application/x-vhd` | Microsoft Virtual Hard Disk / VHDX image formats. | `binary-opaque` | Extract VHD header/geometry metadata. |
| `.qcow2`, `.qcow` | `application/x-qemu-disk` | QEMU Copy-On-Write disk image format. | `binary-opaque` | `qemu-img info` $\rightarrow$ extract virtual size, disk size, backing file chain. |
| `.vdi` | `application/x-virtualbox-vdi` | Oracle VirtualBox Virtual Disk Image format. | `binary-opaque` | Extract VDI header (UUID, disk size, block map). |
| `.raw`, `.dd` | `application/octet-stream` | Raw bitstream disk dump (forensic acquisition). | `binary-opaque` | Compute forensic hashes (MD5, SHA-256), extract partition table summary. |
| `.e01` | `application/x-encase-image` | Expert Witness Format / EnCase forensic disk image (compressed chunks + case metadata). | `archive-expand` / `binary-opaque` | `libewf` $\rightarrow$ extract acquisition case notes, examiner details, volume directory tree. |

---

### 2.10 Fonts, 3D Geometry, CAD & E-Books

This category covers typographic fonts, 3D polygon meshes, scene descriptions, parametric CAD drawings, and digital e-book publications.

| Extension(s) | Common MIME Type(s) | Description & Semantic Payload | Ingest Posture | Ingest Router Target & Notes |
| :--- | :--- | :--- | :--- | :--- |
| `.ttf`, `.otf` | `font/ttf`, `font/otf` | TrueType and OpenType font files. Glyph outlines, kerning, OpenType tables (`name`, `cmap`). | `binary-opaque` | `fonttools` $\rightarrow$ extract font family, designer, copyright, license, glyph count to Markdown. |
| `.woff`, `.woff2` | `font/woff`, `font/woff2` | Web Open Font Format (WOFF2 uses Brotli-compressed table streams). | `binary-opaque` | `fonttools` $\rightarrow$ extract font metadata table (`name`). |
| `.eot` | `application/vnd.ms-fontobject` | Embedded OpenType font format (legacy Internet Explorer font). | `binary-opaque` | Extract font metadata. |
| `.pfa`, `.pfb`, `.afm` | `application/x-font-type1` | PostScript Type 1 font formats and Adobe Font Metrics. | `text-native` / `binary-opaque` | Parse AFM text metrics $\rightarrow$ font family details. |
| `.bdf`, `.pcf` | `application/x-font-bdf` | Bitmap Distribution Format (ASCII text) and Portable Compiled Format for X11. | `text-native` / `binary-opaque` | Parse BDF text headers $\rightarrow$ font properties. |
| `.obj` (+ `.mtl`) | `model/obj`, `text/plain` | Wavefront 3D Object format. Plain text vertices (`v`), normals (`vn`), texture coords (`vt`), faces (`f`). | `text-native` / `structured-parse` | Parse vertex/face count, bounding box dimensions, material library links to Markdown. |
| `.gltf` | `model/gltf+json` | GL Transmission Format (Khronos JSON 3D scene descriptor). | `structured-parse` | Parse JSON node hierarchy, mesh names, animations, material definitions to Markdown. |
| `.glb` | `model/gltf-binary` | Binary GLTF container (JSON chunk + binary buffer chunk). | `binary-opaque` / `media-transcribe` | Extract JSON scene graph chunk + render multi-angle previews to `media/`. |
| `.fbx` | `application/octet-stream` | Autodesk Filmbox 3D exchange format (ASCII text or binary format). | `structured-parse` / `binary-opaque` | Extract 3D scene hierarchy, object names, bone rigs, animation clips to Markdown. |
| `.dae` | `model/vnd.collada+xml` | COLLADA 3D digital asset exchange schema (ISO/PAS 17506 XML). | `structured-parse` | XML parse $\rightarrow$ extract visual scenes, node hierarchy, geometry summaries. |
| `.stl` | `model/stl` | Stereolithography 3D geometry format (ASCII text or 80-byte binary header + triangles). | `structured-parse` / `binary-opaque` | Compute triangle count, bounding box bounds, surface area, and manifold volume. |
| `.ply` | `text/plain`, `application/octet-stream` | Polygon File Format / Stanford Triangle Format (ASCII header + vertex/face properties). | `structured-parse` | Parse PLY header $\rightarrow$ element counts (vertex, face), property types, bounding box. |
| `.blend` | `application/x-blender` | Blender 3D Project file (`BLENDER` magic header + DNA1 struct definitions). | `binary-opaque` | Extract Blender version, scene names, material names, object inventory. |
| `.usda` | `model/usd` | Universal Scene Description (Pixar USD plain text ASCII format). | `text-native` | USD stage hierarchy, prim definitions, material bindings in Markdown. |
| `.usdc`, `.usdz` | `model/vnd.usd+zip` | USD Crate binary format (`.usdc`) and USD Zipped package (`.usdz`). | `archive-expand` / `binary-opaque` | Extract USD scene graph, stage metadata, and embedded texture assets. |
| `.abc` | `application/octet-stream` | Alembic open computer graphics interchange cache format (HDF5 or Ogawa format). | `binary-opaque` | Extract Alembic object hierarchy and property schemas. |
| `.wrl`, `.x3d` | `model/vrml`, `model/x3d+xml` | Virtual Reality Modeling Language (VRML97) and Extensible 3D (X3D XML ISO 19775). | `text-native` / `structured-parse` | Parse scene graph nodes, geometry primitives, viewpoints to Markdown. |
| `.step`, `.stp` | `application/step` | ISO 10303 STEP (Standard for the Exchange of Product model data - ASCII text format). | `structured-parse` / `text-native` | Parse STEP header (schema name, file description), component hierarchy, assemblies. |
| `.iges`, `.igs` | `model/iges` | Initial Graphics Exchange Specification (ASCII formatted CAD entity data). | `structured-parse` / `text-native` | Parse IGES directory entry and parameter data $\rightarrow$ CAD part summary. |
| `.dxf` | `image/vnd.dxf` | AutoCAD Drawing Exchange Format (ASCII tagged data representing AutoCAD drawings). | `structured-parse` / `text-native` | `ezdxf` $\rightarrow$ parse `ENTITIES` section, extract text layers, blocks, dimensions. |
| `.dwg` | `image/vnd.dwg` | AutoCAD Drawing binary format (proprietary Autodesk binary vector database). | `binary-opaque` | `libredwg` $\rightarrow$ extract drawing metadata, layer names, embedded text entities. |
| `.skp` | `application/vnd.sketchup.skp` | Trimble SketchUp 3D model format (modern versions are OLE/ZIP). | `binary-opaque` | Extract component definitions, layer names, scene views. |
| `.ifc` | `application/x-step` | Industry Foundation Classes (BIM building model data - ISO 16739 STEP text). | `structured-parse` / `text-native` | Parse building spatial structure (IfcProject, IfcSite, IfcBuilding, IfcBuildingStorey, IfcWall). |
| `.epub` | `application/epub+zip` | Electronic Publication (IDPF/W3C standard - OCF ZIP wrapping XHTML + OPF manifest). | `document-convert` | Parse `content.opf` reading order $\rightarrow$ concatenate XHTML chapters to `document.md`; images to `media/`. |
| `.mobi`, `.prc` | `application/x-mobipocket-ebook` | Mobipocket eBook format (PalmDOC binary container with compressed HTML). | `document-convert` | `ebook-convert` / Python Mobi $\rightarrow$ extract clean chapter text to Markdown. |
| `.azw`, `.azw3`, `.kfx` | `application/vnd.amazon.ebook` | Amazon Kindle eBook formats (AZW3 is KF8 format; KFX is ion-based). | `document-convert` | Unpack non-DRM book stream $\rightarrow$ chapter Markdown. |
| `.fb2` | `application/x-fictionbook+xml` | FictionBook 2.0 (Russian standard XML e-book format with embedded base64 images). | `document-convert` / `structured-parse` | XML parse $\rightarrow$ extract book title, author, chapter sections to Markdown; images to `media/`. |
| `.lit` | `application/x-ms-reader` | Microsoft Reader legacy eBook format (ITOL/ITLS format). | `document-convert` | Convert HTML payloads to Markdown. |
| `.cbz`, `.cbr`, `.cbt`, `.cb7` | `application/vnd.comicbook+zip`, `application/vnd.comicbook-rar` | Comic Book Archives (ZIP, RAR, TAR, 7Z containing ordered page images). | `archive-expand` / `media-transcribe` | Unpack page images $\rightarrow$ OCR / VLM comic panel descriptions in reading sequence order. |

---

### 2.11 Scientific, Bioinformatics, Astronomy & Binary Instrument Data

This category covers genomic sequencing files, biological sequence alignments, crystallographic structures, chemical molecules, astronomical FITS containers, and lab instrument signals.

| Extension(s) | Common MIME Type(s) | Description & Semantic Payload | Ingest Posture | Ingest Router Target & Notes |
| :--- | :--- | :--- | :--- | :--- |
| `.fasta`, `.fa`, `.fna`, `.faa` | `text/x-fasta` | FASTA biological sequence format (`>header\nSEQUENCE` for DNA, RNA, amino acids). | `structured-parse` / `text-native` | Biopython $\rightarrow$ parse sequence accessions, descriptions, length, sequence type to Markdown. |
| `.fastq`, `.fq` | `text/x-fastq` | FASTQ sequencing reads with per-base Phred quality scores (`@header\nSEQ\n+\nQUAL`). | `structured-parse` | Summarize read count, GC content, mean Phred quality score profile; raw preserved. |
| `.sam` | `text/x-sam` | Sequence Alignment Map (tab-delimited human-readable genomic alignments). | `structured-parse` | Parse `@HD`/`@SQ` headers $\rightarrow$ chromosome references, alignment tool version, mapping stats. |
| `.bam` | `application/x-bam` | Binary Alignment Map (BGZF-compressed binary SAM format). | `structured-parse` / `binary-opaque` | `pysam` $\rightarrow$ extract reference sequence dictionary, read group metadata, alignment stats. |
| `.cram` | `application/x-cram` | CRAM reference-based compressed genomic alignment format (EBI/Sanger). | `binary-opaque` | Extract CRAM header and reference mapping summary. |
| `.vcf` | `text/x-vcf` | Variant Call Format (tab-delimited text for single nucleotide polymorphisms and indels). | `structured-parse` | `cyvcf2` $\rightarrow$ parse `##` metadata headers, sample IDs, variant count, chrom distribution. |
| `.bcf` | `application/x-bcf` | Binary Variant Call Format (BGZF-compressed binary VCF). | `binary-opaque` | Extract header definitions and variant statistics. |
| `.gff`, `.gff3`, `.gtf` | `text/x-gff` | General Feature Format / Gene Transfer Format (tab-delimited genome feature annotations). | `structured-parse` | Parse feature types (gene, mRNA, exon, CDS), attributes (`gene_id`, `gene_name`) to Markdown. |
| `.bed` | `text/x-bed` | Browser Extensible Data format (genomic intervals: `chrom`, `chromStart`, `chromEnd`). | `structured-parse` | Parse interval count, total base-pair coverage, chromosome distribution to Markdown. |
| `.wig`, `.bigwig`, `.bw` | `application/x-bigwig` | Wiggle track format and binary indexed BigWig for continuous dense genomic data. | `structured-parse` / `binary-opaque` | `pyBigWig` $\rightarrow$ extract chromosome ranges, zoom levels, mean signal statistics. |
| `.bigbed`, `.bb` | `application/x-bigbed` | BigBed binary indexed version of BED files. | `binary-opaque` | `pyBigWig` $\rightarrow$ extract field schemas and interval counts. |
| `.cif`, `.mmcif` | `chemical/x-cif` | Macromolecular Crystallographic Information File (IUCr standard for atomic coordinates). | `structured-parse` / `text-native` | `gemmi` / Biopython $\rightarrow$ extract structure ID, title, resolution, polymer chains, ligand names. |
| `.pdb` | `chemical/x-pdb` | Protein Data Bank atomic coordinate format (fixed-column records: `ATOM`, `HETATM`, `HELIX`). | `structured-parse` / `text-native` | Biopython $\rightarrow$ extract header, title, resolution, compound name, chain inventory, residue count. |
| `.sdf`, `.mol`, `.mol2` | `chemical/x-mdl-sdfile`, `chemical/x-molfile` | MDL Structure-Data File, Molfile, and Tripos Mol2 chemical structure formats. | `structured-parse` / `text-native` | RDKit $\rightarrow$ parse molecular formulas, SMILES, InChI, molecular weight, IUPAC name. |
| `.smi`, `.smiles` | `chemical/x-daylight-smiles` | Simplified Molecular-Input Line-Entry System (plaintext chemical ASCII representation). | `text-native` / `structured-parse` | RDKit $\rightarrow$ compute 2D depiction to `media/` + canonical SMILES and molecular properties. |
| `.pqr` | `chemical/x-pdb` | PDB format modified with partial charge ($) and atomic radius ($). | `text-native` / `structured-parse` | Parse charge distribution summary to Markdown. |
| `.fits`, `.fit`, `.fts` | `image/fits`, `application/fits` | Flexible Image Transport System (IAU astronomical data container for images and binary tables). | `structured-parse` / `media-transcribe` | `astropy.io.fits` $\rightarrow$ parse FITS header keywords (TELESCOP, INSTRUME, OBJECT) + preview. |
| `.root` | `application/x-root` | CERN ROOT object-oriented scientific data framework tree format. | `structured-parse` / `binary-opaque` | `uproot` $\rightarrow$ inspect `TTree` branches, leaves, entry counts, histograms to Markdown. |
| `.mzml`, `.mzxml` | `application/xml` | Mass Spectrometry XML markup standards (HUPO-PSI standard). | `structured-parse` | `pyteomics` $\rightarrow$ extract instrument model, ionization source, MS levels (MS1/MS2), scan count. |
| `.ab1` | `application/octet-stream` | Applied Biosystems Sanger sequencing electropherogram / chromatogram trace binary. | `structured-parse` | Extract base-call sequence, signal-to-noise ratio, trace peak positions. |
| `.edf`, `.bdf` | `application/octet-stream` | European Data Format / BioSemi Data Format (multichannel medical EEG, ECG, polysomnography). | `structured-parse` | `mne` $\rightarrow$ extract channel labels, sampling frequencies, recording duration, subject metadata. |
| `.las`, `.laz` | `application/vnd.lasperf` | ASPRS LiDAR point cloud format (`.laz` is LASzip compressed). | `structured-parse` / `binary-opaque` | `laspy` $\rightarrow$ extract point count, spatial bounding box (, Y, Z$), classification returns. |
| `.pcap`, `.pcapng` | `application/vnd.tcpdump.pcap` | Packet Capture / PCAP Next Generation network traffic dump files. | `structured-parse` / `binary-opaque` | `scapy` / `pyshark` $\rightarrow$ extract capture duration, IP conversations, protocol breakdown, packet count. |

---

### 2.12 Databases, File-Based Dumps & Search Indexes

This category covers embedded relational database files, database export dumps, columnar file engines, key-value stores, and search engine inverted index segments.

| Extension(s) | Common MIME Type(s) | Description & Semantic Payload | Ingest Posture | Ingest Router Target & Notes |
| :--- | :--- | :--- | :--- | :--- |
| `.sqlite`, `.sqlite3`, `.db`, `.db3`, `.s3db` | `application/vnd.sqlite3` | SQLite Database file (`SQLite format 3\000` magic). Self-contained relational database. | `structured-parse` | `sqlite3` inspect $\rightarrow$ extract schema (`sqlite_master`), table names, row counts, sample top-5 rows. |
| `.duckdb` | `application/octet-stream` | DuckDB embedded analytical columnar database file. | `structured-parse` | DuckDB connect $\rightarrow$ inspect catalogs, schemas, tables, column types, sample aggregates. |
| `.accdb`, `.mdb` | `application/x-msaccess` | Microsoft Access Database (JET / ACE relational engine binary file). | `structured-parse` | `mdbtools` / `pyodbc` $\rightarrow$ extract table definitions, schema diagrams, sample records. |
| `.fdb` | `application/x-firebird` | Firebird relational database file. | `binary-opaque` | Opaque storage unless Firebird client available. |
| `.sql` | `application/sql`, `text/x-sql` | Structured Query Language script (DDL table definitions + DML `INSERT` statements). | `text-native` / `structured-parse` | SQL parser $\rightarrow$ extract `CREATE TABLE` schemas, database dialect, table relationships. |
| `.dump`, `.dmp`, `.pgdump` | `application/octet-stream` | Binary database dump exports (PostgreSQL custom format, Oracle Data Pump, MySQL binary). | `structured-parse` / `binary-opaque` | `pg_restore -l` $\rightarrow$ extract catalog TOC (table list, schemas, indexes); raw retained. |
| `.cfs`, `.cfe`, `.si`, `.segments` | `application/octet-stream` | Apache Lucene / Elasticsearch inverted index segment files. | `binary-opaque` | Opaque binary; extract Lucene codec version header; raw preserved. |
| `.sst`, `.ldb` | `application/octet-stream` | RocksDB / LevelDB Sorted String Table (SSTable) immutable key-value data blocks. | `binary-opaque` | Opaque storage; extract SSTable property block (key count, comparator name). |
| `.rdb` | `application/x-redis-rdb` | Redis Database Snapshot Dump (`REDIS0011` magic). | `structured-parse` / `binary-opaque` | `rdbtools` $\rightarrow$ extract key count, memory usage by data type, database IDs. |
| `.lmdb` | `application/x-lmdb` | Symas Lightning Memory-Mapped Database (B+ tree key-value store). | `structured-parse` / `binary-opaque` | Extract environment info, database names, entry count. |
| `.kch`, `.kct` | `application/octet-stream` | Kyoto Cabinet Hash Database / Tree Database file. | `binary-opaque` | Opaque key-value store. |
| `.cdb` | `application/x-cdb` | D. J. Bernstein Constant Database (fast immutable associative array). | `structured-parse` / `binary-opaque` | Extract key count, record size statistics. |

---

### 2.13 Certificates, Cryptography, Keys & Signed Envelopes

This category covers public key infrastructure certificates, private keys, cryptographic signatures, encrypted archives, and password vaults.

| Extension(s) | Common MIME Type(s) | Description & Semantic Payload | Ingest Posture | Ingest Router Target & Notes |
| :--- | :--- | :--- | :--- | :--- |
| `.crt`, `.cer`, `.pem` | `application/x-x509-ca-cert`, `application/x-pem-file` | X.509 Digital Certificates (PEM Base64 ASCII armored or binary DER format). | `structured-parse` / `text-native` | `cryptography` $\rightarrow$ parse Subject, Issuer, Validity (NotBefore/NotAfter), SANs, Key Usage. |
| `.der` | `application/x-x509-ca-cert` | Distinguished Encoding Rules (binary ASN.1 encoding of X.509 certs/keys). | `structured-parse` | `cryptography` $\rightarrow$ decode ASN.1 structure $\rightarrow$ certificate details in Markdown. |
| `.pfx`, `.p12` | `application/x-pkcs12` | PKCS #12 Personal Information Exchange Syntax (contains certificate chain + private key). | `dangerous/quarantine` | High sensitivity! Quarantine; do not expose private key bytes; parse public certificate chain only. |
| `.p7b`, `.p7c`, `.p7s` | `application/pkcs7-mime`, `application/pkcs7-signature` | Cryptographic Message Syntax / PKCS #7 signed message or certificate bundle. | `structured-parse` | Extract certificate list and signer info (signature validity, digest algorithm). |
| `.csr` | `application/pkcs10` | Certificate Signing Request (PKCS #10 public key + requested subject DN + extensions). | `structured-parse` / `text-native` | Parse requested Subject DN, public key algorithm, requested SAN DNS names to Markdown. |
| `.crl` | `application/pkix-crl` | Certificate Revocation List (X.509 list of revoked certificate serial numbers). | `structured-parse` | Parse CRL Issuer, Last Update, Next Update, count of revoked serial numbers. |
| `.pub` | `text/plain` | OpenSSH / PGP Public Key (SSH RSA, Ed25519, ECDSA public key string). | `text-native` / `structured-parse` | Parse key type (`ssh-ed25519`), bit length, comment/fingerprint to Markdown. |
| `.key`, `.priv` | `application/octet-stream`, `text/plain` | Private Cryptographic Key (RSA, ECDSA, Ed25519 private key in PEM/DER). | `dangerous/quarantine` | High risk! Quarantine; redact private exponent/seed; log key type and cryptographic algorithm only. |
| `.asc`, `.gpg`, `.pgp`, `.sig` | `application/pgp-signature`, `application/pgp-encrypted` | OpenPGP ASCII Armor / binary encrypted message or detached signature. | `structured-parse` / `binary-opaque` | `gnupg` $\rightarrow$ parse packet headers, key IDs, signature timestamps, encryption algorithms. |
| `.age` | `application/octet-stream` | Age encryption tool encrypted file format (`age-encryption.org/v1`). | `binary-opaque` | Extract Age recipient stanzas and header metadata; payload remains encrypted. |
| `.minisig` | `text/plain` | Minisign signature format (`untrusted comment\n...`). | `text-native` | Parse signature comment and public key ID. |
| `.kdbx` | `application/x-keepass2` | KeePass 2 Password Database (AES/Twofish/ChaCha20 encrypted database). | `dangerous/quarantine` | Secure vault! Quarantine; extract database header version and KDF parameters; payload opaque. |
| `.md5`, `.sha1`, `.sha256`, `.sha512`, `.sfv` | `text/plain` | Cryptographic Checksum verification files (GNU `coreutils` / BSD checksum format). | `text-native` / `structured-parse` | Parse filename-to-hash mappings $\rightarrow$ structured verification table in Markdown. |

---

### 2.14 System Binaries, Debug Symbols, Dumps & Hardware Firmware

This category covers compiled machine code, shared libraries, intermediate bytecode, debug symbol databases, memory core dumps, and hardware ROM firmware.

| Extension(s) | Common MIME Type(s) | Description & Semantic Payload | Ingest Posture | Ingest Router Target & Notes |
| :--- | :--- | :--- | :--- | :--- |
| `.exe`, `.dll`, `.sys`, `.scr`, `.ocx` | `application/vnd.microsoft.portable-executable`, `application/x-msdownload` | Microsoft Windows Portable Executable (PE32 / PE32+). Machine code, imports, exports, resources. | `dangerous/quarantine` / `binary-opaque` | `pefile` $\rightarrow$ extract PE header, architecture (x86/x64/ARM64), compile timestamp, imported DLLs/APIs, exports. |
| `.elf`, `.so`, `.o`, `.a` | `application/x-executable`, `application/x-sharedlib` | Executable and Linkable Format (UNIX/Linux ELF executable, shared object, static archive). | `binary-opaque` / `dangerous/quarantine` | `pyelftools` $\rightarrow$ extract ELF header, architecture, dynamic libraries (`DT_NEEDED`), exported symbols. |
| `.dylib`, `.macho`, `.bundle` | `application/x-mach-binary` | Mach-O Binary (macOS / iOS executable, dynamic library, kernel extension, Universal Fat Binary). | `binary-opaque` / `dangerous/quarantine` | `macholib` $\rightarrow$ extract architectures (x86_64, arm64e), load commands, linked frameworks. |
| `.wasm` | `application/wasm` | WebAssembly binary format (`\0asm` magic). Stack-based virtual machine bytecode. | `binary-opaque` / `dangerous/quarantine` | `wasmparser` $\rightarrow$ extract module imports, exports, memory limits, custom section names. |
| `.wat` | `text/vnd.wasm.wat` | WebAssembly Text Format (S-expression representation of WebAssembly). | `text-native` | Direct S-expression code passthrough to `document.md`. |
| `.class` | `application/java-vm` | Java Bytecode Class file (`\xCA\xFE\xBA\xBE` magic). Compiled JVM instructions. | `binary-opaque` | `javap` / ASM $\rightarrow$ extract class name, superclass, interfaces, field/method signatures. |
| `.pyc`, `.pyo`, `.pyd` | `application/x-python-code` | Python compiled bytecode file (magic number + timestamp/hash + marshalled code object). | `binary-opaque` | `dis` / `uncompyle6` $\rightarrow$ extract Python version, code object names, disassembly. |
| `.luac` | `application/x-lua-bytecode` | Lua compiled bytecode binary. | `binary-opaque` | Extract Lua VM version, function prototypes, constant pool. |
| `.beam` | `application/x-erlang-beam` | Erlang / Elixir BEAM virtual machine bytecode file. | `binary-opaque` | Extract BEAM chunk metadata (module name, exported functions, attributes). |
| `.pdb` (Windows) | `application/octet-stream` | Microsoft Program Database (MSF container storing PDB 7.0 debug symbols and types). | `structured-parse` / `binary-opaque` | Disambiguate from Protein Data Bank! Extract source file paths, symbol names, function offsets. |
| `.dSYM` | `application/x-mach-binary` | macOS Debug Symbol Package (directory bundle containing DWARF symbols). | `binary-opaque` | Extract DWARF symbol tables and compilation units. |
| `.dbg`, `.map` | `text/plain`, `application/json` | GDB symbol file or JavaScript / CSS Source Map (v3 JSON format). | `structured-parse` / `text-native` | If JS Source Map $\rightarrow$ parse `sources`, `mappings`, and reconstruct original source file tree. |
| `.trace`, `.prof`, `.cpuprofile`, `.heapsnapshot` | `application/json`, `application/octet-stream` | V8 / Chrome DevTools performance traces, CPU profiles, and memory heap snapshots. | `structured-parse` | Parse JSON call trees, flame graph samples, memory allocation nodes to Markdown. |
| `.dmp`, `.mdmp` | `application/octet-stream` | Windows Minidump / Memory Crash Dump (processor context, loaded modules, thread stacks). | `binary-opaque` | `minidump` $\rightarrow$ extract OS version, crash reason (Exception Code), faulting thread stack trace. |
| `.core` | `application/x-coredump` | UNIX Core Dump file (ELF core dump generated upon SIGSEGV/SIGABRT). | `binary-opaque` | Extract signal number, faulting instruction pointer, thread register state. |
| `.crash` | `text/plain` | Apple macOS / iOS Diagnostic Crash Log. | `text-native` | Parse crash thread backtrace, binary images, exception type to Markdown. |
| `.hprof` | `application/octet-stream` | Java Heap Dump file (binary dump of all live JVM heap objects and references). | `binary-opaque` | Extract heap summary, total instances by class, dominant memory consumers. |
| `.bin`, `.rom`, `.fw` | `application/octet-stream` | Generic binary dump, BIOS / UEFI ROM, or embedded hardware firmware binary. | `binary-opaque` | `binwalk` signature scan $\rightarrow$ list embedded filesystems (SquashFS, CramFS), bootloaders. |
| `.hex`, `.srec` | `text/plain` | Intel HEX (`:LLAAAATTDD...CC`) and Motorola S-Record ASCII firmware representations. | `text-native` / `structured-parse` | Parse memory address ranges, record counts, checksum validation to Markdown. |
| `.uf2` | `application/octet-stream` | USB Flashing Format (UF2 512-byte blocks designed for microcontroller flashing via USB MSC). | `binary-opaque` | Extract target board family ID, block count, target flash address range. |

---

## 3. Deep Dive into Cross-Cutting Complexities

```
+----------------------------------------------------------------------------------------------------+
|                                CROSS-CUTTING COMPLEXITY MATRIX                                     |
+----------------------------------------------------------------------------------------------------+
| Multimedia Multiplexing   | Container (ISOBMFF/MKV) != Codec (AV1/H.264/Opus/AAC)                 |
| Compound Packages         | ZIP masquerades (DOCX, XLSX, EPUB, JAR) & OLE CFBF (DOC, XLS, MSG)    |
| Extension Collisions      | .ts (TypeScript vs MPEG-TS), .mod (Tracker vs Go Mod), .pdb (PDB vs MS)|
| Transport Degradation     | application/octet-stream fallback, text/plain sniffing failures       |
| Hostile Polyglots         | GIFAR (GIF+JAR), PDF+ZIP polyglots, Zip-Bombs, XXE Entity Expansion    |
| Spreadsheet Semantics     | Cached <v> vs dynamic <f>, dynamic arrays, 1900 vs 1904 date systems   |
+----------------------------------------------------------------------------------------------------+
```

### 3.1 Containers vs. Codecs & Elementary Streams

A frequent architectural failure in ingestion pipelines is treating container extensions (`.mp4`, `.mkv`, `.mov`, `.ogg`) as atomic formats. An ISOBMFF (`.mp4`) or Matroska (`.mkv`) file is a multiplexer combining discrete elementary streams:

1. **Video Streams**: H.264 (AVC), H.265 (HEVC), VP8, VP9, AV1, Apple ProRes, Motion JPEG.
2. **Audio Streams**: AAC, Opus, MP3, Vorbis, FLAC, AC-3, E-AC-3, LPCM.
3. **Timed Text Streams**: WebVTT, 3GPP Timed Text, SubRip, ASS/SSA, CEA-608/708 closed captions.
4. **Data Tracks**: GPS telemetry, camera gyroscope IMU data, timecode tracks.

**Ingest Router Rule [Observed]**: E0 must never route media by extension alone. Media handling requires a probe step (via `ffprobe` / `libavformat`) to discover track topologies:
- Extract all audio streams $\rightarrow$ route primary spoken language track to Whisper-class ASR.
- Extract embedded subtitle streams $\rightarrow$ if human captions exist, ingest directly via `structured-parse` as high-confidence grounding alongside ASR.
- Extract video keyframes (I-frames) at scene changes $\rightarrow$ route to `media/` as visual grounding artifacts.

### 3.2 Compound Documents & Packaging Masquerades

Modern office and application documents are compound archives rather than linear byte streams:

- **Office OpenXML (OOXML)**: `.docx`, `.xlsx`, `.pptx` are ZIP archives containing standard parts:
  - `[Content_Types].xml`: Root MIME part dictionary.
  - `_rels/.rels`: Package relationship graph.
  - Payload XMLs: `word/document.xml`, `xl/worksheets/sheet1.xml`, `ppt/slides/slide1.xml`.
  - Embedded Media: `word/media/image1.png`, `ppt/media/video1.mp4`.
- **Compound File Binary Format (OLE2 CFBF)**: Legacy `.doc`, `.xls`, `.ppt`, and `.msg` files implement an internal FAT-like virtual filesystem inside a single file with sector allocation tables (SAT), directory streams, and mini-streams.
- **Apple Directory Bundles**: On macOS, `.pages`, `.keynote`, `.rtfd`, and `.dSYM` are filesystem directories presented as files by the Finder. When uploaded via HTTP multipart forms or non-Apple tools, they arrive as folder trees or compressed ZIP archives.

**Ingest Router Rule [Inference]**: E0 must detect when an uploaded file is a compound package. For ZIP-based office docs, E0 should extract embedded media into `media/` and convert the structural XML into `document.md`. For macOS bundles, E0 must normalize directory uploads before invoking document converters.

### 3.3 The Extension Collision Minefield

File extensions are ambiguous hints. Below is the taxonomy of critical collision vectors that require magic-byte and structural disambiguation:

| Colliding Extension | Candidate Format A (Signature / Sniff) | Candidate Format B (Signature / Sniff) | Disambiguation Strategy |
| :--- | :--- | :--- | :--- |
| **`.ts`** | **TypeScript Source** (`text/typescript`): UTF-8 text containing `import`, `export`, `interface`, `const`. | **MPEG-2 Transport Stream** (`video/mp2t`): Binary packets beginning with sync byte `0x47` every 188 bytes. | Check first byte: if `0x47` every 188 bytes $\rightarrow$ `media-transcribe`; else if valid UTF-8 $\rightarrow$ `text-native`. |
| **`.mod`** | **ProTracker Audio Module** (`audio/x-mod`): Binary audio tracking data with signature `M.K.`, `4CHN`, `8CHN` at offset 1080. | **Go Module File** (`go.mod`): Plain text beginning with `module <path>\ngo <version>`. | Check offset 1080 for `M.K.` $\rightarrow$ `structured-parse`; check text for `module ` $\rightarrow$ `text-native`. |
| **`.dat`** | **Generic Binary Blob**: Raw proprietary binary stream. | **Minecraft Anvil Region / VCD Stream**: Chunked format with specific header blocks. | Inspect magic bytes; default to `binary-opaque` with metadata. |
| **`.pdb`** | **Protein Data Bank 3D Model** (`chemical/x-pdb`): ASCII text with line records `HEADER`, `ATOM`, `HETATM`, `END`. | **Microsoft Program Database** (`application/octet-stream`): Binary MSF starting with `Microsoft C/C++ MSF 7.00\r\n\x1A\x44\x53`. | Check first 32 bytes for `Microsoft C/C++ MSF` $\rightarrow$ debug symbols; check for `HEADER`/`ATOM` $\rightarrow$ bio structure. |
| **`.m`** | **Objective-C Source**: Text with `#import <...>`, `@interface`, `@implementation`. | **MATLAB / Octave Script**: Text with `%` comments, `function`, `matrix` operations. | Heuristic keyword scan (`@interface` vs `function`/`%`) $\rightarrow$ tree-sitter grammar selection. |
| **`.pl`** | **Perl Script**: Text with `#!/usr/bin/env perl`, `use strict;`, `my `. | **Prolog Source**: Text with `:-`, `?-`, clauses ending in `.` without semicolons. | Keyword / syntax scan $\rightarrow$ select Perl vs Prolog syntax highlighter. |
| **`.v`** | **Verilog HDL**: Text with `module <name>(...);`, `input`, `output`, `wire`, `reg`, `endmodule`. | **Coq Theorem Proof**: Text with `Require Import`, `Inductive`, `Lemma`, `Proof.`, `Qed.`. | Scan for `endmodule` (Verilog) vs `Proof.`/`Qed.` (Coq). |
| **`.r`** | **R Statistical Script**: Text with `<-`, `library(...)`, `ggplot`. | **REBOL Script**: Text beginning with `REBOL [...]`. | Scan for `REBOL` header stanza. |
| **`.sub`** | **MicroDVD Subtitles**: Plain text lines with frame numbers `{100}{250}Hello`. | **VobSub Subtitles**: Binary MPEG subpicture packet stream. | Check for text `{frame}` pattern $\rightarrow$ `structured-parse`; else `media-transcribe`. |
| **`.msg`** | **Outlook Mail Message**: OLE2 CFBF starting with `\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1`. | **ROS Message Definition**: Plain text with field types `int32 count\nstring name`. | Check OLE2 magic bytes $\rightarrow$ email converter; else text passthrough. |
| **`.d`** | **D Source Code**: Text with `module ...;`, `import std.stdio;`. | **Makefile Dependency Fragment**: Text with `target.o: target.c header.h`. | Scan for Make colon target syntax vs D module syntax. |
| **`.raw`** | **Camera RAW Sensor File**: TIFF-like header with camera manufacturer tags. | **Raw Disk Image / Raw PCM Audio**: Headerless binary sectors or audio samples. | Check for TIFF/Exif header $\rightarrow$ Camera RAW; else `binary-opaque`. |
| **`.cls`** | **LaTeX Document Class**: TeX text containing `\ProvidesClass`, `\DeclareOption`. | **Visual Basic Class Module**: Text beginning with `VERSION 1.0 CLASS\nBEGIN\n...`. | Scan for `\ProvidesClass` vs `VERSION 1.0 CLASS`. |

### 3.4 MIME Type Unreliability & Sniffing Hierarchy

Ingestion systems that rely on HTTP `Content-Type` headers fail catastrophically in production:

1. **The `application/octet-stream` Trap**: Over 40% of programmatic S3 uploads and API clients default to `application/octet-stream` for all non-HTML files, masking CSVs, PDFs, JSON, and audio files.
2. **The `text/plain` Fallback**: Web servers frequently emit `text/plain` for Python files, shell scripts, Markdown, YAML, and CSVs.
3. **MIME Confusion in Web Browsers**: Chromium and Safari apply divergent MIME sniffing algorithms (MIME Sniffing Standard) that misclassify binary streams with initial ASCII characters.

**Deterministic Sniffing Hierarchy for E0 [Observed]**:

```
+--------------------------------------------------------------------+
| 1. Magic Byte Inspection (FileSig / PRONOM database)               |
+--------------------------------------------------------------------+
                                | (Match found)
                                v
+--------------------------------------------------------------------+
| 2. Structural Content Probe (JSON/XML parse, UTF-8 BOM, RIFF/ISOBMFF)|
+--------------------------------------------------------------------+
                                | (Indeterminate / Plain text)
                                v
+--------------------------------------------------------------------+
| 3. Extension Heuristic & Syntax Disambiguation                     |
+--------------------------------------------------------------------+
                                | (Unknown extension)
                                v
+--------------------------------------------------------------------+
| 4. Client Transport MIME Hint (Untrusted fallback)                 |
+--------------------------------------------------------------------+
```

### 3.5 Security Hazards, Polyglots & Resource Bombs

Ingestion pipelines for autonomous agents operate on untrusted user and agent files. They are vulnerable to active and passive file attacks:

```
+---------------------------------------------------------------------------------------+
| Threat Category      | Attack Mechanism                          | E0 Defense Invariant       |
+---------------------------------------------------------------------------------------+
| Polyglot Payloads    | GIFAR (GIF + JAR), PDF + ZIP polyglots    | Validate primary header;   |
|                      | executing in secondary contexts           | strip executable execution |
+---------------------------------------------------------------------------------------+
| Zip-Bombs            | High-compression / recursive archives     | Strict uncompressed size   |
|                      | (e.g. 42.zip, 4.5 PB uncompressed)        | quota & recursion limit    |
+---------------------------------------------------------------------------------------+
| Path Traversal       | Zip-Slip (`../../../../etc/passwd` in TAR)| Sanitize extraction paths; |
|                      |                                           | reject relative escapes    |
+---------------------------------------------------------------------------------------+
| XML Entity Bombs     | XXE injection & Billion Laughs expansion  | Disable DTD processing &   |
|                      | in SVG, DOCX, XML                         | external entity resolution |
+---------------------------------------------------------------------------------------+
| Decompression Bombs  | 1 KB TIFF expanding to 100k x 100k raster | Pre-allocate memory caps   |
|                      | (40 GB uncompressed bitmap)               | before raster decoding     |
+---------------------------------------------------------------------------------------+
| Active Scripting     | Embedded VBA in DOCM/XLSM, JS in PDF,     | Strip active macros/JS;    |
|                      | `<script>` tags in SVG diagrams           | render passive text/raster |
+---------------------------------------------------------------------------------------+
```

### 3.6 Encrypted, Password-Protected & DRM Assets

When documents are encrypted without provided credentials:
- **Encrypted PDFs**: Standard Security Handler (AES-128 / AES-256).
- **Office RMS / Agile Encryption**: Encrypted compound packages (`EncryptedPackage` stream).
- **Encrypted Archives**: Password-protected ZIP (WinZip AES), 7z, or RAR headers.

**Ingest Posture [Inference]**:
- Treat as `binary-opaque` (do not fail the pipeline or drop the file).
- Extract unencrypted container metadata (author, creation date, encryption method).
- Emit a structured stub `document.md` indicating that the document content is encrypted and requires decryption credentials.
- Retain the original encrypted asset immutably in `raw/`.

### 3.7 The "Spreadsheet Fallacy": Why "Support `.xlsx`" is not Simple Text Extraction

A pervasive misconception in LLM memory systems is that extracting `.xlsx` is equivalent to dumping rows of text:

```
+----------------------------------------------------------------------------------------------------+
| SPREADSHEET COMPLEXITY VECTOR | SEMANTIC FAILURE MODE IF TREATED NAIVELY                           |
+-------------------------------+--------------------------------------------------------------------+
| Formula vs. Cached Value      | Cells store `<f>SUM(A1:A10)</f>` and cached `<v>100</v>`. If       |
|                               | calculated values are stale, text extraction yields incorrect data.|
+-------------------------------+--------------------------------------------------------------------+
| Multi-Sheet Topology          | Workbooks contain multiple sheets with inter-sheet formulas.       |
|                               | Flattening sheets blindly loses coordinate boundaries.             |
+-------------------------------+--------------------------------------------------------------------+
| Hidden Rows, Columns, Sheets  | Hidden tabs often contain lookup tables, intermediate calculations, |
|                               | or sensitive financial models that require explicit tagging.       |
+-------------------------------+--------------------------------------------------------------------+
| Semantic Formatting Colors    | "Red rows represent overdue accounts" — losing cell fill color     |
|                               | destroys domain meaning.                                           |
+-------------------------------+--------------------------------------------------------------------+
| Date System Discrepancies     | Excel 1900 date system (with leap-year bug) vs. Mac 1904 system     |
|                               | causes serial number dates (e.g. 45180) to shift by 4 years.       |
+-------------------------------+--------------------------------------------------------------------+
| Dynamic Array Formulas        | Modern Excel `#SPILL!` arrays compute variable-length ranges       |
|                               | at runtime that are not represented in static grid records.        |
+-------------------------------+--------------------------------------------------------------------+
```

---

## 4. RememberStack-Oriented Grouping & Roadmap

RememberStack organizes file ingestion around three fundamental architectural invariants:
1. **Markdown-First Coordinate System**: E0 converts inputs into `document.md`, which serves as the immutable coordinate space for blockizer offsets (D57) and claim grounding (D32).
2. **Immutable Raw Retention**: Original sensory and binary files remain reachable via `raw_uri` pointers (D51) off the navigation path.
3. **Derived Supporting Assets**: Derived figures, keyframes, and thumbnails reside on the browse path in `artifacts/media/`.

```
+----------------------------------------------------------------------------------------------------+
|                                REMEMBERSTACK INGESTION TIERS                                       |
+----------------------------------------------------------------------------------------------------+
| TIER 1: Core Surface (Current & Immediate Scope)                                                   |
| - Paged Documents: Digital & Scanned PDF (with page_map & bounding boxes)                         |
| - Office Formats: DOCX, PPTX, XLSX, ODT (via MarkItDown / Calamine)                                |
| - Plain Text & Web: Markdown, HTML, RFC 822 Email (EML, MSG)                                       |
| - Sensory Media: Audio (MP3, WAV, M4A, Opus) $\rightarrow$ ASR + Diarization + {t_start, t_end}     |
| - Visual Media: Video (MP4, WebM, MOV) $\rightarrow$ ASR spine + keyframes; Images $\rightarrow$ OCR/VLM|
+----------------------------------------------------------------------------------------------------+
| TIER 2: Next Logical Horizons for Agent Memory                                                     |
| - Structured Code & Repos: Source code (.py, .ts, .rs, .go) with tree-sitter AST symbol chunking   |
| - Interactive Notebooks: Jupyter (.ipynb) with cell separation & output plot extraction to media/  |
| - Columnar & Tabular Data: Parquet, Arrow, CSV with schema metadata & sample top-k slices          |
| - Vector Diagrams & Canvases: SVG, Excalidraw, Draw.io with node topology extraction               |
| - Safe Archive Recursion: ZIP, TAR.GZ with quota-controlled directory tree indexing                |
| - Conversational Chat Exports: Slack, Discord, WhatsApp with speaker turn structuring              |
+----------------------------------------------------------------------------------------------------+
| TIER 3: Specialized Domain Plugins (via Agent Sidecars)                                            |
| - Bioinformatics: FASTA, VCF, PDB, mmCIF with molecular structure summaries                       |
| - Geospatial / GIS: GeoJSON, GeoTIFF, Shapefiles with bounding box & layer metadata                |
| - 3D Models & CAD: GLTF, USD, STEP, DXF with bounding volume, vertex count & preview renders       |
| - Medical Imaging: DICOM, NIfTI with study metadata & windowed slice thumbnails                    |
+----------------------------------------------------------------------------------------------------+
| TIER 4: Passive Opaque Storage & Quarantine                                                        |
| - Compiled Executables: Windows PE (.exe), Linux ELF (.so), macOS Mach-O (.dylib)                  |
| - Memory & Crash Dumps: Windows Minidump (.dmp), UNIX Core dumps (.core)                          |
| - Database SSTables & Indexes: Lucene segments, RocksDB SSTable blocks                             |
| - Encrypted Keystores: KeePass (.kdbx), PKCS#12 (.p12)                                             |
+----------------------------------------------------------------------------------------------------+
```

---

## 5. Explicit Non-Goals & Epistemic Boundaries

To maintain rigorous architectural hygiene, the following non-goals are formally recorded:

1. **No Binding MIME Allowlists**: This document does not establish a restrictive MIME allowlist for RememberStack. The router architecture is designed to handle unknown or unexpected formats gracefully using the `binary-opaque` fallback posture.
2. **No Claim of Current Parser Implementation**: Enumeration of a format in this inventory does not imply that the current prototype codebase contains an active parser for that format.
3. **Preservation of Binding Architectural Decisions**: This analysis does not alter, contradict, or supersede existing binding decisions (D38, D51, D57, D59).
4. **Epistemic Status of Assertions**:
   - `[Observed]`: Verified industry format specifications, IANA registrations, PRONOM signatures, and parser behaviors (e.g. FFmpeg track multiplexing, OOXML ZIP container structures).
   - `[Inference]`: Architectural mapping of formats to RememberStack E0 ingestion postures and coordinate systems.
   - `[Experiment needed]`: Empirical optimization of chunking granularities for specific formats (e.g. AST tree-sitter chunking vs. naive line chunking for code; Parquet sample density vs. token context limits; keyframe extraction frequency for long video streams).

---

## 6. Standards Sources & References

- **IANA Media Types Registry**: Official MIME type registrations (`https://www.iana.org/assignments/media-types/media-types.xhtml`, accessed ~2026-08-26).
- **The National Archives (UK) PRONOM Technical Registry**: Binary file signatures and format identifiers (PUID database).
- **Gary Kessler's File Signature Table (FileSig)**: Standard magic byte reference database.
- **Apache Tika Parser Registry (v2.x/v3.x)**: Multi-format parsing and content extraction specifications.
- **FFmpeg Multimedia Framework Documentation**: `libavformat` container formats and `libavcodec` codec specifications.
- **LibreOffice Filter Matrix**: Comprehensive office document format import/export filters.
- **ISO / IEC Standards**:
  - ISO/IEC 14496-12 (ISO Base Media File Format - ISOBMFF).
  - ISO 32000-2 (Document management — Portable Document Format — PDF 2.0).
  - ISO/IEC 29500 (Office Open XML File Formats — OOXML).
  - ISO/IEC 26300 (Open Document Format for Office Applications — ODF).
  - ISO 28500 (WARC file format).
  - ISO 10303-21 (STEP CAD exchange structure).
- **BioPython, RDKit, Astropy & GDAL/OGR Documentation**: Domain-specific scientific and geospatial format libraries.
