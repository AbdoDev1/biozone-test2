#!/bin/bash
# سكريبت نسخ احتياطي لقاعدة بيانات Biozone.
#
# بيعمل pg_dump كامل لقاعدة البيانات (من داخل حاوية db بتاعة docker-compose)،
# يضغطه (gzip)، ويحفظه في مجلد backups/ جوه المشروع بتاريخ ووقت في اسم الملف.
# وبعدين بيمسح تلقائيًا أي نسخة أقدم من RETENTION_DAYS يوم عشان القرص متمتلاش.
#
# الاستخدام (لازم تشغّله من نفس مجلد المشروع، جنب docker-compose.yml):
#   ./scripts/backup_db.sh
#
# للتشغيل التلقائي اليومي، ضيفه في crontab (راجع docs/تجهيز-النشر-للسيرفر-الحقيقي.md).

set -euo pipefail

# --- الإعدادات ---
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env.production"
BACKUP_DIR="$PROJECT_DIR/backups"
RETENTION_DAYS="${RETENTION_DAYS:-14}"   # عدد الأيام اللي بنحتفظ فيها بالنسخ قبل ما نمسحها
# لو الفلاشة متركّبة فعليًا كـ mount point منفصل (راجع تعليق أعلى
# docker-compose.yml)، خلي REQUIRE_MOUNTPOINT=true — ساعتها السكريبت
# هيرفض يكمّل لو $BACKUP_DIR مجرد مجلد عادي على قرص السيرفر (يعني
# الفلاشة اتقلعت)، بدل ما يكتب هناك من غير ما حد ياخد باله. زي
# RETENTION_DAYS بالظبط، بتتحدد في سطر crontab نفسه مش في .env.production
# (مثال: `REQUIRE_MOUNTPOINT=true MIN_FREE_MB=1000 /path/to/backup_db.sh`).
# سايبينها false افتراضيًا عشان تفضل شغالة وانت لسه بتظبط الإعداد بتاعك.
REQUIRE_MOUNTPOINT="${REQUIRE_MOUNTPOINT:-false}"
MIN_FREE_MB="${MIN_FREE_MB:-500}"        # أقل مساحة فاضية (ميجا) مطلوبة قبل ما نبدأ
LOG_FILE="$PROJECT_DIR/logs/backup.log"
# نص الخطأ الحقيقي (stderr بتاع pg_dump) عند الفشل بس — منفصل عن
# backup.log لأن ده مخصص لصفحة "إعادة المحاولة اليدوية" في لوحة الموظفين
# (staff/views/backup.py)، اللي بتوفره كملف تحميل لصاحب صلاحية
# 'staff.manage_backup' بدل ما يحتاج يدخل السيرفر يقرا اللوج مباشرة.
LAST_ERROR_FILE="$BACKUP_DIR/last_error.txt"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') | $1" | tee -a "$LOG_FILE"
}

# بتبلّغ نظام الإشعارات جوه Django (بث لحظي + جرس عند الفشل) بنتيجة
# المحاولة دي، عن طريق أمر report_backup_result (راجع تعليقه للتفاصيل).
# بتتنفذ من غير ما توقف السكريبت لو فشلت هي نفسها (مثلاً حاوية web واقعة
# أصلًا — سبب شائع لفشل النسخ) عشان النتيجة الحقيقية تفضل متسجلة في
# backup.log وlast_error.txt بغض النظر عن نجاح الإبلاغ نفسه.
notify_success() {
    docker compose exec -T web python manage.py report_backup_result --success --file "$1" >/dev/null 2>&1 \
        || log "تنبيه: تعذّر إبلاغ نظام الإشعارات بنجاح النسخة (الحاوية web شغالة؟)."
}
notify_error() {
    docker compose exec -T web python manage.py report_backup_result --error "$1" >/dev/null 2>&1 \
        || log "تنبيه: تعذّر إبلاغ نظام الإشعارات بفشل النسخة (الحاوية web شغالة؟)."
}

cd "$PROJECT_DIR"

mkdir -p "$BACKUP_DIR" "$PROJECT_DIR/logs"

if [ ! -f "$ENV_FILE" ]; then
    log "خطأ: ملف $ENV_FILE مش موجود. لازم تشغّل السكريبت من مجلد المشروع."
    notify_error "ملف $ENV_FILE مش موجود على السيرفر."
    exit 1
fi

# قراءة بيانات القاعدة من .env.production (نفس الملف اللي Django بيستخدمه)
DB_NAME=$(grep -E '^DB_NAME=' "$ENV_FILE" | cut -d '=' -f2-)
DB_USER=$(grep -E '^DB_USER=' "$ENV_FILE" | cut -d '=' -f2-)
DB_PASSWORD=$(grep -E '^DB_PASSWORD=' "$ENV_FILE" | cut -d '=' -f2-)

