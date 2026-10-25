#!/usr/bin/env python3
"""Public Odoo storefront catalog extractor for ateerat.com (no Odoo credentials)."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = "https://www.ateerat.com"
START_PRODUCT = "https://www.ateerat.com/shop/perf-049-%D9%87%D9%8A%D8%B1%D8%B4-2-124"
AGENT = "AteeratCatalogReader/1.0 (+store owner catalog sync)"
VALID_HOSTS = {"ateerat.com", "www.ateerat.com"}
MAX_PRODUCTS = 200


def same_site(url: str) -> bool:
    parts = urlparse(url)
    return parts.scheme in ("http", "https") and (parts.hostname or "").lower() in VALID_HOSTS


def is_product_url(url: str) -> bool:
    if not same_site(url):
        return False
    path = urlparse(url).path.rstrip("/")
    if not path.startswith("/shop/"):
        return False
    excluded = ("/shop/category", "/shop/cart", "/shop/checkout", "/shop/confirm_order", "/shop/payment", "/shop/address")
    return not any(path == s or path.startswith(s + "/") for s in excluded)


def session_factory() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": AGENT, "Accept-Language": "ar,en;q=0.8"})
    retry = Retry(total=2, connect=2, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def fetch(s: requests.Session, url: str, *, xml: bool = False) -> str:
    if not same_site(url):
        raise ValueError(f"رفض عنوان خارج متجر عطيرات: {url}")
    r = s.get(url, timeout=(10, 20), allow_redirects=True)
    r.raise_for_status()
    if not same_site(r.url):
        raise ValueError(f"إعادة توجيه خارج المتجر: {r.url}")
    if len(r.content) > 8_000_000:
        raise ValueError("حجم الصفحة أكبر من الحد المسموح")
    if not xml and "html" not in r.headers.get("content-type", "text/html").lower():
        raise ValueError("الرابط لم يعرض صفحة HTML")
    return r.text


def robot_parser(s: requests.Session) -> RobotFileParser | None:
    """Return robots policy; None means policy not available (log explicitly)."""
    robots_url = BASE + "/robots.txt"
    try:
        r = s.get(robots_url, timeout=(5, 10))
        if r.ok:
            rp = RobotFileParser()
            rp.parse(r.text.splitlines())
            return rp
        print(f"ملاحظة: robots.txt أعاد الحالة {r.status_code}", file=sys.stderr)
    except requests.RequestException as e:
        print(f"ملاحظة: تعذر فحص robots.txt: {e}", file=sys.stderr)
    return None


def sitemap_products(s: requests.Session, rp: RobotFileParser | None, *, max_products: int) -> list[str]:
    # Odoo may have a sitemap index; recursively follow it within the same origin.
    pending = [BASE + "/sitemap.xml"]
    seen_sitemaps: set[str] = set()
    products: list[str] = []
    while pending and len(seen_sitemaps) < 30 and len(products) < max_products:
        url = pending.pop(0)
        if url in seen_sitemaps or not same_site(url) or (rp and not rp.can_fetch(AGENT, url)):
            continue
        seen_sitemaps.add(url)
        try:
            body = fetch(s, url, xml=True)
            root = ET.fromstring(body)
        except Exception as e:
            print(f"تعذر قراءة خريطة الموقع {url}: {e}", file=sys.stderr)
            continue
        tag = root.tag.split("}")[-1].lower()
        locs = [(node.text or "").strip() for node in root.iter() if node.tag.split("}")[-1].lower() == "loc"]
        if tag == "sitemapindex":
            pending.extend([u for u in locs if same_site(u) and u not in seen_sitemaps])
        else:
            for u in locs:
                u = urldefrag(u)[0]
                if is_product_url(u) and u not in products:
                    products.append(u)
                    if len(products) >= max_products:
                        break
        time.sleep(0.2)
    return products


def first_content(soup: BeautifulSoup, selectors: list[str], *, attr: str | None = None) -> str | None:
    for selector in selectors:
        el = soup.select_one(selector)
        if el:
            val = el.get(attr) if attr else el.get_text(" ", strip=True)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def ld_products(soup: BeautifulSoup) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for sub in node:
                visit(sub)
        elif isinstance(node, dict):
            kinds = node.get("@type", [])
            kinds = [kinds] if isinstance(kinds, str) else kinds
            if "Product" in kinds:
                products.append(node)
            if "@graph" in node:
                visit(node["@graph"])

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            visit(json.loads(script.string or script.get_text()))
        except (json.JSONDecodeError, TypeError):
            continue
    return products


def clean_price(raw: Any) -> str | None:
    if raw is None:
        return None
    t = str(raw).strip().replace("\u066b", ".").replace("\u066c", ",")
    t = t.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    t = re.sub(r"[^0-9.,]", "", t)
    if not t:
        return None
    # Currency-specific number formatting is unknown: prefer unambiguous JSON-LD and microdata.
    if "," in t and "." in t:
        t = t.replace(",", "") if t.rfind(".") > t.rfind(",") else t.replace(".", "").replace(",", ".")
    elif "," in t:
        parts = t.split(",")
        t = "".join(parts) if len(parts[-1]) == 3 else t.replace(",", ".")
    elif t.count(".") > 1:
        t = t.replace(".", "")
    elif t.count(".") == 1 and len(t.split(".")[-1]) == 3:
        # 14.000 is typically a thousands-separated display price, not 14 with 3 decimals.
        t = t.replace(".", "")
    try:
        price = float(t)
        return f"{price:.2f}" if price > 0 else None
    except ValueError:
        return None


def as_url(raw: Any, page_url: str) -> str | None:
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if isinstance(raw, dict):
        raw = raw.get("url") or raw.get("contentUrl")
    if not isinstance(raw, str) or not raw.strip():
        return None
    result = urljoin(page_url, raw.strip())
    return result if urlparse(result).scheme in ("http", "https") else None


def parse_product(html: str, url: str) -> dict[str, Any] | None:
    soup = BeautifulSoup(html, "html.parser")
    obj = (ld_products(soup) or [{}])[0]
    offer = obj.get("offers") or {}
    if isinstance(offer, list):
        offer = offer[0] if offer else {}
    if not isinstance(offer, dict):
        offer = {}
    title = obj.get("name") or first_content(soup, ['h1[itemprop="name"]', 'h1', 'meta[property="og:title"]'], attr=None)
    if not title:
        title = first_content(soup, ['meta[property="og:title"]', 'meta[name="twitter:title"]'], attr="content")
    if title and " | " in title and not obj.get("name"):
        title = title.split(" | ")[0].strip()
    raw_price = offer.get("price") or first_content(soup, ['meta[itemprop="price"]', '[itemprop="price"][content]', 'meta[property="product:price:amount"]'], attr="content")
    if not raw_price:
        raw_price = first_content(soup, ['.oe_price .oe_currency_value', '.product_price .oe_currency_value', '[itemprop="price"]', '.oe_price'], attr=None)
    price = clean_price(raw_price)
    currency = (offer.get("priceCurrency") or first_content(soup, ['meta[itemprop="priceCurrency"]', 'meta[property="product:price:currency"]'], attr="content"))
    if currency and isinstance(currency, str):
        currency = currency.strip().upper()
    image = as_url(obj.get("image"), url) or as_url(first_content(soup, ['meta[property="og:image"]'], attr="content"), url)
    if not image:
        image = as_url(first_content(soup, ['[itemprop="image"]', '.product_detail_img img', '#o-carousel-product .carousel-item img'], attr="src"), url)
    description = obj.get("description") or first_content(soup, ['meta[name="description"]'], attr="content")
    if not description:
        description = first_content(soup, ['[itemprop="description"]', '#product_details .text-muted'])
    availability = offer.get("availability")
    if isinstance(availability, str):
        availability = availability.rsplit("/", 1)[-1]
        availability = availability if availability in ("InStock", "OutOfStock", "PreOrder", "BackOrder", "LimitedAvailability") else "Unknown"
    else:
        availability = "Unknown"
    if not title:
        return None
    # Only content in the public storefront; do not infer private inventory.
    ready = bool(price and currency and image and availability == "InStock")
    return {
        "name": title,
        "price": price,
        "currency": currency or None,
        "image_url": image,
        "description": re.sub(r"\s+", " ", BeautifulSoup(str(description or ""), "html.parser").get_text(" ", strip=True))[:1200],
        "availability": availability,
        "url": url,
        "source": "ateerat.com public storefront",
        "ready_for_automated_ad": ready,
        "review_reason": None if ready else "السعر/العملة/الصورة/التوفر غير مؤكدة بالكامل من الصفحة العامة",
    }


def write_catalog(products: list[dict[str, Any]], output_dir: Path) -> tuple[Path, Path]:
    if not products:
        raise ValueError("لم يتم استخراج أي منتج؛ لم نكتب ملف كتالوج فارغًا فوق البيانات السابقة")
    output_dir.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    doc = {"source": BASE, "fetched_at_utc": now, "count": len(products), "products": products}
    target_json = output_dir / "catalog.json"
    old_products = []
    if target_json.exists():
        try:
            old_products = json.loads(target_json.read_text(encoding="utf-8")).get("products", [])
        except (ValueError, OSError):
            pass
    old = {p["url"]: p for p in old_products if "url" in p}
    diff = []
    for p in products:
        old_p = old.get(p["url"])
        if old_p is None:
            diff.append({"url": p["url"], "change": "new"})
        else:
            changed = {k: {"before": old_p.get(k), "after": p.get(k)} for k in ("name", "price", "currency", "image_url", "availability") if old_p.get(k) != p.get(k)}
            if changed:
                diff.append({"url": p["url"], "change": "updated", "fields": changed})
    target_json.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "changes.json").write_text(json.dumps({"fetched_at_utc": now, "changes": diff}, ensure_ascii=False, indent=2), encoding="utf-8")
    target_csv = output_dir / "catalog.csv"
    with target_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(products[0].keys()))
        w.writeheader()
        w.writerows(products)
    return target_json, target_csv


def main() -> int:
    p = argparse.ArgumentParser(description="Extract Odoo public storefront products at ateerat.com")
    p.add_argument("--url", default=START_PRODUCT, help="Seed product URL")
    p.add_argument("--all", action="store_true", help="Discover products from /sitemap.xml")
    p.add_argument("--max-products", type=int, default=MAX_PRODUCTS)
    p.add_argument("--delay", type=float, default=0.75, help="Seconds between product requests")
    p.add_argument("--out", default="data", help="Output directory")
    p.add_argument("--html-file", help="Offline test: extract from an already saved HTML file")
    args = p.parse_args()
    if not is_product_url(args.url):
        p.error("يجب أن يكون رابط المنتج من ateerat.com/shop/")
    if args.max_products < 1 or args.max_products > 500:
        p.error("max-products must be between 1 and 500")
    if args.html_file:
        product = parse_product(Path(args.html_file).read_text(encoding="utf-8"), args.url)
        products = [product] if product else []
    else:
        session = session_factory()
        robots = robot_parser(session)
        urls = [args.url]
        if args.all:
            found = sitemap_products(session, robots, max_products=args.max_products)
            if not found:
                print("لم تُرجع خريطة الموقع أي صفحات منتجات؛ أوقفنا المزامنة الكاملة بدل إنشاء كتالوج ناقص.", file=sys.stderr)
                return 1
            urls = list(dict.fromkeys(found))[:args.max_products]
            print(f"اكتشفنا {len(found)} رابط منتج من sitemap")
        products = []
        for i, url in enumerate(urls):
            if robots and not robots.can_fetch(AGENT, url):
                print(f"تم تخطي رابط محجوب في robots.txt: {url}", file=sys.stderr)
                continue
            try:
                product = parse_product(fetch(session, url), url)
                if product:
                    products.append(product)
                    print(f"[{i+1}/{len(urls)}] {product['name']} | {product['price'] or 'سعر غير متاح'} {product['currency'] or ''}")
            except Exception as e:
                print(f"تعذر استخراج {url}: {e}", file=sys.stderr)
            if i < len(urls) - 1:
                time.sleep(max(0.5, args.delay))
    try:
        paths = write_catalog(products, Path(args.out))
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"تم إنشاء {paths[0]} و {paths[1]} | {len(products)} منتج | جاهز للإعلانات الآلية: {sum(x['ready_for_automated_ad'] for x in products)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
