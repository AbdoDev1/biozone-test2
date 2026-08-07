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
    نسخة احتياطية الآن" بيشغّل نفس السكريبت بشكل متزامن
    (staff/services/backup.py — perform_backup).
    """
    return render(request, 'staff/backup.html', {
        'has_error': LAST_ERROR_FILE.exists(),
        'recent_backups': recent_backups(limit=RECENT_BACKUPS_LIMIT),
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
    elif isinstance(error_detail, str) and 'شغالة بالفعل دلوقتي' in error_detail:
        # مش خطأ فني — مجرد تعارض توقيت (مثلاً الكرون شغال دلوقتي بالظبط).
        messages.warning(request, error_detail)
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
