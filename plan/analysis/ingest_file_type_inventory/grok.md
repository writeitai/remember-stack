# Exhaustive ingest file-type inventory (Grok view)

**Status:** non-binding analysis. **Date:** 2026-08-26. **Author:** Grok.
**Path:** `plan/analysis/ingest_file_type_inventory/grok.md`
**Companions:** [Codex](external_agents/codex.md), [Antigravity](external_agents/agy.md), [SYNTHESIS](SYNTHESIS.md).
**Related:** [`../media_handling/`](../media_handling/) (media plane design analysis).

## Short answer

“Exhaustive” here means **agent-uploadable file extensions and format families**, grouped by how RememberStack-like systems would *posture* them at ingest — not a complete codec matrix (H.264 vs HEVC vs AV1 live *inside* containers), not every vendor RAW revision, and not every programming-language extension on earth.

Organize along three axes at once:

1. **Container vs payload** (`.mp4` / `.mkv` / `.webm` vs codecs; `.docx` is a ZIP).
2. **Extension vs MIME vs magic bytes** (extensions lie; MIME is often wrong; sniff carefully).
3. **Ingest posture** — what the memory system should do first:

- `text-native`
- `structured-parse`
- `document-convert`
- `media-transcribe`
- `archive-expand`
- `binary-opaque`
- `dangerous/quarantine`

**[Inference]** RememberStack should never confuse “we accept the bytes” with “we understand the format.” Most of this list is either convert, recurse, transcribe, or quarantine.

## Claim labels

- **Observed** — widely documented format/extension/MIME associations
- **Inference** — product posture for an agent-memory system
- **Experiment needed** — whether a specific converter/parser should be wired

## Documents / page description / office prose

| Extensions | MIME / type hint | What | Ingest posture |
| --- | --- | --- | --- |
| `.pdf` | application/pdf | Portable Document Format | `document-convert` |
| `.pdfa .pdf/a` | application/pdf | PDF/A archival variants (still PDF) | `document-convert` |
| `.doc` | application/msword | Legacy Microsoft Word binary | `document-convert` |
| `.docx` | application/vnd.openxmlformats-officedocument.wordprocessingml.document | OOXML Word (ZIP+XML) | `document-convert` |
| `.docm` | application/vnd.ms-word.document.macroEnabled.12 | Word OOXML with macros | `dangerous` |
| `.dot .dotx .dotm` | application/msword / OOXML template | Word templates | `document-convert` |
| `.rtf` | application/rtf | Rich Text Format | `document-convert` |
| `.odt` | application/vnd.oasis.opendocument.text | OpenDocument text | `document-convert` |
| `.ott` | application/vnd.oasis.opendocument.text-template | ODT template | `document-convert` |
| `.pages` | application/x-iwork-pages-sffpages | Apple Pages (package/zip) | `document-convert` |
| `.wpd .wps` | application/wordperfect / works | WordPerfect / MS Works legacy | `document-convert` |
| `.abw .zabw` | application/x-abiword | AbiWord | `document-convert` |
| `.sxw` | application/vnd.sun.xml.writer | OpenOffice.org 1.x Writer | `document-convert` |
| `.lwp` | application/vnd.lotus-wordpro | Lotus Word Pro | `binary-opaque` |
| `.mcw` | application/macwriteii | MacWrite legacy | `binary-opaque` |
| `.cwk` | application/x-appleworks | AppleWorks | `binary-opaque` |
| `.hwp .hwpx` | application/x-hwp | Hangul Word Processor | `document-convert` |
| `.wri` | application/x-mswrite | Windows Write | `binary-opaque` |
| `.djvu .djv` | image/vnd.djvu | DjVu scanned docs | `document-convert` |
| `.xps .oxps` | application/vnd.ms-xpsdocument | XML Paper Specification | `document-convert` |
| `.oxps` | application/oxps | OpenXPS | `document-convert` |
| `.ps .eps .epsf .epsi` | application/postscript | PostScript / Encapsulated PS | `document-convert` |
| `.ai` | application/postscript | Adobe Illustrator (often PDF-compatible) | `document-convert` |
| `.indd .idml` | application/x-indesign / zip+xml | InDesign / IDML | `binary-opaque` |
| `.pub` | application/vnd.ms-publisher | Microsoft Publisher | `document-convert` |
| `.tex .ltx .latex .sty .cls .bib` | application/x-tex / text | TeX/LaTeX sources | `text-native` |
| `.dvi` | application/x-dvi | TeX DVI | `document-convert` |
| `.typ` | text/plain | Typst source | `text-native` |
| `.md .markdown .mdown .mkd .mdx` | text/markdown | Markdown / MDX | `text-native` |
| `.rst .rest` | text/x-rst | reStructuredText | `text-native` |
| `.adoc .asciidoc .asc` | text/asciidoc | AsciiDoc | `text-native` |
| `.org` | text/org | Org-mode | `text-native` |
| `.textile` | text/x-textile | Textile markup | `text-native` |
| `.wiki .mediawiki` | text/x-wiki | Wiki markup | `text-native` |
| `.fountain` | text/plain | Screenplay Fountain | `text-native` |
| `.nfo` | text/x-nfo | Scene NFO / info text | `text-native` |
| `.readme .1st` | text/plain | Informal text docs | `text-native` |

## Spreadsheets / tabular / data interchange

