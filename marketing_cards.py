#!/usr/bin/env python3
"""Ateerat Visual Identity v2: generate REVIEW-ONLY product marketing cards.

Input comes exclusively from fresh publicly available ateerat.com catalog data.
The bottle image is downloaded unchanged (except white-photo-background removal
where safely detectable). Visual flourishes are illustrative, never claimed notes.
The script NEVER publishes or schedules anything.

Required files:
  assets/ateerat/Cairo-VariableFont_slnt,wght.ttf  (upload your own licensed copy)
  assets/ateerat/logo_navy.png
  assets/ateerat/logo_white.png
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import io
import json
import math
import os
import random
import re
from decimal import Decimal
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter, ImageChops, features
from marketing_drafts import load_catalog, is_store_url, format_yer

SIZE = (1080, 1350)
NAVY = '#102042'
GOLD = '#CFAD84'
WHITE = '#FFFFFF'
ROOT = Path(__file__).resolve().parent
# Simple root-file uploads work in GitHub's web UI; nested assets work too.
ASSETS = (ROOT / 'assets' / 'ateerat'
          if (ROOT / 'assets' / 'ateerat' / 'logo_navy.png').is_file()
          else ROOT)
MAX_IMAGE_BYTES = 9_000_000
Image.MAX_IMAGE_PIXELS = 30_000_000

PALETTES = {
    'amber': ('#2B1F2B', '#81452D', '#BF8550', '#102042'),
    'ocean': ('#0B2038', '#155277', '#4F8B9E', '#102042'),
    'tropical': ('#0C303E', '#237F81', '#82B9A3', '#102042'),
    'violet': ('#202946', '#666194', '#ABA1C5', '#102042'),
    'rose': ('#352638', '#975F69', '#C995A1', '#102042'),
    'classic': ('#14223A', '#2F4C69', '#798FA5', '#102042'),
}


def font_path() -> str:
    """Use the user's licensed Cairo font, never substitute another family."""
    preferred = os.environ.get('ATEERAT_CAIRO_FONT')
    paths = ([Path(preferred)] if preferred else []) + [ASSETS / 'Cairo-VariableFont_slnt,wght.ttf']
    for p in paths:
        if p.is_file():
            return str(p)
    raise FileNotFoundError(
        'Cairo font missing: upload your font to '
        'Cairo-VariableFont_slnt,wght.ttf next to marketing_cards.py '
        'or inside assets/ateerat/ (or set ATEERAT_CAIRO_FONT).'
    )


def cairo(size: int, weight: int = 600) -> ImageFont.FreeTypeFont:
    if not features.check('raqm'):
        raise RuntimeError('Pillow with libraqm is required for joined Arabic letters')
    f = ImageFont.truetype(font_path(), size, layout_engine=ImageFont.Layout.RAQM)
    try:
        f.set_variation_by_axes([max(200, min(1000, weight)), 0])
    except OSError as exc:
        raise RuntimeError('Invalid Cairo variable font: weight axis missing') from exc
    return f


def text_dir(s: str) -> str:
    return 'rtl' if re.search(r'[\u0600-\u06ff]', s) else 'ltr'


def width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font, direction=text_dir(text))
    return bbox[2] - bbox[0]


def fit_font(draw: ImageDraw.ImageDraw, text: str, maximum: int,
             target: int, minimum: int = 20, weight: int = 700) -> ImageFont.FreeTypeFont:
    for sz in range(target, minimum - 1, -1):
        f = cairo(sz, weight)
        if width(draw, text, f) <= maximum:
            return f
    raise ValueError('Text cannot fit at the safe minimum font size')


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
              max_width: int, max_lines: int = 2) -> list[str]:
    lines, current = [], ''
    for w in text.split():
        t = f'{current} {w}'.strip()
        if width(draw, t, font) <= max_width:
            current = t
        elif current:
            lines.append(current)
            current = w
        else:
            raise ValueError('Product name contains too-wide single word')
    if current:
        lines.append(current)
    if len(lines) > max_lines or any(width(draw, l, font) > max_width for l in lines):
        raise ValueError('Product name is too wide for selected font')
    return lines


def color_int(hex_color: str) -> tuple[int, int, int]:
    return tuple(int(hex_color[i:i+2], 16) for i in (1, 3, 5))


