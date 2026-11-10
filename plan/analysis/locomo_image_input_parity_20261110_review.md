# OCR/Luna input restoration: validation and review

Non-binding verification record, 2026-11-10. The binding contract is
`plan/designs/locomo_benchmark_design.md` §3. This is not a benchmark score.

The implementation was based on main `4f0e3a03d137266bff9f5ad32e107caae0396883`.
The first review snapshot was `ad2a4357f14d4a944d234f4ca2c0a7712b7e6952`.
Antigravity was invoked with the owner's exact requested model/effort workflow:
`agy --dangerously-skip-permissions --model "Gemini 4 Argon (High)" --print-timeout 180m0s -p <inline-public-source-snapshot>`.
It ran in an empty temporary directory, without repository remotes; its prompt
prohibited tools, edits, network/VM actions, resets, pushes and merges.

## Local verification

- 442 benchmark and shared OCR/image-converter tests passed after the fixes
  below. This includes 30 synthetic image preparation integration cases. The
  actual converter ran against canned provider HTTP responses, including
  binary assets and paid usage; no live model request was made.
- Pyright across src/ and benchmarks/ found zero errors before the focused
  review. The modified image preparation/renderer/runner/CLI files again
  passed after review corrections. Ruff and diff whitespace checks pass.
- The repository's PostgreSQL 19 image was built from the unchanged current
  Dockerfile. A disposable local container ran both real database reuse
  modules: `src/tests/workers/test_reuse_lifecycle.py` and
  `src/tests/workers/test_d140_text_origin_pg.py`. Result: 17 passed, one
  existing strict expected failure for D122's eight-chunk cross-section
  Claimify window, no skips. Provider-call-count tests retain their original
  assertions. The container was removed; no benchmark database was accessed.
- Documentation truth checks, generated llms text, and shard shell syntax
  passed. The pinned real dataset's dry preparation plan enumerated 910
  source-image references and 862 URLs, without creating a cache/output or
  making provider/source HTTP calls.
- Answer template SHA256 remains
  `0b8945c8ac47c9d1558335cedaabe43a06e66a50e1fa0f69b444b3ca191bd7ed`.
  Judge template SHA256 remains
  `cd4007be1e96c9d6441c2c68aac09c2de8fe662f2be9e3730157aa622ce758ca`.

The two native identity helper extractions preserve the previous byte strings
and generations. PostgreSQL reuse was checked on that same core delta; later
review fixes affect benchmark image preparation/accounting only. New image
content is the intentional source-input boundary requiring fresh ingestion.

## First review and resolution

Antigravity requested changes. Format parsing now has its own bounded typed
error handling, including malformed URL ports and corrupted/bomb images.
Immutable binary artifacts publish through a fully flushed temporary inode
and an exclusive hard link, so a partial final file cannot be accepted.
Cache hits and offline bundles require a completed matching conversion
attempt and all native lane usage receipts; a populated paid cache without
its ledger fails closed. Planning and execution validate the same manifest,
finite limits and unoccupied output path. Integration cases cover missing
ledgers, unresolved completion, truncated outcomes/receipts, Pillow errors
and invalid plan bounds. A follow-up review remains required before merge.

The source-image policy may explicitly record permanently unavailable
originals, with visible coverage markers. It does not admit provider failure,
missing conversion files, or missing accounting as caption-only success.
No new LoCoMo run, paid image preparation or score is established by these
local checks. Existing stores and historical scores are unchanged.

## Reviewer output for the first snapshot

**CHANGES REQUESTED**

The overall design, D56 converter-version extractions (`src/rememberstack/adapters/converters/mistral_ocr.py`, `src/rememberstack/adapters/converters/image_ocr_description.py`), answer/judge prompt byte preservation, and offline run isolation are sound. There are two blocking issues in source-image error classification/handling and accounting/cache-state validation, plus one minor planning-mode validation gap.

---

### 1. [Medium / Blocking] `HttpImageFetcher.fetch` misclassifies image-format errors as `network_error` and leaves Pillow `SyntaxError` / `DecompressionBombError` / `EOFError` / `struct.error` uncaught

* **Location**: `benchmarks/locomo/image_preparation.py` — `HttpImageFetcher.fetch` (lines 115–196) and `src/tests/benchmarks/test_locomo_images.py` — `test_download_checks_redirects_byte_limit_and_static_image_format` (lines 480–504)
* **Issue**:
  1. `with Image.open(io.BytesIO(content)) as image:` runs inside the outer `try ... except (httpx.HTTPError, OSError, ValueError) as error:` block. When a source URL returns HTTP 200 with non-image bytes (such as an HTML soft-404 page or `b"not an image"` in `test_download_checks_redirects_byte_limit_and_static_image_format(kind="format")`), `PIL.UnidentifiedImageError` (a subclass of `OSError`) is caught by the outer handler and recorded as `ImageUnavailable(kind="network_error", detail="UnidentifiedImageError")` instead of `kind="unsupported_image"`.
  2. On corrupted PNG chunks, truncated streams, or decompression bombs, Pillow's `Image.open()` and `PngImageFile.verify()` raise `SyntaxError` (e.g. `SyntaxError("broken PNG file ...")`), `Image.DecompressionBombError`, `EOFError`, or `struct.error`. None of these inherit from `(httpx.HTTPError, OSError, ValueError)`, so they escape `HttpImageFetcher.fetch` uncaught and abort `prepare-images` even when `--allow-unavailable` is explicitly set.
  3. Similarly, if `target` has a malformed port, accessing `parsed.port` raises `ValueError`, which is caught by the outer handler and classified as `kind="network_error"` instead of `kind="invalid_url"`.
