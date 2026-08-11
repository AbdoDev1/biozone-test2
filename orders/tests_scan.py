from decimal import Decimal

from django.test import TestCase

from accounts.models import User
from inventory.models import Inventory
from orders.models import Order, OrderItem
from products.models import Category, Product, ProductUnit


class OrderFindItemByBarcodeTestCase(TestCase):
    """
    مرحلة 6 — شاشة المراجعة بالسكانر. اختبارات على منطق المطابقة نفسه
    (Order.find_item_by_barcode) و OrderItem.set_scanned، بمعزل عن الـ view.
    """

    def setUp(self):
        self.client_user = User.objects.create_user(
            username='client1', email='c1@example.com', password='x', role=User.Role.CLIENT,
        )
        category = Category.objects.create(name='مواد غذائية', slug='food')

        self.product_a = Product.objects.create(
            category=category, name_ar='منتج أ', barcode='1111', barcode_2='2222',
        )
        self.unit_a = ProductUnit.objects.create(
            product=self.product_a, size=ProductUnit.Size.SMALL, name='قطعة',
            qty_in_small=1, unit_price=Decimal('10.00'),
        )
        Inventory.objects.create(product=self.product_a, quantity=100, min_quantity=5)

        self.product_b = Product.objects.create(category=category, name_ar='منتج ب', barcode='3333')
        self.unit_b = ProductUnit.objects.create(
            product=self.product_b, size=ProductUnit.Size.SMALL, name='قطعة',
            qty_in_small=1, unit_price=Decimal('5.00'),
        )
        Inventory.objects.create(product=self.product_b, quantity=50, min_quantity=5)

        self.order = Order.objects.create(client=self.client_user)
        self.item_a = OrderItem.objects.create(
            order=self.order, product_unit=self.unit_a, quantity=2,
            public_price=self.unit_a.unit_price, unit_price=self.unit_a.unit_price,
        )
        self.item_b = OrderItem.objects.create(
            order=self.order, product_unit=self.unit_b, quantity=1,
            public_price=self.unit_b.unit_price, unit_price=self.unit_b.unit_price,
        )
        self.service_item = OrderItem.objects.create(
            order=self.order, is_service_fee=True, service_name='مصاريف توصيل',
            quantity=1, public_price=Decimal('20.00'), unit_price=Decimal('20.00'),
        )

    def test_matches_primary_barcode(self):
        self.assertEqual(self.order.find_item_by_barcode('1111'), self.item_a)

    def test_matches_secondary_barcode_field(self):
        self.assertEqual(self.order.find_item_by_barcode('2222'), self.item_a)

    def test_match_is_case_insensitive_and_trims_whitespace(self):
        self.assertEqual(self.order.find_item_by_barcode('  1111  '), self.item_a)

    def test_matches_different_product_correctly(self):
        self.assertEqual(self.order.find_item_by_barcode('3333'), self.item_b)

    def test_unknown_barcode_returns_none(self):
        self.assertIsNone(self.order.find_item_by_barcode('9999'))

    def test_empty_barcode_returns_none(self):
        self.assertIsNone(self.order.find_item_by_barcode(''))
        self.assertIsNone(self.order.find_item_by_barcode(None))

    def test_service_fee_item_never_matched(self):
        """صنف خدمي بدون product_unit خالص — لازم يتخطى من غير أي خطأ."""
        self.assertIsNone(self.order.find_item_by_barcode('anything'))
        # مفيش استثناء اتطلع من مرور الحلقة على الصنف الخدمي (لا AttributeError من محاولة الوصول لـ product_unit)

    def test_barcode_from_another_orders_product_does_not_match(self):
        """باركود حقيقي بس لمنتج مش موجود في *هذا* الطلب — يرجع None."""
        other_product = Product.objects.create(category=self.product_a.category, name_ar='منتج تاني', barcode='7777')
        self.assertIsNone(self.order.find_item_by_barcode('7777'))


class OrderItemSetScannedTestCase(TestCase):
    def setUp(self):
        client_user = User.objects.create_user(
            username='client2', email='c2@example.com', password='x', role=User.Role.CLIENT,
        )
        category = Category.objects.create(name='مواد غذائية', slug='food')
        product = Product.objects.create(category=category, name_ar='منتج')
        unit = ProductUnit.objects.create(
            product=product, size=ProductUnit.Size.SMALL, name='قطعة',
            qty_in_small=1, unit_price=Decimal('10.00'),
        )
        Inventory.objects.create(product=product, quantity=10, min_quantity=1)
        order = Order.objects.create(client=client_user)
        self.item = OrderItem.objects.create(
            order=order, product_unit=unit, quantity=1,
            public_price=unit.unit_price, unit_price=unit.unit_price,
        )

    def test_set_scanned_true_records_timestamp(self):
        self.assertFalse(self.item.scanned)
        self.assertIsNone(self.item.scanned_at)
        self.item.set_scanned(True)
        self.item.refresh_from_db()
        self.assertTrue(self.item.scanned)
        self.assertIsNotNone(self.item.scanned_at)

    def test_set_scanned_false_clears_timestamp(self):
        self.item.set_scanned(True)
        self.item.set_scanned(False)
        self.item.refresh_from_db()
        self.assertFalse(self.item.scanned)
        self.assertIsNone(self.item.scanned_at)

    def test_scanning_does_not_touch_order_status(self):
        """تأكيد صريح إن set_scanned علامة عرض بحتة — مفيش أي تأثير على حالة الطلب."""
        order = self.item.order
        original_status = order.status
        self.item.set_scanned(True)
        order.refresh_from_db()
        self.assertEqual(order.status, original_status)
