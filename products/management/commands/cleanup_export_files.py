"""
تنظيف ملفات الإكسل المؤقتة (تصدير منتجات/تصدير تقارير/استيراد) اللي فضلت
على القرص من غير مسح — نسخة أشمل من الأمر القديم cleanup_exports (منقول
من mg، مع تكييف على تصميم biozone الفعلي: عندنا مجلد مؤقت مشترك واحد
(tmp_imports) لكل الأنواع التلاتة بدل مجلدين منفصلين زي mg، وبنفرّق
النوع من بادئة اسم الملف بس — راجع products/tasks.py:_export_tmp_dir()،
staff/report_export.py، وIMPORT_TMP_DIR في
staff/views/products/import_export.py، الثلاثة بيكتبوا لنفس المسار).

ملفات export_*.xlsx (تصدير منتجات) وreport_*.xlsx (تصدير تقارير): بتتبني
بواسطة export_products_task/build_report_export_task وتفضل موجودة على
القرص لحد ما export_products_download/reports_export_download يقدّمها
ويمسحها. حالة الكاش اللي بتشاور عليها ليها TTL نص ساعة بس — لو الموظف
بدأ تصدير وماحملوش، الكاش بيمسح نفسه لوحده لكن الملف الفعلي مش بيتلمس،
فبيفضل على القرص للأبد من غير الأمر ده.

ملفات الاستيراد (اسمها hex عشوائي بدون بادئة، راجع import_products في
staff/views/products/import_export.py): أقل عرضة لنفس المشكلة عمليًا،
لأن parse_import_file (products/tasks.py) بتمسح الملف المرفوع في finally
دايمًا (نجح أو فشل) — لكن لو الـtask نفسها ماتنفذتش خالص (عطل في
الطابور مثلًا)، الملف المرفوع هيفضل هو كمان، فبنمسحه هنا كتغطية إضافية.

آمن يتنفذ أكتر من مرة، ومفروض يتحط في crontab دوري (كل ساعتين مثلاً) زي
activity.trim_activity_logs وnotifications.trim_notifications.

الاستخدام:
    python manage.py cleanup_export_files
    python manage.py cleanup_export_files --hours 4
"""
import os
import time

from django.conf import settings
from django.core.management.base import BaseCommand

# نفس القيمة المحسوبة في products/tasks.py:_export_tmp_dir() — مكرّرة هنا
# عمدًا بدل استيراد الدالة الخاصة (بادئة _) من ملف تاني، الأمر ده بسيط
# بما فيه الكفاية إنه ميستاهلش كسر التغليف عشان سطر واحد. نفس المسار
# بالظبط اللي staff/views/products/import_export.py:IMPORT_TMP_DIR
# بيستخدمه — مجلد واحد مشترك لكل الأنواع التلاتة (استيراد/تصدير منتجات/
# تصدير تقارير).
TMP_DIR = os.path.join(settings.BASE_DIR, 'tmp_imports')

DEFAULT_MAX_AGE_HOURS = 2


class Command(BaseCommand):
    help = (
        f'يمسح ملفات .xlsx المؤقتة (استيراد/تصدير منتجات/تصدير تقارير) '
        f'الأقدم من {DEFAULT_MAX_AGE_HOURS} ساعة افتراضيًا من tmp_imports. '
        'آمن يتنفذ أكتر من مرة، ومفروض يتحط في crontab دوري (كل ساعتين '
        'مثلاً) زي activity.trim_activity_logs. مثال crontab:\n'
        '  0 */2 * * * cd /path/to/project && '
        'docker compose exec -T web-staff python manage.py cleanup_export_files'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=float,
            default=DEFAULT_MAX_AGE_HOURS,
            help=f'عمر الملف بالساعات قبل ما يتمسح (افتراضي {DEFAULT_MAX_AGE_HOURS}).',
        )

    def handle(self, *args, **options):
        if not os.path.isdir(TMP_DIR):
            self.stdout.write('مفيش مجلد ملفات مؤقتة أصلًا — مفيش حاجة تتنضف.')
            return

        cutoff = time.time() - options['hours'] * 3600
        deleted = 0
        for name in os.listdir(TMP_DIR):
            if not name.endswith('.xlsx'):
                continue
            path = os.path.join(TMP_DIR, name)
            try:
                age_ok = os.path.getmtime(path) < cutoff
            except OSError:
                continue
            if not age_ok:
                continue
            try:
                os.remove(path)
                deleted += 1
            except OSError:
                # ملف اتمسح بالفعل من عملية تانية (مثلًا export_products_download
                # طلع نفس اللحظة) — تجاهل ومتابعة الباقي.
                continue

        self.stdout.write(self.style.SUCCESS(
            f'تم مسح {deleted} ملف أقدم من {options["hours"]} ساعة.'
        ))