def blend(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(x*(1-t)+y*t) for x, y in zip(a, b))


def choose_palette(photo: Image.Image) -> str:
    """Select only a visual palette from the bottle/photo colors, not scent notes."""
    sample = ImageOps.contain(photo.convert('RGB'), (80, 80))
    hits = {k: 0 for k in ('amber', 'ocean', 'tropical', 'violet', 'rose')}
    pixels = sample.get_flattened_data() if hasattr(sample, 'get_flattened_data') else sample.getdata()
    for r, g, b in pixels:
        mx, mn = max(r,g,b), min(r,g,b)
        if mx < 52 or mx > 245 or mx - mn < 26:
            continue
        if r > g * 1.22 and r > b * 1.23:
            hits['amber' if g >= b*0.76 else 'rose'] += 1
        elif b > r*1.18 and b > g*1.05:
            hits['ocean'] += 1
        elif g > r*1.18 and g >= b*0.8:
            hits['tropical'] += 1
        elif b > g*1.15 and r > g*1.12:
            hits['violet'] += 1
    name = max(hits, key=hits.get)
    return name if hits[name] >= 25 else 'classic'


def _glow(base: Image.Image, center: tuple[int,int], radius: int,
          color: str, strength: int) -> None:
    layer = Image.new('RGBA', SIZE)
    d = ImageDraw.Draw(layer)
    x,y = center
    d.ellipse((x-radius,y-radius,x+radius,y+radius), fill=(*color_int(color),strength))
    layer = layer.filter(ImageFilter.GaussianBlur(radius // 3))
    base.alpha_composite(layer)


def create_background(mood: str, seed: str) -> Image.Image:
    """Illustrative abstract art; no invented real scent ingredients."""
    top, mid, edge, dark = [color_int(c) for c in PALETTES[mood]]
    img = Image.new('RGBA', SIZE)
    d = ImageDraw.Draw(img)
    for y in range(SIZE[1]):
        if y < 610:
            c = blend(top, mid, y/610)
        elif y < 1060:
            c = blend(mid, edge, (y-610)/450*0.48)
        else:
            c = blend(edge, dark, (y-1060)/290)
        d.line((0,y,1080,y), fill=(*c,255))
    rng = random.Random(int(hashlib.sha256(seed.encode('utf8')).hexdigest()[:15],16))
    rays = Image.new('RGBA', SIZE)
    rd = ImageDraw.Draw(rays)
    for k in range(24):
        a = 2 * math.pi * (k / 24) + 0.05
        da = 0.055 + k % 4 * 0.018
        x,y=540,720
        rad=1150
        poly=[(x,y),(x + rad*math.cos(a-da),y+rad*math.sin(a-da)),
              (x + rad*math.cos(a+da),y+rad*math.sin(a+da))]
        rd.polygon(poly, fill=(255,255,255,9 if k%2==0 else 5))
    img.alpha_composite(rays)
    _glow(img,(540,695),540,'#FFFFFF',56)
    _glow(img,(760,770),360,'#FFFFFF',30)
    deco = Image.new('RGBA', SIZE)
    d=ImageDraw.Draw(deco)
    for i in range(95):
        x=rng.randint(0,1080); y=rng.randint(65,1110); s=rng.randint(2,8)
        # Sparkles are decorative; they do not suggest named perfume notes.
        d.ellipse((x-s,y-s,x+s,y+s),fill=(255,255,255,rng.randrange(12,75)))
    for i in range(16):
        x=rng.randint(160,940); y=rng.randint(420,1040)
        d.arc((x-160,y-70,x+160,y+95),rng.randint(10,150),rng.randint(155,335),
              fill=(255,255,255,60),width=rng.randint(2,5))
    # Right-side stepped diamonds, recognizable in older Ateerat posts.
    for col in range(5):
        for row in range(11):
            x = 955 + col*16 + ((row+col)%2)*4
            y = 255 + row*44 + col*27
            sz = 6 + (row%4)*2
            alpha = max(24, 135 - abs(row-5)*15)
            d.polygon([(x,y-sz),(x+sz,y),(x,y+sz),(x-sz,y)],
                      fill=(255,255,255,alpha))
    img.alpha_composite(deco)
    return img


def _subject(photo: Image.Image) -> tuple[Image.Image, bool]:
    """Optional near-white background removal; otherwise preserve rectangular photo."""
    p = ImageOps.exif_transpose(photo).convert('RGBA')
    rgb = p.convert('RGB')
    w,h=rgb.size
    corners = [rgb.getpixel(pt) for pt in [(3,3),(w-4,3),(3,h-4),(w-4,h-4)]]
    light = sum(min(v)>240 and max(v)-min(v)<15 for v in corners)
    if light < 3 or w<50 or h<50:
        return p,False
    # Erase only sufficiently near-white backgrounds, not a non-white bottle.
    dist = ImageChops.difference(rgb, Image.new('RGB',rgb.size,'white')).convert('L')
    # PIL native LUT - smooth antialiasing at white edges.
    alpha = dist.point(lambda z: max(0,min(255,int((z-13)*255/42))))
    box = alpha.point(lambda z: 255 if z > 30 else 0).getbbox()
    if not box:
        return p,False
    margin=8
    box=(max(0,box[0]-margin),max(0,box[1]-margin),min(w,box[2]+margin),min(h,box[3]+margin))
    p.putalpha(alpha)
    return p.crop(box),True


def _place_bottle(canvas: Image.Image, photo: Image.Image) -> None:
    subject,cutout=_subject(photo)
    maxsize=(700,750) if cutout else (660,690)
    obj=ImageOps.contain(subject,maxsize,Image.Resampling.LANCZOS)
    # Height is matched to upper-third title and fixed footer safe areas.
    x=(1080-obj.width)//2
    y=336 + (760-obj.height)//2
    shadow=Image.new('RGBA',SIZE)
    sd=ImageDraw.Draw(shadow)
    sd.ellipse((280,976,800,1056),fill=(10,15,30,135))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(32)))
    if cutout:
        bottle_shadow = Image.new('RGBA',SIZE)
        b=Image.new('RGBA',obj.size,(8,15,26,0))
        b.putalpha(obj.getchannel('A').point(lambda z: int(z*.45)))
        bottle_shadow.alpha_composite(b,(x+14,y+20))
        canvas.alpha_composite(bottle_shadow.filter(ImageFilter.GaussianBlur(22)))
        canvas.alpha_composite(obj,(x,y))
    else:
        # Keep real photo intact if its background cannot be isolated safely.
        frame=Image.new('RGBA',SIZE)
        fd=ImageDraw.Draw(frame)
        fd.rounded_rectangle((x-24,y-24,x+obj.width+24,y+obj.height+24),
                             radius=33,fill=(248,247,244,235),outline=(255,255,255,125),width=2)
        canvas.alpha_composite(frame)
        canvas.alpha_composite(obj,(x,y))


