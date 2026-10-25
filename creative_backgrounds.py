#!/usr/bin/env python3
"""Ateerat V3 visual director. Generate background-only concept art for reviewed ads.

No bottle, logo, price, text or brand claims are AI generated: those are composed
from the official product photo, Cairo font and official logos by marketing_cards.py.
External image API use is optional, paid, limited, and off without explicit opt-in.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path

import requests
from PIL import Image, ImageOps

from marketing_cards import choose_palette, fetch_original_photo, require_valid_draft
from marketing_drafts import load_catalog

SOURCE = 'https://www.ateerat.com/'
DEFAULT_MODEL = 'gpt-image-2.5-flare'
API_URL = 'https://api.openai.com/v1/images/generations'
SCENE_VERSION = 'ATEERAT-CINEMA-V3.1'

SCENES = (
    ('museum-light', 'monumental light sculptures, gently illuminated arches, polished obsidian, delicate haze, dramatic chiaroscuro'),
    ('liquid-glass', 'abstract translucent glass waves, elegant crystalline refractions, suspended liquid-like ribbons and controlled specular highlights'),
    ('night-gallery', 'luxury art gallery at night, illuminated bronze structures, soft cinematic spotlights, architectural shadows and atmospheric depth'),
    ('celestial', 'luminous flowing aurora-like ribbons, elegant light trails, deep velvety gradient, subtle reflections and stardust'),
    ('sculptural', 'premium studio sculpture, flowing satin-like metal, translucent mineral surfaces, dramatic grazing light and delicate floating particles'),
    ('water-abstract', 'abstract illuminated water currents and refractive glass, high-end glossy reflections, underwater studio light and subtle mist'),
    ('silk-motion', 'elegant layered silk-like shapes swirling in slow motion, radiant rim light, delicate gold dust and rich environmental depth'),
    ('geometric-luxe', 'immersive grand geometric architecture, refined shadows, sculptural columns, cinematic atmospheric spotlights and glossy floor'),
)
PALETTE_WORDS = {
    'amber': 'warm bourbon-brown, copper, champagne gold, deep caramel and midnight navy',
    'ocean': 'ocean blue, teal, icy silver, dark navy, cool radiant light',
    'tropical': 'aqua, mint, sea-green, rich emerald and pearl white',
    'violet': 'violet, lilac, midnight indigo and soft silver',
    'rose': 'muted rose, terracotta, dark berry and champagne gold',
    'classic': 'midnight navy, graphite, platinum and warm champagne accent',
}


def visual_plan(product: dict, *, cycle: str = '') -> dict:
    """Stable campaign art direction, diverse across products and renewed by cycle."""
    if not cycle:
        cycle = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m')
    identity = f"{product['url']}|{product['image_url']}|{cycle}"
    digest = hashlib.sha256(identity.encode('utf-8')).hexdigest()
    scene = SCENES[int(digest[:8], 16) % len(SCENES)]
    mood = product['palette']
    fingerprint = hashlib.sha256(f"{SCENE_VERSION}|{DEFAULT_MODEL}|{identity}|{mood}|{scene[0]}".encode()).hexdigest()[:20]
    return {'scene': scene[0], 'scene_detail': scene[1], 'palette': mood,
            'fingerprint': fingerprint, 'cycle': cycle, 'model': DEFAULT_MODEL}


def make_prompt(plan: dict) -> str:
    return f"""Create one high-end commercial fragrance advertisement BACKGROUND PLATE ONLY, vertical 4:5 composition, no foreground product.
