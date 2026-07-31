"""
دوال مساعدة للمتابعات المجدولة — الواجهة الوحيدة اللي المفروض أي view
يستخدمها بدل ما ينشئ FollowUp يدويًا في كل مكان (نفس فكرة activity/services.py
و tags/services.py بالظبط).
"""
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from .models import FollowUp


def followups_for(instance):
    """كل متابعات instance (مفتوحة ومنجزة)، المفتوحة أولًا ثم الأقرب استحقاقًا."""
    content_type = ContentType.objects.get_for_model(instance.__class__)
    return FollowUp.objects.filter(
        content_type=content_type, object_id=instance.pk,
    ).select_related('assigned_to', 'done_by').open_first()


def open_followups_count_for(instance):
    """عدد المتابعات المفتوحة على instance — للعدادات في تابات صفحات التفاصيل."""
    content_type = ContentType.objects.get_for_model(instance.__class__)
    return FollowUp.objects.filter(
        content_type=content_type, object_id=instance.pk,
    ).open().count()


def create_followup(instance, *, activity_type, due_date, assigned_to, note='', user=None):
    content_type = ContentType.objects.get_for_model(instance.__class__)
    return FollowUp.objects.create(
        content_type=content_type, object_id=instance.pk,
        activity_type=activity_type, due_date=due_date,
        assigned_to=assigned_to, note=note, created_by=user,
    )


def mark_done(followup, user):
    followup.done_at = timezone.now()
    followup.done_by = user
    followup.save(update_fields=['done_at', 'done_by'])


def delete_followups_for(instance):
    """
    بتمسح كل متابعات instance قبل ما هو نفسه يتمسح — لازمة لأن الربط عن
    طريق ContentType عام (object_id) مش FK حقيقي، فمفيش CASCADE تلقائي
    من قاعدة البيانات وقت حذف السجل الأصلي (نفس منطق
    activity.delete_activity_logs_for و tags.delete_tagged_items_for).
    """
    content_type = ContentType.objects.get_for_model(instance.__class__)
    FollowUp.objects.filter(content_type=content_type, object_id=instance.pk).delete()
