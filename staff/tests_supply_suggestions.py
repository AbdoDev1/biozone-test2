"""
اختبارات مرحلة 7 (صفحة "مقترحات التوريد" — ROADMAP.md): تجميع الأصناف
تحت الحد الأدنى في صفحة واحدة، وحساب الكمية المقترح توريدها.
"""
from django.test import Client as HttpClient, TestCase
from django.urls import reverse

from accounts.models import User
from inventory.models import Inventory
from products.models import Category, Product


def make_admin():
    return User.objects.create_user(
        username='admin1', email='admin1@example.com',
        password='testpass123', role=User.Role.ADMIN,
    )


class InventorySuggestedReorderTestCase(TestCase):
    """اختبارات الخاصية على الموديل مباشرة — مستقلة عن أي view."""

    def setUp(self):
        self.category = Category.objects.create(name='أدوية', slug='meds')
        self.product = Product.objects.create(category=self.category, name_ar='دواء تجريبي')

    def test_suggested_qty_is_zero_when_not_low(self):
        inv = Inventory.objects.create(product=self.product, quantity=100, min_quantity=5)
        self.assertEqual(inv.suggested_reorder_qty, 0)

    def test_suggested_qty_equals_deficit_when_low(self):
        inv = Inventory.objects.create(product=self.product, quantity=3, min_quantity=10)
        self.assertTrue(inv.is_low)
        self.assertEqual(inv.suggested_reorder_qty, 7)

    def test_suggested_qty_accounts_for_reserved(self):
        """المتاح = الرصيد - المحجوز، فالمقترح لازم يحسب على المتاح مش الرصيد الكلي."""
        inv = Inventory.objects.create(product=self.product, quantity=20, reserved=15, min_quantity=10)
        self.assertEqual(inv.available, 5)
        self.assertEqual(inv.suggested_reorder_qty, 5)


class SupplySuggestionsViewTestCase(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='أدوية', slug='meds')
        self.low_product = Product.objects.create(category=self.category, name_ar='صنف منخفض')
        self.ok_product = Product.objects.create(category=self.category, name_ar='صنف كافي')
        Inventory.objects.create(product=self.low_product, quantity=2, min_quantity=10)
        Inventory.objects.create(product=self.ok_product, quantity=100, min_quantity=5)

        self.http = HttpClient()
        self.http.force_login(make_admin())

    def test_only_low_stock_items_appear(self):
        response = self.http.get(reverse('staff:reports_supply_suggestions'))
        self.assertEqual(response.status_code, 200)
        products_shown = [item.product_id for item in response.context['items']]
        self.assertIn(self.low_product.pk, products_shown)
        self.assertNotIn(self.ok_product.pk, products_shown)

    def test_category_filter(self):
        other_category = Category.objects.create(name='مواد غذائية', slug='food')
        other_low_product = Product.objects.create(category=other_category, name_ar='صنف تاني منخفض')
        Inventory.objects.create(product=other_low_product, quantity=1, min_quantity=10)

        response = self.http.get(reverse('staff:reports_supply_suggestions'), {'category': self.category.pk})
        products_shown = [item.product_id for item in response.context['items']]
        self.assertIn(self.low_product.pk, products_shown)
        self.assertNotIn(other_low_product.pk, products_shown)

    def test_client_role_is_redirected(self):
        client_user = User.objects.create_user(
            username='client1', email='client1@example.com',
            password='testpass123', role=User.Role.CLIENT,
        )
        client_http = HttpClient()
        client_http.force_login(client_user)
        response = client_http.get(reverse('staff:reports_supply_suggestions'))
        self.assertEqual(response.status_code, 302)
