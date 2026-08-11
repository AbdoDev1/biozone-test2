from decimal import Decimal

from django.test import TestCase, Client as TestClient
from django.contrib.auth.models import Permission

from accounts.models import User
from accounting.models import AccountTransaction
from inventory.models import Inventory
from invoices.models import Invoice, InvoiceReversal, InvoiceReversalItem
from orders.models import Order, OrderItem
from products.models import Category, Product, ProductUnit


class PostDeliveryReturnModelTestCase(TestCase):
    """
    اختبارات نظام المرتجعات (POST_DELIVERY) على مستوى الموديل مباشرة —
    راجع InvoiceReversal.create_post_delivery_return.
    """

    def setUp(self):
        self.client_user = User.objects.create_user(
            username='client_return', email='client_return@example.com',
            password='testpass123', role=User.Role.CLIENT,
        )
        self.staff_user = User.objects.create_user(
            username='staff_return', email='staff_return@example.com',
            password='testpass123', role=User.Role.WAREHOUSE,
        )
        category = Category.objects.create(name='أدوات طبية', slug='medical')
        self.product = Product.objects.create(category=category, name_ar='قفازات طبية')
        self.unit = ProductUnit.objects.create(
            product=self.product, size=ProductUnit.Size.SMALL, name='قطعة',
            qty_in_small=1, unit_price=Decimal('15.00'),
        )
        self.inventory = Inventory.objects.create(product=self.product, quantity=100, min_quantity=5)
        self.order = Order.objects.create(client=self.client_user)
        self.item = OrderItem.objects.create(
            order=self.order, product_unit=self.unit, quantity=10,
            public_price=self.unit.unit_price, unit_price=self.unit.unit_price,
        )
        self.order.confirm(actor=self.client_user)
        self.invoice = self.order.invoice
        self.invoice_item = self.invoice.items.first()

    def test_partial_return_restocks_and_reduces_debt(self):
        """مرتجع جزئي (3 من أصل 10) لازم يزوّد المخزون بـ3 ويقلل مديونية العميل بـ45 (3×15)."""
        self.inventory.refresh_from_db()
        stock_before = self.inventory.quantity  # 90 (100 - 10 المخصومة وقت التأكيد)
        debt_before = AccountTransaction.balance_for(self.client_user)  # 150

        reversal = InvoiceReversal.create_post_delivery_return(
            invoice=self.invoice, items=[(self.invoice_item, 3)],
            actor=self.staff_user, note='صنف زيادة عن الحاجة',
        )

        self.assertEqual(reversal.stage, InvoiceReversal.Stage.POST_DELIVERY)
        self.assertEqual(reversal.amount, Decimal('45.00'))
        self.assertTrue(reversal.return_number.startswith('RTN-'))

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, stock_before + 3)

        self.assertEqual(AccountTransaction.balance_for(self.client_user), debt_before - Decimal('45.00'))

        tx = reversal.account_transactions.first()
        self.assertIsNotNone(tx)
        self.assertTrue(tx.is_return)
        self.assertEqual(tx.display_kind_label, 'مرتجع')
        self.assertEqual(tx.display_reference, reversal.return_number)
        self.assertEqual(tx.amount, Decimal('-45.00'))

        self.invoice_item.refresh_from_db()
        self.assertEqual(self.invoice_item.returned_quantity, 3)
        self.assertEqual(self.invoice_item.remaining_quantity, 7)

        self.assertEqual(InvoiceReversalItem.objects.filter(reversal=reversal).count(), 1)
        self.assertTrue(self.order.logs.filter(note__icontains=reversal.return_number).exists())

    def test_multiple_returns_on_same_invoice_are_cumulative(self):
        """مسموح بأكتر من إشعار مرتجع على نفس الفاتورة بمرور الوقت طالما المجموع لا يتعدى الكمية الأصلية."""
        InvoiceReversal.create_post_delivery_return(
            invoice=self.invoice, items=[(self.invoice_item, 4)], actor=self.staff_user,
        )
        second = InvoiceReversal.create_post_delivery_return(
            invoice=self.invoice, items=[(self.invoice_item, 2)], actor=self.staff_user,
        )

        self.invoice_item.refresh_from_db()
        self.assertEqual(self.invoice_item.returned_quantity, 6)
        self.assertEqual(self.invoice_item.remaining_quantity, 4)
        self.assertEqual(self.invoice.reversals.count(), 2)
        # رقم الإشعار الثاني تسلسلي ومختلف عن الأول
        first = self.invoice.reversals.exclude(pk=second.pk).first()
        self.assertNotEqual(first.return_number, second.return_number)

    def test_return_more_than_remaining_quantity_raises(self):
        """طلب إرجاع كمية أكبر من remaining_quantity لازم يترفض من غير ما يعدّل أي حاجة."""
        with self.assertRaises(ValueError):
            InvoiceReversal.create_post_delivery_return(
                invoice=self.invoice, items=[(self.invoice_item, 11)], actor=self.staff_user,
            )
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 90)  # لم يتغير
        self.assertEqual(AccountTransaction.balance_for(self.client_user), Decimal('150.00'))
        self.assertEqual(self.invoice.reversals.count(), 0)

    def test_return_with_no_positive_quantities_raises(self):
        """طلب مرتجع بدون أي كمية موجبة لازم يترفض."""
        with self.assertRaises(ValueError):
            InvoiceReversal.create_post_delivery_return(
                invoice=self.invoice, items=[(self.invoice_item, 0)], actor=self.staff_user,
            )

    def test_return_number_sequence_covers_pre_delivery_reversal_too(self):
        """رقم الإشعار (return_number) بيتولد لأي InvoiceReversal، حتى المسار
        القديم PRE_DELIVERY (رفض طلب بعد التأكيد)، مش بس POST_DELIVERY الجديد."""
        client2 = User.objects.create_user(
            username='client_return2', email='client_return2@example.com',
            password='testpass123', role=User.Role.CLIENT,
        )
        order2 = Order.objects.create(client=client2)
        OrderItem.objects.create(
            order=order2, product_unit=self.unit, quantity=5,
            public_price=self.unit.unit_price, unit_price=self.unit.unit_price,
        )
        order2.confirm(actor=client2)
        order2.reject(actor=client2, reason='إلغاء تجريبي')

        reversal = order2.invoice.reversals.first()
        self.assertTrue(reversal.return_number.startswith('RTN-'))
        tx = reversal.account_transactions.first()
        self.assertTrue(tx.is_return)
        self.assertEqual(tx.display_kind_label, 'مرتجع')