def _logo(canvas: Image.Image, color: str, rect: tuple[int,int,int,int]) -> None:
    path=ASSETS / ('logo_navy.png' if color=='navy' else 'logo_white.png')
    if not path.is_file():
        raise FileNotFoundError(f'Logo missing: {path}. Upload the official brand logo.')
    with Image.open(path) as original:
        logo=ImageOps.contain(original.convert('RGBA'), (rect[2]-rect[0],rect[3]-rect[1]),Image.Resampling.LANCZOS)
    px=rect[0]+(rect[2]-rect[0]-logo.width)//2
    py=rect[1]+(rect[3]-rect[1]-logo.height)//2
    canvas.alpha_composite(logo,(px,py))


def _footer(canvas: Image.Image) -> None:
    layer=Image.new('RGBA',SIZE)
    d=ImageDraw.Draw(layer)
    # Frosted dark footer - brand has consistent placement across palettes.
    d.rounded_rectangle((-30,1061,1110,1360),radius=38,fill=(10,25,49,218))
    d.line((55,1062,1025,1062),fill=(255,255,255,45),width=2)
    canvas.alpha_composite(layer)
    d=ImageDraw.Draw(canvas)
    d.line((116,1220,462,1220),fill='#FFFFFF',width=4)
    d.line((618,1220,964,1220),fill='#FFFFFF',width=4)
    for x in (116,964):
        d.polygon([(x-16,1220),(x,1208),(x+16,1220),(x,1232)],fill=WHITE)
    label_font=cairo(37,600)
    d.text((288,1161),'اطلب الآن',font=label_font,fill=WHITE,anchor='mm',direction='rtl')
    d.text((804,1161),'تسوق عبر الموقع',font=cairo(31,600),fill=WHITE,anchor='mm',direction='rtl')
    d.ellipse((466,1146,614,1294),fill='#FFFFFF')
    _logo(canvas,'navy',(477,1181,603,1252))
    d.text((540,1312),'ATEERAT.COM',fill='#CFAD84',font=cairo(21,600),anchor='mm',direction='ltr')


