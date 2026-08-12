# نُقل المحتوى الفعلي لهذا الملف إلى studio/validators.py (المرحلة 1 من
# خطة الاستوديو — راجع STUDIO_PLAN.md، القرار رقم 7 وملاحظة الأمان في
# المرحلة 8) عشان يبقى مصدر واحد للتحقق من الصور يُستخدم من
# studio.StudioImage مباشرة، ولاحقًا من Product/Category بعد تحويلهم
# لـ ForeignKey. الاستيراد هنا موجود بس للتوافق الخلفي مع أي كود قديم
# بيستورد validate_image_size من products.validators مباشرة — مش نسخة
# تانية من المنطق.
from studio.validators import MAX_IMAGE_SIZE_MB, validate_image_size  # noqa: F401
