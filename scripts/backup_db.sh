#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env.production"
BACKUP_DIR="$PROJECT_DIR/backups"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
REQUIRE_MOUNTPOINT="${REQUIRE_MOUNTPOINT:-false}"
MIN_FREE_MB="${MIN_FREE_MB:-500}"
LOG_FILE="$PROJECT_DIR/logs/backup.log"
LAST_ERROR_FILE="$BACKUP_DIR/last_error.txt"
# نفس ملف القفل بالظبط اللي staff/services/backup.py بيستخدمه (logs/.backup.lock)
# — مقصود يتحط في logs/ مش backups/ عشان يفضل شغال حتى لو الفلاشة نفسها
# مش متركّبة أو بصيغة FAT32/exFAT بدل ext4 (راجع تعليق LOCK_FILE في
# backup.py للتفاصيل). logs/ متعمول لها bind mount في docker-compose.yml
# زي backups/ بالظبط، فالسكريبت ده (شغال على الـ host) وperform_backup()
# (شغالة جوه الحاوية) بيقفلوا نفس الملف الحقيقي على نفس القرص.
LOCK_FILE="$PROJECT_DIR/logs/.backup.lock"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') | $1" | tee -a "$LOG_FILE"
}

notify_success() {
    local out
    # web-store هي الخدمة الوحيدة اللي مضمون وجودها ومستقرة دايمًا (راجع
    # docker-compose.yml — الاتنين التانيين depends_on عليها) بعد تقسيم
    # الخدمة الواحدة "web" لـ web-store/web-staff/celery-worker (مرحلة 1
    # من خطة الأداء) — أي واحدة فيهم كانت هتشتغل بنفس الشكل لأمر manage.py
    # لمرة واحدة زي ده، لكن web-store الأضمن وجودًا.
    if ! out=$(docker compose exec -T web-store python manage.py report_backup_result --success --file "$1" 2>&1); then
        log "تنبيه: تعذّر إبلاغ نظام الإشعارات بنجاح النسخة. التفاصيل: $(echo "$out" | tr '\n' ' ' | head -c 300)"
    fi
}
notify_error() {
    local out
    if ! out=$(docker compose exec -T web-store python manage.py report_backup_result --error "$1" 2>&1); then
        log "تنبيه: تعذّر إبلاغ نظام الإشعارات بفشل النسخة. التفاصيل: $(echo "$out" | tr '\n' ' ' | head -c 300)"
    fi
}

cd "$PROJECT_DIR"
mkdir -p "$BACKUP_DIR" "$PROJECT_DIR/logs"

# قفل بسيط عشان نمنع تشغيلتين سوا (السكريبت ده + الزرار اليدوي/الكرون
# جوه Django) — لو محاولة تانية شغالة بالفعل، نسيب اللي شغالة تكمل ونطلع
# من غير ما نعتبرها "فشل" (exit 0، مش 1، عشان ماي فعّلش تنبيهات مراقبة
# خارجية زي cron email على حاجة مش خطأ حقيقي).
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    log "تم تجاهل هذه المحاولة: في نسخة احتياطية تانية شغالة بالفعل (يدوي عن طريق Django أو محاولة تانية) — استنى لحد ما تخلص."
    exit 0
fi

if [ ! -f "$ENV_FILE" ]; then
    log "خطأ: ملف $ENV_FILE مش موجود. لازم تشغّل السكريبت من مجلد المشروع."
    notify_error "ملف $ENV_FILE مش موجود على السيرفر."
    exit 1
fi

DB_NAME=$(grep -E '^DB_NAME=' "$ENV_FILE" | cut -d '=' -f2-)
DB_USER=$(grep -E '^DB_USER=' "$ENV_FILE" | cut -d '=' -f2-)
DB_PASSWORD=$(grep -E '^DB_PASSWORD=' "$ENV_FILE" | cut -d '=' -f2-)

if [ -z "$DB_NAME" ] || [ -z "$DB_USER" ]; then
    log "خطأ: DB_NAME أو DB_USER مش موجودين في $ENV_FILE."
    notify_error "DB_NAME أو DB_USER مش موجودين في $ENV_FILE."
    exit 1
fi

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
    rm -f "$LAST_ERROR_FILE" "$ERROR_TMP"
    notify_success "$(basename "$BACKUP_FILE")"
else
    log "فشل النسخ الاحتياطي! التفاصيل الكاملة في $LAST_ERROR_FILE"
    cp "$ERROR_TMP" "$LAST_ERROR_FILE" 2>/dev/null || echo 'تعذّر التقاط نص الخطأ.' > "$LAST_ERROR_FILE"
    notify_error "$(cat "$LAST_ERROR_FILE" 2>/dev/null | head -c 1500)"
    rm -f "$BACKUP_FILE" "$ERROR_TMP"
    exit 1
fi

DELETED=$(find "$BACKUP_DIR" -mindepth 1 -type d -name lost+found -prune \
    -o -type f -name "biozone_*.sql.gz" -mtime "+$RETENTION_DAYS" -print -delete \
    | wc -l) || DELETED=0
if [ "$DELETED" -gt 0 ]; then
    log "تم مسح $DELETED نسخة قديمة (أقدم من $RETENTION_DAYS يوم)."
fi

log "== انتهى =="
