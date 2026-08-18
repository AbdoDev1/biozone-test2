import os
import time

from django.conf import settings
from django.core.management.base import BaseCommand

# نفس القيمة المحسوبة في products/tasks.py:_export_tmp_dir() — مكرّرة هنا
# عمدًا بدل استيراد الدالة الخاصة (بادئة _) من ملف تاني، الأمر ده بسيط
# بما فيه الكفاية إنه ميستاهلش كسر التغليف عشان سطر واحد.
EXPORT_TMP_DIR = os.path.join(settings.BASE_DIR, 'tmp_imports')

# أي ملف export_*.xlsx أقدم من كده معناه غالبًا إن الموظف فتح شاشة
# التصدير وسابها من غير ما يحمّل الملف (التحميل العادي بيمسح الملف على
# طول — راجع export_products_download) — ساعتين هامش كافي جدًا لأي تأخير
# طبيعي، والملف أصلًا بيتجدد بضغطة تانية على "تصدير" في أي وقت.
STALE_AFTER_SECONDS = 60 * 60 * 2


class Command(BaseCommand):
    help = (
        'بيمسح ملفات تصدير المنتجات القديمة (export_*.xlsx) اللي فضلت '
        'على القرص المشترك (import_tmp) من غير ما يتحمّلوا — شبكة أمان '
        'لو الموظف قفل التاب قبل التحميل أو فيه Celery task اتقطع فجأة. '
        'التحميل العادي بيمسح ملفه بنفسه فورًا، فالأمر ده مش بديل عن ده، '
        'مجرد تنظيف احتياطي دوري. آمن يتنفذ أكتر من مرة. مثال crontab '
        '(مرة كل ساعتين كافي جدًا):\n'
        '  0 */2 * * * cd /path/to/project && '
        'docker compose exec -T web-staff python manage.py cleanup_exports'
    )

    def handle(self, *args, **options):
        if not os.path.isdir(EXPORT_TMP_DIR):
            self.stdout.write('مفيش مجلد ملفات مؤقتة أصلًا — مفيش حاجة تتنضف.')
            return

        now = time.time()
        deleted = 0
        for name in os.listdir(EXPORT_TMP_DIR):
            if not (name.startswith('export_') and name.endswith('.xlsx')):
                continue
            path = os.path.join(EXPORT_TMP_DIR, name)
            try:
                age = now - os.path.getmtime(path)
            except OSError:
                continue
            if age > STALE_AFTER_SECONDS:
                try:
                    os.remove(path)
                    deleted += 1
                except OSError:
                    pass

        self.stdout.write(self.style.SUCCESS(f'تم مسح {deleted} ملف تصدير قديم.'))