VISUAL ART DIRECTION: {plan['scene_detail']}.
COLOR STORY: {PALETTE_WORDS[plan['palette']]}.
Think of international luxury fragrance art direction: extraordinary cinematic depth, beautiful physical lighting, complex premium materials, dramatic yet tasteful details, high craftsmanship, refined visual rhythm.
COMPOSITION LOCK:
- Preserve a visually calm CENTRAL VERTICAL CORRIDOR from ~33% to ~67% width, especially y=28-76%; a real perfume bottle will be placed there later in separate software. Keep the most striking scenery to both sides and behind that corridor.
- Keep top 25% relatively DARK, uncluttered, suitable for later typography and brand logo.
- Reserve last 22% as a clean dark navy area for a separate footer overlay, without any important props there.
- Keep the middle-right around y=65-75% readable for a future price badge.
- Rich texture, premium reflections, studio-quality realism, editorial polish, subtle volumetric light, atmospheric particles; avoid busy patterns across the bottle area.
STRICT NEGATIVE: NO perfume bottle, bottle silhouette, packaging, product, logo, brandmark, watermark, lettering, words, numbers, captions, pricing, banners, humans, recognizable brands, or text of any kind. No specific flowers/fruits/ingredients implying factual scent notes. The actual product photo, approved logo, price and Cairo text will be composited separately.
Render the background only, as a beautifully art-directed image with outstanding visual originality."""


def image_api_generate(prompt: str, *, model: str = DEFAULT_MODEL,
                       quality: str = 'medium', api_key: str) -> Image.Image:
    """One paid API call; intentionally no retries to cap spend."""
    if not api_key.strip():
        raise ValueError('Image API key is missing')
    if model not in ('gpt-image-2.5-flare', 'gpt-image-2.5-sunburst'):
        raise ValueError('Model is not approved for this workflow')
    if quality not in ('low', 'medium', 'high'):
        raise ValueError('Quality not approved for this workflow')
    payload = {'model': model, 'prompt': prompt, 'size': '1024x1536',
               'quality': quality, 'n': 1, 'output_format': 'jpeg'}
    try:
        resp = requests.post(API_URL, json=payload,
                             headers={'Authorization': f'Bearer {api_key}',
                                      'Content-Type': 'application/json'},
                             timeout=(15, 200))
        # Never print an untrusted response body (could contain unexpected data).
        if resp.status_code != 200:
            raise RuntimeError(f'Image API returned HTTP {resp.status_code}; no images generated')
        body = resp.json()
        encoded = body['data'][0]['b64_json']
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 16_000_000:
            raise ValueError('Image API returned oversized data')
        with Image.open(io.BytesIO(raw)) as im:
            im.load()
            if im.width < 700 or im.height < 900:
                raise ValueError('Generated background is too small')
            return ImageOps.exif_transpose(im).convert('RGB')
    except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
        raise RuntimeError(f'Image generation failed ({type(exc).__name__}); no publish operation') from None


def load_usable_products(catalog_file: Path, drafts_file: Path) -> list[dict]:
    valid, _ = load_catalog(catalog_file)
    by_url = {p['url']: p for p in valid}
    doc = json.loads(drafts_file.read_text(encoding='utf-8'))
    if doc.get('source') != SOURCE or doc.get('publish_enabled') is not False:
        raise ValueError('Only review-only official Ateerat drafts are accepted')
    selected = []
    for draft in doc.get('drafts', []):
        product = dict(require_valid_draft(draft, by_url))
        product['draft_date'] = draft['date']
        selected.append(product)
    if not selected:
        raise ValueError('No verified, in-stock product drafts available')
    return selected


def _valid_existing(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 6000:
        return False
    try:
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            return im.width == 1080 and im.height == 1350
    except (OSError, ValueError):
        return False


def build_backgrounds(catalog: Path, drafts: Path, out: Path,
                      *, max_new: int = 1, api_key: str = '',
                      generate=image_api_generate,
                      palette_provider=None,
                      cycle: str = '', model: str = DEFAULT_MODEL,
                      quality: str = 'medium') -> dict:
    if not 0 <= max_new <= 3:
        raise ValueError('max-new must be between 0 and 3; cost safety cap')
    if model not in ('gpt-image-2.5-flare', 'gpt-image-2.5-sunburst'):
        raise ValueError('Unsupported image model')
    out.mkdir(parents=True, exist_ok=True)
    products = load_usable_products(catalog, drafts)
    results = []
    new_count = 0
    for product in products:
        # Avoid paid vision classification. Derive mood only from existing original
        # bottle pixels (same rule as the V2 renderer).
        if palette_provider is None:
            photo = fetch_original_photo(product['image_url'])
            mood = choose_palette(photo)
        else:
            mood = palette_provider(product)
        if mood not in PALETTE_WORDS:
            mood = 'classic'
        product['palette'] = mood
        plan = visual_plan(product, cycle=cycle)
        # Use requested model in cache keys as well.
        if model != DEFAULT_MODEL:
            plan['model'] = model
            plan['fingerprint'] = hashlib.sha256((plan['fingerprint'] + '|' + model).encode()).hexdigest()[:20]
        file_name = f"{plan['fingerprint']}.jpg"
        path = out / file_name
        exists = _valid_existing(path)
        if not exists and api_key and new_count < max_new:
            prompt = make_prompt(plan)
            try:
                img = generate(prompt, model=model, quality=quality, api_key=api_key)
                fitted = ImageOps.fit(img.convert('RGB'), (1080, 1350), method=Image.Resampling.LANCZOS)
                tmp = out / (file_name + '.tmp')
                fitted.save(tmp, format='JPEG', quality=91, optimize=True)
                tmp.replace(path)
                new_count += 1
                exists = True
            except (RuntimeError, ValueError, OSError, requests.RequestException) as exc:
                # Network/model errors must not block the entire catalog sync;
                # the official-photo V2 layout remains the safe fallback.
                print(f'AI scene unavailable ({type(exc).__name__}); local visual fallback will be used.')
        if exists:
            results.append({'product_url': product['url'], 'image_url': product['image_url'],
                            'product_name': product['name'], 'background_file': 'creative_backgrounds/' + file_name,
                            'creative_style': plan['scene'], 'palette': mood, 'cycle': plan['cycle'],
                            'fingerprint': plan['fingerprint'], 'model': plan['model']})
    doc = {'source': SOURCE, 'creative_version': SCENE_VERSION,
           'publish_enabled': False, 'generated_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
           'new_ai_images_this_run': new_count, 'available_backgrounds': len(results),
           'max_new_per_run': max_new, 'backgrounds': results,
           'note': 'Images are background-only; product, price and identity are composed locally. No publishing.'}
    (out.parent/'creative_backgrounds_manifest.json').write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Creative backgrounds available: {len(results)}; newly generated paid images: {new_count}. No publishing.')
    return doc


def main() -> None:
    p = argparse.ArgumentParser(description='Optional paid AI background director; review-only')
    p.add_argument('--catalog', default='data/catalog.json')
    p.add_argument('--drafts', default='marketing/drafts.json')
    p.add_argument('--out', default='marketing/creative_backgrounds')
    p.add_argument('--max-new', default=1, type=int)
    p.add_argument('--quality', default='medium', choices=['low','medium','high'])
    p.add_argument('--model', default=DEFAULT_MODEL, choices=['gpt-image-2.5-flare','gpt-image-2.5-sunburst'])
    args = p.parse_args()
    key = os.environ.get('OPENAI_API_KEY', '')
    build_backgrounds(Path(args.catalog), Path(args.drafts), Path(args.out),
                      max_new=args.max_new, api_key=key, model=args.model, quality=args.quality)


if __name__ == '__main__':
    main()
