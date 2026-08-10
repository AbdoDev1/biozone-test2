from decimal import Decimal

from django.test import TestCase

from accounts.models import User
from inventory.models import Inventory
from invoices.models import Invoice
from orders.models import Order, OrderItem
from products.models import Category, Product, ProductUnit


class OrderLifecycleTestCase(TestCase):
    """
    اختبارات على أهم مسار في النظام: حياة الطلب من الإنشاء لحد التسليم،
    وتأثيره على المخزون والفواتير. الهدف إننا نلاحظ فورًا لو أي تعديل
    مستقبلي كسر حساب المخزون أو إصدار الفواتير.
    """

    def setUp(self):
        self.client_user = User.objects.create_user(
            username='client1',
            email='client1@example.com',
            password='testpass123',
            role=User.Role.CLIENT,
        )
        category = Category.objects.create(name='مواد غذائية', slug='food')
        self.product = Product.objects.create(category=category, name_ar='منتج تجريبي')
        self.unit = ProductUnit.objects.create(
            product=self.product,
            size=ProductUnit.Size.SMALL,
            name='قطعة',
            qty_in_small=1,
            unit_price=Decimal('10.00'),
        )
        self.inventory = Inventory.objects.create(
            product=self.product,
            quantity=100,
            min_quantity=5,
        )
        self.order = Order.objects.create(client=self.client_user)
        self.item = OrderItem.objects.create(
            order=self.order,
            product_unit=self.unit,
            quantity=20,
            public_price=self.unit.unit_price,
            unit_price=self.unit.unit_price,
        )

    def test_confirm_deducts_stock_and_issues_draft_invoice(self):
        """من مرحلة 3: التأكيد (مش التسليم) هو لحظة خصم المخزون الفعلي
        وإصدار الفاتورة (كمسودة is_draft=True) برقم ثابت ومديونية حقيقية."""
        self.order.confirm(actor=self.client_user)

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 80)  # 100 - 20
        self.assertEqual(self.order.status, Order.Status.CONFIRMED)
        self.assertTrue(Invoice.objects.filter(order=self.order).exists())
        self.assertEqual(self.order.invoice.total, Decimal('200.00'))  # 20 * 10
        self.assertTrue(self.order.invoice.is_draft)

    def test_mark_delivered_after_confirm_only_finalizes_invoice(self):
        """التسليم بعد التأكيد لازم يحوّل الفاتورة لنهائية بنفس رقمها من
        غير أي خصم مخزون إضافي — المخزون كان اتخصم بالفعل وقت confirm()."""
        self.order.confirm(actor=self.client_user)
        self.inventory.refresh_from_db()
        quantity_after_confirm = self.inventory.quantity
        invoice_number = self.order.invoice.invoice_number

        self.order.mark_delivered(actor=self.client_user)

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, quantity_after_confirm)  # لم يتغير
        self.assertEqual(self.order.status, Order.Status.DELIVERED)
        self.order.invoice.refresh_from_db()
        self.assertFalse(self.order.invoice.is_draft)
        self.assertEqual(self.order.invoice.invoice_number, invoice_number)  # لم يتغير

    def test_confirm_twice_does_not_double_deduct_stock(self):
        """double-submit/سباق: نداء confirm() تاني على طلب اتأكد بالفعل
        لازم يترفض ومايخصمش من المخزون مرة تانية."""
        self.order.confirm(actor=self.client_user)
        self.inventory.refresh_from_db()
        quantity_after_first_confirm = self.inventory.quantity

        with self.assertRaises(Exception):
            self.order.confirm(actor=self.client_user)

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, quantity_after_first_confirm)  # لم يتغير

    def test_mark_delivered_twice_does_not_double_charge_invoice(self):
        """issue_for_order لازم تكون idempotent — نداءها مرتين ميعملش فاتورة تانية.
        confirm() (مرحلة 2) هي اللي بتصدر الفاتورة، فلازم تتنادى الأول."""
        self.order.confirm(actor=self.client_user)
        self.order.mark_delivered(actor=self.client_user)
        first_invoice_id = self.order.invoice.id

        Invoice.issue_for_order(self.order, actor=self.client_user)
        self.order.refresh_from_db()

        self.assertEqual(Invoice.objects.filter(order=self.order).count(), 1)
        self.assertEqual(self.order.invoice.id, first_invoice_id)

    def test_confirm_fails_when_stock_insufficient(self):
        """من مرحلة 3: لو الكمية مش متوفرة وقت التأكيد، confirm() (مش
        mark_delivered) هي اللي تفشل — والطلب يفضل زي ما هو من غير فاتورة
        ولا خصم مخزون (@transaction.atomic بيلغي كل حاجة مع بعض)."""
        self.inventory.quantity = 5
        self.inventory.save(update_fields=['quantity'])

        with self.assertRaises(Exception):
            self.order.confirm(actor=self.client_user)

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 5)  # لم يتغير
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertFalse(Invoice.objects.filter(order=self.order).exists())

    def test_reject_twice_raises(self):
        """رفض طلب مرفوض بالفعل لازم يمنع، عشان مايتكررش في الـ log أو الإشعارات."""
        self.order.reject(actor=self.client_user, reason='تجربة')
        self.assertEqual(self.order.status, Order.Status.REJECTED)

        with self.assertRaises(ValueError):
            self.order.reject(actor=self.client_user, reason='تاني')

    def test_reject_delivered_order_raises(self):
        """طلب اتسلّم بالفعل مينفعش يترفض."""
        self.order.mark_delivered(actor=self.client_user)

        with self.assertRaises(ValueError):
            self.order.reject(actor=self.client_user)

    def test_amend_item_quantity_rejects_more_than_available(self):
        """طلب زيادة كمية أكبر من المتاح في المخزون لازم يترفض قبل ما يتحفظ."""
        with self.assertRaises(ValueError):
            self.order.amend_item_quantity(self.item, new_quantity=1000, actor=self.client_user)

        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 20)  # لم يتغير

    def test_amend_item_quantity_updates_price(self):
        """تعديل الكمية لازم يعيد حساب unit_price بناءً على الكمية الجديدة."""
        self.order.amend_item_quantity(self.item, new_quantity=10, actor=self.client_user)

        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 10)
        self.assertEqual(self.item.unit_price, Decimal('10.00'))


