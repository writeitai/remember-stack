import concurrent.futures
import json
import os
from pathlib import Path
import sys
import time

from dotenv import load_dotenv
load_dotenv('.env')

from rememberstack.adapters.converters.image_ocr_description import ImageOcrDescriptionConverter
from rememberstack.adapters.converters.image_ocr_description import ImageDescriptionSettings
from rememberstack.adapters.converters.mistral_ocr import MistralOcrConverter
from rememberstack.adapters.converters.mistral_ocr import MistralOcrSettings

def convert_turn(dia_id, item, cache_dir, converted_dir, ocr_settings, desc_settings):
    clean_id = item['clean_id']
    target_json = converted_dir / f'{clean_id}.json'
    target_md = converted_dir / f'{clean_id}.md'

    if target_json.exists() and target_md.exists():
        return dia_id, 'already_converted', None

    if item.get('status') != 'cached':
        return dia_id, 'fallback_blip', None

    filename = item.get('filename')
    img_path = cache_dir / filename
    if not img_path.exists():
        return dia_id, 'missing_file', f'File {img_path} not found'

    content = img_path.read_bytes()
    mime = item.get('mime', 'image/jpeg')

    converter = ImageOcrDescriptionConverter(
        ocr=MistralOcrConverter(settings=ocr_settings),
        description_settings=desc_settings
    )

    start_t = time.time()
    try:
        res = converter.convert(content=content, mime=mime)
        elapsed = time.time() - start_t
        target_md.write_text(res.document_md, encoding='utf-8')
        record = {
            'dia_id': dia_id,
            'clean_id': clean_id,
            'elapsed_s': elapsed,
            'document_md': res.document_md,
            'warnings': list(res.warnings),
            'components': [c.model_dump() for c in res.manifest.components]
        }
        target_json.write_text(json.dumps(record, indent=2), encoding='utf-8')
        return dia_id, 'success', f'{elapsed:.1f}s'
    except Exception as e:
        return dia_id, 'error', str(e)

def main():
    sample_id = 'conv-42'
    cache_dir = Path(f'benchmarks/locomo/cache/{sample_id}')
    manifest_path = cache_dir / 'manifest.json'

    if not manifest_path.exists():
        print(f'Manifest {manifest_path} does not exist!')
        sys.exit(1)

    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)

    converted_dir = cache_dir / 'converted'
    converted_dir.mkdir(parents=True, exist_ok=True)

    ocr_settings = MistralOcrSettings()
    desc_settings = ImageDescriptionSettings()

    print(f'Starting batch conversion of {len(manifest)} image turns for {sample_id}...')
    print(f'OCR: {ocr_settings.model} @ {ocr_settings.base_url}')
    print(f'Vision: {desc_settings.model} @ {desc_settings.base_url}')

    cached_items = [(dia_id, item) for dia_id, item in manifest.items() if item.get('status') == 'cached']
    print(f'Cached images to process: {len(cached_items)}')

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        future_map = {
            executor.submit(convert_turn, dia_id, item, cache_dir, converted_dir, ocr_settings, desc_settings): dia_id
            for dia_id, item in manifest.items()
        }
        for future in concurrent.futures.as_completed(future_map):
            dia_id = future_map[future]
            try:
                d_id, status, details = future.result()
                results[d_id] = (status, details)
                print(f'[{len(results)}/{len(manifest)}] {dia_id}: {status} ({details})', flush=True)
            except Exception as e:
                print(f'{dia_id}: unhandled exception {e}', flush=True)

    successes = sum(1 for s, _ in results.values() if s in ('success', 'already_converted'))
    fallbacks = sum(1 for s, _ in results.values() if s == 'fallback_blip')
    errors = sum(1 for s, _ in results.values() if s in ('error', 'missing_file'))

    print(f'\nBatch conversion completed!')
    print(f'Success / Converted: {successes}')
    print(f'Fallback BLIP: {fallbacks}')
    print(f'Errors: {errors}')

if __name__ == '__main__':
    main()
