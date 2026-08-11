"""
مرحلة 6 (شاشة المراجعة التفاعلية بالسكانر) — اختبارات على staff.views.orders.order_scan_review:
صلاحيات الوصول، القيد على حالة الطلب (PENDING/NEEDS_APPROVAL بس)، مسار المسح
الناجح/الفاشل/idempotent، التعليم اليدوي، واستقلالها التام عن منطق confirm/reject.
"""
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.test import Client as HttpClient, TestCase
from django.urls import reverse

from accounts.models import User
from inventory.models import Inventory
from orders.models import Order, OrderItem
from products.models import Category, Product, ProductUnit


def make_admin(username='admin1'):
    return User.objects.create_user(
        username=username, email=f'{username}@example.com',
        password='testpass123', role=User.Role.ADMIN, status=User.Status.ACTIVE,
    )


def make_warehouse(username='wh1', with_change_perm=True):
    user = User.objects.create_user(
        username=username, email=f'{username}@example.com',
        password='testpass123', role=User.Role.WAREHOUSE, status=User.Status.ACTIVE,
    )
    perms = ['view_order']
    if with_change_perm:
        perms.append('change_order')
    for codename in perms:
        user.user_permissions.add(Permission.objects.get(codename=codename, content_type__app_label='orders'))
    return user


class OrderScanReviewViewTestCase(TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.client_user = User.objects.create_user(
            username='client1', email='c1@example.com', password='x', role=User.Role.CLIENT,
        )
        category = Category.objects.create(name='مواد غذائية', slug='food')
        self.product = Product.objects.create(category=category, name_ar='منتج تجريبي', barcode='12345')
        self.unit = ProductUnit.objects.create(
            product=self.product, size=ProductUnit.Size.SMALL, name='قطعة',
            qty_in_small=1, unit_price=Decimal('10.00'),
        )
        Inventory.objects.create(product=self.product, quantity=100, min_quantity=5)

        self.order = Order.objects.create(client=self.client_user)
        self.item = OrderItem.objects.create(
            order=self.order, product_unit=self.unit, quantity=3,
            public_price=self.unit.unit_price, unit_price=self.unit.unit_price,
        )
        self.service_item = OrderItem.objects.create(
            order=self.order, is_service_fee=True, service_name='مصاريف توصيل',
            quantity=1, public_price=Decimal('20.00'), unit_price=Decimal('20.00'),
        )

        self.http = HttpClient()
        self.url = reverse('staff:order_scan_review', args=[self.order.pk])

    # ---- وصول/صلاحيات ----

    def test_requires_login(self):
        response = self.http.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_get_allowed_with_view_permission_only(self):
        warehouse_view_only = make_warehouse('wh_view_only', with_change_perm=False)
        self.http.force_login(warehouse_view_only)
        response = self.http.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_post_without_change_permission_is_rejected(self):
        warehouse_view_only = make_warehouse('wh_view_only2', with_change_perm=False)
        self.http.force_login(warehouse_view_only)
        self.http.post(self.url, {'action': 'scan_barcode', 'barcode': '12345'})
        self.item.refresh_from_db()
        self.assertFalse(self.item.scanned)

    # ---- القيد على حالة الطلب ----

    def test_redirects_away_when_order_not_pending_or_needs_approval(self):
        self.order.status = Order.Status.CONFIRMED
        self.order.save()
        self.http.force_login(self.admin)
        response = self.http.get(self.url)
        self.assertRedirects(response, reverse('staff:order_detail', args=[self.order.pk]))

    def test_accessible_when_needs_approval(self):
        self.order.status = Order.Status.NEEDS_APPROVAL
        self.order.save()
        self.http.force_login(self.admin)
        response = self.http.get(self.url)
        self.assertEqual(response.status_code, 200)

    # ---- مسار المسح ----

    def test_scan_correct_barcode_marks_item_found(self):
        self.http.force_login(self.admin)
        response = self.http.post(
            self.url, {'action': 'scan_barcode', 'barcode': '12345'},
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertTrue(self.item.scanned)
        self.assertContains(response, 'تم تسجيل')

    def test_scan_same_barcode_twice_is_idempotent(self):
        self.http.force_login(self.admin)
        self.http.post(self.url, {'action': 'scan_barcode', 'barcode': '12345'}, HTTP_HX_REQUEST='true')
        response = self.http.post(self.url, {'action': 'scan_barcode', 'barcode': '12345'}, HTTP_HX_REQUEST='true')
        self.item.refresh_from_db()
        self.assertTrue(self.item.scanned)
        self.assertContains(response, 'اتفحص بالفعل')

    def test_scan_unknown_barcode_does_not_mark_anything(self):
        self.http.force_login(self.admin)
        response = self.http.post(self.url, {'action': 'scan_barcode', 'barcode': '99999'}, HTTP_HX_REQUEST='true')
        self.item.refresh_from_db()
        self.assertFalse(self.item.scanned)
        self.assertContains(response, 'مش موجود في هذا الطلب')

    def test_service_fee_item_excluded_from_scan_panel_totals(self):
        self.http.force_login(self.admin)
        response = self.http.get(self.url)
        self.assertEqual(response.context['total_count'], 1)  # الصنف الخدمي مستبعد

    # ---- التعليم اليدوي ----

    def test_manual_toggle_marks_and_unmarks(self):
        self.http.force_login(self.admin)
        self.http.post(self.url, {'action': 'toggle_manual', 'item_id': self.item.pk}, HTTP_HX_REQUEST='true')
        self.item.refresh_from_db()
        self.assertTrue(self.item.scanned)

        self.http.post(self.url, {'action': 'toggle_manual', 'item_id': self.item.pk}, HTTP_HX_REQUEST='true')
        self.item.refresh_from_db()
        self.assertFalse(self.item.scanned)

    def test_cannot_manually_toggle_service_fee_item(self):
        self.http.force_login(self.admin)
        response = self.http.post(
            self.url, {'action': 'toggle_manual', 'item_id': self.service_item.pk}, HTTP_HX_REQUEST='true',
        )
        self.assertEqual(response.status_code, 404)

    # ---- استقلالية عن منطق حالة الطلب ----

    def test_scanning_never_changes_order_status_or_stock(self):
        self.http.force_login(self.admin)
        inventory = Inventory.objects.get(product=self.product)
        original_status = self.order.status
        original_qty = inventory.quantity

        self.http.post(self.url, {'action': 'scan_barcode', 'barcode': '12345'}, HTTP_HX_REQUEST='true')

        self.order.refresh_from_db()
        inventory.refresh_from_db()
        self.assertEqual(self.order.status, original_status)
        self.assertEqual(inventory.quantity, original_qty)
