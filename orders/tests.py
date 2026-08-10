from decimal import Decimal

from django.test import TestCase

from accounts.models import User
from accounting.models import AccountTransaction
from inventory.models import Inventory
from invoices.models import Invoice, InvoiceReversal
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

    def test_reject_from_pending_does_not_touch_stock_or_accounting(self):
        """مرحلة 4: رفض من PENDING (بدون تأكيد) لازم يفضل زي ما كان قبل
        مرحلة 4 تمامًا — مفيش مخزون اتلمس ومفيش فاتورة ولا مديونية أصلًا،
        لأن مفيش حاجة حصلت للطلب بعد."""
        self.order.reject(actor=self.client_user, reason='رفض قبل التأكيد')

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 100)  # لم يتغير
        self.assertEqual(self.order.status, Order.Status.REJECTED)
        self.assertFalse(Invoice.objects.filter(order=self.order).exists())
        self.assertEqual(AccountTransaction.balance_for(self.client_user), 0)
        self.assertFalse(self.inventory.movements.exists())

    def test_reject_confirmed_order_reverses_stock_and_accounting(self):
        """مرحلة 4: رفض/إلغاء طلب CONFIRMED لازم يعكس خصم المخزون (StockMovement
        IN مقابلة) ويعكس المديونية (AccountTransaction ADJUSTMENT سالبة تساوي
        إجمالي الفاتورة)، من غير ما يحذف أو يعدّل الفاتورة نفسها."""
        self.order.confirm(actor=self.client_user)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 80)  # 100 - 20
        self.assertEqual(AccountTransaction.balance_for(self.client_user), Decimal('200.00'))
        invoice_number = self.order.invoice.invoice_number

        self.order.reject(actor=self.client_user, reason='إلغاء بعد التأكيد')

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 100)  # رجع زي الأول
        self.assertEqual(self.order.status, Order.Status.REJECTED)
        # مديونية العميل رجعت صفر (INVOICE +200 ثم ADJUSTMENT -200)
        self.assertEqual(AccountTransaction.balance_for(self.client_user), 0)

        # الفاتورة لسه موجودة، مش محذوفة، نفس رقمها الثابت
        self.order.invoice.refresh_from_db()
        self.assertTrue(Invoice.objects.filter(order=self.order).exists())
        self.assertEqual(self.order.invoice.invoice_number, invoice_number)
        self.assertTrue(self.order.invoice.is_draft)  # لم تتغير هي نفسها

        # حركة مخزون IN واحدة بنفس كمية الـ OUT الأصلية
        in_movements = self.inventory.movements.filter(movement_type='IN')
        self.assertEqual(in_movements.count(), 1)
        self.assertEqual(in_movements.first().quantity, 20)

        # OrderLog فيه ملاحظة صريحة توثّق الإلغاء بعد التأكيد
        self.assertTrue(
            self.order.logs.filter(note__icontains='بعد التأكيد').exists()
        )

    def test_reject_confirmed_order_without_invoice_does_not_touch_stock(self):
        """حماية ضد المسار المهمّش المعروف (client_approve_amendment بتحط
        CONFIRMED مباشرة من غير confirm()، فمفيش فاتورة ولا خصم مخزون
        أصلًا — راجع ملاحظة PROGRESS.md تحت مرحلة 3). رفض طلب CONFIRMED
        من غير فاتورة لازم *ميضيفش* مخزون وهمي (IN بلا OUT مقابل)."""
        self.order.client_approve_amendment(actor=self.client_user)
        self.assertEqual(self.order.status, Order.Status.CONFIRMED)
        self.assertFalse(hasattr(self.order, 'invoice'))
        self.inventory.refresh_from_db()
        quantity_before_reject = self.inventory.quantity  # لسه 100، مفيش خصم حصل

        self.order.reject(actor=self.client_user, reason='إلغاء مسار bypass')

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, quantity_before_reject)  # لم يتغير
        self.assertFalse(self.inventory.movements.exists())
        self.assertEqual(self.order.status, Order.Status.REJECTED)
        self.assertEqual(AccountTransaction.balance_for(self.client_user), 0)

    def test_reject_confirmed_order_creates_pre_delivery_invoice_reversal(self):
        """مرحلة 5: رفض/إلغاء طلب CONFIRMED لازم يسجّل InvoiceReversal واحد
        بـ stage=PRE_DELIVERY (البضاعة أصلًا لسه ماتسلّمتش) وقيمة تساوي
        إجمالي الفاتورة، من غير ما يمسّ الفاتورة نفسها."""
        self.order.confirm(actor=self.client_user)
        invoice = self.order.invoice
        self.assertEqual(invoice.reversals.count(), 0)

        self.order.reject(actor=self.client_user, reason='إلغاء بعد التأكيد')

        self.assertEqual(invoice.reversals.count(), 1)
        reversal = invoice.reversals.first()
        self.assertEqual(reversal.stage, InvoiceReversal.Stage.PRE_DELIVERY)
        self.assertEqual(reversal.amount, Decimal('200.00'))
        self.assertEqual(reversal.created_by, self.client_user)
        # الفاتورة لسه موجودة وبرقمها الثابت — الإشعار مستند منفصل بجانبها
        invoice.refresh_from_db()
        self.assertTrue(Invoice.objects.filter(pk=invoice.pk).exists())

    def test_reject_confirmed_order_without_invoice_creates_no_reversal(self):
        """مرحلة 5: نفس سيناريو المسار المهمّش (بدون فاتورة أصلًا) — مفيش
        InvoiceReversal يتسجّل لأن مفيش فاتورة يترتبط بيها أصلًا."""
        self.order.client_approve_amendment(actor=self.client_user)
        self.assertFalse(hasattr(self.order, 'invoice'))

        self.order.reject(actor=self.client_user, reason='bypass')

        self.assertEqual(InvoiceReversal.objects.count(), 0)

    def test_reject_confirmed_order_twice_does_not_double_reverse(self):
        """double-submit/سباق: رفض طلب CONFIRMED مرتين لازم يترفض التاني
        ومايعكسش المخزون أو المديونية مرة تانية."""
        self.order.confirm(actor=self.client_user)
        self.order.reject(actor=self.client_user)
        self.inventory.refresh_from_db()
        quantity_after_first_reject = self.inventory.quantity
        balance_after_first_reject = AccountTransaction.balance_for(self.client_user)

        with self.assertRaises(ValueError):
            self.order.reject(actor=self.client_user)

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, quantity_after_first_reject)
        self.assertEqual(
            AccountTransaction.balance_for(self.client_user), balance_after_first_reject,
        )
        self.assertEqual(self.order.invoice.reversals.count(), 1)  # لم يتكرر

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


