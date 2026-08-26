# Exhaustive ingestible file-type inventory — independent Codex analysis

**Status:** non-binding analysis only  
**Date:** 2026-08-26  
**Scope:** file-format enumeration and coarse ingest posture; not a converter-router design,
allowlist, implementation claim, or change to any binding RememberStack document.

## 1. Short answer

There is no finite, authoritative list of “all file types.” A useful exhaustive inventory has to
index several overlapping universes:

1. **Filename extensions** — convenient hints (`.pdf`, `.xlsx`, `.webm`), neither unique nor
   trustworthy.
2. **Media types** — registered or conventional content labels (`application/pdf`,
   `audio/mpeg`), often absent, generic, wrong, or more/less specific than the bytes.
3. **Byte formats and versions** — what a signature/deep parser identifies (for example OLE2
   compound storage containing a legacy Word document, or ZIP containing OOXML parts).
4. **Containers and payload encodings** — MP4, Matroska, Ogg, MXF, ZIP, HDF5, TIFF, and PDF are
   containers or compound formats; H.264, AV1, AAC, Opus, and FLAC are codecs/bitstreams that may
   occur inside several containers.
5. **Compound documents and datasets** — one logical upload can be a package, directory, sidecar
   set, split archive, database plus WAL, shapefile family, web archive, or application bundle.
6. **Semantic profiles** — PDF/A, GeoTIFF, DICOM, FHIR JSON, EPUB, `.docx`, `.apk`, and a generic
   ZIP can share lower-level machinery but require different interpretation.

Here, **“exhaustive” means operationally broad rather than mathematically closed**: the inventory
covers well over a hundred real format families and hundreds of extensions that people commonly
attach, export, archive, or ask agents to inspect; it also names the long tail by specialist
family. It does not pretend that every vendor application, scientific instrument, game, firmware
revision, or unregistered extension can be enumerated forever. The durable answer is a layered
identifier plus an `unknown/opaque` outcome, not an ever-growing extension switch.

### 1.1 Claim labels and table convention

- **Observed** — directly present in the repository or supported by a cited registry,
  specification, or tool catalog.
- **Inference** — this analysis's product-fit or ingest-posture recommendation.
- **Experiment needed** — behavior or fidelity that needs fixtures/tool evaluation before any
  support claim.

Unless a row says otherwise, its extension/format description is **Observed** and its posture is
an **Inference**. MIME values are representative common values, not an allowlist. `—` means “no
single dependable common media type located”; `application/octet-stream` means only “generic
binary,” not successful identification. Vendor (`vnd.`), personal (`prs.`), and historical `x-`
types are included where they are what real senders emit. Case variants are not repeated.

### 1.2 Coarse ingest-posture vocabulary

| Label | Meaning in this inventory |
|---|---|
| `text-native` | Decode as bounded text with explicit charset/newline handling; retain bytes. |
| `structured-parse` | Parse records/tree/package semantics, then render useful Markdown + sidecars. |
| `document-convert` | Use a layout/document converter; produce Markdown and provenance to page/slide/sheet/object. |
| `media-transcribe` | Derive OCR/transcript/description/keyframes or specialist renderings; retain and link raw. |
| `archive-expand` | Enumerate and safely recurse into members under depth/count/size/path limits. |
| `binary-opaque` | Hash, identify, and extract safe metadata; retain raw pending a specialist adapter. |
| `dangerous/quarantine` | Do not execute/mount/activate. Inspect only in a resource-limited sandbox, if at all. |

Labels can compose. For example, `.docm` is `document-convert` plus
`dangerous/quarantine`; `.svgz` is bounded decompression plus `structured-parse` and safe visual
rendering; `.apk` is `archive-expand` plus `dangerous/quarantine`.

## 2. Taxonomy at a glance

| Top-level bucket | Representative families | Default posture |
|---|---|---|
| Documents / office / page description | text processors, PDF/PS/XPS, diagrams, desktop publishing | `document-convert` |
| Spreadsheets / tabular / interchange | Excel/ODS, delimited text, JSON/XML, columnar/statistical files | `structured-parse` |
| Presentations | PowerPoint/Impress/Keynote, slide shows/templates | `document-convert` |
| Email / messaging / calendars | RFC messages, mailboxes, Outlook stores, chat exports, iCalendar/vCard | `structured-parse` |
| Markup / web / config / code / notebooks | Markdown, HTML, XML grammars, source trees, notebooks, logs | `text-native` or `structured-parse` |
| Images | raster, vector, layered editor, raw camera, medical, GIS/remote sensing | `media-transcribe` |
| Audio | elementary streams, containers, lossless/lossy codecs, MIDI/trackers, sessions | `media-transcribe` |
| Video + captions | containers, elementary streams, playlists, captions/subtitles | `media-transcribe` |
| Haptics | interchange/binary touch-effect streams and vendor patterns | `structured-parse` or `binary-opaque` |
| Archives / packages / disk images | compression, archives, software packages, filesystem images | `archive-expand` or quarantine |
| Fonts | desktop/web/bitmap/type-design/source fonts | `binary-opaque` |
| 3D / CAD / BIM / manufacturing | meshes/scenes, interchange, native CAD, point clouds, PCB | specialist `structured-parse` or opaque |
| Ebooks / publishing | EPUB/MOBI/AZW, comic archives, help systems, bibliographic exports | `document-convert` / `archive-expand` |
| Scientific / instrument | arrays, earth science, chemistry, genomics, medical signals, telemetry | specialist `structured-parse` |
| Databases / dumps / indexes | SQLite and vendor databases, SQL dumps, key-value/search stores | read-only `structured-parse` or opaque |
| Certificates / crypto / signed objects | PEM/DER, PKCS/CMS, PGP, keystores, signatures | safe metadata parse; secrets quarantined |
| Executables / platform / legacy / misc. | binaries, bytecode, installers, shortcuts, firmware, ROMs | `dangerous/quarantine` |

## 3. Detailed inventory

### 3.1 Documents, office files, page description, diagrams, and publishing

#### 3.1.1 Text documents and word processors

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| Plain text | `.txt`, `.text`, `.asc`, `.me`, extensionless `README`/`LICENSE` | `text/plain` | Unstructured text in an inferred/declared character encoding. | `text-native` |
| Rich Text Format | `.rtf`, `.rtx` | `application/rtf`, `text/rtf` | Microsoft rich-text control-word format; can embed objects/pictures. | `document-convert` |
| Word OOXML | `.docx`, `.dotx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, `application/vnd.openxmlformats-officedocument.wordprocessingml.template` | ZIP/OPC package of XML parts, media, relationships, comments, revisions, etc. | `document-convert` |
| Macro-enabled Word OOXML | `.docm`, `.dotm` | `application/vnd.ms-word.document.macroEnabled.12`, `application/vnd.ms-word.template.macroEnabled.12` | OOXML package permitted to contain VBA. | `document-convert`; `dangerous/quarantine` |
| Legacy Microsoft Word/OLE | `.doc`, `.dot`, `.wiz` | `application/msword`, `application/vnd.ms-word` | Binary Word formats, commonly in OLE2 compound storage. | `document-convert`; quarantine embedded/active content |
| Word XML / WordprocessingML | `.xml` | `application/xml`, `application/vnd.ms-word` | Word 2003 XML or generic XML; extension alone cannot select it. | `structured-parse` / `document-convert` |
| OpenDocument text | `.odt`, `.ott`, `.odm`, `.oth` | `application/vnd.oasis.opendocument.text`, `application/vnd.oasis.opendocument.text-template`, `application/vnd.oasis.opendocument.text-master`, `application/vnd.oasis.opendocument.text-web` | ODF ZIP packages for documents/templates/master/web documents. | `document-convert` |
| Flat OpenDocument text | `.fodt` | `application/vnd.oasis.opendocument.text-flat-xml` | Single XML serialization of ODF text. | `structured-parse` / `document-convert` |
| OpenOffice/StarOffice Writer | `.sxw`, `.stw`, `.sxg`, `.sdw`, `.sgl`, `.vor` | `application/vnd.sun.xml.writer`, `application/vnd.stardivision.writer`, often `application/octet-stream` | Pre-ODF XML/package and legacy binary Writer families. | `document-convert` |
| Apple Pages | `.pages` | `application/vnd.apple.pages`, `application/x-iwork-pages-sffpages` | Apple iWork word-processing package; version-dependent compound archive. | `document-convert` |
| WordPerfect | `.wp`, `.wpd`, `.wp5`, `.wp6`, `.w60`, `.w61` | `application/vnd.wordperfect`, `application/wordperfect` | Binary WordPerfect document generations. | `document-convert` |
| Microsoft Works Writer | `.wps`, `.wpt` | `application/vnd.ms-works` | Legacy Works word-processing document/template. | `document-convert` |
| Microsoft Publisher | `.pub` | `application/vnd.ms-publisher`, `application/x-mspublisher` | Desktop-publishing document; collides with public-key usage. | `document-convert` |
| AbiWord | `.abw`, `.zabw` | `application/x-abiword` | XML word-processing document; `.zabw` is compressed. | `structured-parse` / `document-convert` |
| Hangul/Hancom | `.hwp`, `.hwpx`, `.hwt` | `application/x-hwp`, `application/vnd.hancom.hwp`, `application/vnd.hancom.hwpx` | Korean word-processing binary/OLE and ZIP/XML generations. | `document-convert` |
| Lotus Word Pro | `.lwp` | `application/vnd.lotus-wordpro` | Legacy Lotus word-processing binary. | `document-convert` |
| ClarisWorks/AppleWorks | `.cwk` | `application/x-appleworks-document`, often `application/octet-stream` | Legacy integrated-suite document whose internal subtype must be detected. | `document-convert` |
| MacWrite | `.mw`, `.mcw` | `application/macwriteii`, often `application/octet-stream` | Classic Macintosh word-processing documents. | `document-convert` |
| T602 / 602Text | `.602` | `application/x-t602` | Central-European legacy text-processor document. | `document-convert` |
| Unified Office Format text | `.uot`, `.uof` | `application/vnd.uof.text`, often `application/octet-stream` | Chinese UOF office document/package. | `document-convert` |
| Ichitaro | `.jtd`, `.jtt`, `.jaw`, `.jbw`, `.juw` | `application/x-ichitaro` | Japanese word-processing document generations/templates. | `document-convert` |
| FrameMaker | `.fm`, `.frame`, `.mif`, `.book` | `application/vnd.framemaker`, `application/x-mif` | Native binary documents/books plus text-based Maker Interchange Format. | `.mif`: `structured-parse`; others `document-convert` |
| Ventura/legacy DTP | `.vp`, `.vpt`, `.chp` | often `application/octet-stream` | Ventura Publisher publication/project components. | `binary-opaque` unless specialist converter |
| TeX-family source documents | `.tex`, `.ltx`, `.latex`, `.sty`, `.cls`, `.dtx`, `.ins`, `.bib`, `.bst` | `application/x-tex`, `text/x-tex`, `text/x-bibtex` | TeX/LaTeX sources, packages, classes, and bibliography/style files. | `text-native`; optionally render/parse includes |
| Troff/roff | `.roff`, `.troff`, `.man`, `.ms`, `.me`, `.mom`, extensionless man pages | `text/troff`, `application/x-troff` | Roff-family marked-up source for manuals/documents. | `text-native` / `structured-parse` |

#### 3.1.2 Fixed-layout and page-description formats

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| PDF family | `.pdf`, sometimes `.pdfa` | `application/pdf` | Portable Document Format; may hold digital text, scans, forms, annotations, JavaScript, attachments, portfolios, signatures, 3D, or encryption. | `document-convert`; quarantine active/embedded content |
| PostScript | `.ps` | `application/postscript` | Programmable page-description language. | sandboxed `document-convert`; `dangerous/quarantine` |
| Encapsulated PostScript | `.eps`, `.epsf`, `.epsi` | `application/postscript`, `image/x-eps` | PostScript graphic/page subset, still executable PostScript. | sandboxed render + `media-transcribe`; quarantine |
| Adobe Illustrator legacy/PDF | `.ai` | `application/postscript`, `application/pdf`, `application/illustrator` | Illustrator artwork; version may be EPS/PostScript, PDF-compatible, or proprietary. | detect then `document-convert` / `media-transcribe` |
| XPS / OpenXPS | `.xps`, `.oxps` | `application/vnd.ms-xpsdocument`, `application/oxps` | ZIP-packaged fixed-layout XML document. | `document-convert`; safely inspect package |
| DjVu | `.djvu`, `.djv` | `image/vnd.djvu`, `image/x-djvu` | Scanned-document container with image layers and optional OCR text. | `document-convert` / OCR |
| DVI / XDV | `.dvi`, `.xdv` | `application/x-dvi` | TeX device-independent rendered pages; XDV extends for modern engines. | `document-convert` |
| PCL / HP-GL | `.pcl`, `.prn`, `.hpgl`, `.hpg`, `.plt` | `application/vnd.hp-pcl`, `application/vnd.hp-hpgl` | Printer/plotter command streams; `.prn` is generic and ambiguous. | sandboxed render; otherwise `binary-opaque` |
| AFP / MO:DCA | `.afp`, `.mcf`, `.modca` | `application/vnd.afpc.modca`, `application/afp` | IBM high-volume print/page document and resources. | specialist `document-convert` |
| Microsoft Document Imaging | `.mdi` | `image/vnd.ms-modi` | TIFF-like scanned-document container from Office Document Imaging. | `document-convert` / OCR |
| Mixed Raster Content | `.mrc` | `image/x-mrc` | Layered scanned-document compression, often within PDF workflows. | specialist render/OCR |
| Final Form / print streams | `.pclm`, `.spl`, `.xsr` | varies / `application/octet-stream` | Printer spool or rendered-page streams with vendor-specific structure. | `binary-opaque`; sandboxed specialist render |

#### 3.1.3 Diagrams, drawings, project, and knowledge-map documents

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| Visio OOXML | `.vsdx`, `.vssx`, `.vstx`, `.vtx` | `application/vnd.ms-visio.drawing`, `application/vnd.ms-visio.stencil`, `application/vnd.ms-visio.template`, and related vendor types | ZIP/XML drawings, stencils, and templates. | `document-convert` + relationship/shape-text parse |
| Visio macro-enabled | `.vsdm`, `.vssm`, `.vstm` | `application/vnd.ms-visio.drawing.macroEnabled.12`, related vendor types | Visio packages allowed to carry VBA. | `document-convert`; `dangerous/quarantine` |
| Legacy Visio | `.vsd`, `.vss`, `.vst`, `.vdx`, `.vsx` | `application/vnd.visio` and related vendor types | Binary/OLE and XML Visio diagrams, stencils, templates. | `document-convert` |
| Draw.io / diagrams.net | `.drawio`, `.dio`, `.xml` | `application/xml`, `text/xml` | XML (often deflate/base64-compressed internally) diagram model. | `structured-parse` + safe render |
| Mermaid / PlantUML / Graphviz | `.mmd`, `.mermaid`, `.puml`, `.plantuml`, `.dot`, `.gv` | `text/plain`, `text/vnd.graphviz` | Text-native diagram source. | `text-native`; sandbox renderer with no includes/network |
| FreeMind / Freeplane / XMind | `.mm`, `.mmx`, `.xmind` | `application/x-freemind`, `application/vnd.xmind.workbook` | XML or ZIP/package mind maps. | `structured-parse` |
| OmniGraffle | `.graffle`, `.gtemplate` | `application/x-omnigraffle` | macOS diagram package/binary plist generations. | `structured-parse` where decoded; else opaque |
| SmartDraw | `.sdr`, `.sdt`, `.scz` | `application/octet-stream` | Proprietary diagram/template/compressed project. | `binary-opaque` |
| Microsoft Project | `.mpp`, `.mpt`, `.mpx`, `.xml` | `application/vnd.ms-project`, `application/x-project`, `application/xml` | Project plans/templates; MPX/XML are interchange forms. | `structured-parse` / `document-convert` |
| Primavera | `.xer`, `.xml`, `.plf` | `text/plain`, `application/xml`, often `application/octet-stream` | Project scheduling interchange/native layout. | `.xer`/XML `structured-parse`; other opaque |
| Concept map / graph exchange | `.cmap`, `.graphml`, `.gexf`, `.net`, `.pajek` | `application/xml`, `text/plain` | Concept/network graph documents and exchange forms. | `structured-parse` |
| OpenDocument drawing | `.odg`, `.otg`, `.fodg` | `application/vnd.oasis.opendocument.graphics`, `application/vnd.oasis.opendocument.graphics-template`, `application/vnd.oasis.opendocument.graphics-flat-xml` | ODF drawings/templates, packaged or flat XML. | `document-convert` / `structured-parse` |
| Legacy OpenOffice Draw | `.sxd`, `.std`, `.sda` | `application/vnd.sun.xml.draw`, `application/vnd.stardivision.draw` | Pre-ODF and StarOffice drawing formats. | `document-convert` |

### 3.2 Spreadsheets, tabular data, statistics, and data interchange

#### 3.2.1 Spreadsheet workbooks

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| Excel OOXML workbook | `.xlsx`, `.xltx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, `application/vnd.openxmlformats-officedocument.spreadsheetml.template` | ZIP/OPC workbook/template with XML sheets and related parts. | `structured-parse` + sheet-aware Markdown |
| Excel macro-enabled | `.xlsm`, `.xltm`, `.xlam` | `application/vnd.ms-excel.sheet.macroEnabled.12`, `application/vnd.ms-excel.template.macroEnabled.12`, `application/vnd.ms-excel.addin.macroEnabled.12` | OOXML workbook/template/add-in permitted to carry VBA or XLM macros. | `structured-parse`; `dangerous/quarantine` |
| Excel binary workbook | `.xlsb` | `application/vnd.ms-excel.sheet.binary.macroEnabled.12` | ZIP/OPC package whose workbook parts use BIFF12 binary records; can contain active content. | `structured-parse`; `dangerous/quarantine` |
| Legacy Excel/BIFF | `.xls`, `.xlt`, `.xlw`, `.xlc`, `.xlm` | `application/vnd.ms-excel`, `application/msexcel` | OLE2 or older BIFF workbook/template/workspace/chart/macro-sheet formats. | `structured-parse`; `dangerous/quarantine` if macros/OLE |
| Excel add-ins/libraries | `.xla`, `.xll`, `.xlb` | `application/vnd.ms-excel`, `application/octet-stream` | VBA add-in, native-code add-in, or toolbar/customization data. | `dangerous/quarantine`; metadata only |
| Excel XML Spreadsheet | `.xml` | `application/xml` | SpreadsheetML 2003; generic extension requires root-element detection. | `structured-parse` |
| OpenDocument spreadsheet | `.ods`, `.ots`, `.fods` | `application/vnd.oasis.opendocument.spreadsheet`, `application/vnd.oasis.opendocument.spreadsheet-template`, `application/vnd.oasis.opendocument.spreadsheet-flat-xml` | ODF spreadsheet/template, ZIP/XML or flat XML. | `structured-parse` |
| OpenOffice/StarOffice Calc | `.sxc`, `.stc`, `.sdc` | `application/vnd.sun.xml.calc`, `application/vnd.stardivision.calc` | Pre-ODF XML/package and legacy spreadsheet formats. | `structured-parse` |
| Apple Numbers | `.numbers` | `application/vnd.apple.numbers`, `application/x-iwork-numbers-sffnumbers` | iWork spreadsheet package with version-specific serialized objects. | `structured-parse` |
| Gnumeric | `.gnumeric` | `application/x-gnumeric` | Gzip-compressed XML workbook. | bounded decompress + `structured-parse` |
| Lotus 1-2-3 | `.123`, `.wk1`, `.wk2`, `.wk3`, `.wk4`, `.wks`, `.fm3`, `.fmt` | `application/vnd.lotus-1-2-3`, `application/x-123` | Legacy Lotus worksheet/workbook/support formats. | `structured-parse` |
| Quattro Pro | `.wb1`, `.wb2`, `.wb3`, `.qpw`, `.wq1` | `application/x-quattro-pro`, `application/vnd.corel-quattropro` | Legacy Borland/Corel spreadsheets. | `structured-parse` |
| Microsoft Works spreadsheet | `.wks`, `.xlr` | `application/vnd.ms-works` | Works spreadsheet; `.wks` collides with Lotus. | `structured-parse` after detection |
| Unified Office spreadsheet | `.uos`, `.uof` | `application/vnd.uof.spreadsheet` | UOF spreadsheet/package. | `structured-parse` |
| PlanMaker / SoftMaker | `.pmd`, `.pmdx`, `.pmv`, `.pmvx` | `application/x-pmd`, often `application/octet-stream` | Proprietary office spreadsheet/template formats. | `structured-parse` if specialist tool; otherwise opaque |

#### 3.2.2 Delimited, record-oriented, and statistical tables

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| Comma-separated values | `.csv` | `text/csv`, `application/csv` | Delimited rows; dialect, charset, header, quoting, and formula interpretation vary. | `structured-parse` |
| Tab-separated values | `.tsv`, `.tab` | `text/tab-separated-values` | Tab-delimited rows; `.tab` also appears in GIS/product files. | `structured-parse` |
| Other delimited text | `.psv`, `.ssv`, `.dsv`, `.del`, `.dat` | `text/plain`, often `text/csv` | Pipe, semicolon, space, or arbitrary-delimited records. | sniffed `structured-parse` |
| Fixed-width / print table | `.fwf`, `.prn`, `.asc`, `.dat` | `text/plain` | Positional columns; schema often external or inferred. | `structured-parse`; preserve lines |
| DIF | `.dif` | `application/x-dif` | Data Interchange Format for tabular data. | `structured-parse` |
| SYLK | `.slk`, `.sylk` | `text/spreadsheet`, `application/x-sylk` | Symbolic Link spreadsheet exchange text. | `structured-parse`; neutralize formula-like cells |
| dBASE table | `.dbf` | `application/x-dbf`, `application/dbase` | Fixed-record database table, also a Shapefile component. | `structured-parse` with code-page handling |
| Epi Info / legacy tables | `.rec`, `.qes`, `.epi` | often `application/octet-stream` | Epidemiology data and questionnaire tables. | specialist `structured-parse` |
| SAS data/catalog/transport | `.sas7bdat`, `.sas7bcat`, `.xpt`, `.cport` | `application/x-sas-data`, `application/x-sas-transport` | SAS datasets, catalogs, and transport files. | specialist `structured-parse` |
| SPSS | `.sav`, `.zsav`, `.por`, `.sps` | `application/x-spss-sav`, `application/x-spss-por`, `text/plain` | Binary/system/portable data plus syntax. | data `structured-parse`; `.sps` `text-native` |
| Stata | `.dta`, `.do`, `.ado`, `.ster` | `application/x-stata`, `text/plain` | Binary datasets/results plus command/program files. | `.dta` specialist parse; code `text-native` |
| R data | `.rds`, `.rda`, `.rdata` | `application/x-r-data`, `application/octet-stream` | Serialized R object(s), potentially language/runtime-specific. | isolated specialist parse; otherwise `binary-opaque` |
| MATLAB/Octave | `.mat`, `.fig`, `.m` | `application/x-matlab-data`, `application/octet-stream`, `text/plain` | Numeric workspace, figure, and source; MAT v7.3 is HDF5. | data specialist parse; `.m` detect/text-native |
| JMP / Minitab | `.jmp`, `.mtw`, `.mpj` | `application/octet-stream` | Proprietary statistical worksheet/project formats. | `binary-opaque` unless specialist adapter |

#### 3.2.3 General structured interchange and columnar files

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| JSON | `.json`, `.map`, `.geojson`, `.topojson` | `application/json`, `application/geo+json`, `application/topo+json` | UTF JSON tree; semantics depend on schema/profile. | `structured-parse` |
| JSON Lines / NDJSON | `.jsonl`, `.ndjson`, `.geojsonl` | `application/x-ndjson`, `application/ndjson`, `application/geo+json-seq` | One JSON value/record per line. | streaming `structured-parse` |
| JSON5 / HJSON | `.json5`, `.hjson` | `application/json5`, `application/hjson` (often `text/plain`) | Human-friendly JSON supersets; not strict JSON. | `structured-parse` with matching grammar |
| XML | `.xml` | `application/xml`, `text/xml` | Extensible markup container; root namespace/schema defines the real format. | hardened `structured-parse` |
| YAML | `.yaml`, `.yml` | `application/yaml`, `text/yaml`, `application/x-yaml` | Human-oriented serialization with tags/anchors and version-specific typing. | safe-schema `structured-parse` |
| TOML | `.toml` | `application/toml` | Typed configuration/interchange text. | `structured-parse` |
| CBOR | `.cbor` | `application/cbor`, `application/cbor-seq` | Concise Binary Object Representation, optionally sequences/tags. | `structured-parse` with limits |
| MessagePack | `.msgpack`, `.mpk` | `application/msgpack`, `application/x-msgpack` | Compact typed binary objects. | `structured-parse` with limits |
| BSON | `.bson` | `application/bson` | Binary JSON-like documents, common in MongoDB ecosystems. | `structured-parse` |
| Amazon Ion | `.ion` | `application/ion`, `text/ion` | Text or binary typed self-describing values. | `structured-parse` |
| Protocol Buffers | `.proto`, `.pb`, `.protobuf`, `.binpb` | `text/plain`, `application/x-protobuf` | Schema language and encoded messages; binary is not self-describing. | schema: `text-native`; payload parse only with schema |
| Apache Thrift | `.thrift`, binary payloads | `application/vnd.apache.thrift.binary`, `application/vnd.apache.thrift.compact`, `text/plain` | IDL plus binary/compact/JSON protocol payloads requiring schema. | schema parse; payload specialist/opaque |
| Apache Avro | `.avro`, `.avsc`, `.avdl` | `application/avro`, `application/vnd.apache.avro+json`, `text/plain` | Schema-bearing object container plus JSON schema/IDL files. | `structured-parse` |
| Apache Parquet | `.parquet` | `application/vnd.apache.parquet`, `application/x-parquet` | Compressed columnar data file with schema and row groups. | `structured-parse` + bounded sampling/profiling |
| Apache ORC | `.orc` | `application/vnd.apache.orc`, `application/x-orc` | Columnar analytics file with stripes/indexes/statistics. | `structured-parse` |
| Arrow IPC / Feather | `.arrow`, `.arrows`, `.feather` | `application/vnd.apache.arrow.file`, `application/vnd.apache.arrow.stream` | Arrow record batches in random-access file or stream; Feather v2 is Arrow IPC. | `structured-parse` |
| Cap'n Proto / FlatBuffers | `.capnp`, `.fbs`, binary payloads | `text/plain`, `application/octet-stream` | Schemas plus fast binary messages that normally require the schema. | schema `text-native`; binary specialist/opaque |
| Smile / UBJSON / BJSON | `.smile`, `.ubj`, `.bjson` | `application/x-jackson-smile`, `application/ubjson`, `application/octet-stream` | Binary JSON-family encodings. | `structured-parse` with format-specific parser |
| ASN.1 BER/CER/DER | `.ber`, `.cer`, `.der` | `application/octet-stream`, profile-specific types | Generic TLV encodings; schema/profile supplies meaning. | profile-aware `structured-parse`; otherwise opaque |

