from django.core.management.base import BaseCommand

from activity.services import DEFAULT_RETENTION_DAYS, delete_old_activity_logs


class Command(BaseCommand):
    help = (
        f'يمسح كل سجلات النشاط (بما فيها الملاحظات اليدوية) الأقدم من '
        f'{DEFAULT_RETENTION_DAYS} يوم افتراضيًا. آمن يتنفذ أكتر من مرة، '
        'ومفروض يتحط في crontab دوري (يوميًا مثلًا) زي '
        'notifications.trim_notifications و staff.run_backup.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=DEFAULT_RETENTION_DAYS,
            help=f'عدد أيام الاحتفاظ (افتراضي {DEFAULT_RETENTION_DAYS}).',
        )

    def handle(self, *args, **options):
        days = options['days']
        deleted_count = delete_old_activity_logs(days=days)
        self.stdout.write(self.style.SUCCESS(
            f'تم مسح {deleted_count} سجل نشاط أقدم من {days} يوم.'
        ))
