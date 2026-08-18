"""
مهام Celery الخاصة بالمنتجات. حاليًا مهمة واحدة بس: قراءة/تصنيف ملف
استيراد الإكسل — دي كانت الخطوة اللي بتاخد وقت طويل مع ملفات كبيرة
(3000 صف) وبتحجز Gunicorn worker كامل، وده اللي كان بيسبب 504 من nginx
(راجع تقرير اختبار المرحلة 0).

خطوات الاستيراد الباقية (شاشة المراجعة، والحفظ النهائي في
import_products_confirm) لسه بتحصل بشكل عادي في request عادي — حجم
البيانات وقتها صغير نسبيًا (بس قرارات الموظف على الصفوف)، مش محتاجة
Celery.
"""
import os

from celery import shared_task
from django.core.cache import cache

# نتيجة قراءة الملف بتتخزن في الكاش (Redis) بدل السيشن مباشرة، لأن
# السيشن مرتبط بالطلب اللي أنشأه، والمهمة شغالة في process منفصل تمامًا
# (Celery worker) مالوش وصول لسيشن الطلب الأصلي. المفتاح هنا مبني على
# user_id عشان كل موظف يلاقي نتيجة استيراده هو بس.
IMPORT_RESULT_CACHE_PREFIX = 'product_import_result:'
IMPORT_RESULT_TTL = 60 * 30  # 30 دقيقة — كفاية للموظف يفتح شاشة المراجعة

# نفس فكرة نتيجة الاستيراد، بس هنا القيمة المخزّنة مسار ملف جاهز على القرص
# المشترك (import_tmp) بدل JSON — الملف الفعلي أكبر من إن يتخزن في الكاش
# نفسه (Redis)، فالكاش بيمسك بس "فين الملف" لحد ما الموظف يحمّله (راجع
# export_products_download في staff/views/products/import_export.py).
EXPORT_RESULT_CACHE_PREFIX = 'product_export_result:'
EXPORT_RESULT_TTL = 60 * 30


def import_result_cache_key(user_id):
    return f'{IMPORT_RESULT_CACHE_PREFIX}{user_id}'


def export_result_cache_key(user_id):
    return f'{EXPORT_RESULT_CACHE_PREFIX}{user_id}'


@shared_task(bind=True, soft_time_limit=600, time_limit=900)
def parse_import_workbook_task(self, file_path, max_rows, user_id):
    """
    بتتنفذ في الخلفية بعد ما staff/views/products/import_export.py
    يحفظ الملف المرفوع على القرص المشترك (import_tmp volume) ويرجع
    استجابة فورية للموظف. بتخزّن النتيجة في الكاش وتبعت إشعار لحظي
    (عبر notifications.services.notify، اللي بيستخدم نفس Channels/Redis
    الموجود أصلاً) لما تخلص.
    """
    from products.services import import_export as import_export_service

    try:
        with open(file_path, 'rb') as f:
            rows, errors, error_message = import_export_service.read_import_workbook(
                f, max_rows=max_rows,
            )
        result = {
            'status': 'done',
            'rows': rows,
            'errors': errors,
            'error_message': error_message,
        }
    except Exception as e:
        result = {
            'status': 'failed',
            'error_message': f'حصل خطأ غير متوقع أثناء قراءة الملف: {e}',
        }
    finally:
        # الملف المؤقت خلاص غرضه (سواء نجح أو فشل) — منسيبوش ملفات
        # إكسل قديمة تتراكم على القرص المشترك.
        try:
            os.remove(file_path)
        except OSError:
            pass

    cache.set(import_result_cache_key(user_id), result, timeout=IMPORT_RESULT_TTL)

    # إشعار الموظف عبر نظام الإشعارات الموجود أصلًا — لو فتح تاب تاني أو
    # مقفل الصفحة، هيلاقي إشعار في الجرس أول ما يرجع. لو مفتوح، الجرس
    # بيتحدث لحظيًا (راجع notifications/services.py: notify -> _push_realtime).
    from accounts.models import User
    from notifications.services import notify

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    if result['status'] == 'done' and not result.get('error_message'):
        notify(
            recipient=user,
            kind='IMPORT_READY',
            title='ملف الاستيراد جاهز للمراجعة',
            message=f"تم تجهيز {len(result['rows'])} صف — افتح شاشة المراجعة.",
            url_name='staff:import_products_review',
        )
    else:
        notify(
            recipient=user,
            kind='IMPORT_READY',
            title='مشكلة في قراءة ملف الاستيراد',
            message=result.get('error_message') or 'حصل خطأ غير متوقع أثناء قراءة الملف.',
            url_name='staff:import_products',
        )


