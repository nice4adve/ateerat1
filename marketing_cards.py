#!/usr/bin/env python3
"""Create review-only Arabic product-image cards from ateerat.com public catalog.

This intentionally DOES NOT generate synthetic product photos or publish anywhere.
Requires the original image to be readable and matching fresh catalog data.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import io
import json
import os
import re
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps, features

from marketing_drafts import load_catalog, is_store_url, format_yer

TZ_NAME = 'Asia/Aden'
SIZE = (1080, 1350)
BG = '#111827'
ACCENT = '#D4AF73'
PAPER = '#F9F7F2'
WHITE = '#FFFFFF'
MUTED = '#D7D7D6'
MAX_IMAGE_BYTES = 9_000_000
Image.MAX_IMAGE_PIXELS = 30_000_000


def font_path(*, bold: bool = False, latin: bool = False) -> str:
    """Find an Arabic-capable font provided on Ubuntu or on local machines."""
    options = (
        ['/usr/share/fonts/truetype/noto/NotoKufiArabic-Bold.ttf',
         '/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf'] if bold else
        ['/usr/share/fonts/truetype/noto/NotoKufiArabic-Regular.ttf',
         '/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf']
    ) + ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold
         else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
    # Noto Kufi Arabic may omit Latin letters and digits: use DejaVu for mixed text.
    if latin:
        options = ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold
                   else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
    custom = os.getenv('ATEERAT_FONT_BOLD' if bold else 'ATEERAT_FONT')
    if custom:
        options = [custom] + options
    for candidate in options:
        if Path(candidate).is_file():
            return candidate
    raise FileNotFoundError('Missing Arabic font; install fonts-dejavu-core on Ubuntu.')


def get_font(size: int, *, bold: bool = False, latin: bool = False) -> ImageFont.FreeTypeFont:
    if not features.check('raqm'):
        raise RuntimeError('This Pillow build needs libraqm for correctly joined Arabic text.')
    return ImageFont.truetype(font_path(bold=bold, latin=latin), size,
                              layout_engine=ImageFont.Layout.RAQM)


def text_direction(text: str) -> str:
    return 'rtl' if re.search(r'[\u0600-\u06FF]', text) else 'ltr'


def text_width(draw: ImageDraw.ImageDraw, value: str, font: ImageFont.FreeTypeFont) -> int:
    box = draw.textbbox((0, 0), value, font=font, direction=text_direction(value))
    return box[2] - box[0]


def line_wrap(draw: ImageDraw.ImageDraw, value: str, font: ImageFont.FreeTypeFont,
              max_width: int, max_lines: int = 2) -> list[str]:
    words = value.split()
    lines: list[str] = []
    current = ''
    for word in words:
        trial = (current + ' ' + word).strip()
        if text_width(draw, trial, font) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
                current = word
            else:
                # Avoid drawing a very wide word outside the canvas.
                raise ValueError(f'Text word too wide: {word[:40]}')
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        raise ValueError('Product title needs a smaller font to fit')
    return lines


def fetch_original_photo(url: str, session: requests.Session | None = None) -> Image.Image:
    """Allow only the shop's public HTTPS image files, including after redirects."""
    if not is_store_url(url):
        raise ValueError('Original image must be hosted on ateerat.com using HTTPS')
    s = session or requests.Session()
    with s.get(url, timeout=(12, 30), stream=True, allow_redirects=True,
               headers={'User-Agent': 'AteeratPublicCatalogAssets/1.0'}) as resp:
        resp.raise_for_status()
        if not is_store_url(resp.url):
            raise ValueError('Image redirected outside ateerat.com')
        content_type = resp.headers.get('content-type', '').lower()
        if not content_type.startswith('image/'):
            raise ValueError('Original photo URL did not return an image')
        buf = bytearray()
        for block in resp.iter_content(chunk_size=65536):
            buf.extend(block)
            if len(buf) > MAX_IMAGE_BYTES:
                raise ValueError('Original photo exceeds the safe size limit')
    with Image.open(io.BytesIO(buf)) as source:
        if source.format not in ('JPEG', 'PNG', 'WEBP'):
            raise ValueError('Photo format unsupported')
        photo = ImageOps.exif_transpose(source)
        photo.load()
        return photo.convert('RGBA')


def draw_center(draw: ImageDraw.ImageDraw, value: str, x: int, y: int,
                font: ImageFont.FreeTypeFont, fill: str) -> None:
    draw.text((x, y), value, fill=fill, font=font, anchor='mm',
              direction=text_direction(value))


