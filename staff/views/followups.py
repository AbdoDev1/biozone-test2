from django.contrib.contenttypes.models import ContentType
from django.core.paginator import Paginator
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from followups.models import FollowUp
from staff.permissions import perm_required

STAFF_LIST_PAGE_SIZE = 30


def _attach_targets(followups):
    """
    بتجهّز رابط ووصف الكيان المرتبط بكل متابعة، دفعة واحدة بدل استعلام
    لكل صف على حدة (N+1) — الكيان الوحيد المستخدم حاليًا هو ClientProfile،
    فبيتم تجميع IDs بتاعته في استعلام واحد. لو كيان تاني اتضاف بعدين
    (زي منتج محتاج متابعة توريد خاصة)، يتضاف بنفس الأسلوب بسطرين هنا.
    """
    from accounts.models import ClientProfile

    followups = list(followups)
    client_ct = ContentType.objects.get_for_model(ClientProfile)
    client_ids = [f.object_id for f in followups if f.content_type_id == client_ct.id]
    clients_by_id = {}
    if client_ids:
        clients_by_id = {
            c.pk: c for c in ClientProfile.objects.filter(pk__in=client_ids).only('pk', 'business_name')
        }
    for f in followups:
        if f.content_type_id == client_ct.id:
            client = clients_by_id.get(f.object_id)
            f.target_label = client.business_name if client else 'عميل محذوف'
            f.target_url = reverse('staff:client_detail', args=[f.object_id]) if client else None
        else:
            f.target_label = f'{f.content_type.name} #{f.object_id}'
            f.target_url = None
    return followups


@perm_required('followups.view_followup')
def followup_list(request):
    """
    صفحة "المتابعات" — تجميع كل المتابعات المجدولة (اتصال/زيارة/متابعة
    سداد) في مكان واحد بدل تتبّعها بالذاكرة (راجع "مرحلة 7" في
    ROADMAP.md). الجدولة والإنجاز الفعليين بيحصلوا من فورم المتابعة على
    صفحة الكيان نفسه (مثلًا تاب "المتابعات" في clients/detail.html) —
    الصفحة دي للقراءة والمتابعة اليومية بس، بنفس فكرة activity_list/tag_list
    (نسخة staff مبسطة، مش فورم إدارة كامل).
    """
    followups_qs = FollowUp.objects.select_related('content_type', 'assigned_to', 'created_by')

    scope = request.GET.get('scope', 'mine')
    if scope == 'mine':
        followups_qs = followups_qs.filter(assigned_to=request.user)

    today = timezone.localdate()
    status = request.GET.get('status', 'open')
    if status == 'open':
        followups_qs = followups_qs.filter(done_at__isnull=True)
    elif status == 'overdue':
        followups_qs = followups_qs.filter(done_at__isnull=True, due_date__lt=today)
    elif status == 'done':
        followups_qs = followups_qs.filter(done_at__isnull=False)
    # status == 'all' بيسيب الكل من غير أي فلترة إضافية

    followups_qs = followups_qs.order_by('due_date', 'id')

    paginator = Paginator(followups_qs, STAFF_LIST_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))
    _attach_targets(page_obj.object_list)

    return render(request, 'staff/followups/list.html', {
        'page_obj': page_obj,
        'followups': page_obj,
        'scope': scope,
        'status': status,
        'today': today,
    })
