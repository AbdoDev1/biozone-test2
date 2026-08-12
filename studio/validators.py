from django.core.exceptions import ValidationError

# منقول من products/validators.py (المرحلة 1 من خطة الاستوديو) — بقى هنا
# مصدر واحد للتحقق من صور، يستخدمه studio.StudioImage مباشرة، ومنتجات/أقسام
# هتستخدمه لحد ما تتحول لـ ForeignKey على StudioImage في المرحلة 8.
# products/validators.py بيستورد من هنا بس عشان التوافق الخلفي، مش نسخة
# تانية من نفس المنطق.

MAX_IMAGE_SIZE_MB = 5

ALLOWED_IMAGE_EXTENSIONS = ['jpg', 'jpeg', 'png', 'webp']


def validate_image_size(file):
    max_bytes = MAX_IMAGE_SIZE_MB * 1024 * 1024
    if file.size > max_bytes:
        raise ValidationError(
            f'حجم الصورة أكبر من الحد المسموح ({MAX_IMAGE_SIZE_MB} ميجا).'
        )
