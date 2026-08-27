"""
مهام Celery الخاصة بـ staff. مهمتين: تشغيل النسخة الاحتياطية اليدوية في
الخلفية (المرحلة 2 من خطة الدين التقني — ADR-002)، وبناء ملفات تصدير
تقارير قسم reports في الخلفية (منقول من mg، بعد Phase 1 اللي نقلت تصدير
المنتجات — راجع staff/report_export.py لتفاصيل كاملة).

قبل كده، زرار "تشغيل نسخة احتياطية الآن" (staff/views/backup.py:backup_run_now)
كان بينادي staff/services/backup.py:perform_backup() بشكل متزامن جوه الـ
request — pg_dump كامل + gzip، كله على خدمة web-staff. الفنكشن نفسها معدّلناش
فيها حاجة خالص (زي ما ADR-002 نص) — بس نقلنا نقطة النداء هنا.

منطق القفل (staff/services/backup.py:_acquire_lock عبر fcntl.flock على
logs/.backup.lock) لسه هو هو من غير أي تعديل، وهو مستقل أصلًا عن مين بينادي
perform_backup() (زرار، كرون، أو Celery task زي هنا)، فهيفضل شغال صح تلقائيًا.
"""
import os
import uuid

from celery import shared_task

from notifications.models import Notification
from notifications.services import notify

# نفس نص الرسالة اللي staff/services/backup.py:BackupInProgress بيرفعها —
# لازم يفضل متطابق مع الفحص الموجود أصلًا في backup_run_now (وكان قبل كده
# بيتفحص مباشرة على استجابة الطلب نفسه، دلوقتي بيتفحص هنا جوه الـ task).
_LOCK_CONFLICT_MARKER = 'شغالة بالفعل دلوقتي'


@shared_task(bind=True, soft_time_limit=310, time_limit=330)
def run_manual_backup_task(self, user_id):
    """
    بتتنفذ في الخلفية بعد ما staff/views/backup.py:backup_run_now يرجع
    استجابة فورية للموظف. بتنادي perform_backup() زي ما هي بالظبط،
    وبعدين بتبلّغ الموظف اللي ضغط الزرار (بس هو) بالنتيجة عن طريق نظام
    الإشعارات — لأن الاستجابة الفورية دلوقتي مبتعرفش النتيجة النهائية.

    مهلة soft_time_limit/time_limit أكبر شوية من TIMEOUT_SECONDS (300)
    الموجودة جوه perform_backup() نفسها — عشان لو pg_dump علّق فعلاً،
    perform_backup() هي اللي توقفه وترجع رسالة خطأ واضحة، مش Celery يقتل
    الـ task فجأة من غير ما يوصل تنظيف lock/ملف مؤقت.

    ملحوظة مهمة عن الإشعارات: فشل حقيقي (مش تعارض توقيت) أصلًا بيتبلّغ
    بيه كل أصحاب صلاحية staff.manage_backup (الموظف ده منهم، لأنه محتاج
    نفس الصلاحية أصلًا عشان يشوف الزرار) عن طريق BACKUP_FAILED جوه
    perform_backup() -> report_backup_result() — منطق موجود بالفعل ومتلمسناهوش.
    فلو بعتنا هنا كمان إشعار شخصي على نفس الفشل، الموظف هيشوف نفس الخبر
    مرتين في الجرس. عشان كده هنا بنبعت إشعار شخصي بس في حالتين مالهمش أي
    إشعار حاليًا: النجاح، وتعارض التوقيت (BackupInProgress).
    """
    from accounts.models import User
    from staff.services.backup import perform_backup

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    success, error_detail = perform_backup()

    if success:
        notify(
            recipient=user,
            kind=Notification.Kind.BACKUP_READY,
            title='تم عمل النسخة الاحتياطية بنجاح',
            message='النسخة اللي طلبتها اتعملت بنجاح.',
            url_name='staff:backup_manual',
        )
        return

    if isinstance(error_detail, str) and _LOCK_CONFLICT_MARKER in error_detail:
        # تعارض توقيت (كرون أو محاولة تانية شغالة بالفعل) — مش خطأ فني،
        # فلازم رسالة واضحة تفرّقه عن فشل حقيقي، مش تُعامَل كـ BACKUP_FAILED.
        notify(
            recipient=user,
            kind=Notification.Kind.BACKUP_READY,
            title='لم تُنفَّذ — نسخة تانية شغالة بالفعل',
            message=error_detail,
            url_name='staff:backup_manual',
        )
        return

    # فشل حقيقي: البث اللحظي + إشعار BACKUP_FAILED الجماعي (لكل أصحاب
    # صلاحية staff.manage_backup) اتبعتوا بالفعل جوه perform_backup() نفسها
    # (عبر report_backup_result) — عمدًا مفيش إشعار شخصي إضافي هنا، لتفادي
    # تكرار نفس الخبر مرتين لنفس الموظف (راجع الملحوظة في الـ docstring فوق).


@shared_task(bind=True, soft_time_limit=600, time_limit=900)
def build_report_export_task(self, report_kind, params, user_id):
    """
    بتتنفذ في celery-worker. نظير export_products_task (products/tasks.py)
    بس لتقارير قسم staff/reports.py بدل المنتجات — راجع
    staff/report_export.py لسبب النقل الكامل (منقول من mg).

    params: نسخة request.GET.dict() وقت الطلب الأصلي (staff/views/reports.py
    هي اللي بتلقطها وتحطها في الكاش قبل الـdelay، عشان القيم القابلة
    للتغيير بعد كده مالهاش أي تأثير على تقرير بدأ بناؤه بالفعل).

    مجلد الحفظ وتنسيق حالة الكاش نفس export_products_task بالظبط
    (products/tasks.py) — status: 'done'|'failed'، ومسار الملف على القرص
    المشترك (products.tasks._export_tmp_dir، نفس tmp_imports اللي أمر
    التنظيف الدوري cleanup_export_files بيغطيه) بدل token في الرابط.
    """
    from django.core.cache import cache

    from products.tasks import _export_tmp_dir
    from staff.report_export import (
        REPORT_KIND_BUILDERS,
        REPORT_KIND_FILENAMES,
        REPORT_EXPORT_STATUS_TTL,
        report_export_status_cache_key,
    )

    export_dir = _export_tmp_dir()
    os.makedirs(export_dir, exist_ok=True)
    file_path = os.path.join(export_dir, f'report_{uuid.uuid4().hex}.xlsx')

    try:
        builder = REPORT_KIND_BUILDERS[report_kind]
        wb = builder(params)
        wb.save(file_path)
        result = {
            'status': 'done',
            'file_path': file_path,
            'download_filename': REPORT_KIND_FILENAMES.get(report_kind, 'report.xlsx'),
        }
    except Exception as e:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            pass
        result = {
            'status': 'failed',
            'error_message': f'خطأ غير متوقع أثناء بناء ملف التقرير: {e}',
        }

    cache.set(report_export_status_cache_key(user_id), result, timeout=REPORT_EXPORT_STATUS_TTL)

    from accounts.models import User

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
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
            title='ملف تصدير التقرير جاهز',
            message='اضغط لتحميل الملف.',
            url_name='staff:reports_export_download',
        )
    else:
        notify(
            recipient=user,
            kind='EXPORT_READY',
            title='مشكلة أثناء تجهيز ملف التقرير',
            message=result.get('error_message') or 'حصل خطأ غير متوقع أثناء تجهيز الملف.',
            url_name='staff:reports_dashboard',
        )
