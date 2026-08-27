"""
دوال مساعدة لإرسال الإشعارات — أي app في النظام يقدر يستدعيها من غير ما
يعرف تفاصيل موديل Notification. الاستيراد بيبقى جوه الدالة نفسها (مش
أعلى الملف) في الأماكن اللي بتستخدمها من apps تانية، عشان نتفادى أي
circular import بين orders/accounts/notifications.
"""
from accounts.models import User

from .models import Notification

# أقصى عدد إشعارات بيتم الاحتفاظ بيه لكل مستخدم — أي حاجة أقدم من كده
# بتتمسح تلقائيًا أول ما تتخطى الحد ده (راجع _trim_old أسفل).
MAX_NOTIFICATIONS_PER_USER = 100

# حجم أقصى للدفعة اللي notify_all_clients بتبعتها لـfanout_trim_and_push_task
# (notifications/tasks.py) دفعة واحدة — بدل ما تبعت كل العملاء النشطين في
# task واحدة غير محدودة، بتتقسم لدفعات بالحجم ده. راجع docstring
# notify_all_clients تحت وnotifications/tasks.py لتفاصيل كاملة.
NOTIFY_FANOUT_CHUNK_SIZE = 300


def _push_realtime(recipient_id):
    """
    بتبعت إشارة فورية (event خفيف من غير بيانات) لجرس الإشعارات المفتوح
    فعليًا (WebSocket) عند المستخدم ده، عشان يعمل refresh() لنفسه على طول
    بدل ما يستنى الـ polling الدوري (15 ثانية). البيانات الحقيقية بتتجاب
    زي ما هي دايمًا من notifications:bell_data — الرسالة دي بس "تنبيه"،
    مفيش تكرار لمنطق الـ serialization هنا.

    الاستيراد جوه الدالة (مش أعلى الملف) نفس أسلوب باقي الملف ده، وكمان
    عشان لو الـ channel layer مش شغال لأي سبب (Redis واقع مثلًا)، بنمتص
    الاستثناء بهدوء — الإشعار نفسه اتسجل في الداتابيز بنجاح بالفعل، وأي
    جرس مفتوح هيلحقه لاحقًا من نفس الـ polling العادي (fallback).
    """
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        async_to_sync(channel_layer.group_send)(
            f'notifications_user_{recipient_id}',
            {'type': 'notify'},
        )
    except Exception:
        pass


def _trim_old(recipient_id, keep=MAX_NOTIFICATIONS_PER_USER):
    """
    بتفضّل أحدث `keep` إشعار للمستخدم بس وتمسح الباقي. بتتنادى بعد أي
    إنشاء إشعار (واحد أو bulk) عشان الجدول ميكبرش من غير حد أقصى.
    """
    old_ids = list(
        Notification.objects.filter(recipient_id=recipient_id)
        .order_by('-created_at', '-pk')
        .values_list('pk', flat=True)[keep:]
    )
    if old_ids:
        Notification.objects.filter(pk__in=old_ids).delete()


def notify(recipient, kind, title, message='', url_name='', url_kwargs=None, exclude_actor=None):
    """
    إشعار لمستخدم واحد. لو exclude_actor اتبعت وكان هو نفسه الـ recipient
    (يعني الشخص هو اللي عمل الحدث بنفسه) مبنبعتش إشعار — مفيش داعي نقول
    للعميل "وافقت على تعديلك" وهو اللي عمل الفعل بنفسه دلوقتي.
    """
    if exclude_actor is not None and getattr(exclude_actor, 'pk', None) == getattr(recipient, 'pk', None):
        return None
    if recipient is None:
        return None
    notification = Notification.objects.create(
        recipient=recipient,
        kind=kind,
        title=title,
        message=message,
        url_name=url_name,
        url_kwargs=url_kwargs or {},
    )
    _trim_old(recipient.pk)
    _push_realtime(recipient.pk)
    return notification


def notify_staff_with_perm(codename, kind, title, message='', url_name='', url_kwargs=None, exclude_actor=None):
    """
    بتبعت نفس الإشعار لكل الموظفين النشطين اللي عندهم الصلاحية المطلوبة
    (الأدمن دايمًا مستلم لأنه Superuser، والمخزن لازم يكون عنده الصلاحية
    صراحةً — شوف staff.permissions.PERMISSION_SECTIONS).
    """
    staff = User.objects.filter(
        role__in=[User.Role.ADMIN, User.Role.WAREHOUSE], is_active=True
    )
    notifications = []
    for user in staff:
        if exclude_actor is not None and user.pk == getattr(exclude_actor, 'pk', None):
            continue
        if user.has_perm(codename):
            notifications.append(Notification(
                recipient=user, kind=kind, title=title, message=message,
                url_name=url_name, url_kwargs=url_kwargs or {},
            ))
    if notifications:
        Notification.objects.bulk_create(notifications)
        for n in notifications:
            _trim_old(n.recipient_id)
            _push_realtime(n.recipient_id)
    return notifications


def notify_all_clients(kind, title, message='', url_name='', url_kwargs=None):
    """
    بتبعت نفس الإشعار لكل العملاء النشطين (role=CLIENT, status=ACTIVE).
    استخدام حالي: تنبيه العملاء بوارد جديد بعد استيراد أصناف من إكسل
    (راجع staff/views/products.py — import_products_confirm)، لكنها عامة
    وممكن تتستخدم لأي إشعار جماعي تاني للعملاء مستقبلًا.
    bulk_create عشان لو الكتالوج/قاعدة العملاء كبرت، السطر ده يفضل عملية
    واحدة على قاعدة البيانات بدل استعلام منفصل لكل عميل.

    التسجيل نفسه (bulk_create) بيحصل هنا بشكل متزامن (عشان الإشعار يبقى
    موجود في قاعدة البيانات فورًا لحظة رجوع الدالة — أي كود بينادي الدالة
    دي ويتأكد بعدها من وجود الإشعار هيلاقيه موجود). لكن التقليم (trim)
    والبوش اللحظي (WebSocket) لكل عميل بيتأجّلوا لـCelery (راجع
    notifications/tasks.py — fanout_trim_and_push_task)، مقسّمين لدفعات
    من NOTIFY_FANOUT_CHUNK_SIZE مستلم — قبل كده كانت الدالة دي بتشغّل
    threading.Thread(daemon=True) خام بتلف على كل عميل نشط دفعة واحدة من
    غير حد أقصى ولا retry ولا أي أثر لو فشلت (منقول من mg — راجع
    docstring notifications/tasks.py لتفاصيل المشاكل الثلاث اللي كانت فيها
    والحل الكامل).
    """
    clients = User.objects.filter(role=User.Role.CLIENT, status=User.Status.ACTIVE, is_active=True)
    notifications = [
        Notification(
            recipient=client, kind=kind, title=title, message=message,
            url_name=url_name, url_kwargs=url_kwargs or {},
        )
        for client in clients
    ]
    if notifications:
        Notification.objects.bulk_create(notifications)
        from .tasks import fanout_trim_and_push_task
        recipient_ids = [n.recipient_id for n in notifications]
        for i in range(0, len(recipient_ids), NOTIFY_FANOUT_CHUNK_SIZE):
            fanout_trim_and_push_task.delay(recipient_ids[i:i + NOTIFY_FANOUT_CHUNK_SIZE])
    return notifications