# مجلد التصدير المؤقت — نفس import_tmp volume المشترك بين web-staff
# وceleryworker (راجع IMPORT_TMP_DIR في staff/views/products/import_export.py
# وتعليق import_tmp في docker-compose.yml). بادئة الاسم export_ (بدل
# uuid عادي زي الاستيراد) عشان أمر التنظيف الدوري (products/management/
# commands/cleanup_exports.py) يفرّق ملفات التصدير القديمة عن أي ملف
# استيراد لسه شغال عليه Celery.
def _export_tmp_dir():
    from django.conf import settings
    return os.path.join(settings.BASE_DIR, 'tmp_imports')


@shared_task(bind=True, soft_time_limit=600, time_limit=900)
def export_products_task(self, user_id):
    """
    بتتنفذ في الخلفية بعد ما staff/views/products/import_export.py:export_products
    يرجع استجابة فورية للموظف (بدل ما يبني ملف الإكسل بكل الأصناف متزامن
    جوه الـ request، اللي كان بيحجز worker web-staff لمدة أطول مع نمو
    الكتالوج — نفس فئة مشكلة 504 اللي اتحلت للاستيراد، راجع ADR-001).

    على عكس الاستيراد، النتيجة هنا مش JSON قابل للتخزين في الكاش مباشرة —
    ملف إكسل فعلي، فبيتحفظ على القرص المشترك وبنخزّن مساره بس في الكاش.
    الملف بيتمسح إما لما الموظف يحمّله فعليًا (راجع export_products_download)
    أو عن طريق أمر التنظيف الدوري لو اتنسي.
    """
    import uuid

    from products.models import Product
    from products.services import import_export as import_export_service

    export_dir = _export_tmp_dir()
    os.makedirs(export_dir, exist_ok=True)
    file_path = os.path.join(export_dir, f'export_{uuid.uuid4().hex}.xlsx')

    try:
        products = Product.objects.select_related('category').prefetch_related(
            'units__discounts__account_type',
        ).all()
        wb = import_export_service.build_products_export_workbook(products)
        wb.save(file_path)
        result = {'status': 'done', 'file_path': file_path}
    except Exception as e:
        # لو فشل بعد ما بدأ يكتب الملف، منسيبوش نص ملف تالف على القرص.
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            pass
        result = {
            'status': 'failed',
            'error_message': f'حصل خطأ غير متوقع أثناء تجهيز ملف التصدير: {e}',
        }

    cache.set(export_result_cache_key(user_id), result, timeout=EXPORT_RESULT_TTL)

    from accounts.models import User
    from notifications.services import notify

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        # المستخدم مش موجود (اتمسح؟) — الملف مالوش معنى من غيره، امسحه
        # بدل ما يفضل يتراكم على القرص لحد التنظيف الدوري.
        if result.get('file_path') and os.path.exists(result['file_path']):
            try:
                os.remove(result['file_path'])
            except OSError:
                pass
        return

    if result['status'] == 'done':
        notify(
            recipient=user,
            kind='EXPORT_READY',
            title='ملف تصدير المنتجات جاهز',
            message='اضغط لتحميل الملف.',
            url_name='staff:export_products_download',
        )
    else:
        notify(
            recipient=user,
            kind='EXPORT_READY',
            title='مشكلة أثناء تجهيز ملف التصدير',
            message=result.get('error_message') or 'حصل خطأ غير متوقع أثناء تجهيز الملف.',
            url_name='staff:export_products',
        )
