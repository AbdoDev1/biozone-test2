from django.contrib.contenttypes.models import ContentType
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render

from accounts.models import User
from activity.models import ActivityLog
from staff.permissions import perm_required

STAFF_LIST_PAGE_SIZE = 30


def _attach_entity_labels(logs):
    """
    بيجهّز اسم الكيان الفعلي (مش رقم الـ id بس) لكل سجل نشاط دفعة واحدة —
    مجمّعة حسب نوع الكيان (content_type) بدل استعلام منفصل لكل صف، بنفس
    أسلوب _attach_targets في staff/views/followups.py (لأن الاتنين شغالين
    على GenericForeignKey). لو الكيان الأصلي اتمسح، بيرجع None وبتظهر
    الحالة دي في التمبليت بدل ما يتفكك الصف كله بخطأ.
    """
    logs = list(logs)
    ids_by_ct = {}
    content_types_by_id = {}
    for log in logs:
        ids_by_ct.setdefault(log.content_type_id, set()).add(log.object_id)
        content_types_by_id[log.content_type_id] = log.content_type

    objects_by_ct = {}
    for content_type_id, ids in ids_by_ct.items():
        model_class = content_types_by_id[content_type_id].model_class()
        if model_class is None:
            continue
        objects_by_ct[content_type_id] = {
            obj.pk: obj for obj in model_class.objects.filter(pk__in=ids)
        }

    for log in logs:
        entity = objects_by_ct.get(log.content_type_id, {}).get(log.object_id)
        log.entity_label = str(entity) if entity is not None else None
    return logs


@perm_required('activity.view_activitylog')
def activity_list(request):
    """
    نسخة مبسطة من ActivityLogAdmin (كانت في /admin/activity/activitylog/) —
    عرض/بحث/فلترة بس، بنفس القيود اللي كانت موجودة في الـ admin نفسه:
    السجل للقراءة فقط من هنا (has_add_permission=False هناك)، مفيش إضافة
    ولا تعديل ولا حذف يدوي — السجل بيتكتب من الكود بس وقت الحفظ (راجع
    activity/services.py)، فمفيش داعي أي فورم هنا أصلًا.
    """
    # بيانات تسعير/خصومات حساسة — مش مطلوب تظهر في سجل الأنشطة العام خالص،
    # حتى بشكل مختصر (راجع ActivityLogQuerySet.exclude_pricing_details).
    logs = ActivityLog.objects.exclude_pricing_details().select_related('content_type', 'created_by')

    search_q = request.GET.get('q', '').strip()
    event_filter = request.GET.get('event', '')
    content_type_filter = request.GET.get('content_type', '')
    created_by_filter = request.GET.get('created_by', '')

    if search_q:
        logs = logs.filter(Q(note__icontains=search_q) | Q(changes_summary__icontains=search_q))
    if event_filter:
        logs = logs.filter(event=event_filter)
    if content_type_filter:
        logs = logs.filter(content_type_id=content_type_filter)
    if created_by_filter:
        logs = logs.filter(created_by_id=created_by_filter)

    # قائمة أنواع الكيانات اللي فعلاً ليها سجلات نشاط (بدل كل ContentType
    # المسجل في النظام)، عشان الفلتر يعرض بس اختيارات ذات معنى.
    content_type_choices = ContentType.objects.filter(
        pk__in=ActivityLog.objects.values_list('content_type_id', flat=True).distinct()
    ).order_by('model')

    # نفس الفكرة لفلتر "بواسطة": بس الموظفين اللي فعلاً سجّلوا حركة، مش
    # كل موظفي النظام.
    created_by_choices = User.objects.filter(
        pk__in=ActivityLog.objects.exclude(created_by__isnull=True).values_list('created_by_id', flat=True).distinct()
    ).order_by('username')

    paginator = Paginator(logs, STAFF_LIST_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))
    _attach_entity_labels(page_obj.object_list)

    return render(request, 'staff/activity/list.html', {
        'page_obj': page_obj,
        'logs': page_obj,
        'search_q': search_q,
        'event_filter': event_filter,
        'content_type_filter': content_type_filter,
        'created_by_filter': created_by_filter,
        'event_choices': ActivityLog.Event.choices,
        'content_type_choices': content_type_choices,
        'created_by_choices': created_by_choices,
    })
