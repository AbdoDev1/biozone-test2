from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render

from staff.permissions import perm_required
from staff.services.backup import LAST_ERROR_FILE, backup_status, recent_backups

RECENT_BACKUPS_LIMIT = 5


@perm_required('staff.manage_backup')
def backup_manual(request):
    """
    صفحة النسخ الاحتياطي اليدوي: بتعرض هل آخر محاولة (تلقائية أو يدوية)
    نجحت أو فشلت (وجود backups/last_error.txt = آخر محاولة فشلت)، آخر
    RECENT_BACKUPS_LIMIT نسخة **موجودة فعليًا على القرص دلوقتي** (مش
    سطور من ملف log قديم ممكن يشاور على ملفات اتمسحت)، وزرار "تشغيل
    نسخة احتياطية الآن" بيبعت مهمة Celery في الخلفية وبيرجع فورًا
    (راجع backup_run_now و staff/tasks.py:run_manual_backup_task) —
    النتيجة النهائية بتوصل عن طريق نظام الإشعارات، مش هنا في نفس الصفحة.
    """
    return render(request, 'staff/backup.html', {
        'has_error': LAST_ERROR_FILE.exists(),
        'recent_backups': recent_backups(limit=RECENT_BACKUPS_LIMIT),
        'status': backup_status(),
    })


@perm_required('staff.manage_backup')
def backup_run_now(request):
    """
    بتبعت المهمة لـ Celery (staff/tasks.py:run_manual_backup_task) وترجع
    فورًا من غير ما تستنى pg_dump/gzip يخلصوا (المرحلة 2 من خطة الدين
    التقني — ADR-002؛ قبل كده كانت بتنادي perform_backup() مباشرة وتستنى
    النتيجة جوه نفس الـ request). النتيجة الفعلية (نجاح/فشل/تعارض توقيت)
    بتوصل لاحقًا عن طريق نظام الإشعارات الموجود، مش هنا في رسالة الصفحة.
    """
    if request.method != 'POST':
        return redirect('staff:backup_manual')

    from staff.tasks import run_manual_backup_task

    run_manual_backup_task.delay(request.user.pk)
    messages.info(
        request,
        'بدأ النسخ الاحتياطي في الخلفية — هتوصلك رسالة في الإشعارات لما يخلص (نجاح أو فشل).'
    )
    return redirect('staff:backup_manual')


@perm_required('staff.manage_backup')
def backup_error_download(request):
    """
    بيحمّل نص الخطأ الحقيقي (backups/last_error.txt) كملف نصي بسيط —
    الموظف يقدر يبعته على واتساب للمبرمج من غير ما يحتاج يفهمه أو يدخل
    السيرفر خالص.
    """
    if not LAST_ERROR_FILE.exists():
        messages.info(request, 'لا يوجد خطأ مسجّل حاليًا — آخر محاولة نجحت.')
        return redirect('staff:backup_manual')

    content = LAST_ERROR_FILE.read_text(encoding='utf-8', errors='replace')
    response = HttpResponse(content, content_type='text/plain; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="backup_error_details.txt"'
    return response
