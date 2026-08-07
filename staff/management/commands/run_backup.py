from django.core.management.base import BaseCommand

from staff.services.backup import perform_backup


class Command(BaseCommand):
    help = (
        'بيشغّل النسخة الاحتياطية لقاعدة البيانات (scripts/backup_db.sh) '
        'ويبعت حالة لحظية (WebSocket) لكل الموظفين المتصلين حاليًا، وعند '
        'الفشل بيسجّل إشعار دائم لأصحاب صلاحية staff.manage_backup. '
        'ده الأمر المفروض يتحط في crontab بدل ما السكريبت يتنادى مباشرة '
        '(راجع staff/services/backup.py للتفاصيل)، مثلاً — مرة واحدة يوميًا '
        'الساعة 3 الفجر (وقت هادئ، وكافي مع RETENTION_DAYS=14 الافتراضي؛ '
        'تشغيله كل ساعة كان بيعمل حمل إضافي غير مبرر على القاعدة والفلاشة '
        'من غير داعي حقيقي — لو محتاج استرجاع أدق من فرق يوم، زوّد التردد '
        'لكل 6 ساعات مثلًا بدل كل ساعة):\n'
        '  0 3 * * * cd /path/to/project && '
        'docker compose exec -T web python manage.py run_backup'
    )

    def handle(self, *args, **options):
        success, error_detail = perform_backup()
        if success:
            self.stdout.write(self.style.SUCCESS('تم عمل النسخة الاحتياطية بنجاح.'))
        else:
            self.stderr.write(self.style.ERROR(
                'فشل النسخ الاحتياطي.' + (f'\n{error_detail}' if error_detail else '')
            ))
