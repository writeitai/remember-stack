"""Download, cache, and convert LoCoMo multimodal images (Mistral OCR + GPT-6 Luna)."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import io
import json
from pathlib import Path
import sys
import time
from typing import Any
import urllib.request

from dotenv import load_dotenv
from google.cloud import storage
from PIL import Image

load_dotenv(".env")

from rememberstack.adapters.converters.image_ocr_description import (
    ImageDescriptionSettings,
    ImageOcrDescriptionConverter,
)
from rememberstack.adapters.converters.mistral_ocr import (
    MistralOcrConverter,
    MistralOcrSettings,
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
GCS_BUCKET_NAME = "remember-stack-locomo-backups"
GCS_PREFIX = "datasets/locomo/images"


def _upload_if_missing(
    *, bucket: storage.Bucket | None, gcs_path: str, data: bytes, content_type: str
) -> None:
    """Upload bytes to create-only GCS bucket if the object does not already exist."""
    if bucket is None:
        return
    try:
        blob = bucket.blob(gcs_path)
        if not blob.exists():
            blob.upload_from_string(data, content_type=content_type)
    except Exception as exc:
        print(f"  [GCS warning] {gcs_path}: {exc}", flush=True)


def mirror_sample_images(
    *,
    sample: dict[str, Any],
    cache_dir: Path,
    bucket: storage.Bucket | None,
) -> dict[str, Any]:
    """Download and cache all image turns for one sample, returning its manifest."""
    sample_id = sample["sample_id"]
    sample_cache = cache_dir / sample_id
    sample_cache.mkdir(parents=True, exist_ok=True)
    manifest_path = sample_cache / "manifest.json"

    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}

    conversation = sample.get("conversation", {})
    image_turns: list[dict[str, Any]] = []
    for turns in conversation.values():
        if isinstance(turns, list):
            for turn in turns:
                urls = turn.get("img_url")
                if urls and isinstance(urls, list):
                    image_turns.append(turn)

    def _fetch_one(turn: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        dia_id = str(turn["dia_id"])
        clean_id = dia_id.replace(":", "_")
        urls = turn.get("img_url", [])
        blip = turn.get("blip_caption")

        existing = [
            p
            for p in sample_cache.glob(f"{clean_id}.*")
            if p.name != "manifest.json" and p.is_file()
        ]
        content_bytes: bytes | None = None
        ext = "jpg"
        mime = "image/jpeg"

        if existing:
            cached_file = existing[0]
            content_bytes = cached_file.read_bytes()
            ext = cached_file.suffix.lstrip(".").lower()
            mime = "image/png" if ext == "png" else "image/jpeg"
        else:
            for url in urls:
                try:
                    req = urllib.request.Request(
                        url, headers={"User-Agent": USER_AGENT}
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        raw = resp.read()
                    with Image.open(io.BytesIO(raw)) as img:
                        fmt = (img.format or "JPEG").upper()
                        if fmt not in ("JPEG", "PNG"):
                            continue
                        ext = "png" if fmt == "PNG" else "jpg"
                        mime = "image/png" if fmt == "PNG" else "image/jpeg"
                    content_bytes = raw
                    (sample_cache / f"{clean_id}.{ext}").write_bytes(content_bytes)
                    break
                except Exception:
                    continue

        if content_bytes is not None:
            sha256 = hashlib.sha256(content_bytes).hexdigest()
            gcs_path = f"{GCS_PREFIX}/{sample_id}/{clean_id}.{ext}"
            _upload_if_missing(
                bucket=bucket,
                gcs_path=gcs_path,
                data=content_bytes,
                content_type=mime,
            )
            return dia_id, {
                "dia_id": dia_id,
                "clean_id": clean_id,
                "status": "cached",
                "filename": f"{clean_id}.{ext}",
                "mime": mime,
                "byte_size": len(content_bytes),
                "sha256": sha256,
                "gcs_uri": f"gs://{GCS_BUCKET_NAME}/{gcs_path}",
                "blip_caption": blip,
            }
        return dia_id, {
            "dia_id": dia_id,
            "clean_id": clean_id,
            "status": "fallback_blip",
            "blip_caption": blip,
            "urls": urls,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        for dia_id, entry in pool.map(_fetch_one, image_turns):
            manifest[dia_id] = entry

    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    _upload_if_missing(
        bucket=bucket,
        gcs_path=f"{GCS_PREFIX}/{sample_id}/manifest.json",
        data=manifest_bytes,
        content_type="application/json",
    )
    cached_count = sum(1 for v in manifest.values() if v.get("status") == "cached")
    print(
        f"[{sample_id}] Mirrored images: {cached_count}/{len(image_turns)} cached "
        f"({len(image_turns) - cached_count} fallback_blip)",
        flush=True,
    )
    return manifest


def convert_turn(
    dia_id: str,
    item: dict[str, Any],
    sample_id: str,
    cache_dir: Path,
    converted_dir: Path,
    ocr_settings: MistralOcrSettings,
    desc_settings: ImageDescriptionSettings,
    bucket: storage.Bucket | None,
) -> tuple[str, str, str | None]:
    """Run Mistral OCR + GPT-6 Luna vision description on one cached image turn."""
    clean_id = item["clean_id"]
    target_json = converted_dir / f"{clean_id}.json"
    target_md = converted_dir / f"{clean_id}.md"

    if target_json.exists() and target_md.exists():
        _upload_if_missing(
            bucket=bucket,
            gcs_path=f"{GCS_PREFIX}/{sample_id}/converted/{clean_id}.md",
            data=target_md.read_bytes(),
            content_type="text/markdown",
        )
        _upload_if_missing(
            bucket=bucket,
            gcs_path=f"{GCS_PREFIX}/{sample_id}/converted/{clean_id}.json",
            data=target_json.read_bytes(),
            content_type="application/json",
        )
        return dia_id, "already_converted", None

    if item.get("status") != "cached":
        return dia_id, "fallback_blip", None

    filename = item.get("filename")
    img_path = cache_dir / filename
    if not img_path.exists():
        return dia_id, "missing_file", f"File {img_path} not found"

    content = img_path.read_bytes()
    mime = item.get("mime", "image/jpeg")

    converter = ImageOcrDescriptionConverter(
        ocr=MistralOcrConverter(settings=ocr_settings),
        description_settings=desc_settings,
    )

    start_t = time.time()
    for attempt in range(1, 4):
        try:
            res = converter.convert(content=content, mime=mime)
            elapsed = time.time() - start_t
            md_bytes = res.document_md.encode("utf-8")
            target_md.write_bytes(md_bytes)
            record = {
                "dia_id": dia_id,
                "clean_id": clean_id,
                "elapsed_s": elapsed,
                "document_md": res.document_md,
                "warnings": list(res.warnings),
                "components": [c.model_dump() for c in res.manifest.components],
            }
            json_bytes = json.dumps(record, indent=2).encode("utf-8")
            target_json.write_bytes(json_bytes)
            _upload_if_missing(
                bucket=bucket,
                gcs_path=f"{GCS_PREFIX}/{sample_id}/converted/{clean_id}.md",
                data=md_bytes,
                content_type="text/markdown",
            )
            _upload_if_missing(
                bucket=bucket,
                gcs_path=f"{GCS_PREFIX}/{sample_id}/converted/{clean_id}.json",
                data=json_bytes,
                content_type="application/json",
            )
            return dia_id, "success", f"{elapsed:.1f}s"
        except Exception as exc:
            if attempt == 3:
                return dia_id, "error", str(exc)
            time.sleep(2.0 * attempt)
    return dia_id, "error", "exhausted retries"


def main() -> None:
    """CLI entrypoint for mirroring and converting LoCoMo sample images."""
    parser = argparse.ArgumentParser(
        description="Mirror and convert LoCoMo images via Mistral OCR + GPT-6 Luna"
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("/opt/locomo/locomo10.json"),
        help="Path to locomo10.json",
    )
    parser.add_argument(
        "--samples",
        type=str,
        default="all",
        help="Comma-separated sample IDs or 'all'",
    )
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path("benchmarks/locomo/cache"),
        help="Root directory for per-sample image caches",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Concurrent OCR+Vision conversion workers",
    )
    parser.add_argument(
        "--skip-gcs",
        action="store_true",
        help="Skip uploading cached/converted files to GCS",
    )
    args = parser.parse_args()

    raw_data = json.loads(args.dataset.read_text(encoding="utf-8"))
    requested = (
        [s["sample_id"] for s in raw_data]
        if args.samples == "all"
        else [s.strip() for s in args.samples.split(",") if s.strip()]
    )
    sample_map = {s["sample_id"]: s for s in raw_data}

    bucket: storage.Bucket | None = None
    if not args.skip_gcs:
        try:
            client = storage.Client(project="remember-stack")
            bucket = client.bucket(GCS_BUCKET_NAME)
        except Exception as exc:
            print(f"Warning: GCS client unavailable ({exc})", file=sys.stderr)

    ocr_settings = MistralOcrSettings()
    desc_settings = ImageDescriptionSettings()
    print(f"OCR: {ocr_settings.model} @ {ocr_settings.base_url}", flush=True)
    print(f"Vision: {desc_settings.model} @ {desc_settings.base_url}", flush=True)

    total_errors = 0
    for sample_id in requested:
        sample = sample_map.get(sample_id)
        if sample is None:
            print(f"Unknown sample {sample_id}", file=sys.stderr)
            sys.exit(1)
        manifest = mirror_sample_images(
            sample=sample, cache_dir=args.cache_root, bucket=bucket
        )
        sample_cache = args.cache_root / sample_id
        converted_dir = sample_cache / "converted"
        converted_dir.mkdir(parents=True, exist_ok=True)

        results: dict[str, tuple[str, str | None]] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            future_map = {
                pool.submit(
                    convert_turn,
                    dia_id,
                    item,
                    sample_id,
                    sample_cache,
                    converted_dir,
                    ocr_settings,
                    desc_settings,
                    bucket,
                ): dia_id
                for dia_id, item in manifest.items()
            }
            for future in concurrent.futures.as_completed(future_map):
                dia_id = future_map[future]
                d_id, status, details = future.result()
                results[d_id] = (status, details)
                if status not in ("already_converted", "fallback_blip"):
                    print(
                        f"  [{sample_id} {len(results)}/{len(manifest)}] "
                        f"{dia_id}: {status} ({details})",
                        flush=True,
                    )

        successes = sum(
            1 for s, _ in results.values() if s in ("success", "already_converted")
        )
        fallbacks = sum(1 for s, _ in results.values() if s == "fallback_blip")
        errors = sum(
            1 for s, _ in results.values() if s in ("error", "missing_file")
        )
        total_errors += errors
        print(
            f"[{sample_id}] Done: converted={successes}, "
            f"fallback_blip={fallbacks}, errors={errors}",
            flush=True,
        )

    if total_errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
