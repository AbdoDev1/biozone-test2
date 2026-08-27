#!/bin/bash
set -e

# ROLE بيتحدد من متغير SERVICE_ROLE في docker-compose.yml لكل خدمة:
#   store  -> gunicorn بيخدم زوار المتجر (نفس دور "web" القديم قبل الفصل)
#   staff  -> gunicorn بيخدم لوحة الموظفين (nginx بيوجّه /staff/ هنا فقط)
#   celery -> Celery worker (يشيل المهام الطويلة زي استيراد الإكسل)
# مفيش قيمة افتراضية غير معرّفة عمدًا — كل خدمة في docker-compose.yml
# لازم تحدد SERVICE_ROLE بنفسها صراحة.
ROLE="${SERVICE_ROLE:?لازم تحدد SERVICE_ROLE (store / staff / celery) في docker-compose.yml}"

echo "== انتظار قاعدة البيانات =="
until python manage.py migrate --check 2>/dev/null || python manage.py migrate --noinput; do
  echo "قاعدة البيانات لسه مش جاهزة، بحاول تاني بعد 2 ثانية..."
  sleep 2
done

# الميجريشن وcollectstatic وحساب الأدمن بيتنفذوا مرة واحدة بس، من خدمة
# "store" حصريًا. لو الاتنين (store وstaff) نفذوهم مع بعض وقت الإقلاع،
# ممكن يحصل تعارض على قفل جدول الميجريشن في بوستجريس. خدمتي staff
# وcelery بينتظروا store يبقى healthy (depends_on في docker-compose.yml)
# قبل ما يستلموا حمل فعلي، فالميجريشن بتكون خلصت أصلاً وقت ما يشتغلوا.
if [ "$ROLE" = "store" ]; then
    echo "== تطبيق الميجريشن =="
    python manage.py migrate --noinput

    echo "== تجميع الملفات الثابتة =="
    python manage.py collectstatic --noinput --clear

    # تأكيد وجود حساب الأدمن تلقائيًا لو متغيرات البيئة متحددة. آمن يتنفذ
    # كل مرة (get_or_create + تحديث) — مش هيبوّظ حساب موجود ولا يعمل تكرار.
    if [ -n "${DJANGO_ADMIN_USERNAME:-}" ] && [ -n "${DJANGO_ADMIN_PASSWORD:-}" ]; then
        echo "== التأكد من وجود حساب الأدمن =="
        python manage.py ensure_admin
    fi
fi

if [ "$ROLE" = "celery" ]; then
    CONCURRENCY="${CELERY_CONCURRENCY:-2}"
    # CELERY_QUEUES بيحدد الطوابير اللي الـworker ده بيستهلك منها —
    # 'celery' هو الطابور الافتراضي في Celery (استيراد/تصدير المنتجات
    # والتقارير ونشر الإشعارات الجماعية). لو سايبها فاضية بيرجع لـ'celery'
    # بس، عشان أي خدمة قديمة SERVICE_ROLE=celery من غير المتغير ده تفضل
    # شغالة بنفس السلوك السابق تمامًا. راجع docker-compose.yml —
    # celery-worker-backup لسبب الفصل الكامل (منقول من mg: worker واحد
    # بيشارك فيه الاستيراد/التصدير والنسخ الاحتياطي، فمهمة نسخ احتياطي
    # طويلة كانت ممكن تأخر استيراد/تصدير موظف تاني مستني في نفس الطابور).
    QUEUES="${CELERY_QUEUES:-celery}"
    echo "== تشغيل Celery worker (concurrency: $CONCURRENCY, queues: $QUEUES) =="
    exec celery -A config worker -l info --concurrency "$CONCURRENCY" -Q "$QUEUES"
fi

# عدد الـ workers مستقل لكل خدمة: GUNICORN_WORKERS_STORE أو
# GUNICORN_WORKERS_STAFF حسب الدور. لو مش متحدد، بنحسبه تلقائيًا من عدد
# أنوية المعالج الفعلية للحاوية بمعادلة gunicorn المعتمدة: (2×الأنوية)+1
# — نفس منطق الحساب التلقائي القديم، بس دلوقتي منفصل لكل خدمة عشان
# store وstaff يقدروا ياخدوا عدد مختلف حسب أولوية كل واحدة.
ROLE_UPPER=$(echo "$ROLE" | tr '[:lower:]' '[:upper:]')
WORKERS_VAR="GUNICORN_WORKERS_${ROLE_UPPER}"
if [ -n "${!WORKERS_VAR:-}" ]; then
    WORKERS="${!WORKERS_VAR}"
else
    CORES=$(nproc 2>/dev/null || echo 1)
    WORKERS=$((2 * CORES + 1))
fi
echo "== تشغيل gunicorn ($ROLE) بعدد workers: $WORKERS (أنوية متاحة: $(nproc 2>/dev/null || echo '؟')) =="

# config.asgi:application بدل config.wsgi:application، و worker class بقى
# uvicorn (عبر حزمة uvicorn-worker) بدل الـ worker المتزامن الافتراضي —
# ده اللي بيخلي gunicorn يقدر يستضيف اتصالات WebSocket (جرس الإشعارات
# اللحظي) جنب طلبات HTTP العادية في نفس الوقت، مع الاحتفاظ بنفس منطق
# gunicorn لإدارة عدد الـ workers وإعادة التشغيل عند الأعطال.
exec gunicorn config.asgi:application \
    --worker-class uvicorn_worker.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --workers "$WORKERS" \
    --timeout 60 \
    --max-requests 1000 \
    --max-requests-jitter 100 \
    --access-logfile - \
    --error-logfile -