### 3.3 Presentations and slideware

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| PowerPoint OOXML | `.pptx`, `.potx`, `.ppsx`, `.sldx` | `application/vnd.openxmlformats-officedocument.presentationml.presentation`, `application/vnd.openxmlformats-officedocument.presentationml.template`, `application/vnd.openxmlformats-officedocument.presentationml.slideshow`, `application/vnd.openxmlformats-officedocument.presentationml.slide` | ZIP/OPC presentation/template/show/slide packages. | slide-aware `document-convert` |
| Macro-enabled PowerPoint | `.pptm`, `.potm`, `.ppsm`, `.sldm`, `.ppam` | `application/vnd.ms-powerpoint.presentation.macroEnabled.12`, `application/vnd.ms-powerpoint.template.macroEnabled.12`, `application/vnd.ms-powerpoint.slideshow.macroEnabled.12`, `application/vnd.ms-powerpoint.slide.macroEnabled.12`, `application/vnd.ms-powerpoint.addin.macroEnabled.12` | OOXML packages/add-ins permitted to contain VBA. | `document-convert`; `dangerous/quarantine` |
| Legacy PowerPoint | `.ppt`, `.pot`, `.pps`, `.ppa`, `.pwz` | `application/vnd.ms-powerpoint` | Binary/OLE presentation, template, show, add-in/wizard. | `document-convert`; quarantine active content |
| OpenDocument presentation | `.odp`, `.otp`, `.fodp` | `application/vnd.oasis.opendocument.presentation`, `application/vnd.oasis.opendocument.presentation-template`, `application/vnd.oasis.opendocument.presentation-flat-xml` | ODF slides/template, packaged or flat XML. | `document-convert` |
| OpenOffice/StarOffice Impress | `.sxi`, `.sti`, `.sdd`, `.sdp` | `application/vnd.sun.xml.impress`, `application/vnd.stardivision.impress` | Pre-ODF XML/package and binary presentation formats. | `document-convert` |
| Apple Keynote | `.key`, `.kth` | `application/vnd.apple.keynote`, `application/x-iwork-keynote-sffkey` | iWork presentation/theme package; `.key` collides with cryptographic keys. | `document-convert` after detection |
| Unified Office presentation | `.uop`, `.uof` | `application/vnd.uof.presentation` | UOF presentation package. | `document-convert` |
| Harvard Graphics / Freelance | `.shw`, `.prz`, `.pre` | `application/octet-stream` | Legacy presentation/show formats. | specialist convert or `binary-opaque` |
| Prezi | `.pez` | `application/zip`, `application/octet-stream` | Exported Prezi package with assets/metadata. | safe `archive-expand` + specialist `document-convert` |
| Slide HTML bundles | `.html`, `.htm`, `.zip` plus assets | `text/html`, `application/zip` | Reveal.js, exported web slides, or vendor web bundles. | `structured-parse`; recurse local assets only |
| Flash presentations | `.swf`, `.fla`, `.flv` | `application/x-shockwave-flash`, `application/octet-stream`, `video/x-flv` | Compiled Flash, authoring project, or Flash video; may contain scripts. | `dangerous/quarantine`; sandboxed metadata/media derivation |

### 3.4 Email, messaging, contacts, and calendars

#### 3.4.1 Email and mailbox formats

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| Internet Message Format | `.eml`, `.mime`, `.mht` in some contexts | `message/rfc822` | RFC-style headers/body with MIME multipart and nested attachments. | `structured-parse`; recurse parts |
| Unix mbox | `.mbox`, `.mbx`, extensionless mailbox files | `application/mbox` | Concatenated RFC messages with dialect-specific separators/escaping. | streaming `structured-parse`; recurse messages |
| Maildir | directory with `cur/`, `new/`, `tmp/` | — | Directory-backed mailbox: one message per file plus filename flags. | dataset `structured-parse` |
| Outlook message/template | `.msg`, `.oft` | `application/vnd.ms-outlook` | OLE2 compound individual Outlook item/template. | `structured-parse`; recurse attachments |
| Outlook stores | `.pst`, `.ost` | `application/vnd.ms-outlook`, `application/x-pst` | Personal/offline mailbox stores with folders, messages, contacts, calendars. | specialist `structured-parse`; resource limits |
| Outlook Express / Windows Mail | `.dbx`, `.nch`, `.fol` | `application/octet-stream` | Legacy mailbox/folder stores. | specialist `structured-parse` |
| Apple Mail message/store | `.emlx`, `.mbox` directory package | `message/rfc822`, `application/mbox` | Message plus Apple metadata, or mailbox package. | `structured-parse` |
| Outlook for Mac archive | `.olm` | `application/octet-stream` | ZIP-like Outlook for Mac mailbox export. | safe `archive-expand` + specialist parse |
| Thunderbird profiles/indexes | mbox files, `.msf`, `.sqlite` | `application/mbox`, `application/x-sqlite3` | Messages plus indexes/address books; `.msf` is not the message body. | parse source stores; indexes metadata/opaque |
| TNEF attachment | `.dat` (usually `winmail.dat`) | `application/ms-tnef`, `application/vnd.ms-tnef` | Microsoft transport encapsulation holding body/attachments/properties. | `structured-parse`; recurse members |
| MIME aggregate | `.mime`, multipart body | `multipart/mixed`, `multipart/alternative`, `multipart/related` | A MIME entity containing typed subparts, not necessarily a standalone mail. | `structured-parse`; recurse parts |
| S/MIME/CMS mail | `.p7m`, `.p7s`, `.p7c` | `application/pkcs7-mime`, `application/pkcs7-signature` | Signed/enveloped MIME content or detached signature. | verify/metadata; decrypt only with authorized key |
| Lotus Notes/Domino | `.nsf`, `.ntf` | `application/vnd.lotus-notes` | Notes database/template, often containing mail/documents/apps. | specialist read-only parse; quarantine active formulas/code |
| GroupWise archive | `.db`, `.idx` dataset | `application/octet-stream` | Proprietary mail/groupware database set. | `binary-opaque` unless specialist adapter |

#### 3.4.2 Chat, contact, and calendar exports

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| iCalendar | `.ics`, `.ifb`, `.ical`, `.icalendar` | `text/calendar` | Calendar events, tasks, journals, free/busy; may include recurrence/time zones/attachments. | `structured-parse` |
| vCalendar | `.vcs` | `text/x-vcalendar` | Legacy calendar interchange. | `structured-parse` |
| vCard | `.vcf`, `.vcard` | `text/vcard`, `text/x-vcard` | Contact cards; `.vcf` collides with genomic Variant Call Format. | `structured-parse` after profile detection |
| LDIF | `.ldif`, `.ldi` | `text/ldif` | Directory entries and change records. | `structured-parse` |
| Slack export | `.zip` containing `.json`/attachments | `application/zip`, `application/json` | Workspace/channel export as manifest and per-channel JSON plus files. | `archive-expand` + profile-aware `structured-parse` |
| Teams/Discord/Matrix exports | `.json`, `.html`, `.csv`, `.zip` | generic JSON/HTML/CSV/ZIP types | Vendor/tool export of messages, members, reactions, and attachments. | profile-detect `structured-parse`; recurse local files |
| Telegram export | `.json`, `.html` plus asset folders | `application/json`, `text/html` | Chat/account export with linked media. | dataset `structured-parse` |
| WhatsApp/Signal text export | `.txt`, `.zip` with media | `text/plain`, `application/zip` | Human-readable chat transcript, locale-dependent timestamps and names. | `text-native` / profile parse; recurse archive |
| Skype legacy/main database | `.db`, `.sqlite`, `.json` | `application/x-sqlite3`, `application/json` | Local message/history database or export. | read-only `structured-parse` |
| Apple Messages | `chat.db`, `.ichat`, attachment tree | `application/x-sqlite3`, `application/octet-stream` | SQLite conversation store or archived chat property list. | dataset read-only `structured-parse` |
| IRC/log transcripts | `.log`, `.txt`, `.weechatlog` | `text/plain` | Timestamped conversational logs with client-specific conventions. | `text-native` / profile parse |

### 3.5 Markup, web captures, feeds, configuration, source code, and notebooks

#### 3.5.1 Human-authored markup and publishing source

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| Markdown families | `.md`, `.markdown`, `.mdown`, `.mkd`, `.mkdn`, `.mdwn`, `.mdtxt` | `text/markdown` | Plain-text lightweight markup with dialect/extensions. | `text-native` + Markdown parse |
| reStructuredText | `.rst`, `.rest` | `text/x-rst` | Docutils/Sphinx text markup with directives/includes. | `text-native`; disable external includes/exec |
| AsciiDoc | `.adoc`, `.asciidoc`, `.asc` | `text/asciidoc`, `text/plain` | Rich lightweight documentation markup; `.asc` ambiguous. | `text-native`; sandbox includes/macros |
| Org mode | `.org` | `text/org`, `text/plain` | Outline, notes, tables, tasks, and executable-babel-capable markup. | `text-native`; never execute code blocks |
| Textile / MediaWiki / Creole | `.textile`, `.wiki`, `.mediawiki`, `.creole` | `text/plain` | Wiki/lightweight markup dialects. | `text-native` / grammar parse |
| DocBook / TEI / JATS | `.xml`, `.dbk`, `.docbook`, `.tei`, `.jats` | `application/docbook+xml`, `application/tei+xml`, `application/xml` | XML vocabularies for books, scholarly text, and journal articles. | hardened `structured-parse` |
| DITA | `.dita`, `.ditamap`, `.ditaval` | `application/dita+xml`, `application/xml` | Topic-based XML documentation, maps, and filter rules. | hardened `structured-parse`; bound local references |
| SGML | `.sgml`, `.sgm`, `.dtd` | `text/sgml`, `application/sgml` | Generalized markup and declarations; legacy document systems. | sandboxed `structured-parse` |
| Texinfo | `.texi`, `.texinfo`, `.txi` | `application/x-texinfo` | GNU documentation source. | `text-native`; controlled renderer |
| POD | `.pod`, `.pm`, `.pl` | `text/x-perl` | Perl plain-old-documentation, often embedded in code. | `text-native` |

#### 3.5.2 Web pages, feeds, semantic web, and web archives

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| HTML | `.html`, `.htm`, `.shtml`, `.xht` | `text/html` | Web document with links, scripts, embedded/remote resources. | sanitized `structured-parse`; no script/network execution |
| XHTML | `.xhtml`, `.xht` | `application/xhtml+xml` | XML-serialized HTML. | hardened/sanitized `structured-parse` |
| MHTML/MIME HTML | `.mhtml`, `.mht` | `multipart/related`, `application/x-mimearchive` | MIME bundle of HTML and resources. | `structured-parse`; recurse parts, sanitize |
| MAFF | `.maff` | `application/zip`, `application/x-maff` | ZIP-based saved web page(s) with assets and metadata. | `archive-expand` + sanitized HTML parse |
| Web ARChive | `.warc`, `.warc.gz` | `application/warc`, `application/gzip` | ISO web capture records containing request/response/resource payloads. | streaming `structured-parse`; recurse bounded payloads |
| Internet Archive ARC | `.arc`, `.arc.gz` | `application/x-ia-arc`, `application/gzip` | Pre-WARC web capture container; `.arc` also archive/CAD collision. | streaming `structured-parse` after detection |
| HAR | `.har` | `application/json` | HTTP Archive JSON with requests, responses, timings, cookies, sometimes bodies. | `structured-parse`; sensitive-header redaction policy external |
| WACZ | `.wacz` | `application/wacz`, `application/zip` | ZIP-packaged web archive with WARC, indexes, pages, metadata. | `archive-expand` + web-archive parse |
| RSS | `.rss`, `.xml` | `application/rss+xml`, `application/xml` | XML syndication feed. | `structured-parse` |
| Atom | `.atom`, `.xml` | `application/atom+xml` | XML feed/entry format. | `structured-parse` |
| JSON Feed | `.json` | `application/feed+json`, `application/json` | JSON syndication feed. | `structured-parse` |
| OPML | `.opml`, `.xml` | `text/x-opml`, `application/xml` | Outline/feed-subscription exchange. | `structured-parse` |
| RDF/XML | `.rdf`, `.owl`, `.xml` | `application/rdf+xml` | RDF graph in XML; OWL ontologies often use it. | `structured-parse` |
| Turtle / TriG / N-Triples / N-Quads | `.ttl`, `.trig`, `.nt`, `.nq` | `text/turtle`, `application/trig`, `application/n-triples`, `application/n-quads` | RDF graph/dataset text serializations. | `structured-parse` |
| JSON-LD | `.jsonld` | `application/ld+json` | Linked-data graph encoded as JSON. | `structured-parse`; bound remote-context fetching |
| RDFa/microdata | `.html`, `.xhtml` | HTML/XHTML types | Structured data embedded in web documents. | sanitized HTML + `structured-parse` |
| Web manifests/maps | `.webmanifest`, `.manifest`, `.map` | `application/manifest+json`, `application/json` | PWA metadata, cache manifest, or source map; extension is ambiguous. | `structured-parse` |

#### 3.5.3 XML grammars and interface/configuration descriptions

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| XML Schema / DTD / Relax NG | `.xsd`, `.dtd`, `.rng`, `.rnc` | `application/xml`, `application/relax-ng-compact-syntax` | XML grammars/declarations. | hardened `structured-parse`; no external entity fetch |
| XSLT / XSL-FO | `.xsl`, `.xslt`, `.fo`, `.xslfo` | `application/xslt+xml`, `application/xml` | XML transformations and paged formatting objects. | parse as data; sandbox any transformation |
| WSDL / SOAP | `.wsdl`, `.xsd`, `.xml` | `application/wsdl+xml`, `application/soap+xml` | Service/interface and SOAP message descriptions. | hardened `structured-parse` |
| OpenAPI / AsyncAPI | `.yaml`, `.yml`, `.json` | YAML/JSON types; `application/vnd.oai.openapi+json` | API contracts encoded in JSON/YAML. | schema-aware `structured-parse` |
| RAML / API Blueprint | `.raml`, `.apib` | `application/raml+yaml`, `text/vnd.apiblueprint` | API-description source. | `structured-parse` / `text-native` |
| GraphQL | `.graphql`, `.gql`, `.graphqls` | `application/graphql`, `text/plain` | Query/schema documents. | grammar-aware `structured-parse` |
| WADL / WSDL adjuncts | `.wadl`, `.xjb`, `.wsdd` | `application/xml` | XML web-service/API and binding/deployment descriptions. | hardened `structured-parse` |

#### 3.5.4 Configuration, manifests, build files, and infrastructure as code

