import os
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.validators import FileExtensionValidator
from django.db import models

from .validators import ALLOWED_IMAGE_EXTENSIONS, validate_image_size

# أبعاد الصورة المصغّرة (المرحلة 3) — نفس الأبعاد المذكورة في STUDIO_PLAN.md
# كمثال (300×300)، تُحفظ بالتناسب (aspect ratio) مش قص مربّع إجباري.
THUMBNAIL_SIZE = (300, 300)


class StudioFolder(models.Model):
    """
    مجلد بسيط لتنظيم صور الاستوديو (المرحلة 6). كل صورة في مجلد واحد
    بس — الافتراض الأبسط المذكور في STUDIO_PLAN.md قسم 4 (قرارات مفتوحة)،
    نقطة 1، بدل نظام تصنيفات متعددة زي الوسوم (Tag).

    حذف مجلد **لا يحذف** الصور اللي جواه (`StudioImage.folder` بـ
    `SET_NULL`، نفس فلسفة حذف الصورة نفسها من منتج/قسم في المرحلة 5) —
    الصور بترجع "بلا مجلد" بس.
    """
    name = models.CharField(max_length=100, unique=True, verbose_name='الاسم')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='تاريخ الإنشاء')

    class Meta:
        verbose_name = 'مجلد استوديو'
        verbose_name_plural = 'مجلدات الاستوديو'
        ordering = ['name']

    def __str__(self):
        return self.name