def fetch_original_photo(url: str, session: requests.Session | None = None) -> Image.Image:
    """Only HTTPS photos from ateerat.com, enforcing redirect/content size checks."""
    if not is_store_url(url):
        raise ValueError('Original photo must be from https://www.ateerat.com')
    s=session or requests.Session()
    with s.get(url,timeout=(12,30),stream=True,allow_redirects=True,
               headers={'User-Agent':'AteeratPublicCatalogAssets/2.0'}) as resp:
        resp.raise_for_status()
        if not is_store_url(resp.url):
            raise ValueError('Image redirected outside the official store')
        if not resp.headers.get('content-type','').lower().startswith('image/'):
            raise ValueError('Product image URL did not return an image')
        raw=bytearray()
        for part in resp.iter_content(chunk_size=65536):
            raw.extend(part)
            if len(raw)>MAX_IMAGE_BYTES:
                raise ValueError('Product photo exceeds permitted size')
    with Image.open(io.BytesIO(raw)) as im:
        if im.format not in ('JPEG','PNG','WEBP'):
            raise ValueError('Unsupported image format')
        result=ImageOps.exif_transpose(im)
        result.load()
        return result.convert('RGBA')


def render_card(product_photo: Image.Image, product_name: str, price: object,
                save_to: Path) -> None:
    """Render exact store product image, current price and locked Ateerat identity."""
    name=re.sub(r'\s+',' ',product_name).strip()
    formatted=format_yer(price)
    if not name or not formatted:
        raise ValueError('Name and positive price are required')
    mood=choose_palette(product_photo)
    canvas=create_background(mood,name)
    _place_bottle(canvas, product_photo)
    d=ImageDraw.Draw(canvas)
    # Small upper-left official white logo on dark background.
    _logo(canvas,'white',(115,73,287,165))
    d=ImageDraw.Draw(canvas)
    d.text((116,171),'تشكيلة عطيرات',font=cairo(25,500),fill='#F3E6D7',
           anchor='lt',direction='rtl')
    lines=None; selected=None
    for sz in range(58,28,-1):
        f=cairo(sz,700)
        try:
            candidate=wrap_text(d,name,f,780,2)
        except ValueError:
            continue
        lines,selected=candidate,f; break
    if lines is None:
        raise ValueError('Product name too long for layout')
    for i,line in enumerate(lines):
        d.text((115,231 + i*67),line,font=selected,fill=WHITE,
               anchor='lt',direction=text_dir(line))
    underline_y=231+len(lines)*67+10
    d.line((115,underline_y,360,underline_y),fill=GOLD,width=4)
    d.ellipse((216,underline_y-6,226,underline_y+6),fill=GOLD)
    # Verified price only, no invented promotion or delivery claim.
    price_font=fit_font(d,formatted,440,37,minimum=24,weight=800)
    bw=min(500,max(270,width(d,formatted,price_font)+64))
    bx=1080-bw-72
    by=918
    plate=Image.new('RGBA',SIZE)
    pd=ImageDraw.Draw(plate)
    pd.rounded_rectangle((bx,by,bx+bw,by+74),radius=27,fill=(13,30,57,228),
                         outline=(240,211,174,225),width=2)
    canvas.alpha_composite(plate)
    d=ImageDraw.Draw(canvas)
    d.text((bx+bw/2,by+36),formatted,font=price_font,anchor='mm',fill=WHITE,direction='rtl')
    _footer(canvas)
    save_to.parent.mkdir(parents=True,exist_ok=True)
    canvas.convert('RGB').save(save_to,format='PNG',optimize=True)