* **Minimal Fix**:
  In `HttpImageFetcher.fetch`:
  - Catch `ValueError` around `urlsplit(target)` / `parsed.port` and raise `SourceUnavailable(reason=ImageUnavailable(kind="invalid_url", detail="unsupported source URL"))`.
  - Wrap the `with Image.open(io.BytesIO(content)) as image:` block in its own `try ... except Exception as error:` (re-raising `SourceUnavailable` first) that raises:
    ```python
    except SourceUnavailable:
        raise
    except Exception as error:
        raise SourceUnavailable(
            reason=ImageUnavailable(
                kind="unsupported_image", detail=type(error).__name__
            )
        ) from error
    ```
  - In `test_download_checks_redirects_byte_limit_and_static_image_format`, assert the expected `exc_info.value.reason.kind` (`"invalid_url"` for `redirect_private`, `"unsupported_image"` for `large` and `format`).

---

### 2. [Medium / Blocking] Cache-hit reading lookup and offline bundle validation do not verify `PreparationState` accounting completeness or `"inflight"` status

* **Location**:
  - `benchmarks/locomo/image_preparation.py` — `_immutable_bytes` (lines 284–296), `_ImageCache.read_cached` (lines 388–399), `_ImageCache.convert` (lines 475–512)
  - `benchmarks/locomo/images.py` — `load_image_inputs` (lines 249–302)
* **Issue**:
  1. In `_ImageCache.convert`, `_immutable_bytes(path=self.reading_path(...), ...)` writes `converted/<sha256>/<identity>.json` *before* `self.finish(ordinal=ordinal, error=None)` transitions the attempt in `preparation-state.json` from `"inflight"` to `"complete"`. Additionally, `_immutable_bytes` writes directly to `path.open("xb")` instead of temp-file + `os.replace` (unlike `_write_model`), so a crash mid-write leaves a corrupt partial file at the target path.
  2. `_ImageCache.read_cached` checks only whether `converted/<sha256>/<identity>.json` exists on disk and never checks `self.state.attempts`. If a prior run crashed after writing `reading_path` but before `self.finish()` (leaving `status="inflight"`), or if `preparation-state.json` was removed/tampered with while `converted/` remains, `read_cached` returns the cached reading, bypasses `begin()`'s `"inflight"`/`"blocked"` guard, and publishes a bundle whose `accounting.json` contains an `"inflight"` attempt or lacks attempt records for the converted images.
  3. In `benchmarks/locomo/images.py`, `load_image_inputs` validates `accounting.json` only with `if not isinstance(json.loads(accounting), dict):`. An empty `{}` or an accounting snapshot with `"inflight"` attempts or missing attempt records for converted images passes offline validation.
* **Minimal Fix**:
  - Make `_immutable_bytes` write via a sibling temporary file (`tempfile.mkstemp` + `os.fsync` + `os.link`/`os.replace` or check-if-exists) so partial files cannot be published on crash, and in `_ImageCache.convert`, call `self.finish(ordinal=ordinal, error=None)` only after the reading file is durably written, while having `_ImageCache.read_cached` verify that the latest attempt in `self.state.attempts` for `f"{original_sha256}:{bytes_hash(self.profile.converter_version.encode())}"` exists and has `status == "complete"` (raising `ImagePreparationError` otherwise).
  - Move `ConversionAttempt` and `PreparationState` to `benchmarks/locomo/images.py` (re-exported in `image_preparation.py`) and validate in `load_image_inputs` that `state = PreparationState.model_validate_json(accounting)` has a completed attempt (and latest attempt `status == "complete"`) for every available image's `f"{image.original_sha256}:{bytes_hash(index.profile.converter_version.encode())}"`.

---

### 3. [Low / Non-blocking] `prepare_images_command` skips manifest, flag-bound, and output-path checks when `--execute` is omitted

* **Location**: `benchmarks/locomo/image_preparation.py` — `prepare_images_command` (lines 720–745)
* **Issue**:
  When running `prepare-images` without `--execute` (plan mode), `prepare_images_command` does not call `validate_manifest(dataset=dataset, manifest=manifest)` and returns before `prepare_image_inputs` checks `max_conversions >= 1`, `max_cost_usd > 0`, `1 <= workers <= 16`, and `not output.exists()`. Invalid bounds or an already-occupied `--output` directory pass during planning and only fail once `--execute` is added.
* **Minimal Fix**:
  Call `validate_manifest(dataset=dataset, manifest=manifest)` and run the parameter/output-existence checks in `prepare_images_command` before the `if not execute:` return.
