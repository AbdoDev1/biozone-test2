"""
منطق النسخ الاحتياطي لقاعدة البيانات — بيعمل pg_dump مباشرة (اتصال شبكة
عادي بـ DB_HOST:DB_PORT، بالظبط زي ما Django نفسه بيتصل بقاعدة البيانات)
من غير ما يحتاج docker CLI ولا وصول لـ docker socket. ده بيخليه يشتغل
بنفس الطريقة بالظبط سواء:
  - جوه حاوية web (زرار "تشغيل نسخة احتياطية الآن" في staff/views/backup.py)
  - أو عن طريق الكرون (staff/management/commands/run_backup.py)
  - محليًا (docker-compose.yml العادي) أو على الـ VPS في الإنتاج

الحلقة الوحيدة اللي بتتكلم مع notifications (بث لحظي WebSocket + إشعار
دائم عند الفشل) عشان مفيش تكرار منطق بين الزرار اليدوي وأمر الكرون.

ملحوظة: scripts/backup_db.sh القديم لسه موجود ومنفصل — بديل لمن يفضّل
يشغّل النسخ من على الـ host مباشرة (برّه Django) عن طريق
`docker compose exec ... db pg_dump`. الاتنين بيكتبوا في نفس backups/
بنفس صيغة الاسم، فمتوافقين مع بعض (retention وعرض الحالة شغالين على
نواتج أي منهم).
"""
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from django.conf import settings

PROJECT_DIR = Path(settings.BASE_DIR)
BACKUP_DIR = PROJECT_DIR / 'backups'
LOG_FILE = PROJECT_DIR / 'logs' / 'backup.log'
LAST_ERROR_FILE = BACKUP_DIR / 'last_error.txt'

# نفس أسلوب scripts/backup_db.sh بالظبط (متغيرات بيئة، مش .env.production)
# — RETENTION_DAYS بتتحدد وقت تشغيل الكرون، مش قيمة ثابتة في الكود.
RETENTION_DAYS = int(os.environ.get('RETENTION_DAYS', '14'))
REQUIRE_MOUNTPOINT = os.environ.get('REQUIRE_MOUNTPOINT', 'false').lower() == 'true'
MIN_FREE_MB = int(os.environ.get('MIN_FREE_MB', '500'))

# مهلة أكبر بكثير من المتوقع (النسخ الفعلي بياخد ثواني معدودة لحجم قاعدة
# البيانات الحالي) — بس حماية من عملية معلّقة تفضل شغالة للأبد.
TIMEOUT_SECONDS = 300


def _log(message):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} | {message}\n")


def _broadcast(status, message):
    """
    بتبعت حالة لحظية (running/success/error) لكل الموظفين المتصلين حاليًا
    عن طريق WebSocket (شوف notifications/consumers.py — STAFF_BROADCAST_GROUP
    و backup_status handler). لو الـ channel layer مش شغال لأي سبب (Redis
    واقع)، بنمتص الاستثناء بهدوء — النتيجة الفعلية (نجاح/فشل) لسه بتتسجل
    عادي في logs/backup.log، وأي موظف هيلاقي الإشعار الدائم في الجرس لو
    فشلت المحاولة.
    """
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        from notifications.consumers import STAFF_BROADCAST_GROUP

        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        async_to_sync(channel_layer.group_send)(
            STAFF_BROADCAST_GROUP,
            {'type': 'backup_status', 'status': status, 'message': message},
        )
    except Exception:
        pass


