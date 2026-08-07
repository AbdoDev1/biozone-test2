from django.core.management.base import BaseCommand, CommandError

from staff.services.backup import report_backup_result


class Command(BaseCommand):
    help = (
        'يبلّغ نظام الإشعارات (بث لحظي WebSocket + إشعار دائم في الجرس عند '
        'الفشل) بنتيجة نسخة احتياطية اتعملت فعلًا برّه Django — يعني عن '
        'طريق scripts/backup_db.sh من على الـ host مباشرة — من غير ما يعيد '
        'تنفيذ عملية النسخ نفسها تاني.\n\n'
        'ده مقصود يتنادى من نفس backup_db.sh في آخره (نجح أو فشل)، مش '
        'للتشغيل المباشر من الكرون. للتشغيل من جوه Django نفسه (بدون '
        'السكريبت القديم خالص)، استخدم run_backup بدل كده — لكن افتكر إن '
        'فحص REQUIRE_MOUNTPOINT وقتها بيتنفذ من جوه الحاوية، وده فحص غير '
        'دقيق لأي مسار متربط بـ volume (بيرجع "متوصّل" دايمًا بغض النظر عن '
        'حالة الفلاشة الحقيقية على الـ host) — لذلك backup_db.sh على الـ '
        'host + الأمر ده أدق حل لسيناريو REQUIRE_MOUNTPOINT.\n\n'
        'أمثلة:\n'
        '  python manage.py report_backup_result --success --file biozone_2026-08-07_10h.sql.gz\n'
        '  python manage.py report_backup_result --error "نص رسالة الخطأ هنا"'
    )

    def add_arguments(self, parser):
        parser.add_argument('--success', action='store_true', help='النسخة نجحت.')
        parser.add_argument('--error', type=str, default=None, help='النسخة فشلت — نص رسالة الخطأ.')
        parser.add_argument('--file', type=str, default='', help='اسم ملف النسخة (اختياري، مع --success بس).')

    def handle(self, *args, **options):
        if options['success'] and options['error']:
            raise CommandError('استخدم --success أو --error، مش الاتنين مع بعض.')
        if not options['success'] and not options['error']:
            raise CommandError('لازم تحدد --success أو --error "نص رسالة الخطأ".')

        if options['success']:
            report_backup_result(True, '', backup_name=options['file'])
            self.stdout.write(self.style.SUCCESS('تم إرسال إشعار النجاح.'))
        else:
            report_backup_result(False, options['error'])
            self.stdout.write(self.style.SUCCESS('تم إرسال إشعار الفشل.'))
