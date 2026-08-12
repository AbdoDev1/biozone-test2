"""
اختبارات مرحلة 10 (STUDIO_PLAN.md) — تغطية دائمة بدل التحقق اليدوي/المؤقت
اللي كان بيتعمل قبل تسليم كل مرحلة من 1 لـ9. بتغطي: الرفع، توليد
الـ thumbnail، الحذف مع SET_NULL، فلتر الاستخدام، ومنطق استيراد الإكسل
الجديد (عمود studio_image_id، مرحلة 9).
"""
import shutil
import tempfile
from io import BytesIO

import openpyxl
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client as HttpClient, TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from products.models import Category, Product
from products.services import import_export as svc
from products.services.import_export import REQUIRED_IMPORT_HEADERS

from .models import StudioFolder, StudioImage

_MEDIA_ROOT = tempfile.mkdtemp(prefix='studio_tests_media_')


def make_staff(role=User.Role.WAREHOUSE, username=None):
    username = username or f'staff_{role.lower()}'
    return User.objects.create_user(
        username=username, email=f'{username}@example.com',
        password='testpass123', role=role, status=User.Status.ACTIVE,
    )


def make_image_file(name='photo.jpg', fmt='JPEG', size=(120, 80)):
    """صورة PNG/JPEG حقيقية صغيرة في الذاكرة — لازم تكون صورة فعلية
    (مش بايتات عشوائية) عشان Pillow يقدر يفتحها ويولّد thumbnail منها."""
    from PIL import Image

    buffer = BytesIO()
    Image.new('RGB', size, color=(120, 160, 200)).save(buffer, format=fmt)
    buffer.seek(0)
    content_type = 'image/jpeg' if fmt == 'JPEG' else f'image/{fmt.lower()}'
    return SimpleUploadedFile(name, buffer.read(), content_type=content_type)


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class StudioImageModelTestCase(TestCase):
    """رفع صورة وتوليد thumbnail (المرحلة 1 و3)."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)

    def test_upload_generates_thumbnail_automatically(self):
        image = StudioImage.objects.create(image=make_image_file())
        self.assertTrue(image.thumbnail)
        self.assertTrue(image.thumbnail.name)

    def test_original_filename_auto_recorded_from_uploaded_file(self):
        image = StudioImage.objects.create(image=make_image_file(name='IMG_2024.jpg'))
        self.assertEqual(image.original_filename, 'IMG_2024.jpg')

    def test_duplicate_original_filename_allowed(self):
        """اسم الملف مش unique (قرار رقم 5) — صورتين بنفس الاسم يترفعوا
        بمعرّفين منفصلين بلا أي رفض."""
        first = StudioImage.objects.create(image=make_image_file(name='IMG_2024.jpg'))
        second = StudioImage.objects.create(image=make_image_file(name='IMG_2024.jpg'))
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(
            StudioImage.objects.filter(original_filename='IMG_2024.jpg').count(), 2,
        )

    def test_new_image_is_unused_by_default(self):
        image = StudioImage.objects.create(image=make_image_file())
        self.assertFalse(image.is_used)
        products, categories = image.get_usage()
        self.assertEqual(products, [])
        self.assertEqual(categories, [])

    def test_second_save_does_not_regenerate_thumbnail(self):
        """save() لاحق (زي تعديل uploaded_by) ميعملش thumbnail تاني —
        بس أول مرة (self.pk is None)."""
        image = StudioImage.objects.create(image=make_image_file())
        original_thumb_name = image.thumbnail.name
        image.original_filename = 'renamed.jpg'
        image.save(update_fields=['original_filename'])
        image.refresh_from_db()
        self.assertEqual(image.thumbnail.name, original_thumb_name)


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class StudioImageUsageAndDeletionTestCase(TestCase):
    """حالة الاستخدام (المرحلة 4) والحذف مع SET_NULL (المرحلة 5 و8)."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.image = StudioImage.objects.create(image=make_image_file())
        self.category = Category.objects.create(name='أدوات طبية', slug='medical-tools')

    def _make_product(self, name_ar='صنف تجريبي', code='P-1'):
        return Product.objects.create(
            name_ar=name_ar, category=self.category, is_active=True, code=code,
            image=self.image,
        )

    def test_image_linked_to_product_marked_as_used(self):
        product = self._make_product()
        products, categories = self.image.get_usage()
        self.assertEqual(products, [product])
        self.assertEqual(categories, [])
        self.assertTrue(self.image.is_used)

    def test_image_linked_to_category_marked_as_used(self):
        self.category.image = self.image
        self.category.save(update_fields=['image'])
        products, categories = self.image.get_usage()
        self.assertEqual(categories, [self.category])
        self.assertTrue(self.image.is_used)

    def test_same_image_linked_to_multiple_products(self):
        """نفس الصورة ممكن تتربط بأكتر من منتج (قرار رقم 6)."""
        first = self._make_product(name_ar='صنف أول', code='P-1')
        second = self._make_product(name_ar='صنف ثاني', code='P-2')
        products, _ = self.image.get_usage()
        self.assertCountEqual(products, [first, second])

    def test_deleting_image_sets_product_image_to_null(self):
        """حذف صورة مربوطة بمنتج مش ممنوع — الربط بيختفي بس (SET_NULL،
        قرار رقم 8) بلا ما يفشل الحذف أو يمسح المنتج."""
        product = self._make_product()
        self.image.delete()
        product.refresh_from_db()
        self.assertIsNone(product.image_id)
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())

    def test_deleting_image_sets_category_image_to_null(self):
        self.category.image = self.image
        self.category.save(update_fields=['image'])
        self.image.delete()
        self.category.refresh_from_db()
        self.assertIsNone(self.category.image_id)

    def test_deleting_folder_does_not_delete_images_inside(self):
        folder = StudioFolder.objects.create(name='منتجات جديدة')
        self.image.folder = folder
        self.image.save(update_fields=['folder'])
        folder.delete()
        self.image.refresh_from_db()
        self.assertIsNone(self.image.folder_id)
        self.assertTrue(StudioImage.objects.filter(pk=self.image.pk).exists())


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class StudioGalleryViewTestCase(TestCase):
    """صلاحية الوصول (المرحلة 2) وفلتر الاستخدام (المرحلة 4) في شاشة المعرض."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.client = HttpClient()
        self.admin = make_staff(role=User.Role.ADMIN, username='admin1')
        self.warehouse_no_perm = make_staff(role=User.Role.WAREHOUSE, username='wh_no_perm')
        self.category = Category.objects.create(name='أدوات', slug='tools')

    def test_employee_without_permission_redirected(self):
        self.client.force_login(self.warehouse_no_perm)
        response = self.client.get(reverse('staff:studio'))
        self.assertRedirects(response, reverse('staff:dashboard'))

    def test_admin_can_access_gallery(self):
        """الأدمن superuser تلقائيًا (User.save)، فعنده كل الصلاحيات دايمًا."""
        self.client.force_login(self.admin)
        response = self.client.get(reverse('staff:studio'))
        self.assertEqual(response.status_code, 200)

    def test_usage_filter_separates_used_and_unused_images(self):
        used_image = StudioImage.objects.create(image=make_image_file(name='used.jpg'))
        unused_image = StudioImage.objects.create(image=make_image_file(name='unused.jpg'))
        Product.objects.create(
            name_ar='صنف', category=self.category, is_active=True,
            code='P-USED', image=used_image,
        )

        self.client.force_login(self.admin)

        used_response = self.client.get(reverse('staff:studio'), {'usage': 'used'})
        used_ids = {img.pk for img in used_response.context['images']}
        self.assertIn(used_image.pk, used_ids)
        self.assertNotIn(unused_image.pk, used_ids)

        unused_response = self.client.get(reverse('staff:studio'), {'usage': 'unused'})
        unused_ids = {img.pk for img in unused_response.context['images']}
        self.assertIn(unused_image.pk, unused_ids)
        self.assertNotIn(used_image.pk, unused_ids)


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class StudioUploadViewTestCase(TestCase):
    """رفع فردي/جماعي عبر الفورم (المرحلة 3)."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.client = HttpClient()
        self.admin = make_staff(role=User.Role.ADMIN, username='admin2')
        self.client.force_login(self.admin)

    def test_bulk_upload_creates_separate_rows_for_each_file(self):
        files = [
            make_image_file(name='a.jpg'),
            make_image_file(name='b.jpg'),
            make_image_file(name='a.jpg'),  # نفس الاسم مرة تانية عمدًا
        ]
        self.client.post(reverse('staff:studio_upload'), {'images': files})
        self.assertEqual(StudioImage.objects.count(), 3)

    def test_disallowed_extension_skipped_but_rest_of_batch_succeeds(self):
        bad_file = SimpleUploadedFile('not_an_image.txt', b'hello', content_type='text/plain')
        files = [make_image_file(name='good.jpg'), bad_file]
        response = self.client.post(reverse('staff:studio_upload'), {'images': files}, follow=True)
        self.assertEqual(StudioImage.objects.count(), 1)
        messages = [str(m) for m in response.context['messages']]
        self.assertTrue(any('not_an_image.txt' in m for m in messages))


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class StudioDeleteViewTestCase(TestCase):
    """حذف فردي/جماعي عبر الفورم (المرحلة 5)."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.client = HttpClient()
        self.admin = make_staff(role=User.Role.ADMIN, username='admin3')
        self.client.force_login(self.admin)

    def test_bulk_delete_removes_selected_images_only(self):
        keep = StudioImage.objects.create(image=make_image_file(name='keep.jpg'))
        remove_a = StudioImage.objects.create(image=make_image_file(name='remove_a.jpg'))
        remove_b = StudioImage.objects.create(image=make_image_file(name='remove_b.jpg'))

        self.client.post(reverse('staff:studio_delete'), {
            'image_ids': [remove_a.pk, remove_b.pk],
        })

        self.assertEqual(list(StudioImage.objects.all()), [keep])

    def test_delete_without_selection_shows_warning_and_deletes_nothing(self):
        StudioImage.objects.create(image=make_image_file())
        response = self.client.post(reverse('staff:studio_delete'), {}, follow=True)
        self.assertEqual(StudioImage.objects.count(), 1)
        messages = [str(m) for m in response.context['messages']]
        self.assertTrue(any('لازم تحدد صورة' in m for m in messages))


@override_settings(MEDIA_ROOT=_MEDIA_ROOT)
class ExcelImportStudioImageColumnTestCase(TestCase):
    """
    منطق عمود studio_image_id في استيراد الإكسل (المرحلة 9): معرّف صحيح
    بيتربط، معرّف غلط بيطلع تحذير سطر بلا ما يوقف باقي الدفعة، عمود فاضي
    بلا أي تحذير خالص (الانحراف المتعمّد الموثّق في STUDIO_PLAN.md).
    """

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.category = Category.objects.create(name='أدوات', slug='tools')
        self.image = StudioImage.objects.create(image=make_image_file())

    def _build_workbook(self, rows):
        """rows: list of (name_ar, studio_image_id) — باقي الأعمدة المطلوبة
        بتتملى بقيم افتراضية صالحة."""
        headers = REQUIRED_IMPORT_HEADERS + ['category_slug', 'code', 'studio_image_id']
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(headers)
        for i, (name_ar, studio_image_id) in enumerate(rows, start=1):
            ws.append([name_ar, 'قطعة', 1, 10.0, self.category.slug, f'CODE-{i}', studio_image_id])
        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        return buffer

    def test_valid_studio_image_id_links_successfully(self):
        wb = self._build_workbook([('صنف صحيح', str(self.image.pk))])
        rows, errors, error_message = svc.read_import_workbook(wb, max_rows=1000)
        self.assertIsNone(error_message)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]['studio_image_id'], self.image.pk)

    def test_invalid_studio_image_id_warns_without_failing_batch(self):
        wb = self._build_workbook([
            ('صنف صحيح', str(self.image.pk)),
            ('صنف معرف غلط', '999999'),
        ])
        rows, errors, error_message = svc.read_import_workbook(wb, max_rows=1000)
        self.assertIsNone(error_message)
        self.assertEqual(len(rows), 2, 'الصنف صاحب المعرّف الغلط يفضل موجود في الدفعة، مش مرفوض بالكامل')
        self.assertEqual(len(errors), 1)
        self.assertIn('999999', errors[0])

        by_name = {r['name_ar']: r for r in rows}
        self.assertEqual(by_name['صنف صحيح']['studio_image_id'], self.image.pk)
        self.assertIsNone(by_name['صنف معرف غلط']['studio_image_id'])

    def test_empty_studio_image_id_column_produces_no_warning(self):
        """عمود فاضي = مفيش تغيير مطلوب (سلوك طبيعي)، مش خطأ — بخلاف
        المعرّف الغلط فوق، مفيش أي تحذير هنا خالص."""
        wb = self._build_workbook([('صنف بلا صورة', '')])
        rows, errors, error_message = svc.read_import_workbook(wb, max_rows=1000)
        self.assertIsNone(error_message)
        self.assertEqual(errors, [])
        self.assertIsNone(rows[0]['studio_image_id'])

    def test_non_numeric_studio_image_id_warns(self):
        wb = self._build_workbook([('صنف نص غلط', 'ABC')])
        rows, errors, error_message = svc.read_import_workbook(wb, max_rows=1000)
        self.assertIsNone(error_message)
        self.assertEqual(len(errors), 1)
        self.assertIn('غير صالح', errors[0])
        self.assertIsNone(rows[0]['studio_image_id'])

    def test_commit_product_links_validated_studio_image_id(self):
        wb = self._build_workbook([('صنف صحيح', str(self.image.pk))])
        rows, errors, error_message = svc.read_import_workbook(wb, max_rows=1000)
        row_data = rows[0]
        self.assertEqual(row_data['action'], 'create')

        created, _ = svc.commit_product(row_data, target_pk=None, user=None, account_types_by_pk={})
        self.assertEqual(created, True)
        product = Product.objects.get(name_ar='صنف صحيح')
        self.assertEqual(product.image_id, self.image.pk)