if [ -z "$DB_NAME" ] || [ -z "$DB_USER" ]; then
    log "خطأ: DB_NAME أو DB_USER مش موجودين في $ENV_FILE."
    notify_error "DB_NAME أو DB_USER مش موجودين في $ENV_FILE."
    exit 1
fi

# --- فحوصات ما قبل النسخ: منع الكتابة الصامتة على قرص السيرفر لو
#     الفلاشة اتقلعت، ومنع بدء عملية هتفشل نص الطريق لمساحة ناقصة ---
if [ "$REQUIRE_MOUNTPOINT" = "true" ] && ! mountpoint -q "$BACKUP_DIR"; then
    log "خطأ: $BACKUP_DIR مش Mount Point فعلي — الفلاشة يمكن مش متركّبة. تم إيقاف النسخ قبل ما يبدأ."
    echo "الفلاشة مش متركّبة في $BACKUP_DIR. راجع /etc/fstab أو وصّل الفلاشة تاني." > "$LAST_ERROR_FILE"
    notify_error "الفلاشة مش متركّبة في $BACKUP_DIR. راجع /etc/fstab أو وصّل الفلاشة تاني."
    exit 1
fi

AVAILABLE_MB=$(df -Pm "$BACKUP_DIR" | tail -1 | awk '{print $4}')
if [ "$AVAILABLE_MB" -lt "$MIN_FREE_MB" ]; then
    log "خطأ: المساحة الفاضية في $BACKUP_DIR أقل من الحد الأدنى ($MIN_FREE_MB MB، متاح فعليًا: ${AVAILABLE_MB}MB)."
    echo "المساحة الفاضية غير كافية (متاح ${AVAILABLE_MB}MB، مطلوب ${MIN_FREE_MB}MB على الأقل). فرّغ مساحة أو غيّر الفلاشة." > "$LAST_ERROR_FILE"
    notify_error "المساحة الفاضية غير كافية (متاح ${AVAILABLE_MB}MB، مطلوب ${MIN_FREE_MB}MB على الأقل)."
    exit 1
fi

# اسم الملف: تاريخ + ساعة بس (من غير دقايق/ثواني) عشان يبقى سهل القراءة
# بالعين المجردة، زي biozone_2026-08-06_22h.sql.gz. لو حصل نادرًا نسختين
# في نفس الساعة (تشغيل يدوي من الداشبورد جنب تشغيلة الكرون مثلاً)، بنضيف
# رقم تسلسلي (_2، _3، ...) بدل ما نكتب فوق النسخة الأولى.
TIMESTAMP=$(date '+%Y-%m-%d_%Hh')
BACKUP_FILE="$BACKUP_DIR/biozone_${TIMESTAMP}.sql.gz"
SEQ=2
while [ -e "$BACKUP_FILE" ]; do
    BACKUP_FILE="$BACKUP_DIR/biozone_${TIMESTAMP}_${SEQ}.sql.gz"
    SEQ=$((SEQ + 1))
done

log "== بدء النسخ الاحتياطي: $DB_NAME =="

ERROR_TMP=$(mktemp)
if docker compose exec -T -e PGPASSWORD="$DB_PASSWORD" db \
        pg_dump -U "$DB_USER" "$DB_NAME" 2>"$ERROR_TMP" | gzip > "$BACKUP_FILE"; then
    SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    log "تم بنجاح: $BACKUP_FILE ($SIZE)"
    # لو نجحت المحاولة دي بعد فشل سابق، نمسح ملف الخطأ القديم عشان صفحة
    # "إعادة المحاولة اليدوية" ماتفضلش عارضة خطأ قديم اتحل بالفعل.
    rm -f "$LAST_ERROR_FILE" "$ERROR_TMP"
    notify_success "$(basename "$BACKUP_FILE")"
else
    log "فشل النسخ الاحتياطي! التفاصيل الكاملة في $LAST_ERROR_FILE"
    cp "$ERROR_TMP" "$LAST_ERROR_FILE" 2>/dev/null || echo 'تعذّر التقاط نص الخطأ.' > "$LAST_ERROR_FILE"
    notify_error "$(cat "$LAST_ERROR_FILE" 2>/dev/null | head -c 1500)"
    rm -f "$BACKUP_FILE" "$ERROR_TMP"
    exit 1
fi

# مسح النسخ الأقدم من RETENTION_DAYS يوم
DELETED=$(find "$BACKUP_DIR" -name "biozone_*.sql.gz" -mtime "+$RETENTION_DAYS" -print -delete | wc -l)
if [ "$DELETED" -gt 0 ]; then
    log "تم مسح $DELETED نسخة قديمة (أقدم من $RETENTION_DAYS يوم)."
fi

log "== انتهى =="