def backup_status():
    """
    حالة مجلد النسخ الاحتياطي الحالي — بتُستخدم في لوحة الحالة بصفحة
    staff:backup_manual عشان الأدمن يتأكد إن الفلاشة (أو أي مسار تاني
    متركّب مكانها) شغالة صح من غير ما يدخل السيرفر بالـ SSH خالص.

    بترجع dict فيه: exists / mounted / writable / free_gb / total_gb /
    backup_count / latest_name / latest_size_mb / latest_at.

    ملحوظة عن 'mounted': بيتحسب وقت تحميل الصفحة بس (os.path.ismount) —
    لو الفلاشة اتقلعت بعد ما الحاوية بدأت شغالة، الفحص هنا ممكن يفضل
    يقول "متوصّل" لحد ما الصفحة تتحمّل تاني أو الحاوية تعيد تشغيلها.
    الفحص الموثوق فعليًا (اللي بيمنع الكتابة فعلًا) هو REQUIRE_MOUNTPOINT
    جوه _preflight_checks تحت، لأنه بيتنفذ من جديد مع كل محاولة نسخ.
    """
    status = {
        'path': str(BACKUP_DIR),
        'exists': BACKUP_DIR.exists(),
        'mounted': False,
        'writable': False,
        'free_gb': None,
        'total_gb': None,
        'backup_count': 0,
        'latest_name': '',
        'latest_size_mb': None,
        'latest_at': None,
    }
    if not status['exists']:
        return status

    status['mounted'] = os.path.ismount(BACKUP_DIR)

    try:
        probe = BACKUP_DIR / '.write_test'
        probe.write_text('ok', encoding='utf-8')
        probe.unlink()
        status['writable'] = True
    except OSError:
        pass

    try:
        usage = shutil.disk_usage(BACKUP_DIR)
        status['free_gb'] = round(usage.free / (1024 ** 3), 1)
        status['total_gb'] = round(usage.total / (1024 ** 3), 1)
    except OSError:
        pass

    files = sorted(BACKUP_DIR.glob('biozone_*.sql.gz'), key=lambda p: p.stat().st_mtime)
    status['backup_count'] = len(files)
    if files:
        latest = files[-1]
        stat = latest.stat()
        status['latest_name'] = latest.name
        status['latest_size_mb'] = round(stat.st_size / (1024 ** 2), 1)
        status['latest_at'] = datetime.fromtimestamp(stat.st_mtime)

    return status


def recent_backups(limit=5):
    """
    بترجع آخر `limit` نسخة احتياطية **موجودة فعليًا على القرص دلوقتي**
    (مش سطور من ملف log قديم ممكن يشاور على ملفات اتمسحت أو اتنقلت).
    كل عنصر: {'name', 'size_mb', 'created_at'}، مرتبة من الأحدث للأقدم.
    """
    if not BACKUP_DIR.exists():
        return []

    files = sorted(
        BACKUP_DIR.glob('biozone_*.sql.gz'),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]

    result = []
    for f in files:
        stat = f.stat()
        result.append({
            'name': f.name,
            'size_mb': round(stat.st_size / (1024 ** 2), 1),
            'created_at': datetime.fromtimestamp(stat.st_mtime),
        })
    return result


