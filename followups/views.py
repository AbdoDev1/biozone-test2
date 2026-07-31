from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.shortcuts import get_object_or_404, redirect
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from accounts.models import User
from activity.models import ActivityLog
from activity.services import log_activity

from .models import FollowUp
from .services import create_followup, mark_done

NOTE_MAX_LENGTH = 255


def _can_manage_followups(user):
    """نفس شرط activity.views.add_note / tags.views: أي موظف (أدمن/مخزن) — مش عميل."""
    return user.is_authenticated and user.role in (User.Role.ADMIN, User.Role.WAREHOUSE)


@login_required
@require_POST
def followup_add(request, app_label, model_name, object_id):
    """
    نقطة نهاية عامة واحدة لجدولة متابعة على أي كيان في النظام (عميل
    حاليًا)، بنفس نمط activity.views.add_note و tags.views.tag_add.
    """
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or 'staff:dashboard'

    if not _can_manage_followups(request.user):
        messages.error(request, 'ليس لديك صلاحية جدولة متابعات.')
        return redirect(next_url)

    content_type = get_object_or_404(ContentType, app_label=app_label, model=model_name)
    model_class = content_type.model_class()
    instance = get_object_or_404(model_class, pk=object_id)

    activity_type = request.POST.get('activity_type', '').strip()
    due_date_raw = request.POST.get('due_date', '').strip()
    assigned_to_id = request.POST.get('assigned_to', '').strip()
    note = request.POST.get('note', '').strip()

    if activity_type not in dict(FollowUp.ActivityType.choices):
        messages.error(request, 'يرجى اختيار نوع صحيح للمتابعة.')
        return redirect(next_url)

    due_date = parse_date(due_date_raw)
    if not due_date:
        messages.error(request, 'يرجى تحديد تاريخ استحقاق صحيح.')
        return redirect(next_url)

    assigned_to = User.objects.filter(
        pk=assigned_to_id, role__in=(User.Role.ADMIN, User.Role.WAREHOUSE), status=User.Status.ACTIVE,
    ).first()
    if not assigned_to:
        messages.error(request, 'يرجى اختيار الموظف المسؤول عن المتابعة.')
        return redirect(next_url)

    if len(note) > NOTE_MAX_LENGTH:
        messages.error(request, f'التفاصيل طويلة جدًا ({NOTE_MAX_LENGTH} حرف كحد أقصى).')
        return redirect(next_url)

    followup = create_followup(
        instance, activity_type=activity_type, due_date=due_date,
        assigned_to=assigned_to, note=note, user=request.user,
    )
    log_activity(
        instance, ActivityLog.Event.UPDATED, user=request.user,
        changes_summary=(
            f'تمت جدولة متابعة ({followup.get_activity_type_display()}) '
            f'بتاريخ {due_date} للموظف {assigned_to.username}'
        ),
    )
    messages.success(request, 'تم جدولة المتابعة بنجاح.')
    return redirect(next_url)


@login_required
@require_POST
def followup_done(request, pk):
    """يعلّم متابعة كمنجزة — أي موظف (مش لازم يكون هو المسؤول عنها، زي أي عملية إدارية تانية في النظام)."""
    followup = get_object_or_404(FollowUp, pk=pk)
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or 'staff:dashboard'

    if not _can_manage_followups(request.user):
        messages.error(request, 'ليس لديك صلاحية تحديث المتابعات.')
        return redirect(next_url)

    if not followup.is_done:
        mark_done(followup, request.user)
        instance = followup.content_object
        if instance is not None:
            log_activity(
                instance, ActivityLog.Event.UPDATED, user=request.user,
                changes_summary=f'تم إنجاز متابعة ({followup.get_activity_type_display()})',
            )
        messages.success(request, 'تم تسجيل المتابعة كمنجزة.')
    return redirect(next_url)


@login_required
@require_POST
def followup_delete(request, pk):
    """إلغاء متابعة (لو اتجدولت غلط أو بقت غير لازمة) — مش تسجيلها منجزة."""
    followup = get_object_or_404(FollowUp, pk=pk)
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or 'staff:dashboard'

    if not _can_manage_followups(request.user):
        messages.error(request, 'ليس لديك صلاحية إلغاء المتابعات.')
        return redirect(next_url)

    followup.delete()
    messages.success(request, 'تم إلغاء المتابعة.')
    return redirect(next_url)
