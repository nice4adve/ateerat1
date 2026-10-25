import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import marketing_cards as mc


class MarketingCardsTests(unittest.TestCase):
    def test_render_card_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'ad.png'
            mc.render_card(Image.new('RGB', (640, 900), '#dedede'), 'Asad Bourbon لطافة', '12700.00', p)
            with Image.open(p) as generated:
                self.assertEqual(generated.size, (1080, 1350))
                self.assertEqual(generated.format, 'PNG')

    def test_reject_offsite_image(self):
        with self.assertRaises(ValueError):
            mc.fetch_original_photo('https://other-site.example/example.png')

    def test_reject_changed_prices(self):
        product = {'url': 'https://www.ateerat.com/shop/test-1', 'price': '100.00',
                   'image_url': 'https://www.ateerat.com/web/image/1'}
        draft = {'product_url': product['url'], 'price': '95.00', 'image_url': product['image_url'],
                 'currency': 'YER', 'status': 'DRAFT_REVIEW', 'auto_publish': False}
        with self.assertRaisesRegex(ValueError, 'Price changed'):
            mc.require_valid_draft(draft, {product['url']: product})

    def test_end_to_end_offline_using_sample_photo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = dt.datetime.now(dt.timezone.utc).isoformat()
            prod = {'name': 'عطر الاختبار Test', 'price': '5000.00', 'currency': 'YER',
                    'image_url': 'https://www.ateerat.com/web/image/product.product/7/image_1920',
                    'availability': 'InStock', 'url': 'https://www.ateerat.com/shop/test-1',
                    'source': 'ateerat.com public storefront', 'ready_for_automated_ad': True}
            catalog = {'source': 'https://www.ateerat.com', 'fetched_at_utc': now,
                       'count': 1, 'products': [prod]}
            draft = {'date': '2026-10-26', 'product_url': prod['url'], 'price': prod['price'],
                     'image_url': prod['image_url'], 'currency': 'YER',
                     'caption_ar': 'اختبر الإعلان', 'status': 'DRAFT_REVIEW', 'auto_publish': False}
            (root / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
            (root / 'drafts.json').write_text(json.dumps({'source':'https://www.ateerat.com/',
                             'publish_enabled': False, 'drafts':[draft]}, ensure_ascii=False), encoding='utf-8')
            res = mc.build_cards(root / 'catalog.json', root / 'drafts.json',
                                 root / 'marketing/cards',
                                 fetch_image=lambda url: Image.new('RGB',(500, 700),'#eeeeee'))
            self.assertEqual(res['count'], 1)
            self.assertFalse(res['publish_enabled'])
            self.assertTrue((root / 'marketing/cards/01-2026-10-26.png').exists())
            self.assertTrue((root / 'marketing/preview.html').exists())
            self.assertTrue((root / 'marketing/cards_manifest.json').exists())


if __name__ == '__main__':
    unittest.main()
