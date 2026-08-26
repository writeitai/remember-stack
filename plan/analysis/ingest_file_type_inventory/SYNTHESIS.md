# Ingest file-type inventory — SYNTHESIS

**Status:** non-binding. **Date:** 2026-08-26.
**Inputs:**
[Grok](grok.md) ·
[Codex](external_agents/codex.md) ·
[Antigravity](external_agents/agy.md).

Related prior work: [`../media_handling/`](../media_handling/) (media plane
design analysis). This directory is an **enumeration + ingest-posture taxonomy**,
not a redesign of E0/D65.

## 1. What we asked for

A super-exhaustive list of file types an agent-memory system might be asked to
ingest (webm, mp3, pdf, xlsx, xml, …), with taxonomy and coarse ingest postures.

## 2. Scale of the three passes

| Author | Approx. size | Shape |
| --- | --- | --- |
| Codex | ~1650 lines, ~1000 table rows | Deepest; registries + security + domain science/GIS/CAD |
| Antigravity | ~820 lines, ~500 table rows | Strong multi-layer model (extension→magic→container→codec) |
| Grok | ~555 lines, ~375 families | Broad practical agent-upload catalog with posture labels |

None claims a closed universe. All treat “exhaustive” as **breadth of
upload-facing families**, not every codec FourCC or language source file.

## 3. Consensus

1. **File type ≠ one string.** Distinguish extension, MIME, magic bytes,
   outer container, inner streams/codecs, and semantic features (macros,
   formulas, scripts).
2. **Shared ingest postures** (names vary slightly but map cleanly):
   `text-native`, `structured-parse`, `document-convert`, `media-transcribe`,
   `archive-expand`, `binary-opaque`, `dangerous/quarantine`.
3. **Containers vs codecs:** `.mp4`/`.mkv`/`.webm` are containers; codecs live
   inside. Policy must sniff tracks.
4. **Compound docs:** OOXML/EPUB/JAR/APK are ZIPs; need zip-slip-safe expand +
   inner routing.
5. **Extension collisions are first-class hazards:** especially `.ts`, `.mod`,
   `.m`, `.dat`, `.bin`, `.img`, `.xml`, `.hdr`, `.pdb`, `.obj`, `.apk`.
6. **MIME is untrustworthy;** prefer magic + structural parse, extension as hint.
7. **Accepting bytes ≠ understanding the format.** “Support `.xlsx`” ≠ Excel
   fidelity (formulas, pivots, VBA, charts).
8. **RememberStack grouping:**
   - **Near design:** PDF, OOXML/ODF prose & slides, HTML/Markdown, CSV/JSON,
     common images & A/V, captions, ZIP/TAR.*
   - **Likely next:** email stores, EPUB, parquet/arrow/SQLite, GeoJSON/KML,
     notebooks, some scientific/GIS
   - **Default opaque/quarantine:** executables, disk/VM images, keys/secrets,
     macros, firmware, incomplete downloads
9. **Non-goals shared:** no binding allowlist; no claim of current engine
   support; no design edits in this analysis.

## 4. Useful disagreements / emphases

| Topic | Divergence |
| --- | --- |
| Depth vs skimmability | Codex goes deepest on GIS/medical/forensics/packages; Grok optimizes for agent-upload practicality |
| Security framing | Codex/Agy stress parser isolation, XXE/zip bombs, macros more heavily |
| Dataset-shaped inputs | Codex/Agy emphasize multi-file products (Shapefile sets, DICOMDIR, SAFE) more than Grok |
| Language/code breadth | All treat code as `text-native` with script/`dangerous` caveats; none try to list every extension |

**Resolution:** treat Codex as the **reference catalog**, Agy as the
**identification model**, Grok as the **compact operator map**. Future binding
work should cite the synthesis + one primary catalog, not duplicate all three.

## 5. Recommended next steps (still non-binding)

1. Keep this inventory under `plan/analysis/` as the breadth map for E0 routing
   discussions.
2. When designing converter allow/deny behavior, derive from **posture +
   detection**, not extension lists alone.
3. Reconcile with `media_handling` D65 binding: media families here are the
   sensory sources that feed representation generation + raw retain.
4. Do **not** turn this PR into a MIME allowlist in code.

## 6. File map

```
plan/analysis/ingest_file_type_inventory/
  README.md
  grok.md
  SYNTHESIS.md
  external_agents/
    codex.md
    agy.md
```
