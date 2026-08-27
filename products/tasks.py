"""
مهام Celery الخاصة بالمنتجات. مرحلتين: قراءة/تصنيف ملف استيراد الإكسل
(parse_import_file)، ودلوقتي كمان الحفظ الفعلي لنتيجة الاستيراد
(commit_import_batch_task) — كانت بتتنفذ متزامنة جوه import_products_confirm
على web-staff (0.5 CPU فقط — راجع docker-compose.yml)، وبتلف على كل
صفوف الدفعة (لحد 3000) جوه transaction واحدة، فكانت بتقفل الـworker طول
مدة الحفظ ومعرّضة لـtimeout من nginx/gunicorn مع ملف كبير كفاية — نفس
فئة مشكلة الـ504 اللي اتحلت للقراءة قبل كده (راجع تقرير اختبار المرحلة 0)،
دلوقتي مرحلة التأكيد بتتبع نفس النمط بالظبط (المرحلة 2 من خطة نقل
تعديلات mg).
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

# نفس فكرة IMPORT_RESULT_CACHE_PREFIX فوق، بس لمرحلة التأكيد/الحفظ
# (import_products_confirm) بدل مرحلة القراءة. الدفعة (rows + decisions +
# إعدادات الإشعار) بتتخزن في الكاش قبل الـdelay (مش كـargs للـtask نفسها)
# عشان قيم Decimal في row_data['discounts'] (راجع parsing.py) مش
# JSON-serializable، وCELERY_TASK_SERIALIZER='json' — تخزين الكاش نفسه
# (Redis عبر django cache backend) مش بيعاني من نفس القيد لأنه مش بيمر
# على JSON.
IMPORT_COMMIT_PAYLOAD_PREFIX = 'product_import_commit_payload:'
IMPORT_COMMIT_RESULT_PREFIX = 'product_import_commit_result:'
IMPORT_COMMIT_TTL = 60 * 30

# دفعة المراجعة (rows + errors، بين شاشة المراجعة وشاشة التأكيد) كانت
# متخزنة في request.session[IMPORT_SESSION_KEY] — نفس فئة مشكلة
# SESSION_ENGINE='cached_db' اللي اتحلت قبل كده لنتيجة القراءة/الحفظ
# (راجع IMPORT_RESULT_CACHE_PREFIX فوق): تحديث الكاش (Redis) من جوه
# request منفصل بيوصل فورًا، لكن أي كتابة لـrequest.session لازم تتزامن
# مع نسخة الداتابيز الخاصة بالسيشن، وده مش السبب هنا فعليًا (الكتابة نفسها
# بتحصل جوه request المستخدم العادي مش جوه Celery)، لكن نفس المنطق —
# مفتاح كاش مبني على user_id بدل السيشن — بيوحّد أسلوب تخزين كل مراحل
# الاستيراد المؤقتة في مكان واحد (منقول من mg).
IMPORT_REVIEW_BATCH_PREFIX = 'product_import_review_batch:'
IMPORT_REVIEW_BATCH_TTL = 60 * 30  # 30 دقيقة، بتتجدد مع كل فتح لشاشة المراجعة


def import_result_cache_key(user_id):
    return f'{IMPORT_RESULT_CACHE_PREFIX}{user_id}'


def export_result_cache_key(user_id):
    return f'{EXPORT_RESULT_CACHE_PREFIX}{user_id}'


def import_commit_payload_cache_key(user_id):
    return f'{IMPORT_COMMIT_PAYLOAD_PREFIX}{user_id}'


def import_commit_result_cache_key(user_id):
    return f'{IMPORT_COMMIT_RESULT_PREFIX}{user_id}'


def import_review_batch_cache_key(user_id):
    return f'{IMPORT_REVIEW_BATCH_PREFIX}{user_id}'


@shared_task(bind=True, soft_time_limit=600, time_limit=900)
def parse_import_file(self, file_path, max_rows, user_id):
    """
    بتتنفذ في الخلفية بعد ما staff/views/products/import_export.py
    يحفظ الملف المرفوع على القرص المشترك (import_tmp volume) ويرجع
    استجابة فورية للموظف. بتخزّن النتيجة في الكاش وتبعت إشعار لحظي
    (عبر notifications.services.notify، اللي بيستخدم نفس Channels/Redis
    الموجود أصلاً) لما تخلص.

    الاسم اتوحّد مع مشروع mg (كان parse_import_workbook_task) — تعديل
    تسمية بس، بدون أي تغيير في المنطق أو السلوك (المرحلة 1 من خطة نقل
    تعديلات mg).
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