def require_valid_draft(draft: dict, catalog_by_url: dict) -> dict:
    url=draft.get('product_url')
    if not is_store_url(url,product=True) or url not in catalog_by_url:
        raise ValueError('Product not present in the fresh official catalog')
    product=catalog_by_url[url]
    if str(Decimal(str(draft.get('price')))) != str(Decimal(str(product.get('price')))):
        raise ValueError('Price changed; regenerate marketing draft')
    if draft.get('image_url') != product.get('image_url'):
        raise ValueError('Product image changed; regenerate marketing draft')
    if draft.get('currency')!='YER' or draft.get('status')!='DRAFT_REVIEW':
        raise ValueError('Only official YER review drafts are allowed')
    if draft.get('auto_publish') is not False:
        raise ValueError('Auto-publishing prohibited in visual design step')
    if draft.get('product') and re.sub(r'\s+',' ',draft['product']).strip() != re.sub(r'\s+',' ',product['name']).strip():
        raise ValueError('Product name changed; regenerate draft')
    if product.get('availability')!='InStock' or product.get('ready_for_automated_ad') is not True:
        raise ValueError('Product no longer listed as available')
    return product


def build_cards(catalog: Path, drafts_file: Path, out: Path,
                fetch_image=fetch_original_photo) -> dict:
    valid, rejected=load_catalog(catalog)
    lookup={p['url']:p for p in valid}
    obj=json.loads(drafts_file.read_text(encoding='utf8'))
    if obj.get('source')!='https://www.ateerat.com/' or obj.get('publish_enabled') is not False:
        raise ValueError('Drafts must come from official store and be review-only')
    drafts=obj.get('drafts',[])
    if not drafts:
        raise ValueError('No marketing drafts')
    # Fail early if assets missing: never silently fall back to another identity.
    font_path()
    for name in ('logo_navy.png','logo_white.png'):
        if not (ASSETS/name).is_file():
            raise FileNotFoundError(f'Missing official logo: {name} (repository root or assets/ateerat)')
    out.mkdir(parents=True,exist_ok=True)
    items=[]
    for i,draft in enumerate(drafts,start=1):
        product=require_valid_draft(draft,lookup)
        filename=f"{i:02d}-{draft['date']}.png"
        photo=fetch_image(product['image_url'])
        render_card(photo,product['name'],product['price'],out/filename)
        items.append({'date':draft['date'],'product':product['name'],
                     'price':product['price'],'currency':'YER',
                     'caption_ar':draft.get('caption_ar',''),
                     'image_file':'cards/'+filename,'product_url':product['url'],
                     'status':'DRAFT_REVIEW','auto_publish':False,
                     'identity_version':'ATEERAT-V2'})
    manifest={'source':'https://www.ateerat.com/',
              'generated_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
              'count':len(items),'publish_enabled':False,
              'design_version':'ATEERAT-V2','designs':items,'rejected_products':rejected}
    (out.parent/'cards_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
    articles=[]
    for item in items:
        src=html.escape(item['image_file'],quote=True)
        title=html.escape(item['product'],quote=True)
        caption=html.escape(item['caption_ar'],quote=True)
        articles.append(f'<article><img loading="lazy" src="{src}" alt="{title}"><h2>{title}</h2><pre>{caption}</pre></article>')
    page=('<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8">'
          '<meta name="viewport" content="width=device-width,initial-scale=1">'
          '<title>مراجعة تصاميم عطيرات</title>'
          '<style>body{background:#102042;color:white;font-family:system-ui;padding:24px}'
          'main{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:22px}'
          'article{background:#213756;padding:16px;border-radius:16px}img{width:100%;border-radius:10px}'
          'pre{white-space:pre-wrap;line-height:1.65;font-family:inherit}</style>'
          '<h1>عطيرات — تصاميم للمراجعة فقط، غير منشورة</h1><main>'
          +''.join(articles)+'</main></html>')
    (out.parent/'preview.html').write_text(page,encoding='utf8')
    print(f'Generated {len(items)} brand-consistent review-only Ateerat cards. Nothing published.')
    return manifest


def main() -> None:
    p=argparse.ArgumentParser(description='Ateerat Cairo+logo branded ad renderer')
    p.add_argument('--catalog',default='data/catalog.json')
    p.add_argument('--drafts',default='marketing/drafts.json')
    p.add_argument('--out',default='marketing/cards')
    a=p.parse_args()
    build_cards(Path(a.catalog),Path(a.drafts),Path(a.out))


if __name__=='__main__':
    main()