class ReturnsPermissionTestCase(TestCase):
    """صلاحية 'staff.create_returns' — الأدمن دايمًا عنده، والموظف العادي محتاج يتاخدله يدويًا."""

    def setUp(self):
        self.http = TestClient()
        self.admin = User.objects.create_user(
            username='admin_ret', email='admin_ret@example.com', password='testpass123',
            role=User.Role.ADMIN,
        )
        self.staff_no_perm = User.objects.create_user(
            username='staff_no_perm', email='staff_no_perm@example.com',
            password='testpass123', role=User.Role.WAREHOUSE,
        )
        self.staff_with_perm = User.objects.create_user(
            username='staff_with_perm', email='staff_with_perm@example.com',
            password='testpass123', role=User.Role.WAREHOUSE,
        )
        self.staff_with_perm.user_permissions.add(
            Permission.objects.get(codename='create_returns', content_type__app_label='staff')
        )

        client_user = User.objects.create_user(
            username='client_perm_test', email='client_perm_test@example.com',
            password='testpass123', role=User.Role.CLIENT,
        )
        category = Category.objects.create(name='مستلزمات', slug='supplies')
        product = Product.objects.create(category=category, name_ar='منتج')
        unit = ProductUnit.objects.create(
            product=product, size=ProductUnit.Size.SMALL, name='قطعة',
            qty_in_small=1, unit_price=Decimal('10.00'),
        )
        Inventory.objects.create(product=product, quantity=50, min_quantity=1)
        self.order = Order.objects.create(client=client_user)
        OrderItem.objects.create(
            order=self.order, product_unit=unit, quantity=5,
            public_price=unit.unit_price, unit_price=unit.unit_price,
        )
        self.order.confirm(actor=client_user)

    def test_staff_without_permission_is_denied(self):
        self.http.force_login(self.staff_no_perm)
        response = self.http.get(f'/staff/orders/{self.order.pk}/return/')
        self.assertNotEqual(response.status_code, 200)

    def test_staff_with_permission_can_access(self):
        self.http.force_login(self.staff_with_perm)
        response = self.http.get(f'/staff/orders/{self.order.pk}/return/')
        self.assertEqual(response.status_code, 200)

    def test_admin_can_access_without_explicit_grant(self):
        self.http.force_login(self.admin)
        response = self.http.get(f'/staff/orders/{self.order.pk}/return/')
        self.assertEqual(response.status_code, 200)

    def test_full_return_flow_via_view(self):
        self.http.force_login(self.admin)
        invoice_item = self.order.invoice.items.first()
        response = self.http.post(f'/staff/orders/{self.order.pk}/return/', {
            f'quantity_{invoice_item.pk}': '2',
            'note': 'تالف',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(InvoiceReversal.objects.filter(stage=InvoiceReversal.Stage.POST_DELIVERY).count(), 1)
        invoice_item.refresh_from_db()
        self.assertEqual(invoice_item.returned_quantity, 2)

    def test_return_badge_renders_on_staff_client_detail_page(self):
        """
        تأكيد إن تعديل staff/templates/staff/clients/detail.html (شارة
        "مرتجع" + قسم إشعارات المرتجع تحت الفاتورة) شغال فعليًا من غير
        كسر الصفحة — مش بس تأكيد منطق الموديل زي الاختبارات فوق.
        """
        from accounts.models import ClientProfile, AccountType

        account_type, _ = AccountType.objects.get_or_create(name='جملة تجربة')
        profile = ClientProfile.objects.create(
            user=self.order.client, business_name='محل تجريبي', account_type=account_type,
            address='القاهرة', phone='01000000000',
        )

        InvoiceReversal.create_post_delivery_return(
            invoice=self.order.invoice,
            items=[(self.order.invoice.items.first(), 1)],
            actor=self.admin,
        )

        self.http.force_login(self.admin)
        response = self.http.get(f'/staff/clients/{profile.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'مرتجع')
