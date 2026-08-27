"""
مهام Celery في تطبيق notifications.

fanout_trim_and_push_task: بتنفذ التقليم (trim) وبث الإشعار اللحظي
(WebSocket push) لدفعة من المستلمين — منقولة من _fanout_trim_and_push
(كانت threading.Thread(daemon=True) خام جوه notifications/services.py)
لـCelery task حقيقية (منقول من mg — راجع تقرير الديون التقنية بند
"Fan-out الإشعارات"). المشاكل الثلاث اللي كانت في التصميم القديم:

1. غير محدود (unbounded): loop واحد بيلف على كل عميل نشط دفعة واحدة، من
   غير أي حد أقصى لحجم الدفعة أو مهلة زمنية — notify_all_clients هنا
   بقت بتقسّم المستلمين لدفعات (NOTIFY_FANOUT_CHUNK_SIZE) وتبعت كل دفعة
   كـtask منفصلة، فحجم أي تنفيذ واحد محدود ومعروف مقدمًا.
2. من غير retry: الـthread القديم كان daemon — لو الـprocess اتعاد تشغيله
   (زي إعادة تشغيل celery-worker بعد مهمة تانية) أثناء ما هو شغال، بيتقفل
   فورًا من غير أي أثر ولا استكمال. Celery task عادية بتفضل موجودة في
   الطابور (Redis broker) لحد ما تتنفذ بنجاح فعليًا، وبـmax_retries هنا
   لو فشلت المهمة كلها (زي انقطاع اتصال قاعدة البيانات في نص التنفيذ).
3. من غير observability موثوقة: نتيجة الـthread القديم متوصلش لحد —
   مفيش logging ولا أي أثر يتراجَع لو فشل. هنا بقى فيه logging على مستوى
   الدفعة (كام عملية فشلت من إجمالي الدفعة) + حالة/سجل المهمة نفسها في
   Celery (نتيجة، إعادة محاولات، استثناء لو حصل) بدل ما تختفي بصمت.

التقليم والبوش الفردي لكل مستلم (_trim_old، _push_realtime) لسه best-effort
بنفس فلسفتهم الأصلية بالظبط (لو مستلم واحد فشل، الباقي يكمل عادي) — الفرق
هنا في مستوى الدفعة/التنفيذ ككل، مش في السلوك الفردي لكل عميل.
"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30, soft_time_limit=300, time_limit=360)
def fanout_trim_and_push_task(self, recipient_ids):
    # ملحوظة: من غير connections.close_all() هنا عمدًا — ده كان لازم بس
    # لما الكود ده كان شغال جوه threading.Thread خام (كل thread ياخد
    # connection منفصلة تلقائيًا من Django، ولازم تتقفل يدويًا في النهاية
    # عشان متتسربش). جوه Celery task عادية، الـconnection بتتدار زي أي
    # task تانية في المشروع (CONN_MAX_AGE، راجع config/settings.py) —
    # نفس باقي مهام notifications/products/staff، مفيش حاجة منها بتقفل
    # الاتصال يدويًا.
    from .services import _push_realtime, _trim_old

    failed = 0
    try:
        for recipient_id in recipient_ids:
            try:
                _trim_old(recipient_id)
            except Exception:
                failed += 1
                logger.exception(
                    'notifications.fanout: trim failed for recipient_id=%s', recipient_id,
                )
            _push_realtime(recipient_id)  # best-effort بالفعل جوه نفسها (راجع docstring في services.py)
    except Exception as exc:
        # فشل غير متوقع أثرّ على الدفعة كلها (زي انقطاع اتصال قاعدة
        # البيانات) — مش فشل فردي لمستلم واحد (ده متلقّط فوق). نعيد
        # المحاولة بدل ما نسيب الدفعة دي تضيع بصمت زي الـthread القديم.
        logger.exception('notifications.fanout: batch of %s failed, retrying', len(recipient_ids))
        raise self.retry(exc=exc)

    if failed:
        logger.warning(
            'notifications.fanout: trim failed for %s/%s recipients in this batch',
            failed, len(recipient_ids),
        )
