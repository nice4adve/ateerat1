import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import creative_backgrounds as cb
import marketing_cards as mc


class CreativeBackgroundTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root / 'marketing/creative_backgrounds'
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        self.product = {
            'name': 'Asad Bourbon Lattafa Perfumes', 'price':'12700.00', 'currency':'YER',
            'image_url':'https://www.ateerat.com/web/image/product.product/77/image_1920',
            'availability':'InStock',
            'url':'https://www.ateerat.com/shop/perf-016-asad-bourbon-91',
            'source':'ateerat.com public storefront', 'ready_for_automated_ad': True,
        }
        self.draft = {'date':'2026-10-26','product':self.product['name'],
                      'price':self.product['price'],'currency':'YER',
                      'product_url':self.product['url'], 'image_url':self.product['image_url'],
                      'status':'DRAFT_REVIEW','auto_publish':False,'caption_ar':'للمراجعة'}
        self.catalog_file = self.root/'catalog.json'
        self.draft_file = self.root/'drafts.json'
        self.catalog_file.write_text(json.dumps({'source':'https://www.ateerat.com',
            'fetched_at_utc':now,'products':[self.product]}),encoding='utf-8')
        self.draft_file.write_text(json.dumps({'source':'https://www.ateerat.com/',
            'publish_enabled':False,'drafts':[self.draft]}),encoding='utf-8')

    def generate_fake(self,prompt,**kwargs):
        self.assertIn('BACKGROUND PLATE ONLY', prompt)
        self.assertIn('NO perfume bottle', prompt)
        self.assertNotIn('12,700',prompt)
        self.assertEqual(kwargs['quality'],'medium')
        return Image.new('RGB',(1024,1536),'#AB793C')

    def test_creative_direction_changes_across_products(self):
        p = dict(self.product,palette='amber')
        direction = cb.visual_plan(p,cycle='2026-10')
        self.assertIn(direction['scene'], {x[0] for x in cb.SCENES})
        self.assertIn('brown',cb.make_prompt(direction))
        self.assertIn('NO perfume bottle',cb.make_prompt(direction))
        self.assertNotIn('12700',cb.make_prompt(direction))
        self.assertNotEqual(direction['fingerprint'],cb.visual_plan(p,cycle='2026-11')['fingerprint'])

    def test_missing_api_key_spends_nothing_and_does_not_publish(self):
        generated = []
        doc=cb.build_backgrounds(self.catalog_file,self.draft_file,self.out,
            api_key='', max_new=1, palette_provider=lambda p:'amber',
            generate=lambda *a,**k:generated.append(a))
        self.assertEqual(generated,[])
        self.assertEqual(doc['new_ai_images_this_run'],0)
        self.assertEqual(doc['available_backgrounds'],0)
        self.assertFalse(doc['publish_enabled'])

    def test_exactly_one_paid_image_and_cached_on_rerun(self):
        calls=[]
        def gen(prompt, **kw):
            calls.append(1)
            return self.generate_fake(prompt,**kw)
        first=cb.build_backgrounds(self.catalog_file,self.draft_file,self.out,
            api_key='fake-local-test-only',max_new=1,generate=gen,palette_provider=lambda p:'amber',cycle='2026-10')
        self.assertEqual(first['new_ai_images_this_run'],1)
        self.assertEqual(first['available_backgrounds'],1)
        self.assertEqual(len(calls),1)
        second=cb.build_backgrounds(self.catalog_file,self.draft_file,self.out,
            api_key='fake-local-test-only',max_new=1,generate=gen,palette_provider=lambda p:'amber',cycle='2026-10')
        self.assertEqual(second['new_ai_images_this_run'],0)
        self.assertEqual(len(calls),1)
        self.assertEqual(len(list(self.out.glob('*.jpg'))),1)
        image=mc.read_creative_background(self.out.parent/'creative_backgrounds_manifest.json',self.product,self.draft)
        self.assertEqual(image.size,(1080,1350))
        new_draft=dict(self.draft,price='13000.00')
        with self.assertRaisesRegex(ValueError,'Price changed'):
            mc.require_valid_draft(new_draft,{self.product['url']:self.product})

    def test_tampered_source_does_not_load_scene(self):
        cb.build_backgrounds(self.catalog_file,self.draft_file,self.out,
            api_key='test-only',max_new=1,generate=self.generate_fake,
            palette_provider=lambda p:'amber',cycle='2026-10')
        mf=self.out.parent/'creative_backgrounds_manifest.json'
        obj=json.loads(mf.read_text())
        obj['backgrounds'][0]['background_file']='../secret.jpg'
        mf.write_text(json.dumps(obj))
        self.assertIsNone(mc.read_creative_background(mf,self.product,self.draft))

    def test_paid_api_failure_gracefully_uses_fallback(self):
        def fail(*args, **kwargs):
            raise RuntimeError('network unavailable')
        doc=cb.build_backgrounds(self.catalog_file,self.draft_file,self.out,
            api_key='test-only',max_new=1,generate=fail,
            palette_provider=lambda p:'classic',cycle='2026-10')
        self.assertEqual(doc['available_backgrounds'],0)
        self.assertEqual(doc['new_ai_images_this_run'],0)
        self.assertFalse(doc['publish_enabled'])

    def test_no_more_than_three_images_requested_even_when_enabled(self):
        with self.assertRaisesRegex(ValueError,'safety cap'):
            cb.build_backgrounds(self.catalog_file,self.draft_file,self.out, max_new=4,
                                 palette_provider=lambda p:'amber')

    def test_full_renderer_uses_ai_background_without_changing_the_bottle(self):
        cb.build_backgrounds(self.catalog_file,self.draft_file,self.out,
            api_key='test-only',max_new=1,generate=self.generate_fake,
            palette_provider=lambda p:'amber',cycle='2026-10')
        manifest=mc.build_cards(self.catalog_file,self.draft_file,self.out.parent/'cards',
                fetch_image=lambda url:Image.new('RGB',(500,800),'#81452D'),
                creative_manifest=self.out.parent/'creative_backgrounds_manifest.json')
        self.assertEqual(manifest['design_version'],'ATEERAT-V3')
        self.assertEqual(manifest['designs'][0]['background_mode'],'AI-CINEMATIC')
        self.assertFalse(manifest['publish_enabled'])
        with Image.open(self.out.parent/'cards/01-2026-10-26.png') as im:
            self.assertEqual(im.size,(1080,1350))


if __name__ == '__main__':
    unittest.main()