| Extensions | MIME / type hint | What | Ingest posture |
| --- | --- | --- | --- |
| `.xls` | application/vnd.ms-excel | Legacy Excel BIFF | `structured-parse` |
| `.xlsx` | application/vnd.openxmlformats-officedocument.spreadsheetml.sheet | OOXML Excel | `structured-parse` |
| `.xlsm` | application/vnd.ms-excel.sheet.macroEnabled.12 | Excel macros | `dangerous` |
| `.xlsb` | application/vnd.ms-excel.sheet.binary.macroEnabled.12 | Excel binary OOXML | `structured-parse` |
| `.xlt .xltx .xltm` | Excel templates | Excel templates | `structured-parse` |
| `.xlw` | application/vnd.ms-excel | Excel workspace | `binary-opaque` |
| `.xml` | application/xml / text/xml | Generic XML (also Excel SpreadsheetML) | `structured-parse` |
| `.ods` | application/vnd.oasis.opendocument.spreadsheet | OpenDocument spreadsheet | `structured-parse` |
| `.fods` | application/vnd.oasis.opendocument.spreadsheet-flat-xml | Flat ODF spreadsheet | `structured-parse` |
| `.ots` | ODS template | ODS template | `structured-parse` |
| `.numbers` | application/x-iwork-numbers-sffnumbers | Apple Numbers | `structured-parse` |
| `.csv .tsv .tab .psv` | text/csv / text/tab-separated-values | Delimited text tables | `structured-parse` |
| `.ssv` | text/plain | Semicolon-separated values | `structured-parse` |
| `.dsv` | text/plain | Delimiter-separated values (generic) | `structured-parse` |
| `.parquet` | application/vnd.apache.parquet | Apache Parquet columnar | `structured-parse` |
| `.feather .arrow` | application/vnd.apache.arrow.file | Arrow/Feather | `structured-parse` |
| `.orc` | application/vnd.apache.orc | Apache ORC | `structured-parse` |
| `.avro` | application/avro | Apache Avro | `structured-parse` |
| `.ipc` | application/vnd.apache.arrow.stream | Arrow IPC stream | `structured-parse` |
| `.sas7bdat .xpt .sas7bcat` | application/x-sas-* | SAS datasets | `structured-parse` |
| `.sav .zsav .por` | application/x-spss-* | SPSS | `structured-parse` |
| `.dta` | application/x-stata | Stata dataset | `structured-parse` |
| `.rdata .rda .rds` | application/x-r-data | R serialized data | `binary-opaque` |
| `.mat` | application/x-matlab-data | MATLAB MAT (also collision) | `structured-parse` |
| `.h5 .hdf5 .hdf .he5` | application/x-hdf | HDF4/HDF5 scientific | `structured-parse` |
| `.nc .nc4 .cdf` | application/netcdf / x-netcdf | NetCDF / CDF | `structured-parse` |
| `.fits .fit .fts` | application/fits | FITS astronomy | `structured-parse` |
| `.grib .grib2 .grb` | application/x-grib | GRIB weather grids | `structured-parse` |
| `.sqlite .sqlite3 .db .db3 .s3db .sl3` | application/vnd.sqlite3 | SQLite database file | `structured-parse` |
| `.mdb .accdb` | application/vnd.ms-access | Microsoft Access | `structured-parse` |
| `.dbf` | application/x-dbf | dBASE / FoxPro table | `structured-parse` |
| `.frm .myd .myi` | application/x-mysql-* | MySQL table fragments | `binary-opaque` |
| `.dump .sql` | application/sql / text | SQL dump / DDL | `text-native` |
| `.jsonl .ndjson .ldjson` | application/x-ndjson | Newline-delimited JSON | `structured-parse` |
| `.json` | application/json | JSON | `structured-parse` |
| `.json5 .jsonc` | application/json5 | JSON5 / JSON-with-comments | `structured-parse` |
| `.cbor` | application/cbor | CBOR binary JSON | `structured-parse` |
| `.msgpack .mp` | application/msgpack | MessagePack | `structured-parse` |
| `.bson` | application/bson | BSON | `structured-parse` |
| `.protobuf .pb .protobin` | application/x-protobuf | Protobuf binary | `structured-parse` |
| `.proto` | text/x-protobuf | Protobuf schema | `text-native` |
| `.thrift` | text/x-thrift | Thrift IDL | `text-native` |
| `.avsc` | application/json | Avro schema | `structured-parse` |
| `.yml .yaml` | application/yaml / text/yaml | YAML | `structured-parse` |
| `.toml` | application/toml | TOML | `structured-parse` |
| `.ini .cfg .conf .properties` | text/plain | INI/config properties | `text-native` |
| `.env` | text/plain | dotenv (often secrets!) | `dangerous` |
| `.plist` | application/x-plist / xml | Apple property list | `structured-parse` |
| `.csv.gz .tsv.gz .parquet.gz` | application/*+gzip | Compressed tabular | `archive-expand` |

## Presentations

| Extensions | MIME / type hint | What | Ingest posture |
| --- | --- | --- | --- |
| `.ppt` | application/vnd.ms-powerpoint | Legacy PowerPoint | `document-convert` |
| `.pptx` | application/vnd.openxmlformats-officedocument.presentationml.presentation | OOXML PowerPoint | `document-convert` |
| `.pptm` | application/vnd.ms-powerpoint.presentation.macroEnabled.12 | PPT macros | `dangerous` |
| `.pps .ppsx .ppsm` | PowerPoint show | PowerPoint slideshow | `document-convert` |
| `.pot .potx .potm` | PowerPoint template | PPT templates | `document-convert` |
| `.odp` | application/vnd.oasis.opendocument.presentation | OpenDocument presentation | `document-convert` |
| `.otp` | ODP template | ODP template | `document-convert` |
| `.key` | application/x-iwork-keynote-sffkey | Apple Keynote | `document-convert` |
| `.gslides` | application/vnd.google-apps.presentation | Google Slides stub/export marker | `binary-opaque` |
| `.pez` | application/x-prezi | Prezi (legacy) | `binary-opaque` |
| `.sxi` | application/vnd.sun.xml.impress | OpenOffice Impress 1.x | `document-convert` |

## Email / messaging / calendars / contacts

| Extensions | MIME / type hint | What | Ingest posture |
| --- | --- | --- | --- |
| `.eml` | message/rfc822 | Single email message | `structured-parse` |
| `.emlx` | message/x-emlx | Apple Mail message | `structured-parse` |
| `.msg` | application/vnd.ms-outlook | Outlook MSG | `structured-parse` |
| `.pst .ost` | application/vnd.ms-outlook* | Outlook mailbox stores | `structured-parse` |
| `.mbox .mbx` | application/mbox | Unix mailbox | `structured-parse` |
| `.maildir` | inode/directory | Maildir layout (dir) | `structured-parse` |
| `.ics .ical .ifb` | text/calendar | iCalendar | `structured-parse` |
| `.vcs .vcal` | text/x-vcalendar | vCalendar | `structured-parse` |
| `.vcf .vcard` | text/vcard | vCard contacts | `structured-parse` |
| `.ldif` | text/directory | LDAP data interchange | `structured-parse` |
| `.mht .mhtml .eml.html` | multipart/related | MIME HTML archive | `document-convert` |
| `.chat .imessage` | various | Chat exports (app-specific) | `text-native` |
| `.slack.zip .discord.json` | application/zip / json | Chat platform exports | `archive-expand` |

## Markup / web / hypertext

| Extensions | MIME / type hint | What | Ingest posture |
| --- | --- | --- | --- |
| `.html .htm .shtml .xhtml .xht` | text/html / application/xhtml+xml | HTML / XHTML | `document-convert` |
| `.css .scss .sass .less .styl` | text/css | Stylesheets | `text-native` |
| `.js .mjs .cjs` | text/javascript | JavaScript | `text-native` |
| `.ts .tsx .mts .cts` | text/typescript | TypeScript (also collision with MPEG-TS) | `text-native` |
| `.jsx` | text/jsx | JSX | `text-native` |
| `.vue .svelte` | text/plain | SFC front-end components | `text-native` |
| `.wasm` | application/wasm | WebAssembly binary | `binary-opaque` |
| `.wat` | text/plain | WebAssembly text | `text-native` |
| `.map` | application/json | Source map (also image.map) | `structured-parse` |
| `.webmanifest .manifest` | application/manifest+json | Web app manifest | `structured-parse` |
| `.url .webloc .desktop` | application/internet-shortcut | URL shortcuts | `structured-parse` |
| `.swf .fla` | application/x-shockwave-flash | Flash (legacy) | `dangerous` |
| `.xul` | application/vnd.mozilla.xul+xml | Mozilla XUL | `structured-parse` |

## Code / notebooks / build / lockfiles (representative; not every language)

| Extensions | MIME / type hint | What | Ingest posture |
| --- | --- | --- | --- |
| `.c .h .cpp .cc .cxx .hpp .hh .hxx .cu .cuh` | text/x-c* | C/C++/CUDA | `text-native` |
| `.cs .csx` | text/x-csharp | C# | `text-native` |
| `.java .kt .kts .groovy .scala .sc .clj .cljs .edn` | text/* | JVM / Clojure family | `text-native` |
| `.go .rs .zig .nim .d` | text/* | Systems languages | `text-native` |
| `.py .pyi .pyw .ipynb .pxd .pyx` | text/x-python / json | Python / Cython / Jupyter | `text-native` |
| `.r .R .rmd .qmd` | text/x-r / markdown | R / Quarto / R Markdown | `text-native` |
| `.jl` | text/x-julia | Julia | `text-native` |
| `.m .mm` | text/x-objcsrc / matlab | Obj-C or MATLAB (collision) | `text-native` |
| `.swift` | text/x-swift | Swift | `text-native` |
| `.php .phtml` | application/x-httpd-php | PHP | `text-native` |
| `.rb .erb .rake` | text/x-ruby | Ruby | `text-native` |
| `.pl .pm .t .pod` | text/x-perl | Perl | `text-native` |
| `.lua` | text/x-lua | Lua | `text-native` |
| `.sh .bash .zsh .fish .ps1 .psm1 .psd1 .bat .cmd .command` | text/x-shellscript | Shells / PowerShell / batch | `dangerous` |
| `.sql .pgsql .mysql .ddl` | application/sql | SQL scripts | `text-native` |
| `.graphql .gql` | application/graphql | GraphQL | `text-native` |
| `.ipynb` | application/x-ipynb+json | Jupyter notebook | `structured-parse` |
| `.Rproj .sln .csproj .vbproj .fsproj .vcxproj .xcodeproj .pbxproj` | various | IDE/project files | `structured-parse` |
| `.gradle .kts .maven .pom` | text/xml / groovy | Build scripts | `text-native` |
| `.cmake .make .mak .ninja .bazel .bzl .buck` | text/plain | Build systems | `text-native` |
| `.lock .lockb .sum .mod` | text/plain / go | Dependency lockfiles (go.mod/.sum) | `text-native` |
| `.gitignore .gitattributes .gitmodules .gitkeep` | text/plain | Git metadata files | `text-native` |
| `.diff .patch` | text/x-diff | Patches | `text-native` |
| `.dockerfile Dockerfile Containerfile` | text/plain | Container build files | `text-native` |
| `.tf .tfvars .hcl .nomad` | text/x-hcl | Terraform/HCL | `text-native` |
| `.bicep .pulumi` | text/plain | IaC DSLs | `text-native` |

## Images — raster

| Extensions | MIME / type hint | What | Ingest posture |
| --- | --- | --- | --- |
| `.png` | image/png | PNG | `media-transcribe` |
| `.jpg .jpeg .jpe .jfif .jif` | image/jpeg | JPEG | `media-transcribe` |
| `.jxl` | image/jxl | JPEG XL | `media-transcribe` |
| `.jp2 .j2k .jpf .jpx .jpm` | image/jp2 | JPEG 2000 family | `media-transcribe` |
| `.webp` | image/webp | WebP | `media-transcribe` |
| `.gif` | image/gif | GIF (still/animated) | `media-transcribe` |
| `.apng` | image/apng | Animated PNG | `media-transcribe` |
| `.bmp .dib` | image/bmp | Bitmap | `media-transcribe` |
| `.tif .tiff` | image/tiff | TIFF | `media-transcribe` |
| `.heic .heif .avif .avifs` | image/heic / heif / avif | HEIF/AVIF family | `media-transcribe` |
| `.ico .cur` | image/x-icon | Windows icon/cursor | `media-transcribe` |
| `.icns` | image/icns | Apple icon resource | `media-transcribe` |
| `.tga .targa` | image/x-tga | Targa | `media-transcribe` |
| `.pcx` | image/x-pcx | PCX legacy | `media-transcribe` |
| `.ppm .pgm .pbm .pnm .pam` | image/x-portable-* | Netpbm family | `media-transcribe` |
| `.exr` | image/aces / x-exr | OpenEXR HDR | `media-transcribe` |
| `.hdr .rgbe .xyze` | image/vnd.radiance | Radiance HDR | `media-transcribe` |
| `.pfm` | image/x-portable-floatmap | Portable float map | `media-transcribe` |
| `.dds` | image/vnd-ms.dds | DirectDraw Surface | `media-transcribe` |
| `.ktx .ktx2` | image/ktx | Khronos texture | `media-transcribe` |
| `.astc` | image/astc | ASTC texture | `media-transcribe` |
| `.psd .psb` | image/vnd.adobe.photoshop | Photoshop | `media-transcribe` |
| `.xcf` | image/x-xcf | GIMP | `media-transcribe` |
| `.kra` | application/x-krita | Krita | `media-transcribe` |
| `.clip` | application/clipstudio | Clip Studio Paint | `binary-opaque` |
| `.sai .sai2` | application/x-painttool-sai | PaintTool SAI | `binary-opaque` |
| `.csp` | application/octet-stream | Clip Studio (alt) | `binary-opaque` |

## Images — camera RAW / scientific / medical / fax

| Extensions | MIME / type hint | What | Ingest posture |
| --- | --- | --- | --- |
| `.raw` | image/x-raw | Generic RAW (many vendors; collision-prone) | `media-transcribe` |
| `.cr2 .cr3 .crw` | image/x-canon-cr* | Canon RAW | `media-transcribe` |
| `.nef .nrw` | image/x-nikon-nef | Nikon RAW | `media-transcribe` |
| `.arw .srf .sr2` | image/x-sony-arw | Sony RAW | `media-transcribe` |
| `.orf` | image/x-olympus-orf | Olympus RAW | `media-transcribe` |
| `.raf` | image/x-fuji-raf | Fujifilm RAW | `media-transcribe` |
| `.rw2 .raw` | image/x-panasonic-rw2 | Panasonic RAW | `media-transcribe` |
| `.dng` | image/x-adobe-dng | Adobe DNG | `media-transcribe` |
| `.pef .ptx` | image/x-pentax-pef | Pentax RAW | `media-transcribe` |
| `.x3f` | image/x-sigma-x3f | Sigma RAW | `media-transcribe` |
| `.3fr .fff` | image/x-hasselblad-* | Hasselblad RAW | `media-transcribe` |
| `.mef` | image/x-mamiya-mef | Mamiya RAW | `media-transcribe` |
| `.mos` | image/x-leaf-mos | Leaf MOS | `media-transcribe` |
| `.iiq` | image/x-phaseone-iiq | Phase One | `media-transcribe` |
| `.kdc .dcr` | image/x-kodak-* | Kodak RAW | `media-transcribe` |
| `.srw` | image/x-samsung-srw | Samsung RAW | `media-transcribe` |
| `.dcm .dicom` | application/dicom | DICOM medical imaging | `media-transcribe` |
| `.nii .nii.gz .img .hdr` | application/x-nifti | NIfTI neuroimaging (hdr collision) | `media-transcribe` |
| `.mha .mhd` | application/x-metaimage | MetaImage ITK | `media-transcribe` |
| `.nrrd .nhdr` | application/x-nrrd | NRRD | `media-transcribe` |
| `.svs .ndpi .vms .vmu .scn` | image/x-*-wsi | Whole-slide pathology | `media-transcribe` |
| `.czi` | image/x-zeiss-czi | Zeiss CZI microscopy | `media-transcribe` |
| `.lif` | image/x-leica-lif | Leica LIF | `media-transcribe` |
| `.oib .oif` | image/x-olympus-* | Olympus microscopy | `media-transcribe` |
| `.ims` | image/x-imaris | Imaris | `binary-opaque` |
| `.mrc` | application/x-mrc | MRC cryo-EM | `media-transcribe` |
| `.g3 .g4 .fax` | image/g3fax | Fax encodings | `media-transcribe` |

## Images — vector / design

| Extensions | MIME / type hint | What | Ingest posture |
| --- | --- | --- | --- |
| `.svg .svgz` | image/svg+xml | SVG | `document-convert` |
| `.emf .wmf` | image/x-emf / wmf | Windows metafiles | `media-transcribe` |
| `.cdr` | application/coreldraw | CorelDRAW | `binary-opaque` |
| `.vsd .vsdx .vss .vst` | application/vnd.visio* | Visio | `document-convert` |
| `.sketch` | application/x-sketch | Sketch (zip) | `archive-expand` |
| `.fig` | application/x-xfig / figma | Fig/Xfig or Figma export ambiguity | `binary-opaque` |
| `.xd` | application/vnd.adobe.xd | Adobe XD | `binary-opaque` |
| `.afdesign .afphoto .afpub` | application/x-affinity-* | Affinity suite | `binary-opaque` |
| `.dwg .dxf` | image/vnd.dwg / dxf | AutoCAD drawing | `binary-opaque` |

## Audio — containers & common codecs (extensions)

| Extensions | MIME / type hint | What | Ingest posture |
| --- | --- | --- | --- |
| `.mp3 .mp2 .mpga` | audio/mpeg | MPEG audio | `media-transcribe` |
| `.wav .wave .bwf` | audio/wav | WAV / Broadcast WAV | `media-transcribe` |
| `.flac` | audio/flac | FLAC | `media-transcribe` |
| `.ogg .oga .opus .spx` | audio/ogg / opus | Ogg / Opus / Speex | `media-transcribe` |
| `.m4a .m4b .m4p .aac .mp4a` | audio/mp4 / aac | AAC in MP4/M4A | `media-transcribe` |
| `.wma` | audio/x-ms-wma | Windows Media Audio | `media-transcribe` |
| `.aiff .aif .aifc` | audio/aiff | AIFF | `media-transcribe` |
| `.alac` | audio/alac | Apple Lossless (often .m4a) | `media-transcribe` |
| `.ape` | audio/x-ape | Monkey's Audio | `media-transcribe` |
| `.wv` | audio/wavpack | WavPack | `media-transcribe` |
| `.tta` | audio/tta | True Audio | `media-transcribe` |
| `.tak` | audio/x-tak | Tom's lossless | `media-transcribe` |
| `.ac3 .eac3 .ec3` | audio/ac3 | Dolby Digital | `media-transcribe` |
| `.dts .dtshd` | audio/vnd.dts | DTS | `media-transcribe` |
| `.amr .awb` | audio/amr | Adaptive Multi-Rate | `media-transcribe` |
| `.3ga` | audio/mp4 | 3GPP audio | `media-transcribe` |
| `.caf` | audio/x-caf | Apple Core Audio Format | `media-transcribe` |
| `.au .snd` | audio/basic | Sun/NeXT audio | `media-transcribe` |
| `.mid .midi .kar .rmi` | audio/midi | MIDI | `binary-opaque` |
| `.mod .s3m .xm .it .mptm` | audio/x-mod | Tracker modules (mod collision) | `binary-opaque` |
| `.sid` | audio/prs.sid | C64 SID | `binary-opaque` |
| `.nsf .nsfe` | application/x-nsf | NES sound | `binary-opaque` |
| `.vgm .vgz` | audio/x-vgm | Video Game Music | `binary-opaque` |
| `.ra .ram .rm` | audio/x-pn-realaudio | RealAudio | `media-transcribe` |
| `.voc` | audio/x-voc | Creative VOC | `media-transcribe` |
| `.w64` | audio/x-w64 | Sony Wave64 | `media-transcribe` |
| `.rf64` | audio/x-rf64 | RF64 WAV | `media-transcribe` |
| `.weba` | audio/webm | WebM audio | `media-transcribe` |

## Video — containers (codec is inside)

| Extensions | MIME / type hint | What | Ingest posture |
| --- | --- | --- | --- |
| `.mp4 .m4v .mp4v .mpg4` | video/mp4 | ISO BMFF / MP4 | `media-transcribe` |
| `.mov .qt` | video/quicktime | QuickTime | `media-transcribe` |
| `.webm` | video/webm | WebM | `media-transcribe` |
| `.mkv .mk3d .mka .mks` | video/x-matroska | Matroska | `media-transcribe` |
| `.avi` | video/x-msvideo | AVI | `media-transcribe` |
| `.wmv .asf .asx` | video/x-ms-wmv | Windows Media | `media-transcribe` |
| `.flv .f4v .f4p .f4a .f4b` | video/x-flv | Flash Video | `media-transcribe` |
| `.mpeg .mpg .mpe .m1v .m2v` | video/mpeg | MPEG-PS | `media-transcribe` |
| `.ts .mts .m2ts .m2t .tsv` | video/mp2t | MPEG transport stream (tsv/ts collisions) | `media-transcribe` |
| `.vob .evo` | video/dvd | DVD Video Object | `media-transcribe` |
| `.ogv .ogx` | video/ogg | Ogg video | `media-transcribe` |
| `.3gp .3g2` | video/3gpp | 3GPP mobile video | `media-transcribe` |
| `.rmvb .rm` | application/vnd.rn-realmedia | RealMedia | `media-transcribe` |
| `.divx .xvid` | video/divx | DivX/Xvid labeled files | `media-transcribe` |
| `.mxf` | application/mxf | Material Exchange Format (broadcast) | `media-transcribe` |
| `.gxf` | application/gxf | General eXchange Format | `media-transcribe` |
| `.r3d` | application/x-red-r3d | RED camera | `media-transcribe` |
| `.braw` | application/x-braw | Blackmagic RAW video | `media-transcribe` |
| `.ari` | application/x-arri | ARRI raw video | `media-transcribe` |
| `.dpx` | image/x-dpx | DPX film frames | `media-transcribe` |
| `.y4m` | video/x-yuv4mpeg | YUV4MPEG | `media-transcribe` |
| `.nuv` | video/x-nuv | NuppelVideo | `media-transcribe` |
| `.nsv` | application/x-nsv | Nullsoft Streaming Video | `media-transcribe` |
| `.wtv .dvr-ms` | video/x-ms-wtv | Windows Recorded TV | `media-transcribe` |
| `.tod .mod` | video/mpeg | Camcorder MPEG (mod collision) | `media-transcribe` |
| `.amv` | video/x-amv | Anime Music Video format | `media-transcribe` |

## Captions / subtitles / lyrics / timed text

| Extensions | MIME / type hint | What | Ingest posture |
| --- | --- | --- | --- |
| `.srt` | application/x-subrip | SubRip | `text-native` |
| `.vtt .webvtt` | text/vtt | WebVTT | `text-native` |
| `.ass .ssa` | text/x-ssa | Advanced SubStation | `text-native` |
| `.sub .idx` | application/x-subviewer / image | SUB/IDX (bitmap subs) | `binary-opaque` |
| `.smi .sami` | application/x-sami | SAMI | `text-native` |
| `.ttml .dfxp .xml` | application/ttml+xml | TTML timed text | `structured-parse` |
| `.sbv` | text/plain | YouTube SBV | `text-native` |
| `.lrc` | text/plain | Lyrics LRC | `text-native` |
| `.cap .scc .mcc` | text/plain | Broadcast caption formats | `text-native` |
| `.sup` | application/x-sup | PGS blu-ray subs | `binary-opaque` |

## Archives / packages / compression / disk images

| Extensions | MIME / type hint | What | Ingest posture |
| --- | --- | --- | --- |
| `.zip .zipx` | application/zip | ZIP | `archive-expand` |
| `.rar` | application/vnd.rar | RAR | `archive-expand` |
| `.7z` | application/x-7z-compressed | 7-Zip | `archive-expand` |
| `.tar` | application/x-tar | Tape archive | `archive-expand` |
| `.gz .gzip .tgz .tar.gz` | application/gzip | gzip | `archive-expand` |
| `.bz2 .tbz2 .tar.bz2` | application/x-bzip2 | bzip2 | `archive-expand` |
| `.xz .txz .tar.xz` | application/x-xz | xz | `archive-expand` |
| `.lz .lzma .tlz` | application/x-lzma | LZMA | `archive-expand` |
| `.zst .zstd .tzst` | application/zstd | Zstandard | `archive-expand` |
| `.lz4` | application/x-lz4 | LZ4 | `archive-expand` |
| `.br` | application/brotli | Brotli | `archive-expand` |
| `.Z .z .taz` | application/x-compress | Unix compress | `archive-expand` |
| `.cab` | application/vnd.ms-cab-compressed | Windows Cabinet | `archive-expand` |
| `.iso .img .udf .nrg .mdf .mds .bin .cue` | application/x-iso9660-image | Optical disc images (many collisions) | `dangerous` |
| `.dmg .sparseimage .cdr` | application/x-apple-diskimage | Apple disk images | `dangerous` |
| `.vmdk .vdi .vhd .vhdx .qcow .qcow2 .qed` | application/x-*-disk | VM disk images | `dangerous` |
| `.wim .esd .swm` | application/x-ms-wim | Windows imaging | `dangerous` |
| `.apk .aab` | application/vnd.android.package-archive | Android packages | `dangerous` |
| `.ipa` | application/octet-stream | iOS app package | `dangerous` |
| `.deb .rpm .pkg .msi .msix .appx .msu` | application/*-package | OS installers | `dangerous` |
| `.jar .war .ear` | application/java-archive | Java archives | `dangerous` |
| `.whl .egg` | application/x-wheel+zip | Python packages | `archive-expand` |
| `.nupkg` | application/zip | NuGet | `archive-expand` |
| `.crx .xpi` | application/x-*-extension | Browser extensions | `dangerous` |
| `.sit .sitx .sea` | application/x-stuffit | StuffIt legacy | `archive-expand` |
| `.arc .arj .lzh .lha .zoo .pak` | application/x-* | Legacy archives | `archive-expand` |
| `.cpio .shar` | application/x-cpio | Unix package archives | `archive-expand` |

## Ebooks / comics / publishing

| Extensions | MIME / type hint | What | Ingest posture |
| --- | --- | --- | --- |
| `.epub` | application/epub+zip | EPUB | `document-convert` |
| `.mobi .azw .azw3 .kf8 .kfx` | application/x-mobipocket / amazon | Kindle/Mobipocket | `document-convert` |
| `.fb2 .fb2.zip` | application/x-fictionbook+xml | FictionBook | `document-convert` |
| `.cbz .cbr .cb7 .cbt .cba` | application/vnd.comicbook-* | Comic book archives | `archive-expand` |
| `.lit` | application/x-ms-reader | Microsoft Reader lit | `binary-opaque` |
| `.pdb .prc` | application/vnd.palm | PalmDoc / eReader (pdb collision) | `document-convert` |
| `.lrf .lrx` | application/x-sony-bbeb | Sony BBeB | `binary-opaque` |
| `.ibooks` | application/x-ibooks+zip | Apple iBooks | `document-convert` |

## Fonts

| Extensions | MIME / type hint | What | Ingest posture |
| --- | --- | --- | --- |
| `.ttf .otf .ttc` | font/ttf / otf | TrueType / OpenType | `binary-opaque` |
| `.woff .woff2` | font/woff* | Web fonts | `binary-opaque` |
| `.eot` | application/vnd.ms-fontobject | Embedded OpenType | `binary-opaque` |
| `.pfb .pfm .afm .pfa` | application/x-font-type1 | PostScript Type 1 | `binary-opaque` |
| `.bdf .pcf .snf` | application/x-font-* | Bitmap fonts | `binary-opaque` |
| `.fon` | application/x-windows-fon | Windows FON | `binary-opaque` |

## 3D / CAD / GIS / maps

| Extensions | MIME / type hint | What | Ingest posture |
| --- | --- | --- | --- |
| `.obj .mtl` | model/obj | Wavefront OBJ | `text-native` |
| `.stl .stla .stlb` | model/stl | Stereolithography | `binary-opaque` |
| `.ply` | model/ply | Polygon File Format | `binary-opaque` |
| `.gltf .glb` | model/gltf+json / gltf-binary | glTF | `structured-parse` |
| `.fbx` | application/octet-stream | Autodesk FBX | `binary-opaque` |
| `.dae` | model/vnd.collada+xml | COLLADA | `structured-parse` |
| `.3ds .max` | application/x-3ds | 3D Studio | `binary-opaque` |
| `.blend` | application/x-blender | Blender | `binary-opaque` |
| `.c4d` | application/x-cinema4d | Cinema 4D | `binary-opaque` |
| `.ma .mb` | application/x-maya* | Maya ASCII/binary | `binary-opaque` |
| `.usd .usda .usdc .usdz` | model/vnd.usd* | Universal Scene Description | `structured-parse` |
| `.step .stp .iges .igs` | model/step / iges | CAD exchange | `structured-parse` |
| `.ifc .ifczip` | application/x-step | IFC BIM | `structured-parse` |
| `.rvt .rfa` | application/vnd.autodesk.revit | Revit | `binary-opaque` |
| `.skp` | application/vnd.sketchup.skp | SketchUp | `binary-opaque` |
| `.geojson .topojson` | application/geo+json | GeoJSON | `structured-parse` |
| `.kml .kmz` | application/vnd.google-earth.kml+xml | KML/KMZ | `structured-parse` |
| `.gpx` | application/gpx+xml | GPS Exchange | `structured-parse` |
| `.shp .shx .dbf .prj .cpg .sbn .sbx` | application/x-esri-* | Shapefile sidecar set | `structured-parse` |
| `.gpkg` | application/geopackage+sqlite3 | GeoPackage | `structured-parse` |
| `.tif .tiff geotiff` | image/tiff | GeoTIFF (same ext as TIFF) | `media-transcribe` |
| `.mbtiles` | application/vnd.mapbox-vector-tile+sqlite | MBTiles | `structured-parse` |
| `.pbf .osm` | application/x-protobuf / xml | OpenStreetMap data | `structured-parse` |
| `.las .laz .e57` | application/x-las | LiDAR point clouds | `binary-opaque` |
| `.pcd` | application/x-pcd | Point Cloud Data | `binary-opaque` |

## Certificates / crypto / signing / secrets (often quarantine)

| Extensions | MIME / type hint | What | Ingest posture |
| --- | --- | --- | --- |
| `.pem .crt .cer .der .p7b .p7c` | application/x-pem / pkix-cert | X.509 certificates | `dangerous` |
| `.key .p8 .pk8` | application/pkcs8 | Private keys | `dangerous` |
| `.p12 .pfx` | application/x-pkcs12 | PKCS#12 keystores | `dangerous` |
| `.jks .keystore .truststore` | application/x-java-keystore | Java keystores | `dangerous` |
| `.asc .sig .gpg .pgp` | application/pgp-* | OpenPGP signatures/keys | `dangerous` |
| `.ssh .pub` | text/plain | SSH keys | `dangerous` |
| `.kdbx .kdb` | application/x-keepass | Password databases | `dangerous` |
| `.1pif .agilekeychain` | application/json / dir | 1Password exports | `dangerous` |

## Executables / bytecode / firmware (quarantine)

| Extensions | MIME / type hint | What | Ingest posture |
| --- | --- | --- | --- |
| `.dll .sys .drv .ocx .cpl` | application/x-msdownload | Windows libraries/drivers | `dangerous` |
| `.so .dylib .bundle` | application/x-sharedlib | Unix/macOS shared libs | `dangerous` |
| `.o .obj .a .lib` | application/x-object | Object/static libs (obj collision) | `dangerous` |
| `.elf .axf .out .bin .img` | application/x-executable | ELF/firmware blobs (bin/img collisions) | `dangerous` |
| `.class .dex .odex .vdex` | application/java-vm / android | JVM/Android bytecode | `dangerous` |
| `.pyc .pyo .pyd` | application/x-python-bytecode | Python bytecode | `dangerous` |
| `.app .AppImage .snap .flatpak` | application/x-* | App bundles | `dangerous` |
| `.bat .cmd .ps1 .vbs .js .wsf .hta` | text/* | Script hosts (also dual-use) | `dangerous` |

## Logs / telemetry / observability artifacts

| Extensions | MIME / type hint | What | Ingest posture |
| --- | --- | --- | --- |
| `.log .out .err` | text/plain | Log text | `text-native` |
| `.evtx .evt` | application/x-ms-evtx | Windows Event Log | `structured-parse` |
| `.har` | application/json | HTTP Archive | `structured-parse` |
| `.pcap .pcapng .cap .dmp` | application/vnd.tcpdump.pcap | Network captures / dumps | `binary-opaque` |
| `.trace .perf .speedscope` | application/json / binary | Perf traces | `structured-parse` |

## Misc / platform / legacy / ambiguous high-collision

| Extensions | MIME / type hint | What | Ingest posture |
| --- | --- | --- | --- |
| `.dat .data .bin .tmp .temp .cache .part .crdownload .download` | application/octet-stream | Generic/opaque / incomplete downloads | `binary-opaque` |
| `.bak .old .orig .swp .swo` | application/octet-stream | Editor backups | `binary-opaque` |
| `.DS_Store .localized Thumbs.db desktop.ini` | application/octet-stream | OS junk metadata | `binary-opaque` |
| `.torrent` | application/x-bittorrent | BitTorrent metainfo | `structured-parse` |
| `.magnet` | text/plain | Magnet URI file | `text-native` |
| `.alias` | application/x-apple-alias | macOS alias | `binary-opaque` |
| `.scf` | application/x-wine-extension-scf | Windows Explorer command | `dangerous` |
| `.library-ms .searchConnector-ms` | application/windows-library+xml | Windows library shortcuts | `dangerous` |
| `.msstyles` | application/octet-stream | Windows visual styles | `binary-opaque` |
| `.theme .themepack` | application/x-windows-theme | Windows themes | `archive-expand` |
| `.reg` | text/x-ms-regedit | Windows registry export | `dangerous` |
| `.hlp .chm` | application/winhlp / htmlhelp | Windows help | `document-convert` |

## Cross-cutting notes

### Containers vs codecs (**Observed**)
Video/audio **extensions name containers** more often than codecs. `.mp4` may hold H.264, HEVC, AV1, AAC, etc. Policy must sniff tracks, not trust the suffix.

### Compound documents (**Observed**)
OOXML (`.docx/.xlsx/.pptx`), EPUB, JAR, APK, many “single files” are ZIP packages. Ingest may need zip-slip-safe expand + inner MIME routing.

### Extension collisions (**Observed**)
High-pain collisions: `.ts` (TypeScript vs MPEG-TS), `.mod` (tracker audio vs camcorder MPEG), `.m` (MATLAB vs Objective-C), `.dat/.bin/.img` (everything), `.xml` (generic), `.hdr` (Radiance vs NIfTI), `.pdb` (ebook vs debug symbols), `.obj` (3D vs object code), `.tsv` (tab-separated vs MPEG-TS rare).

### MIME unreliability (**Observed**)
Browsers send `application/octet-stream` constantly. Prefer magic-byte sniffers (e.g. Apache Tika / file(1) / filetype libs) then extension as hint.

### Macros / active content (**Inference**)
`.docm/.xlsm/.pptm`, OLE, VBA, embedded scripts in PDFs → `dangerous/quarantine` until a sandbox policy exists.

### Password-protected / DRM (**Observed**)
Encrypted PDF/Office, Widevine/FairPlay media, password ZIPs: detect and fail closed with a typed error rather than silent empty convert.

### “Support xlsx” ≠ Excel fidelity (**Inference**)
Parsing sheets/CSV export ≠ formulas, pivot caches, VBA, external links, charts-as-images. Split **tabular RAG** from **spreadsheet agent** (see UMC multimedia benchmark synthesis).

## RememberStack-oriented grouping (**Inference**)

| Near current design | Likely next | Stay opaque / quarantine by default |
| --- | --- | --- |
| PDF, OOXML prose/slides, ODF, HTML, Markdown, plain text, CSV/TSV, JSON/YAML, common images, common A/V containers, SRT/VTT, ZIP/TAR.* | Email boxes (eml/mbox/pst), EPUB, parquet/arrow, SQLite, GeoJSON/KML, notebooks, DICOM (domain), GeoTIFF | Executables, disk/VM images, private keys, password DBs, macros, firmware, browser extensions, incomplete `.part` downloads |

## Counts

This Grok pass lists **375 extension families/rows** across the taxonomy tables above (each row may bundle several extensions). Peer analyses may expand codecs, vendor RAW, or language ecosystems further.

## Sources (accessed ~2026-08-26)

- IANA media types: https://www.iana.org/assignments/media-types/
- Apache Tika supported formats documentation
- FFmpeg general / demuxers documentation (containers vs codecs)
- LibreOffice filter / import documentation
- MDN MIME / common web types
- Existing repo analysis: `plan/analysis/media_handling/`

## Non-goals

- Not a binding allowlist or converter roadmap
- Not claiming current engine support
- Not enumerating every rare codec FourCC or every `.c`/`.h` ecosystem file
