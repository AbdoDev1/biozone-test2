from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone


class FollowUpQuerySet(models.QuerySet):
    def open(self):
        """المتابعات لسه ما اتنجزتش (مفيش done_at) — دي اللي لسه محتاجة انتباه."""
        return self.filter(done_at__isnull=True)

    def overdue(self):
        """مفتوحة وتاريخ استحقاقها فات بالفعل."""
        return self.open().filter(due_date__lt=timezone.localdate())

    def due_today_or_overdue(self):
        """مفتوحة ومستحقة النهاردة أو قبل كده — دي اللي المفروض تتعرض كـ'محتاجة اهتمام دلوقتي'."""
        return self.open().filter(due_date__lte=timezone.localdate())

    def open_first(self):
        """
        بترتب المفتوحة قبل المنجزة (بغض النظر عن ترتيب due_date الطبيعي)،
        وجوه كل مجموعة بترتب بتاريخ الاستحقاق. مفيدة لعرض تايم لاين على
        صفحة كيان معيّن (followups/_panel.html) — المفتوحة تبان الأول
        كإجراء مطلوب، والمنجزة تبقى أرشيف تحتها. بنستخدم Case/When بدل
        الاعتماد على سلوك NULLS FIRST/LAST المختلف بين قواعد البيانات.
        """
        return self.annotate(
            _open_first=Case(
                When(done_at__isnull=True, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        ).order_by('_open_first', 'due_date', 'id')


class FollowUp(models.Model):
    """
    متابعة مجدولة (فكرة "Activity" من Odoo) — مهمة مستقبلية مربوطة بموظف
    مسؤول وتاريخ استحقاق ("اتصل بالعميل X يوم كذا")، بتحل مشكلة تتبّع
    حالات العملاء المعلّقة (PENDING) والمتأخرين في السداد اللي كانت بتتم
    بالذاكرة بس (راجع "مرحلة 7" في ROADMAP.md).

    مختلفة عن activity.ActivityLog عن قصد: ActivityLog سجل تاريخي لما
    *حصل بالفعل* (Audit + Chatter)، أما FollowUp مهمة *لسه هتحصل*، ليها
    تاريخ استحقاق وموظف مسؤول وحالة إنجاز — مش نفس الغرض، فمش هنلخبطهم
    في موديل واحد.

    عام (ContentType-based) بنفس فكرة activity.ActivityLog و
    tags.TaggedItem — يشتغل على أي كيان (عميل حاليًا) من غير أي migration
    إضافية لو احتجنا كيان تاني بعدين (مثلًا منتج محتاج طلب توريد خاص).
    """
    class ActivityType(models.TextChoices):
        CALL = 'CALL', 'مكالمة هاتفية'
        VISIT = 'VISIT', 'زيارة'
        PAYMENT_FOLLOWUP = 'PAYMENT_FOLLOWUP', 'متابعة سداد'
        OTHER = 'OTHER', 'أخرى'

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')

    activity_type = models.CharField(
        max_length=20, choices=ActivityType.choices, default=ActivityType.CALL,
        verbose_name='نوع المتابعة',
    )
    note = models.CharField(max_length=255, blank=True, verbose_name='تفاصيل مختصرة')
    due_date = models.DateField(verbose_name='تاريخ الاستحقاق', db_index=True)

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='followups_assigned',
        verbose_name='الموظف المسؤول',
    )

    done_at = models.DateTimeField(null=True, blank=True, verbose_name='وقت الإنجاز')
    done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='followups_completed',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='followups_created',
    )

    objects = FollowUpQuerySet.as_manager()

    class Meta:
        verbose_name = 'متابعة'
        verbose_name_plural = 'المتابعات'
        ordering = ['due_date', 'id']
        indexes = [
            models.Index(fields=['content_type', 'object_id']),
        ]

    def __str__(self):
        return f'{self.get_activity_type_display()} — {self.content_type.name} #{self.object_id} ({self.due_date})'

    @property
    def is_done(self):
        return self.done_at is not None

    @property
    def is_overdue(self):
        """متأخرة = لسه مفتوحة واستحقاقها فات — المنجزة (حتى لو بعد الميعاد) مش متأخرة."""
        return (not self.is_done) and self.due_date < timezone.localdate()

    @property
    def is_due_today(self):
        return (not self.is_done) and self.due_date == timezone.localdate()
