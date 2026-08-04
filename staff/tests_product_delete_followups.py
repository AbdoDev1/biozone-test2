"""
اختبار تراجعي: حذف منتج (product_delete) لازم يمسح متابعاته (FollowUp)
زي ما بيمسح سجلات نشاطه (ActivityLog) بالظبط — عشان منمنعش orphaned rows
لو منتج ليه متابعة مجدولة (followup_panel معروض في فورم المنتج) وبعدين
اتحذف حذف فعلي (منتج من غير مخزون).
"""
from django.contrib.contenttypes.models import ContentType
from django.test import Client as HttpClient, TestCase
from django.urls import reverse

from accounts.models import AccountType, ClientProfile, User
from followups.models import FollowUp
from followups.services import create_followup
from products.models import Category, Product


def make_admin():
    return User.objects.create_user(
        username='admin1', email='admin1@example.com', password='testpass123',
        role=User.Role.ADMIN,
    )


def make_client_profile():
    account_type, _ = AccountType.objects.get_or_create(name='جملة')
    user = User.objects.create_user(
        username='client1', email='client1@example.com', password='testpass123',
        role=User.Role.CLIENT,
    )
    return ClientProfile.objects.create(
        user=user, business_name='محل تجريبي', account_type=account_type,
        address='القاهرة', phone='01000000000',
    )


class ProductDeleteCleansUpFollowUpsTestCase(TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.http = HttpClient()
        self.http.force_login(self.admin)
        self.category = Category.objects.create(name='أدوية', slug='meds')
        self.product = Product.objects.create(
            category=self.category, name_ar='دواء تجريبي', name_en='Test Med',
            manufacturer='شركة تجريبية', barcode='987654321', is_active=True,
        )

    def test_deleting_product_without_stock_removes_its_followups(self):
        from django.utils import timezone

        create_followup(
            self.product, activity_type=FollowUp.ActivityType.CALL,
            due_date=timezone.localdate(), assigned_to=self.admin,
        )
        content_type = ContentType.objects.get_for_model(Product)
        self.assertTrue(
            FollowUp.objects.filter(
                content_type=content_type, object_id=self.product.pk,
            ).exists()
        )

        self.http.post(reverse('staff:product_delete', args=[self.product.pk]))

        self.assertFalse(Product.objects.filter(pk=self.product.pk).exists())
        self.assertFalse(
            FollowUp.objects.filter(
                content_type=content_type, object_id=self.product.pk,
            ).exists(),
            'من المفروض FollowUp يتمسح مع المنتج، مش يفضل orphaned',
        )