class StudioImage(models.Model):
    """
    صورة مركزية في "الاستوديو" — مصدر واحد للصور المستخدمة عبر النظام
    (منتجات، أقسام، ولاحقًا أي كيان تاني محتاج صورة). راجع STUDIO_PLAN.md
    للسياق الكامل والقرارات المعمارية.

    المعرّف الفريد هو الـ Primary Key الرقمي التلقائي العادي (id) — مش
    مبني على اسم الملف (قرار رقم 4 و5 في الخطة): أسرع في المطابقة (index
    مباشر)، وبلا أي تطبيع نص أو قفل (lock) وقت الرفع الجماعي.

    مفيش فحص تكرار محتوى (hash) وقت الرفع (قرار رقم 6) — نفس الصورة ممكن
    تترفع أكتر من مرة وتاخد معرّفات منفصلة، وده تريد-أوف مقبول لصالح
    السرعة على حساب مساحة تخزين أكبر شوية بمرور الوقت (راجع قسم 8 في
    الخطة لملاحظات الأمان/الأداء الإضافية).

    حقل thumbnail موجود من دلوقتي (فاضي مؤقتًا) بس هيتفعّل فعليًا في
    المرحلة 3 (توليد تلقائي بـ Pillow وقت الحفظ).
    """
    image = models.ImageField(
        upload_to='studio/%Y/%m/',
        validators=[FileExtensionValidator(ALLOWED_IMAGE_EXTENSIONS), validate_image_size],
        verbose_name='الصورة',
    )
    thumbnail = models.ImageField(
        upload_to='studio/thumbnails/%Y/%m/',
        blank=True,
        null=True,
        verbose_name='الصورة المصغّرة',
    )
    # اسم الملف الأصلي كـ metadata للعرض بس (قرار رقم 5) — عمدًا بلا
    # unique=True، عشان يسمح بتكرار الاسم (شائع من الموبايل، زي
    # IMG_2024.jpg) بلا أي فحص أو رفض وقت الرفع.
    original_filename = models.CharField(max_length=255, blank=True, verbose_name='اسم الملف الأصلي')

    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name='تاريخ الرفع')
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='studio_images_uploaded',
        verbose_name='رفعها',
    )
    # تنظيم اختياري بمجلدات (المرحلة 6) — صورة من غير مجلد (null) تعتبر
    # "بلا مجلد" في فلتر المعرض، مش خطأ. SET_NULL عشان حذف مجلد ميمسحش
    # الصور اللي جواه (راجع StudioFolder docstring).
    folder = models.ForeignKey(
        StudioFolder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='images',
        verbose_name='المجلد',
    )

    class Meta:
        verbose_name = 'صورة استوديو'
        verbose_name_plural = 'صور الاستوديو'
        ordering = ['-uploaded_at', '-id']
        indexes = [
            models.Index(fields=['-uploaded_at']),
            models.Index(fields=['folder']),
        ]

    def __str__(self):
        return self.original_filename or f'صورة استوديو #{self.pk}'

    def get_usage(self):
        """
        بترجع (منتجات، أقسام) الصورة دي مربوطة بيها حاليًا.

        المرحلة 8 (STUDIO_PLAN.md) نفّذت الربط الفعلي: Product.image
        وCategory.image بقوا ForeignKey على StudioImage، فالعلاقة العكسية
        بقت متاحة فعليًا. related_name='products'/'categories' اتحددوا
        صراحة على الحقلين (بدل الاعتماد على product_set/category_set
        الافتراضي المذكور كمثال في نص الخطة الأصلي) عشان يتماشوا مع باقي
        أسماء العلاقات في المشروع (زي category.products، folder.images).

        فلتر مستخدمة/غير مستخدمة (المرحلة 4) وشاشة الحذف (المرحلة 5)
        بيستخدموا الميثود دي زي ما هي، بلا أي تعديل إضافي مطلوب منهم —
        الآلية كانت جاهزة بالكامل من وقتها زي ما كان متوقع.
        """
        return list(self.products.all()), list(self.categories.all())

    @property
    def is_used(self):
        products, categories = self.get_usage()
        return bool(products) or bool(categories)

    def save(self, *args, **kwargs):
        # تسجيل اسم الملف الأصلي تلقائيًا لو مش متحدد صراحة — يفيد وقت
        # الرفع الجماعي (المرحلة 3) من غير ما نحتاج فورم يطلبه يدويًا.
        if not self.original_filename and self.image:
            self.original_filename = os.path.basename(self.image.name)

        # توليد الـ thumbnail بس أول مرة (سجل جديد لسه معملوش save، وعنده
        # صورة، وما عندوش thumbnail موجود بالفعل) — مش في كل save لاحق
        # (زي تعديل uploaded_by مثلًا)، عشان منولّدش thumbnail من تاني كل
        # مرة من غير داعي.
        needs_thumbnail = self.pk is None and self.image and not self.thumbnail

        super().save(*args, **kwargs)

        if needs_thumbnail:
            self._generate_thumbnail()
            # حفظ تاني بس لحقل الـ thumbnail — بلا استدعاء save() الكامل
            # تاني (يمنع أي تكرار غير لازم لمنطق التسجيل التلقائي فوق).
            super().save(update_fields=['thumbnail'])

    def _generate_thumbnail(self):
        """
        بيولّد نسخة مصغّرة بالتناسب (Image.thumbnail بيحافظ على aspect
        ratio، مش قص مربّع) بنفس امتداد الصورة الأصلية. أي خطأ فتح/تحويل
        (ملف صورة تالف نجح في اجتياز الـ FileExtensionValidator بالامتداد
        بس) بيتم تجاهله بهدوء — الصورة الأصلية بتفضل محفوظة عادي بلا
        thumbnail، بدل ما يفشل الرفع كله.
        """
        try:
            from PIL import Image

            self.image.seek(0)
            img = Image.open(self.image)
            img_format = (img.format or 'JPEG').upper()
            if img_format == 'JPEG' and img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            img.thumbnail(THUMBNAIL_SIZE, Image.LANCZOS)

            buffer = BytesIO()
            save_kwargs = {'quality': 85} if img_format in ('JPEG', 'WEBP') else {}
            img.save(buffer, format=img_format, **save_kwargs)

            ext = os.path.splitext(self.image.name)[1] or '.jpg'
            base_name = os.path.splitext(os.path.basename(self.image.name))[0]
            self.thumbnail.save(f'{base_name}_thumb{ext}', ContentFile(buffer.getvalue()), save=False)
        except Exception:
            pass