@shared_task(bind=True, soft_time_limit=900, time_limit=1200)
def commit_import_batch_task(self, user_id):
    """
    بتتنفذ في celery-worker. بتاخد الدفعة (rows + decisions + notify_clients)
    اللي import_products_confirm خزّنها في الكاش قبل الـdelay (راجع
    IMPORT_COMMIT_PAYLOAD_PREFIX فوق)، وتنفّذ commit_import_batch الفعلية
    جوه transaction واحدة — بالظبط زي ما كانت بتحصل قبل كده جوه الـview،
    بس هنا مش شغالة جوه worker web-staff (0.5 CPU) ومش مربوطة بمهلة
    nginx/gunicorn لطلب HTTP حي. مهلة أطول من parse_import_file (15 دقيقة
    soft / 20 دقيقة صلبة) لأن الحفظ الفعلي (كتابة + validation لكل صف)
    أبطأ جوهريًا من مجرد القراءة.

    النتيجة بتتخزن في الكاش (نفس نمط parse_import_file) — import_products_
    commit_result بيقراها ويحوّلها لـmessages حقيقية (لازم request فعلي،
    مش متاح جوه task)، ويتعامل مع notify_clients / أخطاء القراءة المرحّلة
    من مرحلة parse نفسها.
    """
    from django.db import transaction

    from accounts.models import User
    from notifications.services import notify
    from products.services import import_export as import_export_service

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    payload = cache.get(import_commit_payload_cache_key(user_id))
    cache.delete(import_commit_payload_cache_key(user_id))

    if not payload:
        result = {
            'status': 'failed',
            'error_message': 'انتهت صلاحية عملية الاستيراد دي. من فضلك ارفع الملف تاني.',
        }
        cache.set(import_commit_result_cache_key(user_id), result, timeout=IMPORT_COMMIT_TTL)
        notify(
            recipient=user, kind='IMPORT_COMMITTED',
            title='مشكلة في حفظ الاستيراد', message=result['error_message'],
            url_name='staff:import_products',
        )
        return

    try:
        with transaction.atomic():
            created_count, updated_count, restocked_count = import_export_service.commit_import_batch(
                payload['rows'], payload['decisions'], user,
            )
    except Exception as e:
        # نفس رسالة الخطأ اللي كانت في الـview قبل كده بالظبط — الـ
        # transaction بتتعمل rollback تلقائيًا (نفس ضمان "صفر حفظ جزئي"
        # اللي كان موجود وهي جوه request عادي).
        result = {
            'status': 'failed',
            'error_message': f'حصل خطأ أثناء الحفظ ولم يتم حفظ أي صنف: {e}',
        }
        cache.set(import_commit_result_cache_key(user_id), result, timeout=IMPORT_COMMIT_TTL)
        notify(
            recipient=user, kind='IMPORT_COMMITTED',
            title='فشل حفظ الاستيراد', message=result['error_message'],
            url_name='staff:import_products',
        )
        return

    # إشعار العملاء بالوارد الجديد — نفس منطق الـview القديم بالظبط، منقول
    # هنا لأن created_count/restocked_count مش معروفين إلا بعد الحفظ.
    new_arrivals_total = created_count + restocked_count
    if payload.get('notify_clients') and new_arrivals_total > 0:
        from notifications.services import notify_all_clients
        notify_all_clients(
            kind='NEW_ARRIVALS',
            title='وارد جديد في المتجر 🆕',
            message=f'تم إضافة {new_arrivals_total} صنف جديد أو تزويد رصيده — اطّلع على صفحة الوارد.',
            url_name='store:new_arrivals',
        )

    result = {
        'status': 'done',
        'created_count': created_count,
        'updated_count': updated_count,
        'restocked_count': restocked_count,
        'errors': payload.get('errors') or [],
    }
    cache.set(import_commit_result_cache_key(user_id), result, timeout=IMPORT_COMMIT_TTL)

    notify(
        recipient=user, kind='IMPORT_COMMITTED',
        title='تم حفظ الاستيراد',
        message=f'تم إضافة {created_count} صنف وتحديث {updated_count} صنف.',
        url_name='staff:import_products_errors' if result['errors'] else 'staff:product_list',
    )