def _preflight_checks():
    """بترجع رسالة خطأ (نص) لو فيه مشكلة تمنع البدء، أو None لو كله تمام."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    if REQUIRE_MOUNTPOINT and not os.path.ismount(BACKUP_DIR):
        return f'{BACKUP_DIR} مش Mount Point فعلي — الفلاشة يمكن مش متركّبة.'

    try:
        usage = shutil.disk_usage(BACKUP_DIR)
        free_mb = usage.free / (1024 ** 2)
        if free_mb < MIN_FREE_MB:
            return f'المساحة الفاضية غير كافية (متاح {free_mb:.0f}MB، مطلوب {MIN_FREE_MB}MB على الأقل).'
    except OSError as exc:
        return f'تعذّر التحقق من مساحة {BACKUP_DIR}: {exc}'

    return None


def _run_pg_dump():
    """بتشغّل pg_dump فعليًا وتضغط الناتج. بترجع (success: bool, error_text: str)."""
    db = settings.DATABASES['default']
    today = datetime.now().strftime('%Y-%m-%d')

    # عداد تسلسلي لكل يوم: بنشوف كام نسخة موجودة بالفعل بنفس تاريخ
    # النهاردة ونزود عليهم واحد. لو مفيش نسخ للنهاردة، بيبدأ من 1.
    existing_today = list(BACKUP_DIR.glob(f'biozone_{today}_*.sql.gz'))
    seq = len(existing_today) + 1
    backup_file = BACKUP_DIR / f'biozone_{today}_{seq}.sql.gz'
    # لو حصل تعارض نادر (نفس الرقم موجود فعلًا لأي سبب)، بنزود لحد ما نلاقي رقم فاضي
    while backup_file.exists():
        seq += 1
        backup_file = BACKUP_DIR / f'biozone_{today}_{seq}.sql.gz'

    env = os.environ.copy()
    env['PGPASSWORD'] = db['PASSWORD']
    cmd = [
        'pg_dump',
        '-h', db['HOST'],
        '-p', str(db.get('PORT') or '5432'),
        '-U', db['USER'],
        db['NAME'],
    ]

    dump_proc = None
    gzip_proc = None
    try:
        with open(backup_file, 'wb') as out:
            dump_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            gzip_proc = subprocess.Popen(['gzip'], stdin=dump_proc.stdout, stdout=out)
            dump_proc.stdout.close()  # يسمح لـ gzip يقفل لوحده لو dump_proc وقع
            _, dump_stderr = dump_proc.communicate(timeout=TIMEOUT_SECONDS)
            gzip_proc.communicate(timeout=TIMEOUT_SECONDS)

        if dump_proc.returncode != 0:
            backup_file.unlink(missing_ok=True)
            return False, dump_stderr.decode('utf-8', errors='replace').strip() or 'pg_dump فشل من غير رسالة خطأ واضحة.'
        if gzip_proc.returncode != 0:
            backup_file.unlink(missing_ok=True)
            return False, 'فشل ضغط النسخة (gzip) بعد نجاح pg_dump.'

    except FileNotFoundError:
        return False, 'أداة pg_dump مش متاحة داخل الحاوية — تأكد إن postgresql-client متثبتة (Dockerfile) وإن الصورة اتعمّلها rebuild.'
    except subprocess.TimeoutExpired:
        for p in (dump_proc, gzip_proc):
            if p is not None:
                p.kill()
        backup_file.unlink(missing_ok=True)
        return False, f'تجاوزت العملية {TIMEOUT_SECONDS} ثانية من غير ما تخلص — تم إيقافها.'
    except Exception as exc:
        backup_file.unlink(missing_ok=True)
        return False, f'خطأ غير متوقع أثناء النسخ: {exc}'

    size_mb = backup_file.stat().st_size / (1024 ** 2)
    _log(f'تم بنجاح: {backup_file.name} ({size_mb:.1f} MB)')
    return True, ''


def _cleanup_old_backups():
    """بتمسح النسخ الأقدم من RETENTION_DAYS يوم — نفس منطق backup_db.sh."""
    cutoff = datetime.now().timestamp() - (RETENTION_DAYS * 86400)
    deleted = 0
    for f in BACKUP_DIR.glob('biozone_*.sql.gz'):
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
            deleted += 1
    if deleted:
        _log(f'تم مسح {deleted} نسخة قديمة (أقدم من {RETENTION_DAYS} يوم).')


def perform_backup():
    """
    نقطة الدخول الوحيدة لعمل نسخة احتياطية من جوه Django (زرار التشغيل
    اليدوي أو أمر الكرون). بتبعت حالة لحظية لكل الموظفين المتصلين أول ما
    تبدأ ولما تخلص (نجاح أو فشل).

    عند الفشل، كمان بتسجّل إشعار دائم (يفضل في الجرس حتى لو محدش أونلاين
    دلوقتي) لأصحاب صلاحية 'staff.manage_backup' بس.

    بترجع (success: bool, error_detail: str) — error_detail فاضية عند
    النجاح، أو نص الخطأ الحقيقي عند الفشل.
    """
    _broadcast('running', 'جاري عمل نسخة احتياطية من قاعدة البيانات...')

    error = _preflight_checks()
    if error is None:
        success, error = _run_pg_dump()
    else:
        success = False

    if success:
        LAST_ERROR_FILE.unlink(missing_ok=True)
        _cleanup_old_backups()
        _broadcast('success', 'تم عمل النسخة الاحتياطية بنجاح.')
        return True, ''

    _log(f'فشل النسخ الاحتياطي! {error}')
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    LAST_ERROR_FILE.write_text(error, encoding='utf-8')

    _broadcast('error', 'حصلت مشكلة في النسخ الاحتياطي. جرّب تعمله يدويًا.')

    try:
        from notifications.models import Notification
        from notifications.services import notify_staff_with_perm

        notify_staff_with_perm(
            codename='staff.manage_backup',
            kind=Notification.Kind.BACKUP_FAILED,
            title='فشل النسخ الاحتياطي التلقائي',
            message='جرّب تشغيله يدويًا من صفحة النسخ الاحتياطي.',
            url_name='staff:backup_manual',
        )
    except Exception:
        # لو قاعدة البيانات نفسها اللي واقعة (سبب شائع لفشل النسخ أصلًا)،
        # مش هنقدر نسجّل إشعار فيها برضه — بس ده لازم مايكسرش الاستجابة
        # للموظف (زرار "تشغيل الآن" المفروض يرجّع رسالة لطيفة مش صفحة
        # خطأ 500). last_error.txt والـ log اتسجلوا فوق قبل الخطوة دي.
        pass

    return False, error