def render_card(product_photo: Image.Image, product_name: str, price: object,
                save_to: Path) -> None:
    """Produce a 1080×1350 PNG with original photo, product name, current price."""
    if not product_name.strip() or not format_yer(price):
        raise ValueError('Cannot render a card without product name and valid price')
    base = Image.new('RGB', SIZE, BG)
    draw = ImageDraw.Draw(base)
    # Discreet lines, not invented perfume characteristics.
    draw.rounded_rectangle((50, 45, 1030, 180), radius=26, outline='#475366', width=2)
    draw_center(draw, 'عطيرات', 540, 94, get_font(65, bold=True), PAPER)
    draw_center(draw, 'ATEERAT.COM', 540, 149, get_font(22, latin=True), ACCENT)
    draw.rounded_rectangle((56, 220, 1024, 953), radius=45, fill=PAPER)
    # Preserve the original product image aspect ratio without a synthetic redraw.
    photo = ImageOps.contain(product_photo, (900, 668), Image.Resampling.LANCZOS)
    # Flatten alpha on original light product card.
    flattened = Image.new('RGBA', photo.size, PAPER)
    flattened.alpha_composite(photo.convert('RGBA'))
    base.paste(flattened.convert('RGB'), ((SIZE[0] - photo.width)//2,
                255 + (655 - photo.height)//2))
    draw = ImageDraw.Draw(base)
    draw_center(draw, 'متوفر في متجر عطيرات', 540, 994, get_font(27), ACCENT)
    product_name = re.sub(r'\s+', ' ', product_name).strip()
    # Shrink a long product name until it fits 2 lines.
    lines = None
    chosen_font = None
    for fs in range(53, 26, -2):
        candidate = get_font(fs, bold=True, latin=bool(re.search(r'[A-Za-z0-9]', product_name)))
        try:
            potential = line_wrap(draw, product_name, candidate, 920)
        except ValueError:
            continue
        lines, chosen_font = potential, candidate
        break
    if not lines or not chosen_font:
        raise ValueError('Product name cannot fit the card')
    first_y = 1054 if len(lines) == 2 else 1080
    for i, line in enumerate(lines):
        draw_center(draw, line, 540, first_y + i*60, chosen_font, WHITE)
    price_text = format_yer(price)
    draw_center(draw, price_text, 540, 1188, get_font(41, bold=True, latin=True), ACCENT)
    draw.rounded_rectangle((330, 1240, 750, 1310), radius=30, fill=ACCENT)
    draw_center(draw, 'تسوق من الموقع', 540, 1273, get_font(28, bold=True), BG)
    save_to.parent.mkdir(parents=True, exist_ok=True)
    base.save(save_to, format='PNG', optimize=True)


def require_valid_draft(draft: dict, catalog_by_url: dict) -> dict:
    url = draft.get('product_url')
    if not is_store_url(url, product=True) or url not in catalog_by_url:
        raise ValueError('Product missing from the current approved catalog')
    product = catalog_by_url[url]
    if str(Decimal(str(draft.get('price')))) != str(Decimal(str(product.get('price')))):
        raise ValueError('Price changed; regenerate the marketing draft')
    if draft.get('image_url') != product.get('image_url'):
        raise ValueError('Product image changed; regenerate the marketing draft')
    if draft.get('currency') != 'YER' or draft.get('status') != 'DRAFT_REVIEW':
        raise ValueError('Only YER drafts pending review can be rendered')
    if draft.get('auto_publish') is not False:
        raise ValueError('Autopublish is prohibited in stage three')
    return product


def build_cards(catalog: Path, drafts_file: Path, out: Path,
                fetch_image=fetch_original_photo) -> dict:
    valid, rejected = load_catalog(catalog)
    by_url = {p['url']: p for p in valid}
    drafts_obj = json.loads(drafts_file.read_text(encoding='utf-8'))
    if drafts_obj.get('source') != 'https://www.ateerat.com/' or drafts_obj.get('publish_enabled') is not False:
        raise ValueError('Draft source or review-only flag is not valid')
    drafts = drafts_obj.get('drafts', [])
    if not drafts:
        raise ValueError('No marketing drafts available')
    out.mkdir(parents=True, exist_ok=True)
    items = []
    for i, draft in enumerate(drafts, start=1):
        product = require_valid_draft(draft, by_url)
        filename = f"{i:02d}-{draft['date']}.png"
        path = out / filename
        photo = fetch_image(product['image_url'])
        render_card(photo, product['name'], product['price'], path)
        items.append({'date': draft['date'], 'product': product['name'],
                      'price': product['price'], 'currency': 'YER',
                      'caption_ar': draft.get('caption_ar', ''),
                      'image_file': 'cards/' + filename,
                      'product_url': product['url'],
                      'status': 'DRAFT_REVIEW', 'auto_publish': False})
    manifest = {'source': 'https://www.ateerat.com/',
                'generated_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                'count': len(items), 'publish_enabled': False,
                'designs': items, 'rejected_products': rejected}
    (out.parent / 'cards_manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    previews = []
    for item in items:
        src = html.escape(item['image_file'], quote=True)
        title = html.escape(item['product'], quote=True)
        caption = html.escape(item['caption_ar'], quote=True)
        previews.append(f'<article><img loading="lazy" src="{src}" alt="{title}">' 
                        f'<h2>{title}</h2><pre>{caption}</pre></article>')
    doc = ('<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width,initial-scale=1">'
           '<title>مراجعة منشورات عطيرات</title>'
           '<style>body{background:#111827;color:white;font-family:system-ui;padding:24px}'
           'main{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:28px}'
           'article{background:#283348;padding:16px;border-radius:15px}img{width:100%;border-radius:10px}'
           'pre{white-space:pre-wrap;font-family:inherit;line-height:1.7}</style>'
           '<h1>مسودات عطيرات — للمراجعة فقط (غير منشورة)</h1><main>'
           + ''.join(previews) + '</main></html>')
    (out.parent / 'preview.html').write_text(doc, encoding='utf-8')
    print(f'تم تجهيز {len(items)} صور حقيقية من كتالوج عطيرات. لم يتم نشر أي منشور.')
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description='Create image cards from Ateerat public catalog')
    ap.add_argument('--catalog', default='data/catalog.json')
    ap.add_argument('--drafts', default='marketing/drafts.json')
    ap.add_argument('--out', default='marketing/cards')
    args = ap.parse_args()
    build_cards(Path(args.catalog), Path(args.drafts), Path(args.out))


if __name__ == '__main__':
    main()