# مجلد التصدير المؤقت — نفس import_tmp volume المشترك بين web-staff
# وceleryworker (راجع IMPORT_TMP_DIR في staff/views/products/import_export.py
# وتعليق import_tmp في docker-compose.yml). بادئة الاسم export_ (بدل
# uuid عادي زي الاستيراد) عشان أمر التنظيف الدوري (products/management/
# commands/cleanup_export_files.py) يفرّق ملفات التصدير القديمة عن أي ملف
# استيراد لسه شغال عليه Celery.
def _export_tmp_dir():
    from django.conf import settings
    return os.path.join(settings.BASE_DIR, 'tmp_imports')


@shared_task(bind=True, soft_time_limit=600, time_limit=900)
def export_products_task(self, user_id, product_ids=None, download_filename=None):
    """
    بتتنفذ في الخلفية بعد ما staff/views/products/import_export.py:export_products
    (أو export_products_selected) يرجع استجابة فورية للموظف (بدل ما يبني
    ملف الإكسل متزامن جوه الـ request، اللي كان بيحجز worker web-staff
    لمدة أطول مع نمو الكتالوج — نفس فئة مشكلة 504 اللي اتحلت للاستيراد،
    راجع ADR-001).

    product_ids: None = كل الأصناف (export_products)، أو قائمة IDs محددة
    (export_products_selected) — نفس الـtask بتخدم الحالتين عشان الاتنين
    نفس منطق البناء بالظبط، الفرق بس فلترة اختيارية على الاستعلام. قبل
    التعميم ده كان `export_products_selected` لسه بيبني الملف متزامن
    جوه request/response (نفس فئة المشكلة دي بالظبط، من غير أي حماية)
    رغم إن `export_products` (تصدير الكل) كان أصلاً منقول لـCelery —
    فجوة حقيقية اتقفلت هنا (المرحلة 3 من خطة نقل تعديلات mg، بعد تصحيح:
    راجع الملاحظة تحت لسبب عدم نسخ تصميم mg حرفيًا).

    download_filename: اسم الملف اللي هيتحمّل بيه فعليًا (مختلف بين
    "بكل الأصناف" و"المحددة" — راجع الاستدعاءين في import_export.py).
    افتراضيًا 'biozone_products_export.xlsx' لو معدّاش.

    ملحوظة تصميم (تصحيح بعد مراجعة كود mg الفعلي): mg عندها task مشابهة
    (build_products_export) بس بتخزّن الحالة في جلسة الموظف + token
    عشوائي في الرابط بدل الكاش بمفتاح user_id. راجعنا الكود ولقينا
    docstring صريحة من فريق mg نفسه بتقول إن ده **النمط القديم** اللي
    مقرّرين عمدًا يسيبوه زي ما هو ("مفيش داعي لنفس التعديل هنا دلوقتي —
    نطاق التغيير الحالي الاستيراد بس") — يعني النمط الحديث بتاعهم (كاش +
    fetch خفيف، زي الاستيراد) هو نفسه اللي بيوزون أصلاً شغالة بيه هنا من
    الأول. فبدل ما ننسخ نمط mg القديم (وده كان هيرجّع بيوزون للخلف في
    تجربة "تصدير الكل")، عمّمنا نفس النمط الحديث الموجود أصلاً ليشمل
    "تصدير المحدد" كمان.

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
        )
        if product_ids is not None:
            products = products.filter(pk__in=product_ids)
        wb = import_export_service.build_products_export_workbook(products)
        wb.save(file_path)
        result = {
            'status': 'done',
            'file_path': file_path,
            'download_filename': download_filename or 'biozone_products_export.xlsx',
        }
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