| Format/family | Extension(s) or conventional names | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| INI family | `.ini`, `.cfg`, `.conf`, `.cnf`, `.prefs` | `text/plain` | Section/key configuration dialects with no single universal grammar. | `text-native` / dialect parse |
| Java properties | `.properties` | `text/plain` | Escaped key/value configuration and localization resources. | `text-native` / `structured-parse` |
| Environment files | `.env`, `.envrc`, `dotenv` names | `text/plain` | Shell-like key/value assignments; may contain secrets and expansions. | `text-native`; never source/execute |
| YAML/TOML/JSON config | `.yaml`, `.yml`, `.toml`, `.json` | corresponding structured types | Generic syntax whose schema is selected by filename/context. | `structured-parse`; schema-aware if known |
| XML configuration | `.config`, `.xml`, `.settings`, `.targets`, `.props` | `application/xml` | Application/build configuration using profile-specific XML roots. | hardened `structured-parse` |
| Windows Registry export | `.reg` | `text/plain` | Registry keys/values in a UTF-16/ANSI text serialization. | `text-native` / `structured-parse`; never import |
| Windows setup/config | `.inf`, `.ini`, `.pol`, `.admx`, `.adml` | `text/plain`, `application/xml`, `application/octet-stream` | Driver setup, policy, and administrative templates. | text/XML parse; binary policy opaque |
| Apple property lists | `.plist`, `.strings`, `.stringsdict` | `application/x-plist`, `application/xml`, `text/plain` | XML, binary plist, or localization dictionary files. | `structured-parse` after binary/XML detection |
| freedesktop entries | `.desktop`, `.directory`, `.service` | `application/x-desktop`, `text/plain` | Desktop launcher/metadata or D-Bus service descriptors. | `structured-parse`; never launch |
| systemd units | `.service`, `.socket`, `.timer`, `.mount`, `.path`, `.target`, `.slice` | `text/plain` | INI-like service/unit definitions. | `structured-parse`; never activate |
| Unix system text | `fstab`, `hosts`, `resolv.conf`, `crontab`, `.forward`, `.mailcap`, `.netrc` | `text/plain` | Conventional operating-system configuration. | `text-native` / profile parse; treat secrets as sensitive |
| Web-server config | `.htaccess`, `.htpasswd`, `nginx.conf`, `httpd.conf` | `text/plain` | Server rules and credentials/configuration. | `text-native`; never deploy; sensitive |
| Dockerfile/Containerfile | `Dockerfile`, `Containerfile`, `.dockerfile` | `text/x-dockerfile` | Image build instructions capable of executing arbitrary commands. | `text-native`; `dangerous/quarantine` if building |
| Compose / Kubernetes | `compose.yaml`, `docker-compose.yml`, `.yaml`, `.yml` | `application/yaml` | Container/workload resource graphs and commands/secrets. | schema-aware `structured-parse`; never apply |
| Terraform / HCL | `.tf`, `.tfvars`, `.hcl`, `.tfstate`, `.tfplan` | `application/x-hcl`, `application/json`, `application/octet-stream` | Infrastructure source/variables/state and opaque plan artifacts. | source/state parse; plan opaque; never apply |
| CloudFormation / ARM / Bicep | `.yaml`, `.yml`, `.json`, `.template`, `.bicep` | YAML/JSON types, `text/plain` | Cloud infrastructure templates/source. | `structured-parse` / `text-native`; never deploy |
| Ansible / Salt / Puppet / Chef | `.yaml`, `.yml`, `.sls`, `.pp`, `.rb` | YAML/text source types | Automation declarations and executable recipes. | text/schema parse; never execute |
| Nix / Guix | `.nix`, `.scm` | `text/plain` | Functional package/system definitions. | `text-native`; never evaluate untrusted inputs |
| Dhall / CUE / Jsonnet | `.dhall`, `.cue`, `.jsonnet`, `.libsonnet` | `text/plain`, `application/jsonnet` | Typed/programmatic configuration languages. | `text-native`; sandbox evaluation if required |
| Bazel/Starlark | `BUILD`, `BUILD.bazel`, `WORKSPACE`, `MODULE.bazel`, `.bzl` | `text/plain` | Build graph definitions in Starlark. | `text-native`; never execute during ingest |
| Make/build systems | `Makefile`, `.mk`, `.mak`, `CMakeLists.txt`, `.cmake`, `.ninja`, `meson.build` | `text/x-makefile`, `text/plain` | Build scripts/configuration that can invoke arbitrary commands. | `text-native`; never execute |
| CI/workflow definitions | `.yaml`, `.yml`, `.json`, `Jenkinsfile`, `.gitlab-ci.yml` | YAML/JSON/text | CI job graph, scripts, actions, and secrets references. | schema-aware parse; never run |
| Package manifests | `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`, `Gemfile`, `composer.json`, `.csproj`, `.fsproj`, `.vbproj` | JSON/TOML/XML/text types | Dependency/build metadata; some fields invoke hooks/plugins. | `structured-parse`; no dependency resolution or hooks |
| Lockfiles | `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `Cargo.lock`, `poetry.lock`, `Pipfile.lock`, `Gemfile.lock`, `composer.lock`, `go.sum` | JSON/YAML/TOML/text | Resolved dependency graphs and integrity/version pins. | `structured-parse` / `text-native` |
| IDE/workspace metadata | `.sln`, `.suo`, `.user`, `.vcxproj`, `.idea`, `.iml`, `.code-workspace`, `.project`, `.classpath` | text/XML/JSON or binary | Editor/build workspace and user-state files. | text/XML/JSON parse; user/binary state opaque |

#### 3.5.5 Source code, scripts, templates, bytecode, and diffs

These are deliberately grouped by language family: listing every generated suffix or framework
would add noise without changing ingest behavior. Text source is valuable memory input, but
**ingest must never compile, import, evaluate, render with unbounded includes, or run it**.

| Language/artifact family | Extension(s) or names | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| C / C++ / Objective-C | `.c`, `.h`, `.i`, `.cc`, `.cp`, `.cpp`, `.cxx`, `.c++`, `.hh`, `.hpp`, `.hxx`, `.ipp`, `.tpp`, `.m`, `.mm` | `text/x-c`, `text/x-c++`, `text/x-objective-c` | Native-language source, headers, and preprocessed/template files; `.m` is ambiguous. | `text-native` + language parse |
| C# / .NET source | `.cs`, `.csx`, `.razor`, `.cshtml`, `.fs`, `.fsx`, `.fsi`, `.vb` | `text/x-csharp`, `text/x-fsharp`, `text/x-vb` | C#, Razor, F#, and Visual Basic source/scripts. | `text-native` |
| Java/JVM source | `.java`, `.kt`, `.kts`, `.scala`, `.sc`, `.groovy`, `.gvy`, `.gy`, `.gsh`, `.clj`, `.cljs`, `.cljc`, `.edn` | `text/x-java-source`, `text/x-kotlin`, `text/x-scala`, `text/x-groovy`, `application/edn` | JVM-language source and Clojure data/code. | `text-native`; EDN safe parse only |
| JavaScript / TypeScript | `.js`, `.mjs`, `.cjs`, `.jsx`, `.ts`, `.mts`, `.cts`, `.tsx` | `text/javascript`, `application/javascript`, `text/typescript` | ECMAScript/TypeScript modules and JSX/TSX; `.ts` collides with MPEG transport stream. | `text-native`; never execute |
| Web styles/components | `.css`, `.scss`, `.sass`, `.less`, `.styl`, `.vue`, `.svelte`, `.astro` | `text/css`, `text/x-scss`, `text/plain` | Stylesheets/preprocessors and component source mixing markup/script/style. | `text-native`; grammar-aware split optional |
| Templates | `.jinja`, `.jinja2`, `.j2`, `.mustache`, `.hbs`, `.handlebars`, `.twig`, `.liquid`, `.ejs`, `.erb`, `.haml`, `.pug`, `.jade`, `.vm`, `.ftl` | `text/plain`, sometimes `text/html` | Executable/expanding template languages. | `text-native`; never render untrusted templates |
| Python | `.py`, `.pyw`, `.pyi`, `.pyx`, `.pxd`, `.pxi` | `text/x-python` | Python, typing stubs, and Cython source. | `text-native`; never import/execute |
| Ruby | `.rb`, `.rake`, `.gemspec`, `Rakefile` | `text/x-ruby` | Ruby source, build tasks, and gem specifications. | `text-native`; never evaluate |
| PHP | `.php`, `.php3`, `.php4`, `.php5`, `.phtml`, `.phps` | `application/x-httpd-php`, `text/x-php` | PHP source/templates. | `text-native`; never execute |
| Perl / Raku | `.pl`, `.pm`, `.pod`, `.t`, `.raku`, `.rakumod`, `.rakutest` | `text/x-perl` | Perl/Raku code, modules, tests, and documentation; `.pl` can be Prolog. | `text-native` |
| Lua / Tcl | `.lua`, `.tcl`, `.tk`, `.itcl` | `text/x-lua`, `application/x-tcl` | Embeddable scripting-language source. | `text-native` |
| Shell | `.sh`, `.bash`, `.zsh`, `.ksh`, `.fish`, `.csh`, `.tcsh`, extensionless scripts | `application/x-sh`, `text/x-shellscript` | Unix shell scripts and profiles. | `text-native`; never source/execute |
| PowerShell / Windows command | `.ps1`, `.psm1`, `.psd1`, `.bat`, `.cmd`, `.btm`, `.wsf`, `.wsh`, `.vbs`, `.vbe`, `.js`, `.jse` | `text/plain`, `application/x-powershell`, `application/x-msdos-program` | Windows automation scripts; WSF is XML and encoded variants exist. | `text-native`; `dangerous/quarantine` |
| Go / Rust / Swift / Zig | `.go`, `.rs`, `.swift`, `.zig`, `.zon` | `text/x-go`, `text/x-rust`, `text/x-swift`, `text/plain` | Modern compiled-language source and Zig object notation. | `text-native` |
| Dart / Flutter | `.dart` | `text/x-dart` | Dart application source. | `text-native` |
| Julia / R / MATLAB | `.jl`, `.r`, `.R`, `.Rprofile`, `.m` | `text/x-julia`, `text/x-r`, `text/x-matlab` | Scientific-language source; `.m` needs MATLAB/Objective-C/Wolfram detection. | `text-native` |
| Wolfram Language | `.wl`, `.wls`, `.m` | `application/vnd.wolfram.mathematica`, `text/plain` | Wolfram package/script source. | `text-native` after detection |
| Lisp / Scheme | `.lisp`, `.lsp`, `.cl`, `.el`, `.scm`, `.ss`, `.sld`, `.rkt`, `.rktd` | `text/x-common-lisp`, `text/x-scheme` | Lisp-family code/data. | `text-native`; never evaluate |
| Functional languages | `.hs`, `.lhs`, `.ml`, `.mli`, `.re`, `.rei`, `.elm`, `.idr`, `.agda` | `text/x-haskell`, `text/x-ocaml`, `text/plain` | Haskell, OCaml/Reason, Elm, Idris, and Agda sources. | `text-native` |
| Erlang / Elixir | `.erl`, `.hrl`, `.ex`, `.exs`, `.eex`, `.leex`, `.heex` | `text/x-erlang`, `text/x-elixir` | BEAM-language source/templates. | `text-native` |
| Pascal / Delphi / Ada | `.pas`, `.pp`, `.dpr`, `.dpk`, `.dfm`, `.lfm`, `.adb`, `.ads` | `text/x-pascal`, `text/x-ada` | Source plus textual/binary form resources. | source `text-native`; binary forms detect/opaque |
| Fortran | `.f`, `.for`, `.ftn`, `.f77`, `.f90`, `.f95`, `.f03`, `.f08`, `.f18` | `text/x-fortran` | Fixed/free-form Fortran source. | `text-native` |
| COBOL / mainframe source | `.cob`, `.cbl`, `.cpy`, `.jcl`, `.pli`, `.rexx`, `.rex` | `text/x-cobol`, `text/plain` | COBOL/copybooks, JCL, PL/I, and REXX source. | `text-native`; encoding/record-format aware |
| SQL and procedural SQL | `.sql`, `.ddl`, `.dml`, `.psql`, `.plsql`, `.pls`, `.pks`, `.pkb` | `application/sql`, `text/x-sql` | Database definitions, queries, dumps, and stored procedures. | `text-native` / SQL parse; never execute |
| Prolog / logic | `.pro`, `.prolog`, `.pl`, `.lp` | `text/x-prolog` | Logic-program source; extensions collide. | `text-native` after detection |
| Assembly | `.asm`, `.s`, `.S`, `.inc`, `.nasm`, `.masm` | `text/x-asm` | Assembly and include source; `.s` is ambiguous. | `text-native` |
| HDL / EDA source | `.v`, `.vh`, `.sv`, `.svh`, `.vhd`, `.vhdl`, `.ucf`, `.xdc`, `.sdc` | `text/x-verilog`, `text/x-vhdl`, `text/plain` | Hardware-description and timing/constraint source. | `text-native` |
| Mobile/embedded DSLs | `.ino`, `.pde`, `.spin`, `.bas`, `.pb`, `.lst` | `text/plain` | Arduino/Processing/Spin/BASIC source and listings; `.pb` ambiguous. | `text-native` after detection |
| Smart-contract source | `.sol`, `.vy`, `.move`, `.clar` | `text/plain` | Solidity, Vyper, Move, or Clarity code. | `text-native`; never compile/deploy |
| Grammar/IDL source | `.g`, `.g4`, `.y`, `.yy`, `.yacc`, `.l`, `.lex`, `.peg`, `.proto`, `.thrift`, `.avdl`, `.idl` | `text/plain` | Parser grammars and interface/schema definitions. | `text-native` / grammar parse |
| Patches/diffs | `.diff`, `.patch`, `.rej` | `text/x-diff` | Line-oriented change sets and rejected hunks. | `text-native` + diff parse |
| Source maps | `.map` | `application/json` | Mapping from generated code to sources; `.map` is highly ambiguous. | profile-aware `structured-parse` |
| JVM bytecode/archive | `.class`, `.jar`, `.war`, `.ear`, `.jmod` | `application/java-vm`, `application/java-archive` | Executable bytecode or ZIP packages with resources/manifests. | `dangerous/quarantine`; static metadata/decompile only |
| .NET assemblies/symbols | `.dll`, `.exe`, `.winmd`, `.netmodule`, `.pdb` | `application/vnd.microsoft.portable-executable`, `application/octet-stream` | PE/CLI executable metadata/code and debug symbols. | `dangerous/quarantine`; static metadata only |
| Python/Ruby/Lua bytecode | `.pyc`, `.pyo`, `.rbc`, `.luac` | `application/x-python-code`, `application/octet-stream` | Runtime/version-specific executable bytecode. | `dangerous/quarantine`; metadata/opaque |
| WebAssembly | `.wasm`, `.wat`, `.wast` | `application/wasm`, `text/plain` | Executable WebAssembly binary or text format. | text forms `text-native`; binary quarantine/static parse |
| LLVM/object intermediates | `.ll`, `.bc`, `.o`, `.obj`, `.a`, `.lib` | `text/plain`, `application/octet-stream` | LLVM IR/bitcode, relocatable objects, and static libraries. | `.ll` text; others quarantine/static metadata |

#### 3.5.6 Notebooks, computational documents, logs, and traces

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| Jupyter notebook | `.ipynb` | `application/x-ipynb+json`, `application/json` | JSON cells, code, Markdown, outputs, attachments, and metadata. | `structured-parse`; sanitize outputs; never run cells |
| Quarto / R Markdown | `.qmd`, `.rmd`, `.Rmd` | `text/markdown` | Markdown with executable code cells/options. | `text-native` / AST parse; never execute |
| MyST notebook/Markdown | `.md`, `.myst` | `text/markdown` | Markdown directives and optional notebook metadata. | `text-native`; disable directives that execute/fetch |
| Observable notebook | `.ojs`, `.js`, `.tgz` export | `text/javascript`, archive types | Reactive JavaScript notebook/source or project export. | `text-native`; archive recurse; never execute |
| Zeppelin notebook | `.zpln`, `.json` | `application/json` | JSON notebook paragraphs and interpreter metadata. | `structured-parse`; never execute |
| Mathematica notebook | `.nb`, `.cdf`, `.nbp` | `application/vnd.wolfram.mathematica`, `application/vnd.wolfram.cdf` | Expression-based interactive notebook/demonstration. | specialist `document-convert`; never evaluate dynamic content |
| Maple worksheet | `.mw`, `.mws` | `application/maple`, `application/octet-stream` | XML/binary computational worksheet. | specialist convert; never execute |
| MATLAB live script | `.mlx` | `application/vnd.mathworks.matlab.live-script`, often `application/zip` | ZIP/XML computational document with code/output/media. | `structured-parse`; never execute |
| Sage worksheet | `.sagews`, `.sage` | `text/plain` | Computational worksheet/source. | `text-native`; never execute |
| Generic logs | `.log`, `.out`, `.err`, `.trace`, `.txt` | `text/plain` | Line-oriented application/system output, possibly multiline/rotated. | streaming `text-native` + optional profile parse |
| Structured logs | `.jsonl`, `.ndjson`, `.logfmt` | NDJSON/text types | Record-oriented structured events. | streaming `structured-parse` |
| Syslog/journal export | `.log`, `.journal`, `.journal~` | `text/plain`, `application/vnd.systemd.journal` | Text syslog or binary systemd journal. | text parse; binary specialist read-only parse |
| Windows event logs | `.evtx`, `.evt`, `.etl` | `application/octet-stream` | Binary Windows events and event-trace logs. | specialist `structured-parse` |
| Network capture | `.pcap`, `.cap`, `.pcapng` | `application/vnd.tcpdump.pcap`, `application/x-pcapng` | Captured packets with timestamps/interfaces; payloads may contain nested data/secrets. | specialist bounded `structured-parse`; quarantine malformed captures |
| Chrome/Perfetto trace | `.json`, `.json.gz`, `.pftrace`, `.perfetto-trace` | `application/json`, `application/octet-stream` | Event trace for browser/system performance. | `structured-parse` with event/sample limits |
| Linux perf / ftrace | `perf.data`, `.data`, `.trace`, `trace.dat` | `application/octet-stream`, `text/plain` | Profiling samples or kernel trace events. | specialist parse; `.data` detect carefully |
| OpenTelemetry exports | `.json`, `.jsonl`, `.proto`, `.pb` | `application/json`, `application/x-protobuf` | Trace/metric/log records in JSON or protobuf. | schema-aware `structured-parse` |
| Crash/core dump | `.dmp`, `.mdmp`, `.core`, `core`, `.crash` | `application/octet-stream` | Process memory/register dump, often containing secrets. | `binary-opaque`; `dangerous/quarantine`; restricted access |

### 3.6 Images: raster, vector, layered, raw camera, medical, and GIS

#### 3.6.1 Common raster and exchange images

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| JPEG/JFIF/Exif | `.jpg`, `.jpeg`, `.jpe`, `.jfif`, `.jif` | `image/jpeg` | Lossy/lossless-family still image with optional Exif/IPTC/XMP metadata. | `media-transcribe`; metadata + OCR/VLM; raw retain |
| JPEG 2000 | `.jp2`, `.j2k`, `.jpf`, `.jpx`, `.jpm`, `.mj2` | `image/jp2`, `image/jpx`, `image/jpm`, `video/mj2` | Wavelet codestream and still/compound/motion containers. | image/media derivation after profile detection |
| JPEG XL | `.jxl` | `image/jxl` | Modern still/animation image codec/container. | `media-transcribe` |
| JPEG XR / HD Photo | `.jxr`, `.hdp`, `.wdp` | `image/jxr`, `image/vnd.ms-photo` | Microsoft-origin high-dynamic-range image format. | `media-transcribe` |
| PNG / APNG | `.png`, `.apng` | `image/png`, `image/apng` | Lossless raster; APNG is animated. | still `media-transcribe`; animation treat as short video |
| GIF | `.gif` | `image/gif` | Palette raster, often animated. | still or short-video `media-transcribe` |
| WebP | `.webp` | `image/webp` | Lossy/lossless still or animated image. | still or short-video `media-transcribe` |
| HEIF / HEIC | `.heif`, `.heic`, `.hif` | `image/heif`, `image/heic`, `image/heif-sequence`, `image/heic-sequence` | ISO BMFF image collection/sequence commonly using HEVC. | `media-transcribe`; inspect items/tracks/metadata |
| AVIF | `.avif`, `.avifs` | `image/avif`, `image/avif-sequence` | AV1 image/sequence in HEIF/ISO BMFF. | still or short-video `media-transcribe` |
| TIFF / BigTIFF | `.tif`, `.tiff`, `.btf`, `.tf8` | `image/tiff`, `image/tiff-fx` | Tagged, multi-page/multi-image raster container with many compressions/profiles. | `media-transcribe`; page/image-aware OCR |
| BMP / DIB | `.bmp`, `.dib`, `.rle` | `image/bmp`, `image/x-ms-bmp` | Windows bitmap variants. | `media-transcribe` |
| Netpbm | `.pbm`, `.pgm`, `.ppm`, `.pnm`, `.pam`, `.pfm` | `image/x-portable-bitmap`, `image/x-portable-graymap`, `image/x-portable-pixmap`, `image/x-portable-anymap` | Simple portable bitmap/graymap/pixmap/anymap and float map. | `media-transcribe` |
| TGA | `.tga`, `.targa`, `.icb`, `.vda`, `.vst` | `image/x-tga`, `image/x-targa` | Truevision raster; aliases collide with other `.vst` uses. | `media-transcribe` |
| PCX / DCX | `.pcx`, `.dcx` | `image/vnd.zbrush.pcx`, `image/x-pcx`, `image/x-dcx` | ZSoft raster and multi-page fax container. | `media-transcribe` |
| ICO / CUR / ANI | `.ico`, `.cur`, `.ani` | `image/vnd.microsoft.icon`, `image/x-icon`, `application/x-navi-animation` | Icon/cursor containers and animated cursor. | `media-transcribe`; enumerate frames/sizes |
| XBM / XPM | `.xbm`, `.xpm` | `image/x-xbitmap`, `image/x-xpixmap` | C-source-like monochrome/palette X11 images. | safe parse + `media-transcribe` |
| Sun/SGI raster | `.ras`, `.sun`, `.rgb`, `.rgba`, `.sgi`, `.bw` | `image/x-cmu-raster`, `image/x-sgi` | Legacy workstation raster families. | `media-transcribe` |
| Kodak Photo CD | `.pcd` | `image/x-photo-cd` | Multi-resolution Photo CD image. | `media-transcribe` |
| QOI | `.qoi` | `image/qoi` | Quite OK Image lossless raster. | `media-transcribe` |
| FLIF / BPG | `.flif`, `.bpg` | `image/flif`, `image/bpg` (largely unregistered) | Niche modern compressed still/animation formats. | `media-transcribe` if decoder available; else opaque |
| HDR/RGBE / Radiance | `.hdr`, `.rgbe`, `.pic` | `image/vnd.radiance` | High-dynamic-range environment/image map. | `media-transcribe` with tone-map preview |
| OpenEXR | `.exr`, `.sxr`, `.mxr` | `image/x-exr` | Multi-channel high-dynamic-range image/container. | `media-transcribe`; enumerate layers/channels |
| DPX / Cineon | `.dpx`, `.cin` | `image/dpx`, `image/cineon` | Professional film/scanner frame formats. | `media-transcribe`; metadata + preview |
| FITS as image | `.fits`, `.fit`, `.fts` | `image/fits`, `application/fits` | Scientific multidimensional arrays/tables often visualized as images. | specialist `structured-parse` + rendered preview |

#### 3.6.2 Vector, metafile, and layered/editor images

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| SVG / compressed SVG | `.svg`, `.svgz` | `image/svg+xml`, `image/svg+xml-compressed` | XML vector graphic with text, links, filters, animation, and potentially scripts/external references. | hardened `structured-parse` + safe render/OCR; quarantine active content |
| PDF/EPS/AI vector art | `.pdf`, `.eps`, `.ai` | PDF/PostScript types | Page-description containers commonly used for vector artwork. | sandboxed render + text/object extraction |
| Windows metafiles | `.wmf`, `.emf`, `.wmz`, `.emz` | `image/wmf`, `image/emf`, `application/x-msmetafile` | Windows drawing-command metafiles, optionally compressed. | sandboxed render + `media-transcribe` |
| CGM | `.cgm` | `image/cgm` | Computer Graphics Metafile, binary/character/text encodings. | specialist safe render / parse |
| CorelDRAW | `.cdr`, `.cdt`, `.cmx`, `.ccx` | `application/vnd.corel-draw`, `image/x-coreldraw` | Proprietary vector drawing/template/exchange/clip-art formats. | specialist `document-convert` |
| Affinity | `.afdesign`, `.afphoto` | `application/octet-stream` | Proprietary vector/design and photo projects. | `binary-opaque` unless vendor/specialist export |
| Photoshop | `.psd`, `.psb` | `image/vnd.adobe.photoshop` | Layered raster document; PSB is large-document variant. | parse layers/text/metadata + preview; raw retain |
| GIMP | `.xcf`, `.xcf.gz`, `.xcf.bz2` | `image/x-xcf` | Layered GIMP project, optionally compressed. | parse layers/text + preview |
| Krita / OpenRaster | `.kra`, `.ora` | `application/x-krita`, `image/openraster` | ZIP-packaged layered artwork. | safe package parse + preview |
| Clip Studio / PaintTool SAI | `.clip`, `.lip`, `.sai`, `.sai2` | `application/octet-stream` | Proprietary layered illustration projects. | specialist preview/metadata or opaque |
| PaintShop Pro | `.psp`, `.pspimage`, `.pspframe` | `image/x-paintshoppro` | Layered raster/image-frame formats. | specialist convert |
| Procreate | `.procreate` | `application/zip`, `application/octet-stream` | Packaged layered artwork/time-lapse metadata. | safe archive inspect + specialist preview |
| Sketch | `.sketch` | `application/zip`, `application/vnd.sketch` | ZIP/JSON design document with pages, layers, symbols, assets. | `archive-expand` + `structured-parse` + preview |
| Figma local/export package | `.fig` | `application/octet-stream` | Figma design export/local binary/package, version-dependent. | specialist parse; otherwise opaque |
| Canvas/legacy draw | `.cvx`, `.cvi`, `.gem`, `.wpg` | `application/octet-stream`, `image/vnd.wordperfect` | Legacy drawing/metafile formats. | specialist convert or opaque |

#### 3.6.3 Camera raw and photographic sidecars

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| Adobe Digital Negative | `.dng` | `image/x-adobe-dng` | TIFF/EP-based camera raw container, sometimes with previews/original raw. | specialist demosaic + metadata + `media-transcribe` |
| Canon raw | `.cr2`, `.cr3`, `.crw` | `image/x-canon-cr2`, `image/x-canon-cr3`, `image/x-canon-crw` | Canon TIFF/ISO-BMFF/legacy camera raw generations. | specialist raw decode + metadata |
| Nikon raw | `.nef`, `.nrw` | `image/x-nikon-nef`, `image/x-nikon-nrw` | Nikon camera raw. | specialist raw decode + metadata |
| Sony raw | `.arw`, `.srf`, `.sr2` | `image/x-sony-arw`, `image/x-sony-srf`, `image/x-sony-sr2` | Sony camera raw generations. | specialist raw decode + metadata |
| Fujifilm raw | `.raf` | `image/x-fuji-raf` | Fujifilm camera raw. | specialist raw decode + metadata |
| Olympus/OM raw | `.orf`, `.ori` | `image/x-olympus-orf` | Olympus camera raw. | specialist raw decode + metadata |
| Panasonic raw | `.rw2`, `.raw` | `image/x-panasonic-rw2`, `image/x-panasonic-raw` | Panasonic camera raw; `.raw` is extremely ambiguous. | specialist decode after strong detection |
| Pentax/Samsung raw | `.pef`, `.ptx`, `.dcr` | `image/x-pentax-pef`, `image/x-pentax-ptx`, `image/x-kodak-dcr` | Camera raw variants. | specialist raw decode + metadata |
| Hasselblad/Phase One raw | `.3fr`, `.fff`, `.iiq`, `.cap` | vendor/unregistered image types | Medium-format camera raw. | specialist raw decode |
| Leica/Kodak/Epson raw | `.rwl`, `.kdc`, `.dcs`, `.dcr`, `.erf` | vendor/unregistered image types | Camera-vendor raw formats. | specialist raw decode |
| Sigma raw | `.x3f` | `image/x-sigma-x3f` | Foveon sensor raw. | specialist raw decode |
| RawTherapee/Darktable sidecars | `.pp3`, `.xmp` | `text/plain`, `application/rdf+xml` | Non-destructive edit metadata referencing a separate raw/image. | `structured-parse`; preserve relationship to source image |

#### 3.6.4 Medical, microscopy, GIS, and remote-sensing imagery

| Format/family | Extension(s) / dataset shape | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| DICOM Part 10 | `.dcm`, `.dicom`, sometimes extensionless | `application/dicom` | Medical image/waveform/report objects with patient/study metadata and many pixel encodings. | specialist `structured-parse` + de-identification policy + media derivation |
| DICOMDIR | `DICOMDIR` plus object tree | `application/dicom` | Index and media-file set for DICOM interchange. | dataset specialist parse; preserve hierarchy |
| NIfTI / Analyze | `.nii`, `.nii.gz`, `.hdr` + `.img` | `application/x-nifti`, `application/gzip`, `application/octet-stream` | Neuroimaging volumes; Analyze uses paired header/image. | specialist volume metadata/rendering; pair-aware |
| MINC | `.mnc` | `application/x-minc` | Medical imaging volume built on netCDF/HDF5 generations. | specialist `structured-parse` + previews |
| MGH/MGZ | `.mgh`, `.mgz` | `application/octet-stream`, `application/gzip` | FreeSurfer MRI volume. | specialist parse/render |
| Whole-slide pathology | `.svs`, `.ndpi`, `.scn`, `.mrxs`, `.bif`, `.vms`, `.vmu` | TIFF/vendor types | Gigapixel pyramidal microscopy images and companion data. | specialist tiled `media-transcribe`; no full raster expansion |
| Generic microscopy | `.czi`, `.lif`, `.lsm`, `.oib`, `.oif`, `.vsi`, `.ims`, `.nd2` | vendor/unregistered types | Multichannel/time/z-stack proprietary microscope datasets. | specialist metadata + sampled render; raw retain |
| OME-TIFF / OME-Zarr | `.ome.tif`, `.ome.tiff`, `.ome.zarr` directory | `image/tiff`; no dependable common type for a Zarr directory | Open microscopy pixels plus OME metadata, file or chunked dataset. | specialist `structured-parse` + previews |
| GeoTIFF / COG | `.tif`, `.tiff` | `image/tiff`, `image/tiff; application=geotiff` (conventional) | TIFF carrying georeferencing; COG is an access-optimized GeoTIFF profile. | GIS `structured-parse` + map preview |
| MrSID / ECW | `.sid`, `.ecw` | `image/x-mrsid-image`, `image/ecw` | Wavelet-compressed geospatial raster. | specialist GIS decode/preview |
| ERDAS Imagine | `.img`, `.ige`, `.rrd` | `image/x-erdas-hfa`, generic binary | Geospatial raster plus external overview/large-data sidecars. | dataset specialist parse |
| ENVI raster | `.hdr` + `.dat`/`.img`/`.raw` | `application/octet-stream`, `text/plain` header | Header-labelled raw remote-sensing raster. | pair-aware specialist parse |
| Arc/Info grids / ASCII grids | `.adf` directory, `.asc`, `.grd`, `.flt` + `.hdr` | `text/plain`, `application/octet-stream` | Esri binary grid dataset or ASCII/binary elevation grids. | dataset GIS parse |
| NITF / NSIF | `.ntf`, `.nitf`, `.nsf` | `application/vnd.nitf`, `image/nitf` | Imagery/intelligence container with images, text, and metadata. | specialist `structured-parse`; security-label aware |
| BSB/KAP nautical charts | `.kap`, `.bsb` | `image/x-bsb` | Raster nautical chart plus calibration/metadata. | specialist GIS parse + preview |
| Satellite product packages | `.safe` directory/ZIP, `.dim` + `.data`, `.jp2` sets | generic XML/ZIP/image types | Sentinel SAFE, DIMAP, and similar multi-file imagery products. | dataset `archive-expand` + GIS specialist parse |
| Terrain/elevation | `.hgt`, `.dt0`, `.dt1`, `.dt2`, `.dem`, `.bil` + `.hdr` | `application/octet-stream` | SRTM, DTED, DEM, and band-interleaved raster elevations. | specialist GIS parse + metadata/preview |

### 3.7 Audio: containers, elementary streams/codecs, music data, and projects

#### 3.7.1 Audio files and containers

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| WAVE / BWF / RF64 | `.wav`, `.wave`, `.bwf`, `.rf64` | `audio/wav`, `audio/vnd.wave`, `audio/x-wav` | RIFF-family audio container; Broadcast Wave adds production metadata and RF64 supports large files. | `media-transcribe`; inspect chunks/metadata |
| AIFF / AIFC | `.aif`, `.aiff`, `.aifc` | `audio/aiff`, `audio/x-aiff` | Apple/SGI chunked PCM or compressed audio container. | `media-transcribe` |
| Core Audio Format | `.caf` | `audio/x-caf` | Apple extensible audio container. | `media-transcribe` |
| AU / SND | `.au`, `.snd` | `audio/basic`, `audio/x-au` | Sun/NeXT audio; `.snd` is also a generic legacy extension. | `media-transcribe` after detection |
| MP3 | `.mp3`, `.mpga`, `.mpa` | `audio/mpeg` | MPEG-1/2 Layer III elementary audio with optional ID3/APEv2 tags. | `media-transcribe` |
| MPEG Layers I/II | `.mp1`, `.mp2`, `.mpa` | `audio/mpeg` | MPEG audio Layer I or II bitstreams. | `media-transcribe` |
| AAC elementary | `.aac`, `.adts`, `.adif` | `audio/aac` | AAC in ADTS/ADIF elementary framing; no general-purpose file metadata model. | `media-transcribe` |
| MPEG-4 audio/audiobook | `.m4a`, `.m4b`, `.m4p`, `.mp4` | `audio/mp4`, `application/mp4` | ISO BMFF audio, often AAC or ALAC; M4B chapters, M4P historically protected. | `media-transcribe`; protected files opaque without authorized decrypt |
| Ogg audio | `.ogg`, `.oga`, `.ogx`, `.spx`, `.opus` | `audio/ogg`, `audio/opus` | Ogg container commonly carrying Vorbis, Opus, Speex, FLAC, or multiplexed streams. | `media-transcribe`; enumerate streams |
| FLAC | `.flac` | `audio/flac` | Free Lossless Audio Codec native framing with metadata blocks. | `media-transcribe` |
| Matroska/WebM audio | `.mka`, `.weba`, `.webm` | `audio/x-matroska`, `audio/webm` | Matroska/WebM containers with one or more audio/subtitle tracks. | `media-transcribe`; track-aware |
| ASF / WMA | `.wma`, `.asf`, `.wax` | `audio/x-ms-wma`, `video/x-ms-asf` | ASF container commonly carrying Windows Media Audio; `.asf` may include video. | `media-transcribe` after track inspection |
| RealAudio | `.ra`, `.ram`, `.rm`, `.rmm` | `audio/vnd.rn-realaudio`, `application/vnd.rn-realmedia` | RealNetworks audio/media or pointer/playlist. | `media-transcribe`; distinguish pointer text from media |
| AMR | `.amr`, `.awb` | `audio/amr`, `audio/amr-wb` | Adaptive Multi-Rate narrowband/wideband speech. | `media-transcribe` |
| AC-3 / E-AC-3 | `.ac3`, `.eac3`, `.ec3` | `audio/ac3`, `audio/eac3` | Dolby Digital elementary streams. | `media-transcribe` |
| AC-4 | `.ac4` | `audio/ac4` | Dolby AC-4 elementary/transport audio. | `media-transcribe` if decoder available |
| DTS family | `.dts`, `.dtshd`, `.dtsma` | `audio/vnd.dts`, `audio/vnd.dts.hd` | DTS core/high-resolution/master audio bitstreams. | `media-transcribe` |
| Dolby TrueHD / MLP | `.thd`, `.truehd`, `.mlp` | `audio/true-hd`, `audio/vnd.dolby.mlp` | Lossless multichannel elementary streams. | `media-transcribe` |
| Monkey's Audio | `.ape`, `.apl` | `audio/ape`, `audio/x-ape` | Lossless audio plus optional link metadata. | `media-transcribe` |
| WavPack | `.wv`, `.wvc` | `audio/wavpack` | Lossless/hybrid audio and optional correction file. | pair-aware `media-transcribe` |
| Musepack | `.mpc`, `.mpp`, `.mp+` | `audio/musepack` | Perceptual audio codec; `.mpp` collides with Microsoft Project. | `media-transcribe` after detection |
| TTA / TAK | `.tta`, `.tak` | `audio/x-tta`, `audio/x-tak` | Niche lossless audio codecs. | `media-transcribe` if decoder available |
| OptimFROG / LA / Shorten | `.ofr`, `.ofs`, `.la`, `.shn` | vendor/unregistered audio types | Legacy/niche lossless formats. | specialist decode; otherwise `binary-opaque` |
| DSD | `.dsf`, `.dff`, `.wsd` | `audio/dsd`, `audio/x-dsf`, `audio/x-dff` | Direct Stream Digital in DSF/DSDIFF/WSD containers. | specialist decode/downsample + metadata |
| Creative VOC | `.voc` | `audio/x-voc` | Creative Voice chunked audio. | `media-transcribe` |
| GSM / telephony bitstreams | `.gsm`, `.g722`, `.g723`, `.g726`, `.g729` | `audio/gsm`, `audio/G722`, codec-specific RTP types | Raw/loosely framed telephony audio; parameters may be external. | specialist decode if parameters known; else opaque |
| Audible | `.aa`, `.aax`, `.aaxc` | `audio/audible`, `audio/x-audible` | Audible audiobook containers, normally DRM-protected. | metadata only / opaque unless authorized decode |

#### 3.7.2 Scores, MIDI, trackers, chiptunes, playlists, and cue data

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| Standard MIDI / karaoke | `.mid`, `.midi`, `.kar`, `.rmi` | `audio/midi`, `audio/x-midi` | Musical event/control data, not recorded sound; KAR adds lyrics. | `structured-parse`; render/transcribe score/lyrics optionally |
| MusicXML / compressed MXL | `.musicxml`, `.mxl`, `.xml` | `application/vnd.recordare.musicxml+xml`, `application/vnd.recordare.musicxml` | Symbolic musical score in XML or ZIP package. | `structured-parse` + score render |
| MEI | `.mei` | `application/mei+xml` | Music Encoding Initiative XML score/document. | `structured-parse` |
| ABC / LilyPond | `.abc`, `.ly`, `.ily` | `text/vnd.abc`, `text/x-lilypond` | Text-native music notation/source. | `text-native`; sandbox renderer |
| MuseScore | `.mscz`, `.mscx` | `application/vnd.musescore`, `application/x-musescore+xml` | Compressed package or XML score. | `archive-expand`/`structured-parse` + render |
| Finale | `.mus`, `.musx`, `.etf` | `application/vnd.musician`, `application/octet-stream` | Proprietary score and interchange generations; `.mus` is ambiguous. | specialist parse/render |
| Sibelius | `.sib` | `application/x-sibelius-score` | Proprietary score. | specialist parse/render |
| NIFF / SCORE | `.nif`, `.niff`, `.mus` | `application/octet-stream` | Legacy symbolic score formats. | specialist parse or opaque |
| Module trackers | `.mod`, `.xm`, `.it`, `.s3m`, `.stm`, `.mtm`, `.669`, `.amf`, `.okt`, `.far` | `audio/mod`, `audio/xm`, `audio/it`, `audio/s3m` | Pattern/sample-based music modules; `.mod` has many collisions. | specialist `structured-parse` + audio render |
| Chiptune formats | `.sid`, `.nsf`, `.nsfe`, `.spc`, `.gbs`, `.hes`, `.sap`, `.vgm`, `.vgz`, `.gym`, `.ay` | `audio/prs.sid`, vendor/unregistered types | Console/computer sound-chip music dumps, sometimes containing executable player state. | sandboxed specialist render; quarantine executable aspects |
| Cue sheets | `.cue`, `.toc` | `application/x-cue`, `text/plain` | Track/index metadata referencing external audio/disc images. | `text-native` / `structured-parse`; preserve references |
| Playlists | `.m3u`, `.m3u8`, `.pls`, `.xspf`, `.wpl`, `.asx`, `.zpl` | `audio/mpegurl`, `application/vnd.apple.mpegurl`, `audio/x-scpls`, `application/xspf+xml` | Ordered local/remote media references and metadata. | `structured-parse`; do not fetch remote URLs by default |
| Lyrics | `.lrc`, `.elrc` | `text/plain` | Plain or time-coded lyrics. | `text-native` / timecode parse |

#### 3.7.3 DAW, editing, interchange, and session projects

| Format/family | Extension(s) / package | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| Pro Tools | `.ptx`, `.ptf`, `.pts` plus audio folders | `application/octet-stream` | DAW session metadata referencing audio, plugins, automation. | dataset specialist parse; media-transcribe referenced audio |
| Logic Pro / GarageBand | `.logicx`, `.logic`, `.band` packages | `application/vnd.apple.logic`, generic package types | macOS DAW project packages with assets/settings. | safe package enumerate; specialist metadata; recurse media |
| Ableton Live | `.als`, `.alc`, `.alp` | `application/gzip`, `application/octet-stream` | Gzip-compressed XML sets/clips or pack archives. | bounded decompress + `structured-parse`; recurse assets |
| FL Studio | `.flp`, `.fst`, `.zip` bundle | `application/octet-stream` | Proprietary project/state plus referenced samples/plugins. | specialist metadata; recurse bundled media |
| REAPER | `.rpp`, `.rpp-bak`, `.rtracktemplate` | `text/plain` | Text-based project and templates referencing media/plugins. | `structured-parse`; recurse authorized local media only |
| Audacity | `.aup`, `.aup3`, `_data` directory | `application/xml`, `application/x-sqlite3` | Legacy XML+block-folder or SQLite-based audio project. | dataset/SQLite read-only parse; media derivation |
| Ardour | `.ardour`, session directory | `application/xml` | XML DAW session plus audio interchange. | dataset `structured-parse`; recurse media |
| Cubase / Nuendo | `.cpr`, `.npr`, `.bak` | `application/octet-stream` | Proprietary DAW project. | specialist metadata; otherwise opaque |
| Studio One / Reason | `.song`, `.project`, `.reason`, `.rns` | `application/octet-stream`, sometimes ZIP | Proprietary DAW song/project packages. | inspect package safely; specialist/opaque |
| AAF / OMF | `.aaf`, `.omf`, `.omfi` | `application/vnd.aaf`, `application/octet-stream` | Professional audiovisual edit interchange with embedded or linked essence. | specialist `structured-parse`; recurse essence |
| EDL | `.edl`, `.ale`, `.fcpxml`, `.xml` | `text/plain`, `application/xml` | Edit decision lists, Avid logs, and NLE timeline XML. | `structured-parse`; preserve media references |

### 3.8 Video: containers, codecs, manifests, captions, and subtitles

#### 3.8.1 Video containers and files

| Container/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| ISO BMFF / MP4 | `.mp4`, `.m4v`, `.m4p`, `.m4s`, `.cmfv`, `.cmfa` | `video/mp4`, `application/mp4` | ISO base media container for timed video/audio/text/metadata; fragmented segments included. | track-aware `media-transcribe` |
| QuickTime | `.mov`, `.qt`, `.mqv` | `video/quicktime` | QuickTime File Format, ancestor/relative of ISO BMFF. | track-aware `media-transcribe` |
| 3GPP / 3GPP2 | `.3gp`, `.3gpp`, `.3g2`, `.3gp2` | `video/3gpp`, `video/3gpp2`, audio variants | Mobile ISO-BMFF profiles carrying audio/video/text. | `media-transcribe` |
| Matroska | `.mkv`, `.mk3d`, `.mka`, `.mks` | `video/x-matroska`, `audio/x-matroska` | Extensible EBML container for video, audio, subtitles, chapters, attachments. | track-aware `media-transcribe`; recurse attachments safely |
| WebM | `.webm`, `.weba` | `video/webm`, `audio/webm` | Restricted Matroska profile for web media. | `media-transcribe` |
| AVI / OpenDML | `.avi`, `.divx` | `video/x-msvideo`, `video/vnd.avi` | RIFF video container with broad codec combinations. | `media-transcribe` |
| ASF / WMV | `.asf`, `.wmv`, `.wm`, `.wvx`, `.wmx` | `video/x-ms-asf`, `video/x-ms-wmv` | Windows Media container/files and playlist/metafile pointers. | inspect then transcribe or parse pointer |
| MPEG program/system stream | `.mpg`, `.mpeg`, `.mpe`, `.m1v`, `.m2v`, `.mpv`, `.vob`, `.evo` | `video/mpeg` | MPEG-1/2 system/program or elementary video; VOB/EVO add disc conventions. | `media-transcribe`; disc-set aware for VOB |
| MPEG transport stream | `.ts`, `.mts`, `.m2ts`, `.m2t`, `.trp`, `.tp` | `video/mp2t` | Packetized broadcast/streaming container; `.ts` collides with TypeScript. | track/program-aware `media-transcribe` |
| DVD/Blu-ray structures | `VIDEO_TS`/`AUDIO_TS`, `.ifo`, `.bup`, `.vob`; `BDMV`, `.mpls`, `.clpi`, `.m2ts` | `video/dvd`, `application/octet-stream`, `video/mp2t` | Directory-based optical-disc programs, menus, playlists, and streams. | dataset parse + media derivation; never autorun |
| DVR-MS / WTV | `.dvr-ms`, `.wtv` | `video/x-ms-dvr`, `video/x-ms-wtv` | Microsoft recorded-TV containers. | `media-transcribe` |
| Flash Video | `.flv`, `.f4v`, `.f4p`, `.f4a`, `.f4b` | `video/x-flv`, `video/mp4`, audio variants | FLV or Adobe-branded ISO-BMFF media. | `media-transcribe`; scripts/metadata not executed |
| Ogg video | `.ogv`, `.ogm`, `.ogg`, `.ogx` | `video/ogg`, `application/ogg` | Ogg container commonly carrying Theora/Dirac plus audio/subtitles. | track-aware `media-transcribe` |
| MXF | `.mxf` | `application/mxf` | SMPTE professional container with operational patterns and rich metadata. | specialist track-aware `media-transcribe` |
| GXF / LXF | `.gxf`, `.lxf` | `application/octet-stream` | Broadcast video exchange/server containers. | specialist `media-transcribe` |
| RealMedia | `.rm`, `.rmvb`, `.rv`, `.ram` | `application/vnd.rn-realmedia`, `video/vnd.rn-realvideo` | RealNetworks container/video or pointer. | inspect then transcribe/pointer parse |
| DV | `.dv`, `.dif` | `video/dv` | Raw DV/DIF video stream; `.dif` collides with spreadsheet exchange. | `media-transcribe` after detection |
| Motion JPEG 2000 | `.mj2`, `.mjp2` | `video/mj2` | ISO BMFF carrying JPEG 2000 frame sequences. | `media-transcribe` |
| IVF | `.ivf` | `video/x-ivf` | Simple container commonly holding VP8/VP9/AV1. | `media-transcribe` |
| raw YUV / Y4M | `.yuv`, `.y4m`, `.raw`, `.rgb` | `video/x-raw`, `application/octet-stream` | Uncompressed frames; raw YUV needs external dimensions/rate/pixel format, Y4M has a text header. | Y4M parse; raw opaque unless parameters supplied |
| Indeo/game/camera legacy | `.ivf`, `.smk`, `.bik`, `.bk2`, `.roq`, `.4xm`, `.nuv`, `.vid` | vendor/unregistered video types | Legacy game, capture, and proprietary multimedia containers. | specialist decode; otherwise opaque |
| Animated image containers | `.gif`, `.apng`, `.webp`, `.avifs`, `.heics`, `.mng` | image types, `video/x-mng` | Multi-frame image files with timing/loops. | short-video `media-transcribe` |

#### 3.8.2 Video codecs and elementary bitstreams — not the same as containers

| Codec/bitstream family | Typical raw extension(s) / signaling | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| AVC / H.264 | `.h264`, `.264`, `.avc` or codec ID inside MP4/MKV/TS | `video/H264` | Compressed video elementary stream; timestamps/audio/metadata usually come from a container. | `media-transcribe`; require/probe framing/rate |
| HEVC / H.265 | `.h265`, `.265`, `.hevc` or container codec ID | `video/H265` | HEVC compressed video bitstream. | `media-transcribe` |
| VVC / H.266 | `.h266`, `.266`, `.vvc` | `video/H266` | Versatile Video Coding elementary stream. | specialist decode / opaque if unavailable |
| AV1 | `.av1`, `.obu` or inside MP4/WebM/MKV | `video/AV1` | AV1 compressed video in low-overhead bitstream or container. | `media-transcribe` |
| VP8 / VP9 | `.ivf` or inside WebM/MKV | `video/VP8`, `video/VP9` | Google video codecs normally carried in a container. | `media-transcribe` |
| MPEG-1/2 video | `.m1v`, `.m2v`, `.mpv` | `video/mpeg` | MPEG video elementary stream. | `media-transcribe` |
| MPEG-4 Part 2 | `.m4v` or AVI/MP4 codec IDs (DivX/Xvid) | `video/mp4v-es` | Visual codec carried raw or in AVI/MP4. | `media-transcribe` after container/bitstream detection |
| VC-1 | `.vc1` or ASF/TS/MKV codec ID | `video/vc1` | SMPTE VC-1 elementary video. | `media-transcribe` |
| Theora / Dirac | usually Ogg/MKV; `.drc` for Dirac | `video/theora`, `video/dirac` | Open video codecs, normally containerized. | `media-transcribe` |
| ProRes / DNxHD/DNxHR | typically MOV/MXF/MKV | codec signaling in container | Professional intra-frame editing codecs, not filename formats. | container-aware `media-transcribe` |
| FFV1 / HuffYUV / lossless RGB | typically MKV/AVI | codec signaling in container | Archival/intermediate lossless video codecs. | container-aware `media-transcribe` |
| JPEG/MJPEG | `.mjpg`, `.mjpeg` or AVI/MOV stream | `video/x-motion-jpeg`, `multipart/x-mixed-replace` | Sequence/framing of JPEG images. | `media-transcribe` |
| Raw/uncompressed video | `.raw`, `.yuv`, `.rgb` or container codec ID | `video/raw`, `application/octet-stream` | Pixel planes requiring dimensions, format, rate, and ordering. | opaque until parameters known; then media derivation |

#### 3.8.3 Streaming manifests and media playlists

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| HLS | `.m3u8`, `.m3u` | `application/vnd.apple.mpegurl`, `application/x-mpegURL` | Text master/media playlists referencing segments/keys/subtitles. | `structured-parse`; no remote fetch by default |
| MPEG-DASH | `.mpd` | `application/dash+xml` | XML presentation manifest referencing representations/segments. | hardened `structured-parse`; no remote fetch by default |
| Smooth Streaming | `.ism`, `.ismc`, `.isml`, `.ismv`, `.isma` | `application/vnd.ms-sstr+xml`, MP4 types | XML manifests plus fragmented MP4 media. | manifest parse; recurse uploaded segments only |
| Adobe HDS | `.f4m`, `.f4f` | `application/f4m+xml`, `video/mp4` | XML manifest and fragmented media. | manifest parse; recurse uploaded segments only |
| SMIL | `.smil`, `.smi` | `application/smil+xml` | Synchronized multimedia presentation; `.smi` collides with SAMI captions. | hardened `structured-parse`; no external fetch |
| RTSP/Windows metafiles | `.ram`, `.asx`, `.wax`, `.wvx` | vendor playlist types | Text/XML pointer files to remote streams. | `structured-parse`; record links without fetching |

#### 3.8.4 Captions, subtitles, transcripts, and timed metadata

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| WebVTT | `.vtt` | `text/vtt` | Web timed cues, styling, regions, metadata. | `structured-parse`; canonical text can feed Markdown |
| SubRip | `.srt` | `application/x-subrip`, `text/plain` | Numbered time-coded plain-text subtitles. | `structured-parse` |
| SubStation Alpha | `.ssa`, `.ass` | `text/x-ssa`, `text/x-ass` | Timed dialogue with rich styles/effects. | `structured-parse`; do not execute renderer extensions |
| TTML / DFXP / IMSC | `.ttml`, `.dfxp`, `.xml` | `application/ttml+xml` | XML timed text and broadcast/streaming profiles. | hardened `structured-parse` |
| SAMI | `.smi`, `.sami` | `application/x-sami`, `text/sami` | HTML-like Microsoft captions; `.smi` collides with SMIL. | sanitized `structured-parse` |
| YouTube/SBV | `.sbv` | `text/plain` | Simple time-coded captions. | `structured-parse` |
| MicroDVD/MPL2/SubViewer | `.sub`, `.mpl`, `.mpl2`, `.smi` | `text/plain` | Frame/time-coded subtitle text dialects; `.sub` is highly ambiguous. | dialect-detect `structured-parse` |
| VobSub | `.idx` + `.sub` | `application/octet-stream` | Index plus bitmap subtitle stream. | pair-aware OCR/`media-transcribe` |
| PGS | `.sup` | `application/octet-stream` | Blu-ray bitmap subtitles. | OCR/`media-transcribe` |
| Scenarist / MacCaption | `.scc`, `.mcc` | `text/plain` | Broadcast closed-caption interchange with encoded caption words. | specialist `structured-parse` |
| EBU STL | `.stl` | `application/octet-stream` | Binary broadcast subtitle exchange; collides with 3D stereolithography. | signature-aware specialist parse |
| PAC / 890 / CAP | `.pac`, `.890`, `.cap` | `application/octet-stream` | Broadcast/authoring subtitle formats with extension collisions. | specialist parse after strong identification |
| Spruce subtitle | `.stl`, `.txt` plus images | `text/plain` | DVD authoring subtitle script and bitmap assets. | dataset parse; distinguish EBU STL/3D STL |
| QuickTime text | `.qt.txt`, `.txt` | `text/plain` | Timed-text descriptions/import form. | profile-aware `structured-parse` |
| LRC lyrics | `.lrc`, `.elrc` | `text/plain` | Line/word-timed lyrics. | `structured-parse` |
| Timed ID3 / emsg / CEA-608/708 | usually embedded in media | container/codec signaling | Timed metadata and caption tracks without standalone extension. | extract during container parse; preserve track/time provenance |

**Observed:** FFmpeg exposes container handling as demuxers/muxers and codecs separately, and its
enabled set is build-dependent. **Inference:** RememberStack should record both container identity
and every selected stream's codec/profile; routing merely on `.mp4`, `.mkv`, or `video/*` loses
whether the build can actually decode the contents.

### 3.9 Compression, archives, software packages, backups, and disk images

#### 3.9.1 Compression streams and general archives

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| ZIP / ZIP64 / ZIPX | `.zip`, `.zipx` | `application/zip` | Member archive supporting many compression/encryption methods; also substrate for many compound formats. | bounded `archive-expand`; encrypted members need password workflow |
| 7-Zip | `.7z` | `application/x-7z-compressed` | Solid/member archive with multiple codecs and optional encrypted metadata. | bounded `archive-expand`; password/solid-archive limits |
| RAR | `.rar`, `.r00`–`.r99`, `.partNN.rar` | `application/vnd.rar`, `application/x-rar-compressed` | Proprietary archive generations and multipart volumes. | bounded `archive-expand`; volume-set aware |
| Tar family | `.tar`, `.ustar`, `.pax` | `application/x-tar` | Sequential member archive with POSIX/GNU/vendor variants, links and sparse files. | bounded `archive-expand`; neutralize paths/links/devices |
| Tar + compression | `.tgz`, `.tar.gz`, `.tbz`, `.tbz2`, `.tar.bz2`, `.txz`, `.tar.xz`, `.tlz`, `.tar.lz`, `.tar.lzma`, `.tzst`, `.tar.zst`, `.tar.lz4` | tar plus compression types | A tar archive passed through a compression filter. | bounded decompress then `archive-expand` |
| gzip | `.gz`, `.gzip` | `application/gzip` | Single compressed stream, commonly containing one file or tar. | bounded decompress; detect inner format |
| bzip2 | `.bz2`, `.bz` | `application/x-bzip2`, `application/x-bzip` | Single compressed stream. | bounded decompress; detect inner format |
| xz / LZMA / lzip | `.xz`, `.lzma`, `.lz` | `application/x-xz`, `application/x-lzma`, `application/lzip` | LZMA-family compressed streams with different framing. | bounded decompress; detect inner format |
| Zstandard | `.zst`, `.zstd` | `application/zstd` | Zstandard compressed stream, possibly skippable/concatenated frames. | bounded decompress; detect inner format |
| LZ4 / LZO / lzop | `.lz4`, `.lzo` | `application/x-lz4`, `application/x-lzop` | Fast compressed stream/framing. | bounded decompress |
| Unix compress | `.Z`, `.z` | `application/x-compress` | Legacy LZW compression; lowercase `.z` can mean other things. | bounded decompress after signature detection |
| Brotli | `.br` | `application/brotli` | Brotli compressed stream, often content-encoding rather than named file. | bounded decompress |
| Snappy | `.sz`, `.snappy` | `application/x-snappy-framed`, generic binary | Snappy framing/stream; raw blocks need external framing knowledge. | bounded specialist decompress |
| CPIO | `.cpio` | `application/x-cpio` | Unix archive with binary/odc/newc and other variants. | bounded `archive-expand`; neutralize special files |
| ar / static library | `.ar`, `.a`, `.lib`, `.deb` substrate | `application/x-archive`, `application/x-unix-archive` | Unix member archive used by libraries and Debian packages. | `archive-expand`; executable members quarantine |
| XAR | `.xar`, macOS package substrate | `application/x-xar` | Extensible XML-indexed archive. | bounded `archive-expand`; signatures/installer scripts not run |
| LHA/LZH | `.lha`, `.lzh` | `application/x-lzh-compressed` | Legacy member archive. | bounded `archive-expand` |
| ARJ | `.arj`, `.a01`–`.a99` | `application/x-arj` | Legacy compressed/multipart archive. | bounded `archive-expand` |
| ACE | `.ace`, `.c00`–`.c99` | `application/x-ace-compressed` | Legacy proprietary archive/multipart volumes. | sandboxed `archive-expand` if decoder trusted; else opaque |
| ARC / PKARC | `.arc` | `application/x-arc-compressed` | Legacy archive; collides with Internet Archive ARC and CAD/project uses. | identify deeply then `archive-expand` |
| ZOO | `.zoo` | `application/x-zoo` | Legacy archive. | bounded `archive-expand` |
| CAB | `.cab` | `application/vnd.ms-cab-compressed` | Microsoft cabinet package, often installers/drivers. | `archive-expand`; members/scripts quarantine |
| StuffIt | `.sit`, `.sitx`, `.sea` | `application/x-stuffit`, `application/x-stuffitx` | Classic Mac archive; `.sea` may be self-extracting executable. | archive parse in sandbox; `.sea` quarantine |
| Compact Pro / BinHex | `.cpt`, `.hqx` | `application/mac-compactpro`, `application/mac-binhex40` | Classic Mac archive/ASCII transfer encoding preserving forks. | decode + `archive-expand` |
| AppleSingle/AppleDouble/MacBinary | `.as`, `.appledouble`, `._*`, `.bin` | `application/applefile`, `application/macbinary` | Wrappers/sidecars preserving data/resource forks and metadata. | `structured-parse`; associate forks safely |
| ZPAQ / DAR | `.zpaq`, `.dar` | `application/x-zpaq`, `application/x-dar` | Incremental/versioned backup archives. | bounded specialist `archive-expand` |
| Shar | `.shar` | `application/x-shar`, `text/plain` | Shell-script archive that reconstructs files when run. | parse as text only; `dangerous/quarantine`; never execute |
| uuencode/base64 wrappers | `.uu`, `.uue`, `.b64`, `.base64` | `text/x-uuencode`, `application/base64` | Text transfer encodings that may wrap any file. | bounded decode then re-identify; never trust claimed name |
| Split raw files | `.001`, `.002`, `.split`, `.partNN` | `application/octet-stream` | Arbitrary byte chunks or archive volumes requiring the full ordered set. | dataset-aware join only under explicit limits; re-identify result |

#### 3.9.2 Application, OS, language, and content packages

| Package/family | Extension(s) / dataset | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| Debian | `.deb`, `.udeb`, `.ddeb`, `.changes`, `.dsc` | `application/vnd.debian.binary-package`, `text/plain` | `ar` package containing metadata/control scripts and tar payloads, plus source metadata. | `archive-expand`; `dangerous/quarantine`; never install/run scripts |
| RPM | `.rpm`, `.srpm` | `application/x-rpm` | Signed package header plus CPIO payload and install scripts. | `archive-expand`; `dangerous/quarantine` |
| Alpine / Arch / opkg | `.apk`, `.pkg.tar.zst`, `.pkg.tar.xz`, `.ipk`, `.opk` | `application/vnd.alpine.apk`, archive/generic types | Tar-based Linux packages with metadata and scripts; `.apk` collides with Android. | detect, `archive-expand`, quarantine |
| Snap / Flatpak | `.snap`, `.flatpak`, `.flatpakref`, `.flatpakrepo` | `application/vnd.snap`, `application/vnd.flatpak`, vendor types | SquashFS app package or Flatpak bundle/reference metadata. | package metadata parse; filesystem image quarantine; never install |
| AppImage | `.AppImage`, `.appimage` | `application/vnd.appimage` | Executable ELF plus embedded filesystem. | `dangerous/quarantine`; static metadata/filesystem inspect only |
| macOS packages | `.pkg`, `.mpkg`, `.dmg` | `application/vnd.apple.installer+xml`, `application/x-apple-diskimage` | XAR installer/packages or disk image; may contain scripts. | sandboxed enumerate; `dangerous/quarantine`; never mount/install |
| Windows Installer | `.msi`, `.msp`, `.mst`, `.msm`, `.msix`, `.appx`, `.appxbundle`, `.msixbundle` | `application/x-msi`, `application/msix`, `application/appx` | OLE database or ZIP/OPC application/update/package bundles with executable actions. | `dangerous/quarantine`; static table/package parse only |
| Android application | `.apk`, `.aab`, `.apks`, `.xapk`, `.apkm` | `application/vnd.android.package-archive`, `application/octet-stream` | Signed ZIP application/bundle or multi-APK distribution package. | `archive-expand`; `dangerous/quarantine`; static manifest/resources only |
| Apple application | `.ipa`, `.app` bundle | `application/x-itunes-ipa`, directory/binary types | ZIP or directory bundle containing Mach-O code, resources, entitlements. | `archive-expand`; `dangerous/quarantine`; static metadata only |
| Java packages | `.jar`, `.war`, `.ear`, `.jmod` | `application/java-archive` | ZIP packages of executable bytecode/resources/web/enterprise modules. | `archive-expand`; `dangerous/quarantine`; static metadata only |
| NuGet / VSIX | `.nupkg`, `.snupkg`, `.vsix` | `application/zip`, `application/vsix` | ZIP packages of .NET libraries/symbols or IDE extensions. | `archive-expand`; quarantine executable/install content |
| Python packages | `.whl`, `.egg`, `.pyz`, `.pex`, `.tar.gz`, `.zip` | `application/zip`, `application/gzip` | Wheels/eggs/apps/source distributions, capable of install/build hooks. | `archive-expand`; `dangerous/quarantine`; never install/import |
| Ruby / Rust / Go packages | `.gem`, `.crate`, `.mod`/ZIP cache | `application/octet-stream`, `application/gzip`, `application/zip` | Language packages/source archives and metadata. | `archive-expand`; never build/install |
| npm packages | `.tgz` | `application/gzip` | Tarball with JavaScript package and lifecycle scripts. | `archive-expand`; `dangerous/quarantine`; never run scripts |
| Conda packages | `.conda`, `.tar.bz2` | `application/vnd.conda.package`, archive types | Conda metadata and package payloads. | `archive-expand`; never install/link scripts |
| R packages | `.tar.gz`, `.zip` | archive types | Source or binary R package, potentially with native code/hooks. | `archive-expand`; never install/load |
| Browser extensions | `.crx`, `.xpi`, `.safariextz` | `application/x-chrome-extension`, `application/x-xpinstall`, `application/octet-stream` | Signed/packaged web extension containing executable scripts. | `archive-expand`; `dangerous/quarantine`; static manifest/source only |
| Helm chart | `.tgz` | `application/gzip` | Kubernetes template/package with values and dependencies. | `archive-expand` + template parse; never deploy/render unsafely |
| OCI/Docker image archive | `.tar`, `.oci`, directory layout | `application/vnd.oci.image.manifest.v1+json`, tar types | Manifest/config plus content-addressed filesystem layers. | bounded layer `archive-expand`; quarantine devices/links; never run |
| VM appliance | `.ova`, `.ovf`, `.mf` | `application/ovf`, `application/xml`, `text/plain` | Tarred or directory appliance descriptor, disks, manifest/signatures. | descriptor parse; disks quarantine/opaque |
| Game/resource packages | `.pak`, `.pk3`, `.wad`, `.grp`, `.vpk`, `.bsa`, `.ba2`, `.bundle`, `.assets` | `application/octet-stream` | Engine-specific member archives and resources; some contain scripts/code. | specialist `archive-expand`; executable content quarantine |

#### 3.9.3 Disk, filesystem, forensic, and backup images

| Image/family | Extension(s) / dataset | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| ISO 9660 / UDF optical image | `.iso`, `.udf` | `application/x-iso9660-image`, `application/x-udf` | Filesystem/disc image, potentially bootable and hybrid/polyglot. | `dangerous/quarantine`; read-only sandboxed enumerate, never mount |
| CUE/BIN and optical sets | `.cue` + `.bin`, `.img`, `.iso`, `.wav` | `application/x-cue`, `application/octet-stream` | Descriptor plus track images/audio. | dataset parse; image quarantine; never mount/autorun |
| Nero / Alcohol / CloneCD | `.nrg`, `.mdf` + `.mds`, `.ccd` + `.img` + `.sub`, `.cdi` | `application/octet-stream` | Proprietary optical-disc image sets. | specialist read-only enumerate; quarantine |
| Apple disk image | `.dmg`, `.sparseimage`, `.sparsebundle` | `application/x-apple-diskimage` | Compressed/encrypted filesystem/container, or banded directory bundle. | quarantine; sandboxed read-only inspect, never host-mount |
| Raw disk/forensic image | `.img`, `.dd`, `.raw`, `.bin`, `.ima` | `application/octet-stream` | Sector-for-sector disk/media image; extensions collide broadly. | quarantine; read-only filesystem/partition parser under limits |
| EWF / AFF forensic | `.e01`, `.ex01`, `.s01`, `.aff`, `.afd`, `.afm` | `application/octet-stream` | Chunked/compressed forensic image with checksums/metadata. | specialist read-only parse; quarantine |
| VHD / VHDX | `.vhd`, `.vhdx`, `.avhd`, `.avhdx` | `application/x-vhd`, `application/x-vhdx` | Microsoft virtual disks and differencing disks. | quarantine; dependency-chain-aware read-only inspect |
| VMDK | `.vmdk` plus descriptor/extents | `application/x-vmdk` | VMware sparse/flat/split virtual disk family. | quarantine; dataset read-only inspect |
| QCOW / QED | `.qcow`, `.qcow2`, `.qed` | `application/x-qemu-disk` | QEMU copy-on-write virtual disks, possibly backing another image. | quarantine; no external backing-file fetch; read-only inspect |
| VirtualBox/Parallels disks | `.vdi`, `.hdd`, `.pvm` package | `application/x-virtualbox-vdi`, generic binary | Virtual machine disk/package formats. | quarantine; safe read-only metadata/filesystem inspect |
| WIM / ESD / SWM | `.wim`, `.esd`, `.swm` | `application/x-ms-wim` | Windows file-based deployment image, compressed and sometimes split. | bounded read-only enumerate; `dangerous/quarantine` |
| SquashFS / cramfs / ext image | `.squashfs`, `.sqsh`, `.sfs`, `.cramfs`, `.ext2`, `.ext3`, `.ext4` | `application/octet-stream` | Filesystem images, often firmware/package payloads. | quarantine; userspace read-only parser only |
| Backup images | `.tib`, `.tibx`, `.gho`, `.v2i`, `.bkf`, `.fbw` | `application/octet-stream` | Vendor backup/disk images and sets. | specialist read-only parse; otherwise opaque |
| VM save/memory state | `.vmem`, `.vmsn`, `.vmss`, `.sav`, `.bin` | `application/octet-stream` | Guest memory/device state, often sensitive and version-specific. | restricted `binary-opaque`; quarantine |

### 3.10 Fonts and type-design assets

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| TrueType / OpenType | `.ttf`, `.otf` | `font/ttf`, `font/otf` | SFNT font with TrueType or CFF outlines and layout tables; may be variable/color. | safe metadata/glyph inventory + rendered specimen; `binary-opaque` otherwise |
| Font collections | `.ttc`, `.otc` | `font/collection` | Multiple TrueType/OpenType fonts sharing tables. | enumerate faces + metadata/specimens |
| WOFF / WOFF2 | `.woff`, `.woff2` | `font/woff`, `font/woff2` | Compressed web-font containers. | metadata + safe glyph render |
| Embedded OpenType | `.eot` | `application/vnd.ms-fontobject` | Legacy compressed/obfuscated web font. | metadata + safe glyph render |
| Type 1 | `.pfa`, `.pfb`, `.afm`, `.pfm` | `application/x-font-type1`, `application/postscript` | PostScript outline fonts plus metrics. | sandbox parser/render; keep related files together |
| Bitmap fonts | `.bdf`, `.pcf`, `.fon`, `.fnt`, `.snf` | `application/x-font-bdf`, `application/x-font-pcf`, `application/x-font-dos` | Text/binary bitmap fonts and Windows resources. | metadata + safe specimen; `.fon` may be executable resource |
| macOS fonts | `.dfont`, `.suit`, resource-fork suitcase | `application/x-font-dfont`, `application/octet-stream` | Data-fork/resource-fork font containers. | specialist metadata/render; preserve forks |
| UFO / designspace | `.ufo` directory, `.designspace`, `.glyphs`, `.glyphspackage` | `application/xml`, `text/plain`, directory dataset | Type-design source with glyph outlines, masters, metadata. | dataset `structured-parse` + safe render |
| FontForge / Ikarus source | `.sfd`, `.ik`, `.mf` | `text/plain` | Text/source outline or METAFONT programs. | `text-native`; sandbox any renderer |
| TeX fonts/metrics | `.tfm`, `.vf`, `.pk`, `.gf`, `.ofm`, `.ovf` | `application/x-tex-tfm`, `application/octet-stream` | TeX metrics, virtual fonts, and bitmap glyphs. | specialist metadata/render |

### 3.11 3D, CAD, BIM, manufacturing, point clouds, and EDA

#### 3.11.1 Meshes, scenes, animation, and point clouds

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| glTF / binary glTF | `.gltf`, `.glb` | `model/gltf+json`, `model/gltf-binary` | JSON scene/mesh/material graph with external/data resources, or self-contained binary container. | specialist `structured-parse` + preview; bound references |
| Wavefront OBJ/MTL | `.obj`, `.mtl` | `model/obj`, `text/plain` | Text mesh/geometry and material definitions referencing textures. | `structured-parse` + preview; preserve dependency set |
| PLY | `.ply` | `model/ply`, `application/octet-stream` | ASCII/binary polygon or point-cloud data. | specialist `structured-parse` + preview |
| STL | `.stl` | `model/stl`, `application/sla` | ASCII or binary triangle mesh; extension collides with EBU subtitles. | signature/content detect, parse + preview |
| 3MF | `.3mf` | `model/3mf`, `application/vnd.ms-package.3dmanufacturing-3dmodel+xml` | ZIP/OPC additive-manufacturing model with materials/resources. | safe package parse + 3D preview |
| AMF | `.amf` | `application/x-amf`, `application/xml` | XML additive-manufacturing model. | hardened `structured-parse` + preview |
| COLLADA | `.dae` | `model/vnd.collada+xml` | XML scene/asset interchange with external references. | hardened `structured-parse` + preview |
| FBX | `.fbx` | `application/octet-stream` | Autodesk scene/animation interchange in ASCII or binary generations. | specialist parse + preview; otherwise opaque |
| 3D Studio / MAX | `.3ds`, `.max`, `.prj` | `image/x-3ds`, `application/octet-stream` | Legacy interchange and proprietary 3ds Max scene/project. | `.3ds` specialist parse; `.max` opaque/vendor convert |
| Blender | `.blend` | `application/x-blender` | Versioned native Blender scene database; may include scripts/drivers/packed assets. | specialist metadata/preview; `dangerous/quarantine`; never run scripts |
| Universal Scene Description | `.usd`, `.usda`, `.usdc`, `.usdz` | `model/vnd.usd`, `model/vnd.usdz+zip` | Layered scene description in text, binary crate, or ZIP package. | specialist parse; bound composition references; safe preview |
| Alembic | `.abc` | `application/x-alembic` | Cached animated geometry/scene data; `.abc` collides with music notation. | specialist parse + preview |
| X3D / VRML | `.x3d`, `.x3dv`, `.x3db`, `.wrl`, `.vrml` | `model/x3d+xml`, `model/x3d-vrml`, `model/x3d+fastinfoset`, `model/vrml` | XML/classic/binary 3D scene descriptions and VRML. | hardened parse + safe preview; no scripts/network |
| U3D / PRC | `.u3d`, `.prc` | `model/u3d`, `model/prc` | Compressed 3D exchange commonly embedded in PDF. | specialist parse/preview |
| DirectX / game models | `.x`, `.md2`, `.md3`, `.md5mesh`, `.md5anim`, `.mdl`, `.smd`, `.vta`, `.iqm` | generic binary/text model types | Game-oriented model/animation formats with many extension collisions. | specialist parse + preview after strong detection |
| LightWave/Modo/Cinema 4D | `.lwo`, `.lws`, `.lxo`, `.c4d` | vendor/generic binary types | Proprietary/native modeling and scene files. | specialist parse/vendor convert; otherwise opaque |
| AC3D / OpenGEX / OFF | `.ac`, `.ac3d`, `.ogex`, `.off` | `text/plain`, `model/vnd.opengex` | Mostly text 3D interchange/model formats. | `structured-parse` + preview |
| LAS / LAZ / COPC | `.las`, `.laz`, `.copc.laz` | `application/vnd.las`, `application/vnd.laszip` | Geospatial point-cloud records, compressed LAZ; `.las` also log/legacy formats. | specialist streaming parse + sampled preview |
| E57 | `.e57` | `model/e57`, `application/octet-stream` | ASTM 3D imaging/point-cloud container with images/metadata. | specialist `structured-parse` + sampled preview |
| PCD / PTS / PTX / XYZ | `.pcd`, `.pts`, `.ptx`, `.xyz` | `text/plain`, `application/octet-stream` | Point-cloud text/binary interchange; extensions collide. | schema/dialect-aware streaming parse + sample |

#### 3.11.2 CAD/BIM and native engineering models

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| STEP / STEP-NC | `.step`, `.stp`, `.p21`, `.stpnc` | `model/step`, `application/step` | ISO 10303 product-model exchange in clear-text Part 21. | specialist `structured-parse` + render |
| IGES | `.iges`, `.igs` | `model/iges` | Legacy CAD geometry/product exchange. | specialist parse + render |
| DXF | `.dxf` | `image/vnd.dxf`, `application/dxf` | AutoCAD Drawing Exchange in ASCII/binary forms. | specialist parse + render |
| DWG | `.dwg` | `image/vnd.dwg`, `application/acad` | Versioned proprietary AutoCAD drawing database. | specialist parse/vendor SDK; otherwise opaque |
| DWF / DWFx | `.dwf`, `.dwfx` | `model/vnd.dwf`, `application/vnd.ms-package.xps` | Published CAD/design review package, binary/ZIP-XPS generations. | `document-convert` + CAD metadata |
| MicroStation | `.dgn` | `image/vnd.dgn`, `application/octet-stream` | Bentley CAD design file, V7/V8 families. | specialist parse/render |
| ACIS | `.sat`, `.sab`, `.asat`, `.asab` | `model/vnd.sat`, generic binary | Text/binary solid-model boundary representation. | specialist parse/render |
| Parasolid | `.x_t`, `.x_b`, `.xmt_txt`, `.xmt_bin` | `model/vnd.parasolid.transmit.text`, `model/vnd.parasolid.transmit.binary` | Text/binary solid model interchange. | specialist parse/render |
| Open CASCADE BREP | `.brep`, `.brp` | `model/vnd.opencascade.brep`, `text/plain` | Boundary-representation model. | specialist parse/render |
| JT / PLMXML | `.jt`, `.plmxml` | `model/jt`, `application/xml` | Lightweight product visualization and PLM structure. | specialist parse; XML structure parse |
| IFC / ifcXML / ifcZIP | `.ifc`, `.ifcxml`, `.ifczip` | `application/x-step`, `application/xml`, `application/zip` | Building Information Model in STEP text, XML, or ZIP. | BIM `structured-parse` + preview; recurse package |
| Revit | `.rvt`, `.rfa`, `.rte`, `.rft` | `application/octet-stream` | Proprietary BIM project/family/template formats. | vendor/specialist extract; otherwise opaque |
| SketchUp | `.skp`, `.layout`, `.style` | `application/vnd.sketchup.skp` | Proprietary model/layout/style. | specialist parse/render |
| Rhino | `.3dm` | `model/vnd.3dm`, `application/octet-stream` | OpenNURBS-based CAD model. | specialist parse/render |
| FreeCAD | `.fcstd`, `.fcstd1` | `application/zip`, `application/x-extension-fcstd` | ZIP project containing XML model and binary shape data. | package `structured-parse` + safe render |
| OpenSCAD | `.scad` | `application/x-openscad`, `text/plain` | Programmatic solid-model source. | `text-native`; sandbox render; never include arbitrary files |
| SolidWorks | `.sldprt`, `.sldasm`, `.slddrw`, `.slddrt` | `application/octet-stream` | Proprietary part, assembly, drawing, template. | vendor/specialist metadata/render; otherwise opaque |
| CATIA | `.catpart`, `.catproduct`, `.catdrawing`, `.model`, `.cgr`, `.3dxml` | vendor/generic types | Dassault native/product/visualization formats. | specialist parse/vendor conversion |
| Creo/ProENGINEER | `.prt`, `.asm`, `.drw`, `.frm`, `.neu` | `application/octet-stream` | Versioned native part/assembly/drawing and neutral files. | specialist parse/vendor conversion |
| Siemens NX | `.prt`, `.fem`, `.sim` | `application/octet-stream` | NX CAD/CAE native files; `.prt` collides with Creo. | specialist parse/vendor conversion |
| Autodesk Inventor/Fusion | `.ipt`, `.iam`, `.idw`, `.ipn`, `.f3d`, `.f3z` | `application/octet-stream`, `application/zip` | Native part/assembly/drawing/presentation or Fusion archive. | specialist/vendor conversion; safe package inspect where applicable |

#### 3.11.3 Manufacturing, PCB/EDA, and simulation mesh inputs

| Format/family | Extension(s) / dataset | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| G-code / NC | `.gcode`, `.gco`, `.nc`, `.tap`, `.cnc`, `.ngc` | `text/x-gcode`, `text/plain` | Machine/3D-printer motion and control commands. | `text-native` / command parse; never send to machine |
| Gerber | `.gbr`, `.ger`, `.gtl`, `.gbl`, `.gts`, `.gbs`, `.gto`, `.gbo`, `.gm1`, `.pho` | `application/vnd.gerber`, `text/plain` | PCB artwork layers; extensions often encode layer role. | dataset `structured-parse` + preview |
| Excellon drill | `.drl`, `.xln`, `.exc`, `.tap` | `application/vnd.excellon`, `text/plain` | PCB drill/routing commands. | `structured-parse`; never drive machinery |
| IPC-2581 / ODB++ | `.xml`, `.tgz`, `.zip`, `.odb` directory | `application/xml`, archive types | PCB manufacturing/assembly product models. | dataset `archive-expand` + specialist parse |
| KiCad | `.kicad_sch`, `.kicad_pcb`, `.kicad_pro`, `.kicad_mod`, `.kicad_sym`, legacy `.sch`, `.brd` | `text/plain`; application-specific types are commonly unregistered | S-expression/project PCB and schematic source. | `structured-parse` + preview |
| EAGLE | `.sch`, `.brd`, `.lbr` | `application/xml`, generic binary for old versions | Schematic/board/library in XML or legacy binary. | detect then specialist parse + preview |
| Altium | `.schdoc`, `.pcbdoc`, `.prjpcb`, `.intlib` | `application/octet-stream` | OLE/proprietary PCB/schematic/project/library. | specialist static parse; otherwise opaque |
| SPICE | `.cir`, `.sp`, `.spi`, `.ckt`, `.lib`, `.raw` | `text/plain`, generic binary | Circuit netlists/models and simulator outputs. | source parse; `.raw` dialect-aware specialist parse |
| Gmsh / mesh | `.msh`, `.mesh`, `.med`, `.unv` | `application/octet-stream`, `text/plain` | Finite-element mesh/interchange formats. | specialist `structured-parse` + sampled preview |

### 3.12 Ebooks, digital publishing, help, comics, and bibliography

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| EPUB | `.epub` | `application/epub+zip` | Constrained ZIP publication of XHTML/CSS/media/navigation/metadata; EPUB 3 can contain scripts. | `archive-expand` + sanitized `document-convert`; quarantine scripts |
| Kindle/Mobipocket | `.mobi`, `.prc`, `.azw`, `.azw3`, `.azw4`, `.kfx` | `application/x-mobipocket-ebook`, `application/vnd.amazon.ebook` | PalmDB/Mobipocket or Amazon ebook generations, sometimes DRM-protected. | `document-convert` if unprotected; metadata/opaque if DRM |
| FictionBook | `.fb2`, `.fb2.zip` | `application/x-fictionbook+xml`, `application/zip` | XML ebook, often ZIP-compressed. | `structured-parse` / bounded decompress |
| Microsoft Reader | `.lit` | `application/x-ms-reader` | Compressed/possibly DRM-protected ebook. | specialist convert if unprotected; otherwise opaque |
| Sony BBeB | `.lrf`, `.lrx` | `application/octet-stream` | Sony ebook, with LRX protected variant. | specialist convert / opaque |
| Palm ebook | `.pdb`, `.pml`, `.pmlz` | `application/vnd.palm`, `application/zip` | Palm database ebook or PML source/package; `.pdb` is highly ambiguous. | detect then document parse |
| DAISY | `.opf`, `.ncx`, `.smil`, `.xml`, `.html`, `.mp3` dataset/ZIP | `application/oebps-package+xml`, `application/x-dtbncx+xml`, `application/smil+xml` | Accessible talking-book package joining navigation, text, and audio. | dataset `structured-parse` + `media-transcribe`; preserve sync |
| Open eBook/OEBPS | `.opf`, `.oeb`, package directory | `application/oebps-package+xml` | Pre-EPUB publication package. | dataset `structured-parse` |
| Comic archives | `.cbz`, `.cbr`, `.cb7`, `.cbt`, `.cba` | `application/vnd.comicbook+zip`, `application/vnd.comicbook-rar`, archive types | Ordered page images in ZIP/RAR/7z/tar/ACE. | `archive-expand` + per-page OCR/VLM |
| DjVu/PDF ebooks | `.djvu`, `.djv`, `.pdf` | DjVu/PDF types | Fixed/scanned ebook/document containers. | `document-convert` |
| CHM | `.chm` | `application/vnd.ms-htmlhelp` | Compressed HTML help with index, resources, and potentially active content. | sandboxed `archive-expand` + sanitized HTML parse; quarantine |
| Windows Help | `.hlp`, `.cnt`, `.gid` | `application/winhlp`, `application/octet-stream` | Legacy compiled help and indexes. | specialist static extract; quarantine active macros |
| Unix info/man | `.info`, `.info.gz`, man-page names | `application/x-info`, `text/troff` | Texinfo output or roff manual pages, optionally compressed. | `text-native` / bounded decompress |
| Apple docset | `.docset` directory/tar | `application/x-tar`, directory dataset | HTML documentation plus SQLite search index. | dataset parse; sanitize HTML; SQLite read-only |
| BibTeX/BibLaTeX | `.bib`, `.bibtex` | `application/x-bibtex`, `text/plain` | Bibliographic entries and fields. | `structured-parse` |
| RIS | `.ris` | `application/x-research-info-systems`, `text/plain` | Tagged bibliographic exchange. | `structured-parse` |
| EndNote / RefMan / MEDLINE | `.enw`, `.enlx`, `.enl`, `.nbib`, `.medline`, `.ciw`, `.ref` | `application/x-endnote-refer`, `text/plain`, generic binary | Tagged exports or proprietary reference library/database. | text exports parse; library package specialist/opaque |
| CSL / CSL-JSON | `.csl`, `.json` | `application/vnd.citationstyles.style+xml`, `application/json` | Citation style XML and bibliographic JSON. | `structured-parse` |
| Zotero libraries/translators | `.sqlite`, `.rdf`, `.json`, `.js` plus storage | SQLite/RDF/JSON/JavaScript types | Reference database/export and executable translator scripts. | DB/export parse; scripts text-only/quarantine; preserve attachments |

### 3.13 Scientific, geospatial, engineering, and binary instrument formats

#### 3.13.1 General arrays, workspaces, and scientific containers

| Format/family | Extension(s) / dataset | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| HDF4 | `.hdf`, `.h4`, `.hdf4` | `application/x-hdf` | Hierarchical scientific data with datasets, images, tables, metadata. | specialist `structured-parse`; summarize schema/metadata/sample arrays |
| HDF5 | `.h5`, `.hdf5`, `.hdf`, `.he5`, `.h5ad` | `application/x-hdf5`, `application/vnd.hdf` | Extensible self-describing hierarchy used directly and as substrate for many domain formats. | specialist `structured-parse`; profile-detect; never flatten blindly |
| netCDF | `.nc`, `.nc4`, `.cdf` | `application/netcdf`, `application/x-netcdf` | Self-describing array-oriented scientific data; classic and HDF5-backed variants. | specialist `structured-parse`; `.cdf` collision-aware |
| Zarr | `.zarr` directory, `.zarr.zip` | no dependable common type for a directory; `application/zip` when ZIP-packaged | Chunked N-dimensional arrays plus JSON metadata over directory/object-store keys. | dataset `structured-parse`; bound chunk/sample reads |
| NASA CDF | `.cdf` | `application/x-cdf` | Common Data Format for multidimensional scientific variables; not netCDF. | specialist parse after signature detection |
| FITS | `.fits`, `.fit`, `.fts`, `.fz` | `application/fits`, `image/fits` | Astronomy-standard HDUs containing images, tables, spectra, cubes, headers; `.fz` often tile-compressed FITS. | specialist `structured-parse` + previews |
| ROOT | `.root` | `application/x-root` | CERN object/columnar event container with versioned class schemas. | sandboxed specialist parse; avoid loading arbitrary runtime code |
| NumPy | `.npy`, `.npz` | `application/x-npy`, `application/x-npz` | One typed array or ZIP of arrays; object dtype may contain pickles. | safe numeric parse; object arrays `dangerous/quarantine` |
| Python pickle/joblib | `.pkl`, `.pickle`, `.joblib`, `.p` | `application/python-pickle`, `application/octet-stream` | Python object serialization capable of arbitrary code execution on load. | `dangerous/quarantine`; never deserialize; metadata/opaque only |
| MATLAB | `.mat`, `.fig`, `.mlx` | `application/x-matlab-data`, vendor types | MATLAB workspace/figure/live document; versions include proprietary binary and HDF5. | signature/profile parse in isolation; never execute callbacks/code |
| Octave | `.mat`, `.octave`, `.oct` | generic binary/text | MATLAB-compatible or Octave data and dynamically loadable module. | data parse after detection; `.oct` quarantine executable module |
| R serialization | `.rds`, `.rda`, `.rdata` | `application/x-r-data` | Serialized R values/workspaces, possibly containing language objects/environments. | isolated non-evaluating specialist parse; otherwise opaque |
| Julia JLD | `.jld`, `.jld2` | `application/x-hdf5`, generic binary | Julia data serialization based on HDF5 or custom compatible format. | specialist non-executing parse |
| IDL save | `.sav` | `application/x-idl-save`, generic binary | IDL variables/routines save file; `.sav` is broadly ambiguous. | specialist parse after identification; never restore routines |
| Mathematica binary | `.mx`, `.wxf`, `.wdx` | `application/vnd.wolfram.mathematica`, `application/vnd.wolfram.wxf` | System-dependent dump or Wolfram expression/data exchange. | specialist static parse; never evaluate expressions |
| Origin / LabPlot projects | `.opj`, `.opju`, `.ogg`, `.lml` | `application/octet-stream`, `application/xml` | Plotting/analysis projects holding tables, graphs, scripts; `.ogg` collides with audio. | specialist parse; scripts text-only/quarantine |
| Igor Pro waves/experiments | `.ibw`, `.pxp`, `.itx` | `application/octet-stream`, `text/plain` | Binary waves/packed experiments or text wave interchange. | `.itx` parse; binary specialist parse |

#### 3.13.2 GIS vector, map, and location datasets

| Format/family | Extension(s) / dataset | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| Esri Shapefile | `.shp`, `.shx`, `.dbf`, `.prj`, `.cpg`, `.sbn`, `.sbx`, `.qix` set | `application/vnd.shp`, `application/vnd.dbf`, generic types | Multi-file vector geometry, attributes, projection, encoding, indexes. | dataset `structured-parse`; never ingest `.shp` alone as complete |
| GeoPackage | `.gpkg` | `application/geopackage+sqlite3` | SQLite container for vector features, raster tiles, extensions, metadata. | read-only profile-aware `structured-parse` |
| SpatiaLite | `.sqlite`, `.db`, `.sqlite3` | `application/vnd.sqlite3` | SQLite database with spatial metadata/geometries. | read-only profile-aware parse |
| File Geodatabase | `.gdb` directory | `application/octet-stream` | Esri table/index dataset for vector/raster/attachments. | specialist dataset parse; preserve directory atomically |
| Personal Geodatabase | `.mdb` | `application/vnd.ms-access` | Microsoft Access database with Esri spatial tables. | read-only Access/GIS specialist parse |
| KML / KMZ | `.kml`, `.kmz` | `application/vnd.google-earth.kml+xml`, `application/vnd.google-earth.kmz` | XML map features/styles/tours and ZIP package with assets. | hardened `structured-parse`; KMZ `archive-expand`; no remote fetch |
| GML | `.gml`, `.xml` | `application/gml+xml` | Geography Markup Language feature/coverage XML profiles. | hardened schema-aware `structured-parse` |
| GPX | `.gpx` | `application/gpx+xml` | GPS waypoints, routes, and tracks in XML. | `structured-parse` |
| GeoJSON / TopoJSON | `.geojson`, `.topojson`, `.json` | `application/geo+json`, `application/topo+json` | JSON features/geometries or topology. | streaming `structured-parse` |
| GeoJSON text sequences | `.geojsonl`, `.geojsons` | `application/geo+json-seq` | Record-separated/line-oriented geographic JSON features. | streaming `structured-parse` |
| FlatGeobuf | `.fgb` | `application/flatgeobuf` | Spatially indexed binary vector features. | specialist `structured-parse` |
| GeoParquet / GeoArrow | `.parquet`, `.arrow`, `.feather` | Parquet/Arrow media types | Geospatial metadata and geometry columns layered on columnar formats. | profile-aware `structured-parse` |
| MapInfo | `.tab`, `.map`, `.dat`, `.id`; `.mif` + `.mid` | `application/x-mapinfo`, `text/plain`, generic binary | Native multi-file or text interchange vector dataset. | dataset parse; extensions collision-aware |
| Esri coverage/interchange | `.e00`, `.adf` directory | `application/x-e00`, generic binary | Arc/Info interchange or coverage components. | specialist dataset parse |
| S-57/S-101 hydrographic | `.000`, `.001` updates, `.gml` | `application/vnd.ogc.s57`, `application/gml+xml` | Electronic navigational-chart datasets and updates. | update-set-aware specialist parse |
| OpenStreetMap | `.osm`, `.osm.gz`, `.osm.bz2`, `.pbf`, `.osc`, `.osc.gz` | `application/vnd.openstreetmap.data+xml`, `application/x-protobuf` | OSM XML/PBF planet/extract data or change files. | streaming `structured-parse`; bounded geometry summaries |
| MBTiles | `.mbtiles` | `application/vnd.mapbox-vector-tile`, `application/x-sqlite3` | SQLite map tile database containing raster/vector tiles. | read-only profile parse; sample tiles |
| PMTiles | `.pmtiles` | `application/vnd.pmtiles` | Single-file cloud-addressable tile archive. | specialist index/tile parse |
| Vector tiles | `.mvt`, `.pbf` | `application/vnd.mapbox-vector-tile` | Protobuf-encoded tiled vector features; `.pbf` also generic protobuf/OSM. | profile-aware `structured-parse` |
| GeoPackage/QGIS/ArcGIS projects | `.qgz`, `.qgs`, `.mxd`, `.aprx`, `.lyr`, `.lyrx` | ZIP/XML/generic binary/JSON types | Map project/layout/style documents referencing external datasets. | package/text parse; proprietary projects specialist; do not fetch links |
| Styles | `.sld`, `.qml`, `.lyrx`, `.mapbox`, `.mbstyle` | XML/JSON/text types | GIS cartographic styling and rules; may contain expressions/links. | `structured-parse`; safe preview only |

#### 3.13.3 Earth science, meteorology, seismology, and subsurface

| Format/family | Extension(s) / dataset | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| GRIB / GRIB2 | `.grb`, `.grib`, `.grb2`, `.grib2` | `application/wmo-grib` | WMO gridded meteorological fields with repeated messages. | streaming domain parse; metadata/statistics/maps |
| BUFR | `.bufr`, `.bfr` | `application/wmo-bufr` | WMO binary observational-data messages driven by tables. | specialist `structured-parse`; table/version aware |
| HDF-EOS | `.he2`, `.he4`, `.he5`, `.hdf`, `.h5` | HDF media types | NASA Earth-observation grid/swath/point profiles over HDF4/HDF5. | profile-aware HDF parse + previews |
| NetCDF-CF | `.nc`, `.nc4` | netCDF types | Climate/forecast-convention metadata over netCDF arrays. | profile-aware parse; variables/coordinates/units summary |
| SAFE/SAFE-like products | `.safe` directory/ZIP, XML + imagery | XML/archive/image types | Mission product packages joining manifests, measurement data, calibration, quality. | dataset parse; preserve package topology |
| SEG-Y | `.sgy`, `.segy`, `.seg`, `.su` | `application/x-segy` | Seismic traces with textual/binary headers; `.su` is Seismic Unix variant. | specialist streaming parse; sampled traces/headers |
| SEG-D | `.segd`, `.sgd`, tape/image datasets | `application/octet-stream` | Field-recorded seismic acquisition format with many revisions. | specialist parse; otherwise opaque |
| LAS well log | `.las` | `application/x-las`, `text/plain` | Log ASCII Standard well curves/metadata; collides with point-cloud LAS. | header/content detect then `structured-parse` |
| DLIS / LIS | `.dlis`, `.lis` | `application/octet-stream` | Binary well-log acquisition/interchange. | specialist parse |
| SEED / miniSEED | `.seed`, `.mseed`, `.miniseed`, `.msd` | `application/vnd.fdsn.mseed` | Seismological waveform/metadata volumes or compact records. | specialist `structured-parse` + waveform summary |
| SAC / GSE | `.sac`, `.gse`, `.gse2` | `application/octet-stream`, `text/plain` | Seismic waveform formats and exchange messages. | specialist parse |
| Weather radar | `.nexrad`, `.ar2v`, `.uf`, `.iris`, `.sigmet` | `application/octet-stream` | Radar volumes/rays in operational/vendor formats. | specialist parse + bounded imagery |
| Geophysical grids | `.grd`, `.gxf`, `.ers`, `.isg`, `.gtx` | text/generic binary | Gravity, magnetic, geoid, and surface grid/interchange formats. | specialist GIS parse + preview |

#### 3.13.4 Astronomy, chemistry, structural biology, and mass spectrometry

| Domain format/family | Extension(s) / dataset | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| VOTable | `.vot`, `.votable`, `.xml` | `application/x-votable+xml` | XML tabular astronomical results with typed fields and binary encodings. | hardened `structured-parse` |
| ASDF | `.asdf` | `application/x-asdf` | Astronomy metadata tree plus binary array blocks, YAML-based. | specialist `structured-parse` |
| CASA Measurement Set | `.ms` directory | `application/octet-stream` | Directory/table dataset for radio-interferometry observations. | specialist dataset parse; summary/sample only |
| XISF | `.xisf` | `application/x-xisf` | Extensible astronomical image container. | specialist metadata/image derivation |
| Crystallographic CIF/mmCIF | `.cif`, `.mmcif`, `.mcif` | `chemical/x-cif` | Text data blocks for crystal/macromolecular structures and metadata. | domain `structured-parse` |
| Protein Data Bank | `.pdb`, `.ent`, `.pdb1` | `chemical/x-pdb` | Fixed-column macromolecular coordinates/annotations; `.pdb` collides with debug/Palm DB. | profile-detect `structured-parse` + 3D preview |
| BinaryCIF | `.bcif` | `application/octet-stream` | Compact binary macromolecular structure. | specialist parse + preview |
| MOL / SDF / RXN / RD | `.mol`, `.sdf`, `.sd`, `.rxn`, `.rdf` | `chemical/x-mdl-molfile`, `chemical/x-mdl-sdfile`, `chemical/x-mdl-rxnfile` | CTfile molecule/reaction/database records; `.rdf` collides with RDF/XML. | chemistry `structured-parse` |
| SMILES / SMARTS | `.smi`, `.smiles`, `.sma`, `.smarts` | `chemical/x-daylight-smiles`, `text/plain` | Line-oriented chemical structures/patterns; `.smi` collides with captions. | chemistry grammar parse |
| MOL2 | `.mol2` | `chemical/x-mol2` | Tripos molecular structure with atoms/bonds/substructures. | chemistry parse + preview |
| Chemical Markup Language | `.cml` | `chemical/x-cml`, `application/xml` | XML chemical structures/reactions/properties. | hardened domain parse |
| XYZ / PQR / PSF | `.xyz`, `.pqr`, `.psf` | `chemical/x-xyz`, `chemical/x-pqr`, generic text | Atom coordinates/charges or molecular topology; extensions collide. | domain/dialect parse + preview |
| Gaussian | `.gjf`, `.com`, `.log`, `.out`, `.fchk`, `.chk`, `.cube`, `.cub` | `chemical/x-gaussian-input`, `chemical/x-gaussian-log`, `chemical/x-gaussian-cube` | Quantum-chemistry inputs, logs, checkpoint, and volumetric grids. | text outputs parse; binary checkpoint specialist/opaque |
| Molden / MOPAC | `.molden`, `.mop`, `.mopout`, `.arc` | `chemical/x-molden`, text/generic types | Quantum-chemistry interchange/input/output. | text/domain parse after collision detection |
| Molecular dynamics topology/trajectory | `.gro`, `.top`, `.itp`, `.tpr`, `.xtc`, `.trr`, `.dcd`, `.nc`, `.prmtop`, `.inpcrd`, `.rst7`, `.psf` | chemical/generic binary/text | GROMACS, CHARMM, AMBER topology, coordinates, trajectories. | dataset specialist parse; sampled frames + metadata |
| CCP4/MRC maps | `.mrc`, `.map`, `.ccp4` | `application/octet-stream` | Electron-density/cryo-EM 3D grids; extensions collide. | specialist volume parse + render |
| JCAMP-DX | `.jdx`, `.dx`, `.jcamp` | `chemical/x-jcamp-dx` | Text spectroscopy/chromatography data. | domain `structured-parse` |
| SPC / SPE spectroscopy | `.spc`, `.sp`, `.spe` | `application/octet-stream` | Vendor/binary spectral or detector image files. | specialist parse; otherwise opaque |
| mzML / mzXML | `.mzml`, `.mzxml` | `application/mzml+xml`, `application/mzxml+xml` | XML mass-spectrometry run data, often with encoded arrays. | streaming domain parse |
| mzIdentML / mzTab / MGF | `.mzid`, `.mztab`, `.mgf` | `application/mzidentml+xml`, `text/plain` | Proteomics identification/results and peak-list exchange. | domain `structured-parse` |
| imzML | `.imzml` + `.ibd` | `application/imzml+xml`, generic binary | XML metadata plus binary mass-spectrometry imaging data. | pair-aware domain parse + image preview |
| Vendor mass-spec RAW | `.raw` file or directory, `.wiff` + `.scan`, `.d` directory, `.lcd` | vendor/generic binary | Thermo, SCIEX, Bruker/Agilent, Shimadzu raw acquisition formats. | vendor/specialist metadata/convert; otherwise opaque |
| NMR datasets | Bruker directory (`fid`, `ser`, `acqus`), `.fid`, `.ft`, `.ucsf` | generic binary/text | Time/frequency-domain NMR data plus acquisition metadata. | dataset specialist parse + spectra preview |

#### 3.13.5 Genomics, proteomics, and bioinformatics

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| FASTA | `.fa`, `.fasta`, `.fna`, `.faa`, `.ffn`, `.frn`, `.fas` | `text/x-fasta` | Header plus nucleotide/protein sequences. | streaming `structured-parse` |
| FASTQ | `.fq`, `.fastq`, often `.gz` | `text/x-fastq` | Sequencing reads plus per-base quality values; dialects vary. | streaming parse; bounded sampling/statistics |
| SAM | `.sam` | `application/sam`, `text/plain` | Text Sequence Alignment/Map records. | streaming domain parse |
| BAM / CRAM | `.bam`, `.cram` | `application/bam`, `application/cram` | Binary/compressed alignments; CRAM may require reference sequence. | specialist parse; no unapproved reference fetch |
| Alignment indexes | `.bai`, `.csi`, `.crai`, `.sai` | `application/octet-stream` | Index sidecars for BAM/CRAM/SAM-era tools. | sidecar metadata; associate with primary file |
| VCF / gVCF | `.vcf`, `.gvcf`, often `.gz`, `.bgz` | `text/vcard` is wrong in some systems; commonly `text/x-vcf`/`application/vcf` | Genomic variant records; `.vcf` collides with vCard. | header-detect streaming parse |
| BCF | `.bcf` | `application/bcf` | Binary VCF encoding. | specialist domain parse |
| Variant indexes | `.tbi`, `.csi` | `application/octet-stream` | Tabix/CSI indexes for genomic positional files. | associate with primary; metadata only |
| BED / bedGraph | `.bed`, `.bedgraph`, `.bdg` | `text/bed`, `text/plain` | Genomic intervals and signal annotations; column conventions vary. | streaming profile parse |
| BigBed / BigWig | `.bb`, `.bigbed`, `.bw`, `.bigwig` | `application/x-bigbed`, `application/x-bigwig` | Indexed binary genomic intervals/signal. | specialist range/sample parse |
| GFF / GTF | `.gff`, `.gff2`, `.gff3`, `.gtf` | `text/gff3`, `text/plain` | Genome feature annotations and attributes. | streaming domain parse |
| GenBank / EMBL | `.gb`, `.gbk`, `.gbff`, `.genbank`, `.embl` | `text/x-genbank`, `text/x-embl` | Annotated sequence flat files. | `structured-parse` |
| GFA / assembly graphs | `.gfa`, `.gfa1`, `.gfa2`, `.rgfa` | `text/plain` | Sequence assembly graph records. | streaming domain parse |
| 2bit / nib | `.2bit`, `.nib` | `application/octet-stream` | Compact indexed reference-sequence encodings. | specialist parse |
| SRA / archive reads | `.sra`, `.lite.sra` | `application/octet-stream` | NCBI sequencing run archive container. | specialist metadata/sample extraction; raw retain |
| PLINK | `.bed` + `.bim` + `.fam`; `.pgen` + `.pvar` + `.psam` | generic binary/text | Genotype matrix plus variant/sample metadata; `.bed` collides with text BED. | dataset/signature-aware domain parse |
| Microarray/genotyping | `.cel`, `.chp`, `.idat`, `.gtc`, `.ped`, `.map` | vendor/generic types | Intensity/genotype results and manifests in vendor/text formats. | specialist parse; text pairs structured |
| Phylogenetic | `.newick`, `.nwk`, `.tree`, `.tre`, `.nex`, `.nexus`, `.phy`, `.phylip` | `text/x-nh`, `text/plain` | Trees, alignments, and analysis blocks. | grammar-aware `structured-parse` |
| SBML / BioPAX | `.sbml`, `.owl`, `.rdf`, `.xml` | `application/sbml+xml`, `application/rdf+xml` | Systems-biology models and pathway graphs. | hardened schema-aware parse |

#### 3.13.6 Clinical and biomedical data interchange

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| HL7 v2 ER7 | `.hl7`, `.er7`, `.txt` | `application/hl7-v2`, `text/plain` | Delimiter-encoded clinical messages with segments, fields, escapes, version/profile semantics. | profile/version-aware `structured-parse`; sensitive |
| FHIR | `.json`, `.xml`, `.ttl`, `.ndjson` | `application/fhir+json`, `application/fhir+xml`, `application/fhir+turtle`, `application/fhir+ndjson` | HL7 FHIR resources serialized as JSON, XML, Turtle, or bulk NDJSON. | hardened profile-aware `structured-parse`; sensitive |
| CDA / CCD | `.xml`, `.cda` | `application/cda+xml`, `application/xml` | HL7 Clinical Document Architecture document and continuity-of-care profiles. | hardened `document-convert` + schema-aware parse |
| C-CDA package | `.xml`, `.zip`, attachments | XML/archive/media types | Clinical document with referenced/packaged attachments and templates. | dataset parse; recurse attachments; sensitive |
| openEHR archetypes/templates | `.adl`, `.adls`, `.opt`, `.xml` | `text/plain`, `application/xml` | Clinical information-model archetypes and operational templates. | grammar/schema-aware `structured-parse` |
| CDISC ODM / Define-XML | `.xml` | `application/odm+xml`, `application/xml` | Clinical-study data/metadata and regulatory dataset definitions. | hardened domain parse |
| CDISC transport/data | `.xpt`, `.sas7bdat`, `.csv` | SAS/delimited types | SDTM/ADaM datasets commonly delivered in SAS transport or tabular files. | domain-aware table parse; controlled terminology/labels |
| IHE XDM/XDS export | `.zip`, `INDEX.HTM`, XML/PDF/DICOM members | archive/HTML/XML/document types | Healthcare document-sharing media/package with manifests and clinical documents. | `archive-expand` + profile parse; preserve package/provenance |

#### 3.13.7 Physiological signals, neurophysiology, and laboratory acquisition

| Format/family | Extension(s) / dataset | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| EDF / BDF | `.edf`, `.bdf`, `.rec` | `application/edf`, `application/octet-stream` | European Data Format and BioSemi extension for multichannel physiological signals. | specialist signal parse + metadata/preview |
| WFDB | `.hea` + `.dat` + `.atr`/annotation files | `text/plain`, generic binary | PhysioNet waveform header, samples, annotations. | dataset domain parse; preserve sidecars |
| SCP-ECG | `.scp`, `.ecg` | `application/octet-stream` | Standardized compressed electrocardiogram records. | specialist parse |
| DICOM waveform | `.dcm` | `application/dicom` | ECG/EEG/hemodynamic/audio waveform object within DICOM. | DICOM specialist parse + signal preview |
| BrainVision | `.vhdr` + `.eeg` + `.vmrk` | `text/plain`, generic binary | EEG header, samples, and marker sidecars. | dataset specialist parse |
| EEGLAB | `.set` + optional `.fdt` | MATLAB/generic binary | MATLAB-based EEG dataset plus external float data. | pair-aware specialist parse; never execute MATLAB objects |
| FIF | `.fif`, `.fif.gz` | `application/octet-stream`, `application/gzip` | MNE/Neuromag MEG/EEG data container. | specialist parse |
| XDF | `.xdf` | `application/x-xdf` | Extensible Data Format for synchronized multimodal streams. | specialist stream parse |
| NWB | `.nwb` | `application/x-hdf5` | Neurodata Without Borders profile over HDF5. | profile-aware HDF5 parse |
| Neuralynx / Plexon / Blackrock | `.ncs`, `.nev`, `.ntt`, `.nse`, `.nex`, `.plx`, `.pl2`, `.nsx` | vendor/generic binary | Electrophysiology events/spikes/continuous recordings. | vendor/specialist parse; otherwise opaque |
| LabChart / AcqKnowledge | `.adicht`, `.acq` | `application/octet-stream` | Proprietary physiological acquisition projects/data. | specialist/vendor extract; otherwise opaque |

#### 3.13.8 Engineering telemetry, simulation, and ML model files

| Format/family | Extension(s) / dataset | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| NI TDMS | `.tdms`, `.tdm`, `.tdx` | `application/octet-stream` | National Instruments measurement channels/properties; TDM may use XML + binary sidecar. | specialist parse; pair-aware |
| ASAM MDF | `.mdf`, `.mf4`, `.dat` | `application/octet-stream` | Automotive measurement data, channels, conversions, attachments. | specialist streaming parse |
| CAN logs/database | `.blf`, `.asc`, `.trc`, `.log`, `.dbc`, `.arxml` | generic binary/text/XML | CAN/LIN bus traces and signal/network definitions. | trace + schema-aware specialist parse |
| ROS bag / MCAP | `.bag`, `.db3`, `.mcap` | `application/octet-stream`, `application/x-sqlite3` | Timestamped robotics message archives and schemas. | specialist bounded `structured-parse`; recurse embedded payload profiles |
| Fitness/activity | `.fit`, `.tcx`, `.gpx`, `.pwx` | `application/vnd.ant.fit`, `application/vnd.garmin.tcx+xml`, `application/gpx+xml` | Binary/XML activity, track, lap, sensor records. | domain `structured-parse` |
| VTK legacy/XML | `.vtk`, `.vti`, `.vtr`, `.vts`, `.vtu`, `.vtp`, `.pvtu`, `.pvd` | `application/vnd.vtk`, `application/xml` | Scientific visualization datasets and parallel/time collections. | specialist parse + sampled render |
| XDMF | `.xmf`, `.xdmf` plus HDF5 | `application/xml` | XML topology/metadata referencing heavy array data. | dataset parse; bound local references |
| CGNS | `.cgns` | `application/x-hdf5`, `application/octet-stream` | CFD data model over HDF5 or ADF. | specialist parse |
| Exodus / MED | `.e`, `.exo`, `.ex2`, `.med` | netCDF/HDF5/generic types | Finite-element meshes/results. | specialist parse + sampled render |
| OpenFOAM | case directory with dictionaries/time folders | `text/plain`, generic binary | Directory dataset for CFD mesh, fields, and configuration. | dataset parse; never execute utilities/functions |
| Abaqus | `.inp`, `.odb`, `.fil`, `.dat`, `.msg`, `.sta` | `text/plain`, generic binary | Solver input and proprietary results/logs. | text parse; binary specialist/vendor extract |
| ANSYS / Nastran / LS-DYNA | `.cdb`, `.rst`, `.rth`, `.bdf`, `.nas`, `.op2`, `.pch`, `.k`, `.d3plot` | text/generic binary | Solver models, decks, and results. | text deck parse; binary specialist parse |
| Semiconductor waveforms/layout | `.vcd`, `.fst`, `.fsdb`, `.wlf`, `.gds`, `.gdsii`, `.oas`, `.oasis` | text/generic binary | HDL waveforms and IC layout databases. | specialist parse; sampled signals/layout preview |
| ONNX | `.onnx` | `application/onnx`, `application/x-protobuf` | Protobuf machine-learning computation graph and tensors. | static `structured-parse`; never execute custom ops |
| TensorFlow | `saved_model.pb` directory, `.pb`, `.tflite`, `.ckpt`, `.index`, `.data-*` | protobuf/generic binary | Graph/model/checkpoint formats and directory assets. | dataset static metadata/tensor parse; never load custom ops |
| PyTorch | `.pt`, `.pth`, `.bin` | `application/octet-stream` | Model/checkpoint, often pickle-backed and unsafe to load. | `dangerous/quarantine`; only safe tensor-aware parser if proven |
| SafeTensors | `.safetensors` | `application/octet-stream` | Header-described tensor storage designed without pickle execution. | bounded tensor metadata/statistics parse |
| Keras / HDF5 models | `.keras`, `.h5`, `.hdf5` | `application/zip`, HDF5 types | ZIP-based Keras model or HDF5 model/weights, possibly custom objects/config. | static parse only; never instantiate custom code |
| GGUF / GGML | `.gguf`, `.ggml`, `.bin` | `application/octet-stream` | Quantized LLM metadata and tensors. | bounded static metadata/tensor inventory |
| Core ML | `.mlmodel`, `.mlpackage`, `.mlmodelc` | `application/octet-stream`, directory package | Protobuf model spec, package, or compiled model. | static metadata parse; compiled form opaque |
| PMML / PFA | `.pmml`, `.pfa`, `.json` | `application/xml`, `application/json` | Predictive-model markup or portable scoring function. | hardened `structured-parse`; never score during ingest |
| scikit-learn/joblib | `.joblib`, `.pkl`, `.pickle` | generic binary | Python pickle-based estimator persistence. | `dangerous/quarantine`; never deserialize |

### 3.14 Databases, database dumps, key-value stores, and search/vector indexes as files

#### 3.14.1 Relational and desktop databases

| Format/family | Extension(s) / dataset | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| SQLite 3 | `.sqlite`, `.sqlite3`, `.db`, `.db3`, plus `-wal`, `-shm`, `-journal` companions | `application/vnd.sqlite3`, `application/x-sqlite3` | Single main database plus possible transactional companion files; application schema defines meaning. | copied-snapshot, read-only `structured-parse`; preserve companions if live capture |
| DuckDB | `.duckdb`, `.ddb`, `.db` | `application/vnd.duckdb`, generic binary | Embedded analytical database file. | version-matched read-only `structured-parse` |
| Microsoft Access / Jet / ACE | `.mdb`, `.accdb`, `.mde`, `.accde`, `.accdr`, `.ldb`, `.laccdb` | `application/vnd.ms-access`, `application/x-msaccess` | Desktop relational database/application; can contain forms, macros, VBA; lock files are not the DB. | read-only data/schema parse; active objects `dangerous/quarantine` |
| SQL Server files/backups | `.mdf`, `.ndf`, `.ldf`, `.bak`, `.trn`, `.bacpac`, `.dacpac` | `application/octet-stream`, `application/zip` | Database/data/log, backup, or ZIP package of schema/data. | package parse where documented; native files specialist/opaque; never attach to privileged server |
| PostgreSQL dumps/backups | `.sql`, `.dump`, `.backup`, `.tar`, `.toc`, base-backup directory/tar | `application/sql`, archive/generic types | Plain SQL or pg_dump custom/tar/directory archives and physical cluster backup. | static parse/isolated restore with no untrusted procedural execution; physical backup opaque |
| MySQL/MariaDB | `.sql`, `.ibd`, `.frm`, `.myd`, `.myi`, `.mai`, `.mad`, `ibdata*`, binlogs | SQL/generic binary | Logical dumps or InnoDB/MyISAM/Aria physical tables and logs. | SQL static parse; physical files version-matched specialist parse/opaque |
| Oracle | `.dmp`, `.dbf`, `.ctl`, `.log`, `.arc`, `.trc` | `application/octet-stream`, `text/plain` | Data Pump/export dump or database data/control/redo/archive/trace files. | dump specialist parse/isolated import; physical files opaque |
| Firebird/InterBase | `.fdb`, `.gdb`, `.fbk`, `.gbk` | `application/x-firebird` | Database or backup file. | version-matched read-only/sandboxed restore |
| IBM Db2 exchange/backup | `.ixf`, `.del`, `.wsf`, `.backup`, `.db2` | `text/plain`, `application/octet-stream` | PC/IXF or delimited exchange, scripts, and backup images. | exchange parse; backups specialist/opaque |
| H2 / HSQLDB / Derby | `.mv.db`, `.h2.db`, `.script`, `.properties`, `.data`, `.backup`, Derby directory | text/generic binary | Java embedded databases with multiple companion files or directory stores. | dataset copied-snapshot parse in isolated runtime; never execute aliases/triggers |
| dBASE/FoxPro | `.dbf`, `.fpt`, `.dbt`, `.cdx`, `.idx`, `.dbc`, `.dcx`, `.dct` | `application/x-dbf`, generic binary | Table, memo, index, and database-container sets. | dataset `structured-parse`; code-page/version aware |
| Paradox | `.db`, `.px`, `.mb`, `.val`, `.xg0`, `.yg0` | `application/octet-stream` | Multi-file desktop relational tables/indexes/memos. | specialist dataset parse |
| Btrieve/Pervasive | `.btr`, `.dta`, `.mkd` | `application/octet-stream` | Indexed-record database files. | specialist parse; otherwise opaque |
| FileMaker | `.fmp12`, `.fp7`, `.fp5`, `.fp3` | `application/vnd.filemaker`, `application/octet-stream` | Proprietary database/application file with scripts/layouts. | vendor/specialist extract; scripts quarantine |
| OpenDocument/LibreOffice Base | `.odb` | `application/vnd.oasis.opendocument.database` | ZIP package containing forms/queries/reports/config and possibly embedded HSQLDB/Firebird. | `archive-expand` + read-only DB/document parse; quarantine macros |

#### 3.14.2 NoSQL, key-value, graph, and application stores

| Format/family | Extension(s) / dataset | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| MongoDB dump | `.bson` + `.metadata.json`, archive `.archive` | `application/bson`, `application/json`, generic binary | Collection records and metadata, optionally in mongodump archive. | profile-aware `structured-parse`; no server-side JS execution |
| MongoDB/WiredTiger physical store | `WiredTiger*`, `.wt`, `.turtle`, journal directory | `application/octet-stream` | Versioned storage-engine files requiring a consistent dataset. | `binary-opaque` unless isolated version-matched forensic adapter |
| LevelDB | directory with `.ldb`, `.sst`, `.log`, `MANIFEST-*`, `CURRENT` | `application/octet-stream` | LSM-tree key/value store and logs. | copied-dataset specialist parse; application values recursively identify |
| RocksDB | directory with `.sst`, `.blob`, `.log`, `MANIFEST-*`, `OPTIONS-*` | `application/octet-stream`, `text/plain` | LSM-tree store with column families and versioned table formats. | copied-dataset specialist parse; values profile-dependent |
| LMDB | `data.mdb`, `lock.mdb` | `application/octet-stream` | Memory-mapped B+tree key/value environment. | copied-snapshot read-only parse; never open live writable |
| Berkeley DB | `.db`, `.db3`, `.db4`, environment logs | `application/octet-stream` | B-tree/hash/queue database and transactional environment. | version-aware read-only specialist parse |
| Redis | `.rdb`, `.aof`, `.manifest`/multipart AOF set | `application/x-redis-dump`, `text/plain`/binary | Point-in-time key dump or append-only command log. | isolated parser; never replay commands into privileged server |
| CouchDB | `.couch` | `application/octet-stream` | Append-only database file with documents/revisions/attachments. | version-matched specialist parse |
| Realm | `.realm`, `.realm.lock`, `.realm.management` | `application/octet-stream` | Mobile object database and companion state. | copied-snapshot specialist parse |
| Browser stores | `History`, `Cookies`, `places.sqlite`, `.ldb`, `.log`, IndexedDB directory | SQLite/LevelDB types | Browser history/cookies/bookmarks/storage databases. | read-only profile parse; sensitive; recurse stored blobs cautiously |
| Mobile app stores | `.sqlite`, `.db`, `.realm`, Core Data SQLite + sidecars | SQLite/Realm types | Application-specific local databases. | schema-discovery parse; sensitive; no live app access |
| Neo4j store/backup | store directory, `.backup`, transaction logs | `application/octet-stream` | Native graph records/indexes and backup sets. | specialist/isolated version-matched parse; otherwise opaque |
| RDF stores | Jena TDB/TDB2 directories, RDF4J NativeStore, Blazegraph `.jnl` | `application/octet-stream` | Native triple/quad store and indexes. | prefer RDF export; native files specialist/opaque |

#### 3.14.3 Logical dumps, logs, and migration/export forms

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| SQL dump | `.sql`, `.ddl`, `.dmp` when textual | `application/sql`, `text/plain` | DDL/DML/procedural text, possibly huge and vendor-specific. | streaming parse; never execute; extract schema/comments/data safely |
| CSV/TSV export | `.csv`, `.tsv`, `.txt` | delimited-text types | Logical table extract, often one file per table. | streaming `structured-parse` |
| JSON/BSON/NDJSON export | `.json`, `.jsonl`, `.ndjson`, `.bson` | structured types | Logical document export. | streaming `structured-parse` |
| XML database export | `.xml`, `.xml.gz` | `application/xml`, `application/gzip` | Vendor/schema-specific table/document dump. | hardened streaming parse |
| Change-data-capture logs | `.wal`, `.binlog`, `.avro`, `.jsonl`, `.parquet` | generic/profile types | Ordered database change events, often schema-registry dependent. | specialist profile-aware parse; preserve ordering/offsets |
| Liquibase/Flyway migrations | `.xml`, `.yaml`, `.json`, `.sql` | XML/YAML/JSON/SQL types | Schema-change declarations or scripts. | `structured-parse`; never apply |

#### 3.14.4 Search, analytics, and vector index files

| Index/family | Extension(s) / dataset | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| Apache Lucene | index directory: `segments_N`, `.si`, `.cfs`, `.cfe`, `.fnm`, `.fdx`, `.fdt`, `.tim`, `.tip`, `.doc`, `.pos`, `.pay`, `.dvd`, `.dvm`, etc. | `application/octet-stream` | Versioned multi-file inverted/vector/doc-values index. | `binary-opaque` by default; version-matched read-only inventory only |
| Elasticsearch/Solr | node/core directories, translog, Lucene files, snapshots | `application/octet-stream`, JSON metadata | Distributed-engine state layered on Lucene. | prefer snapshot/export API; raw files opaque, never start cluster on them |
| Xapian / Whoosh | database/index directory, `.toc`, `.DB`, segment files | `application/octet-stream` | Native full-text indexes. | version-matched specialist metadata; otherwise opaque |
| Tantivy | index directory with `.store`, `.term`, `.idx`, `.pos`, metadata JSON | generic binary/JSON | Rust search index segments. | version-matched read-only specialist parse/opaque |
| FAISS | `.faiss`, `.index` | `application/octet-stream` | Serialized vector index, possibly without source IDs/text semantics. | specialist metadata only; treat deserialization as untrusted |
| Annoy / HNSWlib | `.ann`, `.hnsw`, `.bin`, `.index` | `application/octet-stream` | Approximate-nearest-neighbor graph/tree index. | `binary-opaque` unless versioned safe parser and sidecar schema exist |
| Lance / LanceDB | directory dataset with manifests/fragments/indices | `application/vnd.apache.arrow.file`, generic binary | Versioned Arrow-based table and vector/scalar indexes. | dataset-aware specialist parse; do not treat index as authority |
| Qdrant/Milvus/Chroma snapshots | `.snapshot`, `.tar`, `.db`, SQLite/segment directories | archive/SQLite/generic binary | Engine-specific collection snapshot and indexes. | prefer logical export; static metadata/opaque; never restore to privileged service |

**Inference:** a database or index file should never be “opened in place” from a live upload path.
Take an immutable copy/snapshot, use read-only or isolated tools, disable extensions/UDFs/triggers,
and treat its schema plus application profile as part of format identification. Indexes are normally
rebuildable projections; without their source records and mapping metadata they may be nearly
meaningless as memory evidence.

### 3.15 Certificates, keys, signatures, encrypted objects, and trust stores

| Format/family | Extension(s) / names | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| PEM envelope | `.pem`, `.crt`, `.cer`, `.key`, `.csr`, `.pub`, extensionless key files | `application/pem-certificate-chain`, `application/x-pem-file`, `text/plain` | Base64 armor around typed certificate/key/request/parameter blocks. | parse labels/public metadata; private/secret blocks restricted `dangerous/quarantine` |
| X.509 certificate DER | `.cer`, `.crt`, `.der` | `application/pkix-cert` | Binary certificate. | `structured-parse` metadata/chain; no trust assertion merely from parsing |
| Certificate request | `.csr`, `.p10` | `application/pkcs10` | PKCS #10 certificate signing request. | parse subject/key/extensions/signature metadata |
| Certificate revocation list | `.crl` | `application/pkix-crl` | Signed X.509 revocation list. | `structured-parse`; verification/time context explicit |
| PKCS #7 / CMS | `.p7b`, `.p7c`, `.p7m`, `.p7s`, `.cms` | `application/pkcs7-mime`, `application/pkcs7-signature` | Certificate bundle, signed data, detached signature, or encrypted envelope. | parse/verify metadata; decrypt only with authorized key |
| PKCS #8 | `.p8`, `.pk8`, `.key`, `.der`, `.pem` | `application/pkcs8`, `application/pkcs8-encrypted` | Private-key container, plain or encrypted. | secret quarantine; never expose body in derived Markdown |
| PKCS #12 | `.p12`, `.pfx` | `application/pkcs12` | Password-protected bundle of private keys/certificates. | restricted secret quarantine; metadata only if safely unlocked by authorized workflow |
| Java keystore / truststore | `.jks`, `.keystore`, `.truststore`, `.jceks`, `.bcfks` | `application/octet-stream` | Password-protected key/certificate store, provider/version dependent. | restricted specialist metadata; never load as application trust automatically |
| OpenSSH keys/config | `id_rsa`, `id_ed25519`, `.pub`, `authorized_keys`, `known_hosts`, `config` | `text/plain`, `application/ssh-key` | Private/public keys, authorized keys, host fingerprints, and client config. | public/config parse; private keys secret quarantine |
| OpenPGP | `.pgp`, `.gpg`, `.asc`, `.sig` | `application/pgp-encrypted`, `application/pgp-keys`, `application/pgp-signature` | Encrypted/signed data, keys, or ASCII armor. | packet metadata/verification; decrypt only with authorized key; secret keys restricted |
| age / minisign | `.age`, `.agekey`, `.minisig`, `.pub` | `application/age`, `text/plain` | Modern file encryption, identities/recipients, and detached signatures. | ciphertext/signature metadata; secret identities quarantine |
| JOSE | `.jwt`, `.jws`, `.jwe`, JSON | `application/jwt`, `application/jose`, `application/jose+json` | Signed/encrypted JSON claims or compact serialization. | syntax/signature metadata parse; do not treat unverified claims as trusted |
| COSE / CWT | `.cose`, `.cbor` | `application/cose`, `application/cose-key`, `application/cwt` | CBOR signing/encryption/key/token structures. | `structured-parse`; verification status explicit |
| XML signatures/encryption | `.xml`, `.sig` | `application/xml` | XMLDSig/XMLEnc embedded or detached security structures. | hardened parse; verify with explicit trust policy; no external resolution |
| SSH/PGP agent/key databases | `pubring.kbx`, `secring.gpg`, `private-keys-v1.d`, agent sockets | generic binary/directory | Keyring and secret-key datasets. | restricted `binary-opaque`/specialist metadata; never ingest sockets/live state |
| Checksums | `.md5`, `.sha1`, `.sha256`, `.sha512`, `.b2`, `CHECKSUMS` | `text/plain` | Filename-to-digest manifests, not signatures. | `structured-parse`; verify bytes if present; never call authenticity |
| Detached signatures | `.sig`, `.sign`, `.asc`, `.minisig`, `.cosign` | profile-specific signature types | Signature over another file; identity depends on scheme and companion object. | pair-aware verification; preserve algorithm/key/time/result |
| Encrypted generic files | `.enc`, `.encrypted`, `.crypt`, `.aes`, `.gpg`, `.age` | `application/octet-stream`, scheme types | Ciphertext wrapper whose inner type may be hidden. | `binary-opaque`; authorized decrypt then re-identify; never brute-force |
| Password vaults | `.kdb`, `.kdbx`, `.1pif`, `.opvault`, `.agilekeychain`, `.psafe3` | `application/x-keepass2`, generic archive/binary | Encrypted credential databases/exports. | secret `dangerous/quarantine`; metadata only unless explicit high-authority workflow |

### 3.16 Executables, shortcuts, firmware, ROMs, platform metadata, and miscellaneous interchange

#### 3.16.1 Executable and active platform artifacts

| Format/family | Extension(s) / shape | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| Windows PE/COFF | `.exe`, `.dll`, `.sys`, `.drv`, `.ocx`, `.cpl`, `.scr`, `.efi`, `.mui`, `.ax` | `application/vnd.microsoft.portable-executable`, `application/x-msdownload` | Executable/library/driver/resource images. | `dangerous/quarantine`; static headers/imports/strings/signatures only |
| DOS executables | `.com`, `.exe`, `.sys`, `.bat` | `application/x-msdos-program` | DOS binary or script program. | `dangerous/quarantine`; static metadata only |
| Unix ELF | extensionless, `.elf`, `.so`, `.ko`, `.o`, `.out`, `.bin` | `application/x-elf`, `application/x-sharedlib`, generic binary | Executable, shared object, kernel module, or relocatable object. | `dangerous/quarantine`; static metadata only |
| Mach-O / Apple bundles | extensionless, `.dylib`, `.bundle`, `.app`, `.framework`, `.plugin`, `.kext` | `application/x-mach-binary`, bundle/directory types | macOS/iOS executable code and structured bundles. | quarantine; safe bundle enumerate/static code-sign metadata |
| Scripts with active handlers | `.cgi`, `.fcgi`, `.asp`, `.aspx`, `.jsp`, `.jspx`, `.hta`, `.wsf` | text/XML/server-specific types | Server/client executable scripts and applications. | text-only ingest; `dangerous/quarantine`; never host/execute |
| Windows shortcuts | `.lnk`, `.url`, `.website`, `.library-ms`, `.search-ms` | `application/x-ms-shortcut`, `text/plain`, `application/xml` | Binary or text/XML links capable of launching local/remote targets. | static `structured-parse`; never follow/launch automatically |
| macOS aliases/bookmarks | `.alias`, `.webloc`, bookmark blobs | `application/octet-stream`, `application/xml`/plist | Filesystem/web references with serialized target metadata. | parse target metadata; do not resolve automatically |
| Linux launchers | `.desktop` | `application/x-desktop` | INI-like shortcut/application launcher with executable command. | static parse; never launch |
| Autorun/startup files | `autorun.inf`, `.command`, `.workflow`, `.action`, `.app` | text/bundle/generic types | Automatic launch, Automator workflow/action, or app bundle. | `dangerous/quarantine`; display static steps only |
| Office add-ins/templates with code | `.xll`, `.xla`, `.xlam`, `.ppa`, `.ppam`, `.wll`, `.dotm`, `.docm` | Office/vendor/PE types | Native or VBA/XLM-capable extension points. | quarantine; static metadata/text extraction only |

#### 3.16.2 Firmware, disk/ROM images, and hardware programming files

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| Raw firmware | `.bin`, `.rom`, `.fw`, `.img`, `.cap`, `.fd`, `.efi` | `application/octet-stream` | Vendor/device firmware or ROM image; may contain nested filesystems/code. | `dangerous/quarantine`; signature/carving/static metadata only |
| Intel HEX / Motorola S-record | `.hex`, `.ihex`, `.ihx`, `.s19`, `.s28`, `.s37`, `.srec`, `.mot` | `text/plain`, `application/x-ihex` | Addressed ASCII firmware/memory records with checksums. | `structured-parse`; never flash |
| UF2 / DFU | `.uf2`, `.dfu`, `.dfuse` | `application/x-uf2`, `application/octet-stream` | Microcontroller flash-transfer containers. | static parse; `dangerous/quarantine`; never flash |
| FPGA bitstreams | `.bit`, `.bin`, `.sof`, `.pof`, `.rbf`, `.jed`, `.svf`, `.xsvf` | `application/octet-stream`, `text/plain` | FPGA/CPLD configuration and programming sequences. | static metadata/command parse; never program hardware |
| Android boot/update | `.img`, `.bin`, `.dat`, `.br`, `.payload.bin`, `.ota.zip` | generic/archive types | Partition images or signed update payloads. | quarantine; sandboxed filesystem/header inspect only |
| Apple IPSW | `.ipsw` | `application/zip` | Signed firmware/update ZIP with images/manifests. | `archive-expand`; quarantine nested images/executables |
| Console/computer ROMs | `.nes`, `.sfc`, `.smc`, `.gb`, `.gbc`, `.gba`, `.nds`, `.3ds`, `.n64`, `.z64`, `.v64`, `.gen`, `.md`, `.sms`, `.gg`, `.a26`, `.a78`, `.rom` | `application/octet-stream` | Cartridge/ROM dumps with platform-specific headers/mappers. | `binary-opaque`; quarantine; static metadata only |
| MAME CHD | `.chd` | `application/x-mame-chd` | Compressed hunks for disks/optical media. | specialist read-only metadata; quarantine |
| Emulator save/state | `.sav`, `.srm`, `.state`, `.ss0`, `.dsv`, `.mcr`, `.vmc` | `application/octet-stream` | Game save RAM or full emulator state, platform/version specific. | `binary-opaque`; sensitive/user data possible |

#### 3.16.3 Platform metadata, exchange, and miscellaneous real-world files

| Format/family | Extension(s) / names | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| Windows registry hives | `SYSTEM`, `SOFTWARE`, `SAM`, `SECURITY`, `NTUSER.DAT`, `.hiv` | `application/octet-stream` | Binary registry database, often sensitive. | specialist read-only parse; restricted; `.dat` collision-aware |
| Windows thumbnail/cache | `Thumbs.db`, `.db`, `.dat` | generic binary/SQLite variants | Cached thumbnails/metadata. | specialist media extraction; sensitive; otherwise opaque |
| macOS metadata | `.DS_Store`, `._*`, `.Spotlight-V100`, `.Trashes` | generic binary/directory | Finder metadata, AppleDouble forks, Spotlight/trash state. | metadata parse only; usually low-value/opaque |
| Browser bookmarks/session | `.html`, `.json`, `.jsonlz4`, `.bak`, `.sqlite` | HTML/JSON/SQLite/generic types | Exported or native browser navigation/session data. | profile-aware parse; sensitive; no URL fetching |
| BitTorrent | `.torrent` | `application/x-bittorrent` | Bencoded metadata, file tree, hashes, trackers; not the content itself. | `structured-parse`; never contact trackers/peers |
| Metalink | `.meta4`, `.metalink` | `application/metalink4+xml`, `application/metalink+xml` | XML download mirrors/checksums/signatures. | hardened parse; never download automatically |
| URL/link lists | `.url`, `.webloc`, `.desktop`, `.link`, `.list` | text/plist/desktop types | References rather than captured content. | parse metadata; record unresolved external dependency |
| EDI X12 / EDIFACT | `.edi`, `.x12`, `.edifact`, `.edifact` | `application/EDI-X12`, `application/EDIFACT` | Delimiter-based business transaction messages. | profile/version-aware `structured-parse` |
| SWIFT messages | `.fin`, `.swift`, `.mt`, `.mx`, `.xml` | `text/plain`, `application/xml` | Financial messages in FIN/MT text or ISO 20022 XML; extensions collide. | sensitive schema-aware parse |
| ISO 20022 | `.xml` | `application/xml` | Financial message families selected by root namespace/schema. | hardened schema-aware parse |
| vCard/calendar | `.vcf`, `.ics`, `.vcs` | vCard/calendar types | Contact/calendar exchange (repeated here because attachments often arrive as “misc”). | `structured-parse` |
| GPS/navigation | `.gpx`, `.kml`, `.fit`, `.tcx`, `.nmea`, `.log` | XML/FIT/text types | Routes/tracks/sensor or NMEA sentence logs. | domain parse; sensitive location data |
| Terminal recordings | `.cast`, `.ttyrec`, `.asciinema`, `.tlog` | `application/x-asciicast`, generic binary/text | Timestamped terminal output/input sessions. | profile-aware parse; redact/control-sequence sanitization; never replay input |
| PCAP/network/security artifacts | `.pcap`, `.pcapng`, `.etl`, `.evtx`, `.har` | capture/event/JSON types | Network/event/browser traces (also catalogued above) frequently uploaded for diagnosis. | bounded specialist parse; sensitive payloads |
| Generic data | `.dat`, `.data`, `.bin`, `.raw`, `.dump`, `.dmp`, `.bak`, `.tmp` | usually `application/octet-stream` | No format meaning: application-specific bytes, backup, dump, or temporary content. | signature/deep-detect; otherwise `binary-opaque` |
| Empty file | any name, zero bytes | `application/x-empty` (conventional) | Valid zero-length object with no content format. | retain hash/metadata; no derived body beyond explicit empty marker |
| Extensionless file | `README`, `LICENSE`, `Makefile`, `Dockerfile`, binaries, Unix commands | detected type | Filename supplies no suffix; may be text, script, executable, or data. | content identification first; posture follows detected type |

### 3.17 Haptics and tactile-effect files

The IANA registry now has a `haptics` top-level media type, so a 2026 inventory should not silently
force tactile media into audio or generic application data.

| Format/family | Extension(s) | Common media type(s) | What it is | Posture |
|---|---|---|---|---|
| HJIF | `.hjif` | `haptics/hjif` | MPEG JSON-based interchange for temporal/spatial haptic effects and device descriptions. | hardened `structured-parse`; never drive hardware during ingest |
| HMPG | `.hmpg` | `haptics/hmpg` | MPEG streamable binary haptic-effect coding. | specialist `structured-parse`/`binary-opaque`; safe timeline summary |
| IEEE 1918.1.1 IVS | `.ivs`, `.ivt` | `haptics/ivs` | Vibrotactile signal XML/binary representations. | XML/binary specialist parse; never actuate |
| Apple haptic pattern | `.ahap` | `application/json`, `text/plain` | JSON dictionary of continuous/transient haptic and audio events for Core Haptics. | profile-aware `structured-parse`; never play/actuate |
| Meta/Oculus haptic | `.haptic` | `application/json`, `text/plain` | JSON vibrotactile effect interchange used by Meta tooling. | profile-aware `structured-parse`; never actuate |
| Embedded haptic track | within ISO BMFF or other media container | `haptics/*` or container signaling | Timed haptic stream multiplexed with audio/video. | inventory as a separate track; specialist parse and source-time provenance |

## 4. Cross-cutting notes

### 4.1 “Support” is a capability vector, not a Boolean

**Inference:** a future format capability record should distinguish at least the following claims.
This is analysis vocabulary, not a proposed binding schema.

| Capability | Question it answers |
|---|---|
| Identify | Can the system name the byte format/profile/version with a confidence and competing candidates? |
| Inventory | Can it enumerate pages, streams, sheets, members, tables, layers, tracks, attachments, or sidecars? |
| Metadata | Can it safely extract technical/descriptive metadata without rendering or executing content? |
| Text/structure | Can it preserve the human/logical reading order, records, hierarchy, formulas, speaker turns, or coordinates? |
| Visual/audio derivation | Can it produce useful OCR/transcript/description/previews with source locators? |
| Recursion | Can it identify and process embedded/member files without losing parent-child provenance? |
| Fidelity | Which source features are represented, approximated, omitted, locked, corrupt, or unsupported? |
| Audit | Can the consumer reach the exact immutable raw object and source region/time/page/member? |
| Safety | Does parsing happen without running macros, formulas, scripts, plugins, install actions, external fetches, or native code from the file? |

Therefore these statements are materially different:

- “The extension is recognized.”
- “The outer ZIP opens.”
- “Some text was extracted.”
- “Every logical object was inventoried.”
- “The Markdown is complete enough for agent reasoning.”
- “The rendered result matches the authoring application.”
- “The file is safe.”

None implies the next.

### 4.2 Identification should retain evidence from multiple layers

**Inference:** the identification result for an upload should retain, without treating any one as
authoritative:

1. original filename and all suffixes (important for `.tar.gz`, `.ome.tiff`, split volumes);
2. uploader/HTTP-declared media type and parameters;
3. byte-signature/magic candidates and detector versions;
4. structural validation/deep-parser result and warnings;
5. outer container plus member/part profile (for example `ZIP → OOXML SpreadsheetML`);
6. stream codecs/profiles/levels for media;
7. encryption/DRM/signature/active-content indicators;
8. charset, BOM, newline convention, and text-decoder confidence where relevant;
9. companion/dataset resolution status; and
10. a stable `unknown`, `ambiguous`, `locked`, `corrupt`, `unsupported-profile`, or
    `resource-limit` outcome.

PRONOM/DROID-style byte identities are useful, IANA media types are useful, filename hints are
useful, and deep parsers are useful. They answer different questions. A successful deep parse is
stronger than matching a short magic prefix, but it is still not proof that a polyglot has no
second interpretation.

### 4.3 Containers, codecs, wrappers, and compound documents

#### Containers versus codecs

- `.mp4`, `.mov`, `.mkv`, `.webm`, `.ogg`, `.wav`, `.avi`, `.mxf`, `.ts`, and `.asf` mainly name
  **containers/framing**. A specific file can carry multiple video, audio, subtitle, chapter,
  attachment, and metadata tracks.
- H.264/AVC, H.265/HEVC, AV1, VP9, ProRes, AAC, MP3, Opus, Vorbis, FLAC, AC-3, and PCM name
  **encoded bitstreams/codecs**. The same codec can live in several containers; a container parser
  without a decoder can inventory a stream but cannot transcribe/render it.
- A raw elementary stream (`.h264`, `.aac`, `.yuv`) often lacks timestamps, dimensions, rate,
  channel layout, language, or metadata that would have been supplied by a container.
- One file can be partially decodable: for example video works, one audio track uses an unavailable
  codec, subtitles are readable, and an attachment is encrypted. Capability reporting belongs at
  stream/member grain, not only file grain.

#### Common compound substrates

| Substrate | Profiles that can look alike at the outer layer | Why generic expansion is insufficient |
|---|---|---|
| ZIP/OPC | DOCX/XLSX/PPTX/VSDX/XPS, ODF, EPUB, JAR/WAR, APK, 3MF, KMZ, Sketch, iWork, VSIX | Profile-specific relationships, content types, signatures, macros, ordering, and semantics define the document. |
| OLE2/Compound File Binary | DOC/XLS/PPT/MSG/MSI/Access/Visio and embedded OLE objects | Stream names/classes and nested objects require application-specific interpretation; macros may be present. |
| ISO BMFF | MP4/MOV/M4A/M4B/3GP/HEIF/HEIC/AVIF/MJ2 | Brands, boxes, item/track references, codecs, timing, and protection schemes distinguish profiles. |
| EBML | Matroska/WebM | Track codecs, attachments, chapters, cues, and WebM restrictions matter. |
| RIFF | WAV/AVI/WebP and many application formats | Form type/chunk graph, not the `RIFF` magic alone, names the content. |
| Ogg | Vorbis/Opus/Speex/Theora/FLAC/multiplexed media | Codec identification comes from logical bitstreams, not `.ogg`. |
| TIFF | still image, multi-page scan, GeoTIFF, DNG/raw, whole-slide pathology | Private tags, subfile pyramids, compressions, and domain profiles radically change interpretation. |
| HDF5 | generic arrays, netCDF4, HDF-EOS5, MINC2, MATLAB 7.3, NWB, AnnData, CGNS, Keras | Group/dataset conventions supply domain meaning; “can open HDF5” does not mean “understands the profile.” |
| SQLite | generic DB, GeoPackage, MBTiles, SpatiaLite, application/browser store, Audacity project | Application ID/schema and companion files supply meaning; arbitrary SQL extensions/triggers must not run. |
| PDF | pages, forms, portfolios/attachments, signatures, JavaScript, multimedia, geospatial/3D profiles | Text extraction alone misses active, embedded, visual, signed, or portfolio content. |

**Inference:** recurse at the **logical-object boundary**. DOCX should be interpreted as a Word
document with embedded children, not flattened into hundreds of unrelated XML/member documents;
an ordinary user ZIP should normally expose independent child lineages with archive-member
provenance; a Shapefile or DAISY book should remain one dataset with coordinated components.

### 4.4 Extension collisions and names that cannot route safely

The following list is illustrative, not exhaustive.

| Extension/name | Common competing meanings |
|---|---|
| `.ts` | TypeScript source; MPEG transport stream; Qt Linguist translation source. |
| `.m` | Objective-C source; MATLAB/Octave source; Wolfram package. |
| `.mod` | Tracker music module; Modula-2 source/module; Fortran compiler module; package cache naming. |
| `.dat` | Arbitrary application data; fixed-width/delimited table; TNEF `winmail.dat`; signal/database/firmware data. |
| `.bin` | Raw firmware/disk/audio/video/model data; MacBinary; generic executable/payload. |
| `.raw` | Camera raw; raw pixels/video/audio; disk/firmware image; scientific/instrument output; simulator data. |
| `.img` | Raw disk image; ERDAS raster; paired Analyze/NIfTI pixels; firmware; optical track. |
| `.asc` | Plain ASCII; AsciiDoc; armored PGP; ASCII GIS grid; chemistry/telemetry formats. |
| `.key` | Apple Keynote; private/public key material; generic license/key data. |
| `.pub` | Microsoft Publisher; SSH/public key; publication database. |
| `.pdb` | Protein Data Bank structure; Program Database debug symbols; Palm Database/ebook. |
| `.vcf` | vCard contact; genomic Variant Call Format. |
| `.stl` | 3D stereolithography; EBU subtitle; Spruce subtitle list. |
| `.sub` | Text subtitle dialect; VobSub bitmap stream; generic subchannel data. |
| `.smi` | SAMI caption; SMIL; SMILES chemistry. |
| `.ass` | SubStation Alpha subtitles; occasionally source/assembly naming. |
| `.cdf` | NASA Common Data Format; netCDF legacy suffix; Channel Definition Format; compound-document uses. |
| `.fits` / `.fit` | Astronomy FITS; Garmin FIT for `.fit`; generic “fit” application files. |
| `.las` | LAS point cloud; Log ASCII Standard well log. |
| `.bed` | Text genomic intervals; binary PLINK genotype matrix. |
| `.sam` | Sequence Alignment/Map; legacy word-processing/sample formats. |
| `.map` | Source map; map/GIS component; linker map; game map; generic key/value data. |
| `.db` | SQLite/application database; Paradox; Berkeley DB; thumbnail/cache; arbitrary store. |
| `.sav` | SPSS data; IDL save; game/emulator save; VM state. |
| `.dmp` / `.dump` | SQL/logical export; Oracle Data Pump; crash/core/memory dump; arbitrary binary backup. |
| `.mdf` | SQL Server primary database; ASAM Measurement Data; optical-disc image component. |
| `.r` | R source; REBOL/other source in some ecosystems; resource file. |
| `.pl` | Perl; Prolog; playlist/other legacy formats. |
| `.cls` | LaTeX class; compiled Java class on case-insensitive workflows; Visual Basic class. |
| `.h` | C/C++/Objective-C header; other language include/header. |
| `.s` | Assembly source; S-record-related shorthand; generic sample/data. |
| `.x` | DirectX model; linker/executable convention; miscellaneous scientific format. |
| `.prc` | Palm/Mobipocket ebook; Product Representation Compact 3D. |
| `.abc` | ABC music notation; Alembic 3D cache. |
| `.ogg` | Ogg media; Origin graph/project usage in some versions. |
| `.dif` | Spreadsheet Data Interchange Format; DV/DIF video stream. |
| `.arc` | Legacy archive; Internet Archive ARC; application/CAD/project file. |
| `.apk` | Android package; Alpine Linux package. |
| `.app` | macOS directory bundle; application-specific data file elsewhere. |
| `.iso` | Optical filesystem image; generic ISO-standard payload naming. |
| `.xml`, `.json`, `.yaml`, `.zip` | Syntax/container only; root schema/profile/member topology determines the actual format. |
| `README`, `LICENSE`, `Makefile`, `Dockerfile`, Unix commands | Important extensionless text/source or executable files. |

The same extension can even mean different versions of one product, and the same byte format can
use several extensions. Extension matching is a hint and user-interface aid, never a security
boundary or final router decision.

### 4.5 MIME is useful metadata and unreliable truth

- IANA registers **media types**, not a complete extension database. Many real application formats
  have no registered type, use vendor trees, or circulate with historical `x-` aliases.
- HTTP/email `Content-Type` is supplied by an upstream client/server and can be absent or spoofed.
  Browsers, operating systems, object stores, mail gateways, and language libraries ship different
  extension maps.
- Generic `application/octet-stream`, `text/plain`, `application/zip`, `application/xml`, and
  `application/json` often conceal the meaningful profile.
- Parameters matter: text charset, codec parameters, boundary values, profiles, and versions can
  change decoding. The MIME alone usually does not identify an MP4's tracks or a CSV dialect.
- Some common values conflict in the wild (`.vcf` as `text/vcard` versus genomic VCF;
  `.js` as `text/javascript` versus older `application/javascript`; vendor aliases for Office).

**Inference:** retain the claimed MIME verbatim, normalize it as one signal, and record the detected
format separately. Never rewrite the provenance as though the sender had supplied the detected
type.

### 4.6 Magic bytes, deep parsing, polyglots, and parser differentials

- A signature can be short/shared: every OOXML/ODF/EPUB/JAR/APK/ordinary ZIP begins like ZIP;
  OLE2 products share one compound-file signature; RIFF needs its form type; raw text may have none.
- Signatures can occur away from offset zero, be preceded by wrappers, or require a trailer/member
  inventory. Some formats have no mandatory magic.
- A **polyglot** intentionally satisfies two parsers (or places one valid format after/inside
  another). “Chimeric/schizophrenic” files exploit differences between detectors and consumers.
- A signature match does not prove structural validity. Conversely, a strict parser can reject a
  recoverable file that its native application accepts.
- Parser disagreement is security-relevant: if detection routes to parser A while downstream
  serving invokes parser B/browser C, they may see different active content.

**Inference:** store all credible identities/warnings; choose a conservative parser; isolate it;
serve derived content with fixed safe content types; and report ambiguity/polyglot suspicion rather
than silently preferring the filename. Apache Tika explicitly says it is not a security boundary
for polyglots/parser differentials, which is the correct threat model for a broad ingest system.

### 4.7 Encrypted, password-protected, rights-managed, and signed files

Encryption is a first-class outcome, not a generic conversion failure.

- Office, PDF, ZIP/7z/RAR, disk images, backups, databases, key stores, mail, and ebooks may encrypt
  contents and sometimes filenames/metadata. The visible outer signature may identify only the
  wrapper.
- “Password to open” differs from worksheet/document editing protection, rights management, and a
  digital signature. Some “protection” is not cryptographic; some encryption hides nearly all
  content.
- A password can arrive separately, be different per nested member, or be unavailable. It is a
  secret: it must not be written into Markdown, logs, manifests, command lines, or provenance.
- Authorized decryption produces a **derived reading of the same immutable encrypted source**; the
  result and tool versions need audit lineage. The decrypted body itself may require separately
  protected storage under deployment policy.
- DRM-protected `.m4p`, Audible/Kindle ebooks, rights-managed Office/PDF, or proprietary vaults may
  be technically recognizable but intentionally non-decodable without rights/software.
- A cryptographic signature says nothing until verification is performed against a declared trust
  store and time/revocation policy. Parsing a certificate or `checksums.txt` is not authentication.

**Inference:** expose `locked/encrypted`, recognized scheme/profile, visible safe metadata, password
request capability if one ever exists, and `not decrypted`—never claim an empty or corrupted
document. Password-protected content that cannot be malware-scanned/fully parsed stays quarantined.

### 4.8 Why “support `.xlsx`” does not mean “support every Excel feature”

`.xlsx` is a ZIP/OPC package with SpreadsheetML and related parts. A converter can successfully
read cell text while losing behavior or evidence important to an agent. Microsoft itself warns
that saving between formats can lose formatting, data, or features and distinguishes `.xlsx`,
`.xlsm`, and binary `.xlsb`.

| Feature surface | What a serious ingest claim must say |
|---|---|
| Cell values and types | Whether numbers, strings, booleans, errors, dates/times, rich text, and locale/formatting are preserved. |
| Formulas | Formula source versus cached result; whether stale/missing cached values are disclosed. Ingest should not evaluate untrusted formulas. |
| Calculation semantics | Iterative calculation, volatile functions, dynamic arrays, cube functions, add-ins, version-specific functions, and 1900/1904 date system. |
| VBA/XLM/add-ins | Presence and inventory of VBA projects, Excel 4.0 macro sheets, `.xlam`/`.xla`/`.xll`; never execute. |
| External dependencies | External workbook links, data connections, Power Query/M, ODBC/OLE DB, linked pictures, data model/Power Pivot; no network fetch by default. |
| Hidden content | Hidden/very-hidden sheets, hidden rows/columns, defined names, comments/notes/threaded comments, custom XML, metadata. |
| Tables and presentation | Merged cells, tables, filters, conditional formatting, validation, charts, sparklines, shapes, images, print areas, headers/footers. |
| Pivot/data model | Pivot caches/tables/charts, relationships, measures, cube/data-model contents and whether cached source data is present. |
| Embedded objects | OLE packages, other Office files, PDFs/media, ActiveX controls, signatures. Each is a nested file/active surface. |
| Protection/encryption | Workbook/sheet protection versus password-to-open encryption; locked cells do not necessarily hide bytes. |
| Binary/legacy variants | `.xlsb` uses BIFF12 binary workbook parts; `.xls` uses BIFF/OLE; `.xlsm` can carry VBA. One `.xlsx` parser does not cover them. |
| Corruption/recovery | Whether the parser recovered, skipped parts, repaired relationships, truncated rows, or hit limits. Native Excel may repair files differently. |
| Scale | Sheet/cell/formula/shared-string limits, sparse huge dimensions, zip bombs, drawings and embedded objects; sampled output must declare omissions. |

**Inference:** the honest contract is feature-grained: for example “all worksheets and cell
formulas/cached values inventoried; VBA detected but not executed; Power Query definitions listed;
external links unresolved; charts rendered to previews; unsupported embedded OLE object retained.”
That is much stronger than “XLSX supported” and still does not claim native Excel equivalence.

The same principle applies to PDF (forms/attachments/JavaScript/signatures/3D), Word (tracked
changes/comments/text boxes/fields/OLE), presentations (notes/masters/animations/embedded media),
email (MIME/attachments/calendar/TNEF/signatures), and every rich compound format.

### 4.9 One logical input can require several files or a directory

Examples include Shapefiles, Analyze/NIfTI pairs, WFDB and BrainVision signals, DAISY, optical
images, split archives, WavPack correction files, Audacity projects, VM disks with backing/extents,
File Geodatabases, Zarr, OpenFOAM, DICOM media sets, camera sidecars, and database WAL/log files.

**Inference:** intake needs a dataset/package notion or an explicit manifest supplied by the
connector/user. Uploading only `.shp`, `.hdr`, `.cue`, `.vhdr`, `.idx`, `data.mdb`, or a VM
descriptor can yield a syntactically readable but semantically incomplete object. The conversion
manifest should report resolved, missing, ignored, and external companions. It must not scan or
follow arbitrary host paths to “helpfully” find them.

### 4.10 Text is not synonymous with UTF-8 or harmless

- Real files use UTF-8/16/32, Windows code pages, ISO-8859 families, Shift-JIS, EUC-KR, Big5, GBK,
  EBCDIC, Macintosh encodings, mixed/invalid bytes, BOMs, and record-oriented mainframe encodings.
- CSV dialect, decimal/date locale, newline convention, tab width, fixed-record length, and Unicode
  normalization can change meaning.
- “Text” can be executable or expansive: PostScript, shell, Office formulas, SVG/HTML/JavaScript,
  XML entities, templates, build files, notebooks, SQL, macros, and decompression directives.
- Control sequences, bidi controls, zero-width characters, terminal escapes, NULs, extremely long
  lines, and Unicode confusables must be disclosed/sanitized in views without changing raw bytes.

**Inference:** decoding should be bounded and reversible enough to report charset confidence and
replacement/error counts. Derived Markdown is UTF-8, but it must preserve a raw pointer and state
when decoding was lossy.

### 4.11 Recursion, resource exhaustion, and active/external content

Every parser and decoder consumes attacker-controlled input. Risks include ZIP/XML bombs, recursive
archives, huge sparse tables/images, malicious compression ratios, cyclic relationships, path
traversal, symlinks/hardlinks/devices, codec/parser vulnerabilities, memory-mapped giant dimensions,
and content that fetches remote resources.

**Inference:** any eventual implementation needs limits at least on raw size, expanded bytes,
compression ratio, member count, nesting depth, page/sheet/cell/frame/sample/point count, image
dimensions, XML/JSON depth, text output, CPU/wall time, memory, temporary disk, and network. The
limit outcome and partial coverage must be explicit in the manifest. Run format parsers in isolated,
least-privilege workers; keep libraries patched; deny network and host filesystem access; and never
execute macros, formulas, scripts, templates, plugins, UDFs, installers, autorun, firmware, models,
or database triggers during ingest.

External references—HTML images, XML entities/schemas, OOXML relationships, spreadsheet links,
CAD textures, glTF buffers, USD layers, HLS segments, CRAM references, database extensions—are
dependencies, not permission to fetch. Resolve only members of the declared upload/dataset unless a
separate connector-authorized retrieval policy says otherwise.

### 4.12 Opaque and partial outcomes are correct outcomes

**Inference:** if a file is unknown, corrupt, encrypted, requires unavailable proprietary software,
has an unsupported codec/profile, lacks companions, or crosses a resource boundary, E0 should still
be able to retain raw bytes, hash them, record supplied/detected metadata and diagnostics, and emit a
minimal Markdown stub that says exactly why no fuller derivation exists. “Opaque” is preferable to
fabricated empty text, unsafe best-effort execution, or a false complete-conversion claim.

## 5. RememberStack-oriented grouping

This grouping deliberately distinguishes repository observation from recommendation. It says
nothing about the currently implemented library.

### 5.1 Families already near the documented design

| Family | Claim | Why it is near the design | Inventory implications still needed |
|---|---|---|---|
| Plain text | **Observed** | `plan/designs/e0_files_design.md` names plain-text passthrough. | Charset/encoding confidence, extensionless files, structured-text profiles, active text. |
| PDF | **Observed** | E0 names direct extraction for digital PDFs and OCR for scanned/complex PDFs. | Encryption, portfolios/attachments, forms, signatures, scripts, 3D/geospatial profiles, honest coverage. |
| Office documents | **Observed** | E0 names an office route via MarkItDown; the prompt says Excel is analyzed elsewhere. | Feature-grained Word/Excel/PowerPoint/Visio/ODF fidelity, macros/OLE, formulas/cached values, hidden/linked objects. |
| HTML and email | **Observed** | E0 names HTML/email via MarkItDown; cross-reference extraction mentions reply/attachment headers. | Sanitization, MIME recursion, MHTML/TNEF/mailboxes/PST, calendars/contacts, no external fetching. |
| Images | **Observed** | D65 binds standalone-picture VLM description plus OCR, document-vs-picture discrimination, source locators, raw reachability. | Long-tail decoder/profile matrix, animations, layered/raw/GIS/medical images, embedded active content. |
| Audio | **Observed** | D65 binds diarized ASR and media source-time provenance. | Container/codec/track matrix, non-speech audio, score/MIDI/project formats, DRM, corrupt/partial tracks. |
| Video | **Observed** | D65 binds ASR, adaptive keyframes, optional shot notes, timed locators, and raw access. | Container/codec/track matrix, subtitles/manifests, attachments, disc/dataset packages, partial decode. |
| Derived media/text sidecars | **Observed** | D65's canonical-text rule puts ingestible transcript text in `document.md`; `media/` holds derived support/interchange artifacts. | Preserve uploaded source subtitles/sidecars distinctly from generated ones; track dataset relationships. |

**Observed:** the binding design's abstract converter output—`document.md + source_map +
derived_assets[] + manifest`—is broad enough to represent a specialist format route without a new
memory plane. **Inference:** this inventory should populate detector/router capability metadata and
test fixtures, not expand D65 into one bespoke pipeline per suffix.

### 5.2 Likely next high-leverage families

These are recommendations, not commitments.

| Priority group | Claim | Families | Why |
|---|---|---|---|
| A — cheap text/structure | **Inference** | Markdown/RST/AsciiDoc/Org/TeX; source code/config/build files; JSON/JSONL/YAML/TOML/XML/CSV/TSV; SQL dumps; logs | Very common agent inputs; mostly deterministic; preserve more structure than generic text passthrough; no heavy model required. |
| A — computational docs | **Inference** | Jupyter/Quarto/R Markdown, code diffs, API schemas | High agent value; JSON/text parsing is tractable; cell outputs and executable content can be sanitized without running code. |
| B — communications | **Inference** | EML/MIME, mbox/Maildir, ICS/vCard, MSG/TNEF, common chat exports | Directly memory-like testimony with timestamps/speakers/attachments; strong deterministic structure. PST/OST remain a specialist subroute. |
| B — publications/web captures | **Inference** | EPUB, CHM (sandboxed), MHTML, WARC/WACZ, RSS/Atom, bibliographic exports | Text-rich compound documents; common archival/research uploads; recursion and sanitization are reusable. |
| B — safe bounded archive discovery | **Inference** | ZIP/tar/gzip/7z/RAR where a safe decoder exists | Unlocks user bundles and datasets, but needs global recursion/resource/path/encryption controls before broad admission. |
| B — logical table/database exports | **Inference** | Parquet/Arrow/Avro/ORC, SQLite snapshots, DBF, SAS/SPSS/Stata, logical SQL/JSON exports | High structured-memory value and deterministic metadata/table extraction; requires scale/sampling and formula/query safety. |
| C — open map/3D formats | **Inference** | GeoJSON/KML/GPX/Shapefile/GeoPackage; glTF/OBJ/PLY/STL/3MF; SVG/diagram source | Useful structured/visual evidence and mature libraries exist; dataset/reference/projection/render semantics need a specialist route. |

The common architectural prerequisite is not another giant extension list. It is safe recursive
identification, dataset/companion grouping, feature/coverage manifests, and an opaque outcome.

### 5.3 Specialist routes worth admitting only with a concrete corpus

| Family | Claim | Suggested stance |
|---|---|---|
| HDF5/netCDF/Zarr/FITS/GRIB/BUFR | **Inference** | Admit with a scientific corpus and schema/variable summaries; never dump massive arrays indiscriminately. |
| DICOM/NIfTI/pathology/microscopy/physiological signals | **Inference** | Admit with clinical/privacy policy and domain libraries; de-identification is a separate policy, not implied by parsing. |
| Genomics/proteomics/chemistry | **Inference** | Admit with domain-aware summaries, indexes/references/paired-file rules, and scale fixtures. |
| CAD/BIM/EDA/native 3D | **Inference** | Prefer open interchange first; proprietary native formats need licensed/vendor tools or an explicit opaque result. |
| Engineering/telemetry/simulation | **Inference** | Admit per real source family with schema/units/time/channel semantics; `.dat`/`.raw` alone cannot route. |
| Databases and application stores | **Inference** | Prefer logical export; allow copied, read-only, isolated parsing only when the application schema is useful. |
| DAW/video-edit/project files | **Inference** | Treat as dataset manifests plus referenced media; project/plugin fidelity is specialized and usually incomplete. |

### 5.4 Families that should normally stay opaque or quarantined

| Family | Claim | Reason |
|---|---|---|
| Executables, libraries, bytecode, drivers, kernel modules | **Inference** | Static metadata/strings can be useful, but execution is unnecessary and dangerous; decompilation is a separate specialist feature. |
| Installers, app/browser extensions, firmware, FPGA programming files, ROMs | **Inference** | Retain/hash/static-inventory only; never install, launch, flash, emulate, or deploy during memory ingest. |
| Disk/VM/forensic/backup images | **Inference** | Very large, nested, sensitive, and mount/parser-risky. Read-only userspace enumeration should require an explicit corpus. |
| Private keys, password vaults, memory/core dumps | **Inference** | High secret density; body text should not enter general memory surfaces. |
| Encrypted/DRM content without authorized credentials/rights | **Inference** | Correct result is locked/opaque with metadata, never attempted circumvention. |
| Native search/vector indexes and physical DB engine state | **Inference** | Usually rebuildable/version-specific and meaningless without authoritative records/schema; prefer logical exports. |
| Pickle/joblib and unsafe language/runtime serialization | **Inference** | Loading can execute attacker-controlled code. |
| Unsupported proprietary scientific/instrument/CAD formats | **Inference** | Hash/metadata/raw access is honest; guessed conversion is not. |

### 5.5 Experiment-needed register

| Experiment | Claim | What it should establish |
|---|---|---|
| Format zoo and collision corpus | **Experiment needed** | Byte fixtures across families/versions, misleading names/MIMEs, extensionless files, polyglots, corrupt/truncated/empty inputs, encrypted inputs, and compound profiles. |
| Converter capability matrix | **Experiment needed** | Per pinned build of Tika/MarkItDown/LibreOffice/FFmpeg/GDAL/domain tools: identify, parse, inventory, render, and fidelity—not marketing-level “supports.” |
| Compound-office goldens | **Experiment needed** | Tracked changes, comments, text boxes, formulas/cached values, macros, OLE, external links, hidden sheets/slides, pivots/charts, signatures, encryption. |
| Archive adversarial suite | **Experiment needed** | Zip-slip paths, symlinks/devices, bombs, recursion, split/solid/encrypted archives, duplicate names, Unicode paths, nested packages, resource-limit reporting. |
| Media codec/track matrix | **Experiment needed** | Container × codec × profile/level × subtitle × encryption support for the deployed FFmpeg build, including partial-track outcomes. |
| Dataset grouping | **Experiment needed** | Shapefile, DICOMDIR, BrainVision/WFDB, CUE/BIN, Zarr, database+WAL, VM extents, DAISY, and missing/external companion behavior. |
| Structured big-data limits | **Experiment needed** | Sparse XLSX dimensions, huge CSV lines, Parquet/HDF5/Zarr arrays, billion-record logs, point clouds, genomics streams, deterministic sampling/coverage. |
| Parser isolation | **Experiment needed** | CPU/memory/temp-disk/network/filesystem limits, crash containment, timeout behavior, library patch process, and reproducible partial manifests. |
| Markdown usefulness | **Experiment needed** | Whether domain summaries preserve what agents actually need without flattening tables, timelines, coordinates, units, schemas, or nested evidence. |

## 6. Explicit non-goals

This document does **not**:

1. define or recommend a binding MIME/extension allowlist or denylist;
2. claim that RememberStack currently detects, parses, converts, indexes, or safely accepts any
   format enumerated here;
3. change `requirements_v3.md`, any binding design, `decisions.md`, code, or implementation evals;
4. redesign the D65 media plane, raw/artifact layout, provenance model, or E0 converter contract;
5. promise pixel-perfect/native-application fidelity, formula equivalence, macro behavior, or every
   feature of Excel/Office/PDF/CAD/scientific tools;
6. authorize execution of uploaded code, macros, formulas, scripts, notebooks, plugins, database
   routines, installers, models, firmware, or autorun content;
7. authorize mounting disk images, fetching external references, decrypting without credentials,
   bypassing DRM, brute-forcing passwords, or sending uploads to third-party services;
8. define privacy classification, malware scanning, de-identification, retention, legal, licensing,
   trust-store, or secrets-handling policy;
9. assert that a MIME string, extension, magic number, successful parser, or valid signature makes a
   file safe or trustworthy; or
10. freeze the inventory. Registries, formats, codecs, profiles, applications, and parser builds
    continue to change; unknown/opaque must remain a supported outcome.

## 7. Sources and method

All web sources below were accessed **2026-08-26**. This is a synthesis: no single source spans the
extension, MIME, byte-signature, container/codec, compound-document, domain-profile, practical-tool,
and security dimensions. Media types shown in the tables were cross-checked selectively against the
IANA registry and common tool maps; vendor/unregistered aliases are intentionally described as
common rather than canonical.

### 7.1 Registries and breadth catalogs

- [IANA Media Types registry](https://www.iana.org/assignments/media-types/media-types.xhtml) —
  authoritative registered media-type names across `application`, `audio`, `font`, `image`,
  `haptics`, `message`, `model`, `multipart`, `text`, and `video`; not a complete extension
  registry.
- [RFC 9695: The `haptics` Top-Level Media Type](https://www.rfc-editor.org/info/rfc9695/) —
  HJIF/HMPG/IVS media types and the `.hjif`/`.hmpg`/`.ivs`/`.ivt` extensions.
- [PRONOM: About and access](https://pronom.nationalarchives.gov.uk/about) — UK National Archives
  format/version registry and byte-identification signatures used by DROID; searchable by format,
  PUID, and extension.
- [FileSignature.org](https://filesignature.org/) and [Gary Kessler's file-signature
  table](https://www.garykessler.net/library/file_sigs_GCK_latest.html) — practical magic-byte,
  extension, and shared-signature references. Useful inputs, not security or deep-validation proof.
- [Library of Congress Sustainability of Digital Formats categories](https://www.loc.gov/preservation/digital/formats/fdd/)
  — separates file formats, format classes, bitstreams/encodings, and compression; inclusion is not
  an endorsement or support claim.
- [Wikipedia: List of file formats](https://en.wikipedia.org/wiki/List_of_file_formats) and
  [List of filename extensions](https://en.wikipedia.org/wiki/List_of_filename_extensions) — broad
  discovery/indexing sources, checked against primary/tool sources for higher-impact claims.

### 7.2 General document and parser catalogs

- [Apache Tika](https://tika.apache.org/) — current project description says it detects/extracts
  metadata and text from over a thousand file types; [supported-format catalog
  (2.9.2)](https://tika.apache.org/2.9.2/formats.html) and [parser interface
  (3.2.3)](https://tika.apache.org/3.2.3/parser.html) show the practical parser/library families and
  the distinction between detection hints and parsing.
- [LibreOffice 25.2, Working with File Formats, Security, and Exporting](https://books.libreoffice.org/en/GS252/GS25210-FileFormatsSecurityExporting.html)
  — current office/legacy import families for Writer, Calc, Impress, Draw, and Math, plus explicit
  warnings that conversion can lose formatting/images/features.
- [LibreOffice file-conversion filter names](https://help.libreoffice.org/latest/en-US/text/shared/guide/convertfilters.html)
  — CLI filter surface and format/media-type mapping.
- [ECMA-376 Office Open XML](https://ecma-international.org/publications-and-standards/standards/ecma-376/)
  and [Microsoft: SpreadsheetML document structure](https://learn.microsoft.com/en-us/office/open-xml/spreadsheet/structure-of-a-spreadsheetml-document)
  — OOXML vocabulary/packaging and the ZIP package/part structure.
- [Microsoft: Excel-supported file formats](https://support.microsoft.com/en-us/excel/file-formats-that-are-supported-in-excel)
  and [features not transferred between formats](https://support.microsoft.com/en-us/excel/excel-formatting-and-features-that-are-not-transferred-to-other-file-formats)
  — `.xlsx`/`.xlsm`/`.xlsb`/legacy distinctions and feature-loss caveat.

### 7.3 Media, archives, GIS, and 3D tool catalogs

- [FFmpeg supported formats/codecs/features](https://ffmpeg.org/general.html) and [FFmpeg formats
  documentation](https://ffmpeg.org/ffmpeg-formats.html) — separate file formats/demuxers/muxers,
  codecs, image formats, and build-enabled capability; `-formats`/`-codecs` provide build truth.
- [Pillow image-format documentation](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html)
  — practical raster/container/image-plugin catalog and identify-only formats.
- [GDAL raster drivers](https://gdal.org/en/stable/drivers/raster/index.html) and [vector
  drivers](https://gdal.org/en/stable/drivers/vector/index.html) — broad GIS, remote-sensing,
  scientific-array, CAD, database, and dataset/profile coverage; build dependencies vary.
- [libarchive supported formats](https://github.com/libarchive/libarchive/blob/master/README.md) —
  tar/pax/cpio/ISO9660/ZIP/ZIPX/ar/7z/CAB/LHA/RAR/WARC/XAR plus compression filters and explicit
  read/write differences.
- [Assimp supported 3D formats](https://github.com/assimp/assimp/blob/master/doc/Fileformats.md) —
  practical mesh/scene/CAD/game-model import families, with partial/deprecated/external-SDK caveats.

### 7.4 Data, databases, and domain standards

- [Apache Arrow columnar/IPC format](https://arrow.apache.org/docs/format/Columnar.html) and
  [Apache Parquet documentation](https://parquet.apache.org/docs/) — Arrow/Feather IPC identity,
  extensions/media types, schemas/record batches, and Parquet file-format source.
- [SQLite database file format](https://www.sqlite.org/fileformat.html) — main DB, rollback journal,
  WAL and page/schema structure; a SQLite application file can require companion transactional
  state.
- [DICOM current edition](https://www.dicomstandard.org/current/) — especially Part 10 media storage
  and file format; confirms that DICOM spans much more than a raster extension.
- [GA4GH/Samtools HTS format specifications](https://samtools.github.io/hts-specs/) — SAM/BAM/CRAM,
  VCF/BCF, BED, indexes and `crypt4gh`; also documents FASTA/FASTQ reality and variants.
- [NASA/IAU FITS Support Office](https://fits.gsfc.nasa.gov/) and [FITS Standard](https://fits.gsfc.nasa.gov/fits_standard.html)
  — FITS images, multidimensional arrays, tables, headers, compression, and formal structure.
- [Unidata netCDF FAQ: formats and data models](https://docs.unidata.ucar.edu/netcdf-c/current/faq.html)
  — classic/64-bit/netCDF-4 variants and the deliberate `.nc` preference over collision-prone `.cdf`.
- [OGC HDF5 standard page](https://www.ogc.org/standards/hdf5/) — HDF5's multidimensional,
  self-describing, extensible data model and domain profiles.
- [HL7 FHIR resource formats](https://hl7.org/fhir/resource-formats.html) — JSON, XML, and Turtle
  representations illustrate why generic syntax needs a semantic profile.

### 7.5 Security and identification caveats

- [Apache Tika Security Model](https://tika.apache.org/security-model.html) — parsing untrusted data
  can trigger denial of service, XXE/SSRF, command injection, deserialization, crashes, and parser
  differentials; Tika is explicitly not itself a security boundary and recommends process/resource
  isolation.
- [OWASP File Upload Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html)
  — do not trust `Content-Type`, do not rely on extension or signature alone, limit file size,
  isolate storage/parsers, and account for ZIP/XML bombs and active content.
- [Microsoft: macros from the Internet are blocked by default](https://learn.microsoft.com/en-us/microsoft-365-apps/security/internet-macros-blocked)
  — Office macro attachments are an active-content risk, not merely another document feature.
- [Microsoft Defender Safe Attachments and encrypted files](https://learn.microsoft.com/en-us/defender-office-365/safe-attachments-about)
  — password-protected attachments cannot be fully scanned/detonated without the password and may
  need quarantine; “true file type” is not only the filename extension.

## 8. Bottom line

The extension universe is best treated as a **many-to-many hint index over byte formats, profiles,
containers, codecs, compound objects, and datasets**. RememberStack's Markdown-first/raw-reachable
model is compatible with that universe if conversion is honest about partial coverage, recursion,
mediation, and opaque outcomes. The inventory's practical dividing line is not “common versus
obscure”; it is whether a pinned, isolated adapter can derive useful, provenance-linked information
without executing the upload or pretending to preserve semantics it does not understand.
