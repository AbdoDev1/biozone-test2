"""
اختبارات المرحلة 1 من خطة الدين التقني (ADR-001) — نقل تصدير المنتجات
لـ Celery. راجع products/tasks.py:export_products_task و
staff/views/products/import_export.py:export_products/_status/_download.

نمط الاختبار هنا مطابق لمشكلة استيراد الإكسل الأصلية: مفيش أي اختبار
موجود قبل كده يغطي export_products_task ولا parse_import_workbook_task
(كل الاختبارات الحالية بتغطي منطق الخدمة نفسه، مش طبقة الـ Celery فوقه)،
فده أول اختبار للنمط ده في المشروع. استدعاء الـ task كفنكشن عادي (مش
.delay()) بيشغّله متزامن فورًا زي أي كود Python عادي — مفيش داعي لـ
broker حقيقي ولا CELERY_TASK_ALWAYS_EAGER عشان كده بالتحديد.
"""
import os
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client as HttpClient, TestCase
from django.urls import reverse

from accounts.models import User
from notifications.models import Notification
from products.models import Category, Product, ProductUnit
from products.tasks import export_products_task, export_result_cache_key


def make_admin():
    return User.objects.create_user(
        username='export_admin', email='export_admin@example.com', password='testpass123',
        role=User.Role.ADMIN,
    )


class ExportProductsTaskTestCase(TestCase):
    """اختبار الـ task مباشرة — من غير المرور بأي view."""

    def setUp(self):
        # LocMemCache (راجع config/test_settings.py) مش بيتصفّر تلقائيًا
        # بين اختبارات مختلفة زي قاعدة البيانات (مفيش transaction rollback
        # للكاش) — لازم نصفّره يدويًا عشان نتيجة تصدير من اختبار سابق
        # متأثرش على اختبار تاني.
        cache.clear()
        self.user = make_admin()
        category = Category.objects.create(name='أدوية', slug='meds')
        product = Product.objects.create(category=category, name_ar='دواء تجريبي', code='T-001')
        ProductUnit.objects.create(
            product=product, size='S', name='قطعة', qty_in_small=1,
            unit_price=Decimal('12.50'),
        )

    def tearDown(self):
        # أي ملف اتحفظ فعليًا على القرص وقت الاختبار لازم يتمسح، عشان
        # اختبارات تانية (أو تشغيلات متتالية) متلاقيش ملفات قديمة متراكمة.
        cached = cache.get(export_result_cache_key(self.user.pk))
        if cached and cached.get('file_path') and os.path.exists(cached['file_path']):
            os.remove(cached['file_path'])

    def test_task_saves_workbook_file_and_caches_its_path(self):
        export_products_task(self.user.pk)
        cached = cache.get(export_result_cache_key(self.user.pk))
        self.assertIsNotNone(cached)
        self.assertEqual(cached['status'], 'done')
        self.assertTrue(os.path.exists(cached['file_path']))
        self.assertTrue(cached['file_path'].endswith('.xlsx'))

    def test_task_sends_export_ready_notification_on_success(self):
        export_products_task(self.user.pk)
        notification = Notification.objects.get(recipient=self.user, kind='EXPORT_READY')
        self.assertEqual(notification.url_name, 'staff:export_products_download')

    def test_task_notifies_with_error_and_no_cached_file_path_if_build_fails(self):
        # بنكسر بناء الملف بمحاكاة استثناء غير متوقع (بدل ما نحاول نكسر
        # بيانات المنتج نفسها ونتصادم مع FK constraint) عشان نتأكد إن
        # مسار الفشل بيتعامل صح: مفيش ملف متروك على القرص، والإشعار
        # بيوصل برسالة خطأ لا رابط تحميل.
        with patch(
            'products.services.import_export.build_products_export_workbook',
            side_effect=RuntimeError('محاكاة خطأ غير متوقع'),
        ):
            export_products_task(self.user.pk)

        cached = cache.get(export_result_cache_key(self.user.pk))
        self.assertEqual(cached['status'], 'failed')
        self.assertNotIn('file_path', cached)

        notification = Notification.objects.get(recipient=self.user, kind='EXPORT_READY')
        self.assertEqual(notification.url_name, 'staff:export_products')


class ExportProductsViewFlowTestCase(TestCase):
    """اختبار end-to-end عبر الـ views: طلب التصدير -> الحالة -> التحميل."""

    def setUp(self):
        cache.clear()
        self.http = HttpClient()
        self.user = make_admin()
        self.http.force_login(self.user)
        category = Category.objects.create(name='أدوية', slug='meds')
        Product.objects.create(category=category, name_ar='دواء تجريبي', code='T-002')

    def tearDown(self):
        cached = cache.get(export_result_cache_key(self.user.pk))
        if cached and cached.get('file_path') and os.path.exists(cached['file_path']):
            os.remove(cached['file_path'])

    def test_export_products_returns_processing_page_immediately(self):
        response = self.http.get(reverse('staff:export_products'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'staff/products/export_processing.html')
        # الـ task بينفذ فورًا (استدعاء مباشر مش عبر broker)، فالنتيجة
        # المفروض تبقى جاهزة في الكاش فعلًا لحظة رجوع الصفحة.
        self.assertIsNotNone(cache.get(export_result_cache_key(self.user.pk)))

    def test_status_endpoint_reports_ready_after_export(self):
        self.http.get(reverse('staff:export_products'))
        response = self.http.get(reverse('staff:export_products_status'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ready': True, 'failed': False})

    def test_status_endpoint_reports_not_ready_before_any_export(self):
        response = self.http.get(reverse('staff:export_products_status'))
        self.assertEqual(response.json(), {'ready': False, 'failed': False})

    def test_download_returns_xlsx_and_deletes_file_and_cache(self):
        self.http.get(reverse('staff:export_products'))
        cached = cache.get(export_result_cache_key(self.user.pk))
        file_path = cached['file_path']

        response = self.http.get(reverse('staff:export_products_download'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        self.assertIn('attachment', response['Content-Disposition'])

        # رابط استخدام واحد: الملف والكاش لازم يتمسحوا بعد التحميل مباشرة.
        self.assertFalse(os.path.exists(file_path))
        self.assertIsNone(cache.get(export_result_cache_key(self.user.pk)))

    def test_download_without_prior_export_redirects_with_error(self):
        response = self.http.get(reverse('staff:export_products_download'), follow=True)
        self.assertRedirects(response, reverse('staff:export_products'))
        messages = list(response.context['messages'])
        self.assertTrue(any('مفيش ملف تصدير جاهز' in str(m) for m in messages))

    def test_download_twice_second_time_redirects_with_error(self):
        self.http.get(reverse('staff:export_products'))
        self.http.get(reverse('staff:export_products_download'))
        response = self.http.get(reverse('staff:export_products_download'), follow=True)
        self.assertRedirects(response, reverse('staff:export_products'))

    def test_warehouse_without_permission_is_redirected(self):
        warehouse_user = User.objects.create_user(
            username='no_perm_export', email='no_perm_export@example.com',
            password='testpass123', role=User.Role.WAREHOUSE,
        )
        client = HttpClient()
        client.force_login(warehouse_user)
        response = client.get(reverse('staff:export_products'))
        self.assertRedirects(response, reverse('staff:dashboard'))
