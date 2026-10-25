import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from PIL import Image

import marketing_cards as mc


class BrandCardsTests(unittest.TestCase):
    def test_uses_only_cairo(self):
        path = mc.font_path()
        self.assertIn('Cairo', Path(path).name)
        self.assertEqual(mc.cairo(32, 800).get_variation_axes()[0]['maximum'], 1000)

    def test_logo_assets_present(self):
        for filename in ('logo_navy.png', 'logo_white.png'):
            path = mc.ASSETS / filename
            self.assertTrue(path.is_file())
            with Image.open(path) as im:
                self.assertEqual(im.format, 'PNG')
                self.assertEqual(im.mode, 'RGBA')

    def test_visual_card_is_1080_by_1350(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / 'test.png'
            mc.render_card(Image.new('RGB', (650, 800), 'white'),
                           'Asad Bourbon Lattafa Perfumes', '12700.00', dest)
            with Image.open(dest) as out:
                self.assertEqual(out.size, (1080,1350))
                self.assertEqual(out.format, 'PNG')
                self.assertNotEqual(out.getpixel((30,50)), out.getpixel((800,600)))

    def test_palette_based_on_visible_bottle_color_not_claimed_notes(self):
        photo = Image.new('RGB', (200, 200), '#AA5030')
        self.assertEqual(mc.choose_palette(photo), 'amber')
        self.assertEqual(mc.choose_palette(Image.new('RGB',(200,200),'#EFEFEF')), 'classic')

    def test_rejects_off_site_image(self):
        with self.assertRaises(ValueError):
            mc.fetch_original_photo('https://example.org/evil.png')

    def test_rejects_price_change_or_stock_change(self):
        product = {'url':'https://www.ateerat.com/shop/something-1',
                   'name':'Test', 'price':'100.00',
                   'image_url':'https://www.ateerat.com/web/image/1',
                   'availability':'InStock','ready_for_automated_ad':True}
        draft = {'product_url':product['url'],'product':'Test','price':'95.00',
                 'image_url':product['image_url'],'currency':'YER',
                 'status':'DRAFT_REVIEW','auto_publish':False}
        with self.assertRaisesRegex(ValueError,'Price changed'):
            mc.require_valid_draft(draft,{product['url']:product})
        draft['price']='100.00'
        product['availability']='OutOfStock'
        with self.assertRaisesRegex(ValueError,'available'):
            mc.require_valid_draft(draft,{product['url']:product})

    def test_full_pipeline_review_only(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            now=dt.datetime.now(dt.timezone.utc).isoformat()
            p={'name':'عطر الاختبار Test', 'price':'5000.00','currency':'YER',
               'image_url':'https://www.ateerat.com/web/image/product.product/7/image_1920',
               'availability':'InStock','url':'https://www.ateerat.com/shop/test-1',
               'source':'ateerat.com public storefront','ready_for_automated_ad':True}
            catalog={'source':'https://www.ateerat.com','fetched_at_utc':now,'count':1,'products':[p]}
            draft={'date':'2026-10-26','product_url':p['url'],'product':p['name'],
                   'price':p['price'],'image_url':p['image_url'],'currency':'YER',
                   'caption_ar':'مسودة للمراجعة','status':'DRAFT_REVIEW','auto_publish':False}
            (root/'catalog.json').write_text(json.dumps(catalog,ensure_ascii=False),encoding='utf8')
            (root/'drafts.json').write_text(json.dumps({'source':'https://www.ateerat.com/',
              'publish_enabled':False,'drafts':[draft]},ensure_ascii=False),encoding='utf8')
            manifest=mc.build_cards(root/'catalog.json',root/'drafts.json',root/'marketing/cards',
                                    fetch_image=lambda url:Image.new('RGB',(500,800),'#81452D'))
            self.assertEqual(manifest['design_version'],'ATEERAT-V2')
            self.assertFalse(manifest['publish_enabled'])
            self.assertEqual(manifest['count'],1)
            self.assertTrue((root/'marketing/cards/01-2026-10-26.png').exists())
            self.assertTrue((root/'marketing/preview.html').exists())
            self.assertTrue((root/'marketing/cards_manifest.json').exists())
            self.assertFalse(manifest['designs'][0]['auto_publish'])


if __name__=='__main__': unittest.main()
