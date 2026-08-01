from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render

from staff.permissions import perm_required
from staff.services.backup import LAST_ERROR_FILE, PROJECT_DIR, backup_status

LOG_FILE = PROJECT_DIR / 'logs' / 'backup.log'
LOG_TAIL_LINES = 15


def _read_log_tail():
    """آخر LOG_TAIL_LINES سطر من logs/backup.log، أو فاضي لو الملف مش موجود بعد."""
    if not LOG_FILE.exists():
        return []
    lines = LOG_FILE.read_text(encoding='utf-8', errors='replace').splitlines()
    return lines[-LOG_TAIL_LINES:]


@perm_required('staff.manage_backup')
def backup_manual(request):
    """
    صفحة النسخ الاحتياطي اليدوي: بتعرض هل آخر محاولة (تلقائية أو يدوية)
    نجحت أو فشلت (وجود backups/last_error.txt = آخر محاولة فشلت)، آخر
    أسطر من سجل النسخ، وزرار "تشغيل نسخة احتياطية الآن" بيشغّل نفس
    السكريبت بشكل متزامن (staff/services/backup.py — perform_backup).
    """
    return render(request, 'staff/backup.html', {
        'has_error': LAST_ERROR_FILE.exists(),
        'log_lines': _read_log_tail(),
        'status': backup_status(),
    })


@perm_required('staff.manage_backup')
def backup_run_now(request):
    if request.method != 'POST':
        return redirect('staff:backup_manual')

    from staff.services.backup import perform_backup

    success, error_detail = perform_backup()
    if success:
        messages.success(request, 'تم عمل النسخة الاحتياطية بنجاح.')
    else:
        # نفس الرسالة العامة اللي بتوصل لكل الموظفين — التفاصيل التقنية
        # (error_detail) متاحة بس عن طريق زرار "تحميل تفاصيل المشكلة" في
        # نفس الصفحة، مش هنا في رسالة الموقع نفسها.
        messages.error(
            request,
            'حصلت مشكلة في النسخ الاحتياطي. لو اتكررت، المشكلة محتاجة تدخل المبرمج مباشرة.'
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
