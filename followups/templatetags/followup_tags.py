from django import template
from django.contrib.contenttypes.models import ContentType

from accounts.models import User
from followups.models import FollowUp
from followups.services import followups_for

register = template.Library()


@register.inclusion_tag('followups/_panel.html', takes_context=True)
def followup_panel(context, obj):
    """
    بيعرض المتابعات المجدولة (مفتوحة أولًا، بعدين المنجزة) + فورم جدولة
    متابعة جديدة لأي كيان (عميل حاليًا)، بنفس نمط activity_panel/tag_panel
    بالظبط. الاستخدام في أي template:

        {% load followup_tags %}
        {% followup_panel profile %}

    الـ object لازم يكون له pk بالفعل (مش instance جديد لسه ما اتحفظش).
    """
    content_type = ContentType.objects.get_for_model(obj.__class__)
    followups = followups_for(obj)[:30]
    # الموظفين المسموح تكليفهم بمتابعة — نفس شرط _can_manage_followups
    # (أدمن/مخزن نشطين بس)، مش أي عضو في auth.User.
    employees = User.objects.filter(
        role__in=(User.Role.ADMIN, User.Role.WAREHOUSE), status=User.Status.ACTIVE,
    ).order_by('username')
    request = context.get('request')
    return {
        'followups': followups,
        'employees': employees,
        'activity_type_choices': FollowUp.ActivityType.choices,
        'app_label': content_type.app_label,
        'model_name': content_type.model,
        'object_id': obj.pk,
        'request': request,
    }
