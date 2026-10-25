import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog import parse_product, is_product_url, clean_price, sitemap_products, same_site, START_PRODUCT


class TestCatalog(unittest.TestCase):
    def test_example_product(self):
        html = (Path(__file__).parent / 'fixtures/odoo_product.html').read_text(encoding='utf-8')
        p = parse_product(html, START_PRODUCT)
        self.assertEqual(p['name'], 'منتج تجريبي غير حقيقي')
        self.assertEqual(p['price'], '14000.00')
        self.assertEqual(p['currency'], 'YER')
        self.assertEqual(p['availability'], 'InStock')
        self.assertTrue(p['ready_for_automated_ad'])

    def test_no_availability_no_automatic_ad(self):
        p = parse_product('<html><h1>عطر اختبار</h1><span itemprop="price" content="2000"/><meta itemprop="priceCurrency" content="YER"/><meta property="og:image" content="/image.png"/></html>', START_PRODUCT)
        self.assertEqual(p['availability'], 'Unknown')
        self.assertFalse(p['ready_for_automated_ad'])

    def test_price_formats(self):
        self.assertEqual(clean_price('١٤٬٠٠٠'), '14000.00')
        self.assertEqual(clean_price('14,000'), '14000.00')
        self.assertEqual(clean_price('14.000'), '14000.00')
        self.assertEqual(clean_price('14000.50'), '14000.50')

    def test_domain_protection(self):
        self.assertTrue(is_product_url(START_PRODUCT))
        self.assertFalse(is_product_url('https://evil.example/shop/perf-049'))
        self.assertFalse(is_product_url('https://www.ateerat.com/shop/cart'))
        self.assertFalse(same_site('https://ateerat.com.evil.example/shop/a'))


if __name__ == '__main__':
    unittest.main()
