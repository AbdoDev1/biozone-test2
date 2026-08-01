import json

from channels.generic.websocket import AsyncWebsocketConsumer

# مجموعة بث واحدة يشترك فيها كل الموظفين (ADMIN/WAREHOUSE) المتصلين حاليًا
# — مختلفة عن مجموعة الإشعارات الشخصية (notifications_user_<id>) لأن
# الغرض هنا حالة نظام لحظية (زي تقدّم النسخ الاحتياطي) مش إشعار شخصي
# متخزّن، فمحتاجة توصل لكل حد أونلاين دلوقتي بغض النظر عن صلاحياته.
STAFF_BROADCAST_GROUP = 'staff_broadcast'


class NotificationConsumer(AsyncWebsocketConsumer):
    """
    اتصال WebSocket واحد لكل مستخدم مسجّل دخول (موظف أو عميل)، بينضم
    لمجموعة خاصة بيه (`notifications_user_<id>`) وبس بيستقبل إشارة خفيفة
    ("فيه جديد") لما notifications.services تنشئ إشعار له.

    الرسالة نفسها مفيش فيها بيانات الإشعار — الجرس (bell.html) لما يستقبلها
    بينادي notifications:bell_data زي ما بيعمل أصلاً في الـ polling القديم،
    عشان يفضل مصدر واحد بس للحقيقة (نفس الـ endpoint، نفس الـ serialization)
    بدل ما نكرر منطق تجهيز البيانات هنا كمان جوه consumer منفصل.

    الموظفين (ADMIN/WAREHOUSE) كمان بينضموا لمجموعة بث عامة (STAFF_BROADCAST_GROUP)
    عشان يستقبلوا حالات نظام لحظية عامة (زي بدء/انتهاء النسخ الاحتياطي) —
    راجع handler الـ backup_status تحت، وstaff/services/backup.py للمصدر.
    """

    async def connect(self):
        user = self.scope.get('user')
        if user is None or not user.is_authenticated:
            # زائر مش مسجل دخول حاول يفتح الاتصال — نرفضه بهدوء، مفيش
            # داعي إشعارات لمستخدم مجهول أصلًا.
            await self.close()
            return

        self.group_name = f'notifications_user_{user.pk}'
        await self.channel_layer.group_add(self.group_name, self.channel_name)

        self.is_staff_member = getattr(user, 'role', None) in ('ADMIN', 'WAREHOUSE')
        if self.is_staff_member:
            await self.channel_layer.group_add(STAFF_BROADCAST_GROUP, self.channel_name)

        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        if getattr(self, 'is_staff_member', False):
            await self.channel_layer.group_discard(STAFF_BROADCAST_GROUP, self.channel_name)

    async def notify(self, event):
        """Handler للرسايل اللي نوعها 'notify' الجاية من channel layer group
        (شوف notifications/services.py — _push_realtime)."""
        await self.send(text_data=json.dumps({'event': 'new_notification'}))

    async def backup_status(self, event):
        """
        Handler لحالة النسخ الاحتياطي اللحظية (running/success/error)
        المبعوتة لمجموعة STAFF_BROADCAST_GROUP بالكامل (شوف
        staff/services/backup.py — _broadcast). على عكس notify فوق،
        الرسالة هنا فيها البيانات نفسها (status/message) مباشرة، مش
        بس إشارة "روح اجيب التفاصيل" — لأن الحالة دي لحظية وبتختفي،
        مالهاش سجل في قاعدة البيانات نرجع نجيبه منه زي الإشعارات العادية.
        """
        await self.send(text_data=json.dumps({
            'event': 'backup_status',
            'status': event['status'],
            'message': event['message'],
        }))
