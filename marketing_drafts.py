#!/usr/bin/env python3
"""Generate SAFE Arabic marketing drafts from the public ateerat.com catalog.

This is a draft generator, not an autopublisher or an LLM. It deliberately avoids
inventing perfume notes, discounts, stock counts, delivery promises or guarantees.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Aden")
HOSTS = {"ateerat.com", "www.ateerat.com"}
ANGLES = (
    ("التعرّف على العطر", "✨ هل تبحث عن عطر جديد؟\n\nتعرّف على {name} ضمن مجموعة عطيرات."),
    ("الاختيار من المتجر", "🌟 لكل شخص ذوقه في العطور.\n\nاكتشف {name} عبر متجر عطيرات."),
    ("السعر الواضح", "🛍️ عطر من تشكيلة عطيرات يستحق التعرف عليه!\n\n{name}"),
    ("دعوة للاكتشاف", "💫 ما رأيك بتجربة اختيار مختلف؟\n\nتعرّف على {name} ضمن المنتجات المتاحة في عطيرات."),
    ("الشراء من المصدر", "📲 تعرّف على تفاصيل {name} واطلبه من متجر عطيرات مباشرة."),
)


def is_store_url(url: object, product: bool = False) -> bool:
    if not isinstance(url, str):
        return False
    parsed = urlparse(url)
    return (parsed.scheme == "https" and parsed.hostname in HOSTS
            and (not product or parsed.path.startswith("/shop/")))


def format_yer(price: object) -> str | None:
    try:
        amount = Decimal(str(price))
        if not amount.is_finite() or amount <= 0:
            return None
    except (InvalidOperation, ValueError):
        return None
    if amount == amount.to_integral_value():
        return f"{amount:,.0f} ريال يمني"
    return f"{amount:,.2f} ريال يمني"


def validate_product(product: dict) -> tuple[bool, str]:
    if product.get("source") != "ateerat.com public storefront":
        return False, "مصدر غير معتمد"
    if product.get("ready_for_automated_ad") is not True:
        return False, "المنتج غير مجاز في الكتالوج"
    if product.get("availability") != "InStock":
        return False, "غير متوفر بحسب الصفحة العامة"
    if product.get("currency") != "YER" or format_yer(product.get("price")) is None:
        return False, "السعر أو العملة غير صالحين"
    if not isinstance(product.get("name"), str) or not product["name"].strip():
        return False, "اسم ناقص"
    if not is_store_url(product.get("url"), product=True):
        return False, "رابط المنتج لا يعود إلى عطيرات"
    if not is_store_url(product.get("image_url")):
        return False, "رابط الصورة غير موثوق"
    return True, ""


def load_catalog(path: Path, max_age_hours: int = 72) -> tuple[list[dict], list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("source") != "https://www.ateerat.com":
        raise ValueError("الكتالوج ليس من المصدر الرسمي ateerat.com")
    fetched = dt.datetime.fromisoformat(data["fetched_at_utc"].replace("Z", "+00:00"))
    if fetched.tzinfo is None:
        raise ValueError("وقت المزامنة ليس UTC معروفًا")
    age = dt.datetime.now(dt.timezone.utc) - fetched.astimezone(dt.timezone.utc)
    if age > dt.timedelta(hours=max_age_hours) or age < -dt.timedelta(hours=1):
        raise ValueError(f"كتالوج قديم أو وقت مزامنته غير صحيح: {age}")
    good, rejected = [], []
    for p in data.get("products", []):
        ok, reason = validate_product(p)
        if ok:
            good.append(p)
        else:
            rejected.append({"name": p.get("name", "?"), "reason": reason})
    if not good:
        raise ValueError("لا توجد منتجات تستوفي شروط إعداد مسودة آمنة")
    return good, rejected


def build_drafts(products: list[dict], count: int = 7, start: dt.date | None = None) -> list[dict]:
    if start is None:
        start = dt.datetime.now(TZ).date() + dt.timedelta(days=1)
    # Sort by product URL so recomputing the same date is deterministic.
    ranked = sorted(products, key=lambda p: p["url"])
    drafts = []
    for i in range(count):
        day = start + dt.timedelta(days=i)
        product = ranked[(day.toordinal() + i) % len(ranked)]
        angle_name, tmpl = ANGLES[(day.toordinal() + i) % len(ANGLES)]
        name = re.sub(r"\s+", " ", product["name"]).strip()
        price = format_yer(product["price"])
        caption = (f"{tmpl.format(name=name)}\n\n"
                   f"💰 السعر الحالي في الموقع: {price}\n"
                   f"🔗 شاهد المنتج وراجع التوفر قبل الطلب:\n{product['url']}\n\n"
                   "#عطيرات #عطور")
        drafts.append({
            "date": day.isoformat(), "platforms": ["facebook", "instagram"],
            "format": "IMAGE_POST", "angle": angle_name, "product": name,
            "price": product["price"], "currency": "YER",
            "product_url": product["url"], "image_url": product["image_url"],
            "caption_ar": caption, "status": "DRAFT_REVIEW",
            "auto_publish": False,
        })
    return drafts


def main() -> None:
    ap = argparse.ArgumentParser(description="مسودات تسويق عطيرات من الكتالوج الرسمي")
    ap.add_argument("--catalog", default="data/catalog.json")
    ap.add_argument("--output-dir", default="marketing")
    ap.add_argument("--count", type=int, default=7)
    args = ap.parse_args()
    if not 1 <= args.count <= 30:
        ap.error("count must be 1..30")
    products, excluded = load_catalog(Path(args.catalog))
    drafts = build_drafts(products, args.count)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload = {"source": "https://www.ateerat.com/", "timezone": "Asia/Aden",
               "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
               "count": len(drafts), "publish_enabled": False,
               "note": "مسودات آمنة للمراجعة، لم تُرسل إلى Metricool ولم تُنشر",
               "rejected_products": excluded, "drafts": drafts}
    (out / "drafts.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "drafts.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["date", "format", "product", "price", "currency", "angle", "caption_ar", "product_url", "image_url", "status"]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(drafts)
    print(f"أُنشئت {len(drafts)} مسودات في {out}/drafts.json و drafts.csv. لم يتم نشر أي شيء.")
    print(f"المنتجات المؤهلة: {len(products)}؛ منتجات مستبعدة: {len(excluded)}")


if __name__ == "__main__":
    main()
