from dataclasses import dataclass
from typing import Optional

from django.core.paginator import Paginator
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from accounts.models import Employee
from activity.models import ActivityLog
from activity.services import diff_summary
from activity.services import log_activity
from inventory.models import Inventory, PriceChange, StockMovement
from products.models import Product, ProductUnit
from staff.permissions import perm_required
from staff.reports_queries import resolve_period, PERIOD_CHOICES
from staff.utils import list_qs, url_with_qs, redirect_with_qs

_TRACKED_INVENTORY_FIELDS = ['min_quantity', 'is_available']

STAFF_LIST_PAGE_SIZE = 30

# نوع "حركة" وهمي (مش من StockMovement.MovementType) بيمثّل تغيير سعر
# (PriceChange) — بيتعامل معاه زي أي نوع حركة تاني في سجل حركات المخزون
# (فلتر النوع، البادج، ...) رغم إنه مش StockMovement فعليًا ومالوش أي أثر
# على رصيد المخزون. راجع inventory.models.PriceChange لسبب وجوده كموديل
# مستقل بدل ما يتضاف كـ MovementType جديد على StockMovement نفسه (اللي
# بنيته وvalidation بتاعته مبنية على وجود كمية فعلية بتتطبّق على الرصيد).
PRICE_CHANGE_KIND = 'PRICE_CHANGE'
PRICE_CHANGE_LABEL = 'تغيير سعر'

# سجل حركات المخزون بيغطي حركات الرصيد الفعلية (StockMovement: وارد/صادر/
# حجز/إلغاء حجز) + تغييرات السعر (PriceChange) كعنصر مستقل جنبها — تعديل
# إعدادات الصنف (الحد الأدنى/الإتاحة) هو بس اللي فضل في تاب "سجل النشاط"
# الخاص بكل صنف (activity/_panel.html)، لأنه مش حركة ولا سعر.
ALL_MOVEMENT_TYPES = list(StockMovement.MovementType.choices) + [(PRICE_CHANGE_KIND, PRICE_CHANGE_LABEL)]


@dataclass
class MovementFeedRow:
    """صف عرض واحد لحركة مخزون فعلية (StockMovement) في سجل حركات المخزون."""
    kind: str
    type_display: str
    product_name: str
    inventory_pk: int
    unit_name: str
    quantity_display: str
    pieces_display: str
    note: str
    created_by: Optional[object]
    created_at: object


def _stock_movement_to_row(movement):
    return MovementFeedRow(
        kind=movement.movement_type,
        type_display=movement.get_movement_type_display(),
        product_name=movement.inventory.product.display_name,
        inventory_pk=movement.inventory_id,
        unit_name=movement.unit.name,
        quantity_display=str(movement.quantity),
        pieces_display=str(movement.stock_qty),
        note=movement.note or '—',
        created_by=movement.created_by,
        created_at=movement.created_at,
    )


def _price_change_to_row(change):
    """صف عرض واحد لتغيير سعر (PriceChange) — نفس شكل صف حركة المخزون
    بالظبط، عشان الاتنين يتعرضوا في نفس الجدول من غير أي تفريع في التمبلت.
    'الكمية' هنا مش كمية فعلية، فبتتعرض كـ 'من X إلى Y' بدل رقم، وعمود
    '= بالقطعة' مالوش معنى هنا (السعر مش رصيد) فبيتعرض '—'."""
    return MovementFeedRow(
        kind=PRICE_CHANGE_KIND,
        type_display=PRICE_CHANGE_LABEL,
        product_name=change.inventory.product.display_name,
        inventory_pk=change.inventory_id,
        unit_name=change.unit.name,
        quantity_display=f'من {change.old_price} إلى {change.new_price}',
        pieces_display='—',
        note=change.note or '—',
        created_by=change.created_by,
        created_at=change.created_at,
    )


@perm_required('inventory.view_inventory')
def inventory_list(request):
    """
    صفحة المخزون الرئيسية — تبويبين ("المخزون" و"سجل الحركات") جوه نفس
    الصفحة/الـURL بدل ما يكونوا صفحتين منفصلتين، عشان التنقل بينهم يبقى
    زرار فلتر عادي (GET) مش خطوة تنقل لمكان تاني في السيستم. كل تبويب ليه
    فلاتر وترقيم صفحات مستقلين، فمفيش تعارض بينهم ومفيش استعلامين
    بيتنفذوا مع بعض من غير داعي.
    """
    tab = request.GET.get('tab')
    if tab != 'movements':
        tab = 'stock'

    context = {'active_tab': tab}
    if tab == 'movements':
        context.update(_movements_tab_context(request))
    else:
        context.update(_stock_tab_context(request))

    return render(request, 'staff/inventory/list.html', context)


def _stock_tab_context(request):
    items_qs = Inventory.objects.select_related(
        'product__category'
    ).prefetch_related('product__units').order_by('product__name_ar')

    search_q = request.GET.get('q', '').strip()
    if search_q:
        # البحث بيغطي اسم الصنف (عربي/إنجليزي)، الكود الداخلي (BZ-00001)،
        # والباركود — ده اللي بيسمح بالبحث المباشر بقارئ الباركود: القارئ
        # بيكتب الرقم في خانة البحث ويبعت Enter تلقائيًا، فبيترجم لنفس طلب
        # البحث العادي من غير أي كود إضافي.
        items_qs = items_qs.filter(
            Q(product__name_ar__icontains=search_q)
            | Q(product__name_en__icontains=search_q)
            | Q(product__code__icontains=search_q)
            | Q(product__barcode__iexact=search_q)
            | Q(product__barcode_2__iexact=search_q)
            | Q(product__barcode_3__iexact=search_q)
        )

    # فلتر "المخزون المنخفض بس" — ده اللي بتودّي له لوحة التحكم برابط
    # "عرض الكل" بدل ما تجيب كل الأصناف المنخفضة في صفحة واحدة من غير حد.
    low_only = request.GET.get('low') == '1'
    if low_only:
        items_qs = items_qs.low_stock()

    paginator = Paginator(items_qs, STAFF_LIST_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    return {
        'items': page_obj,
        'page_obj': page_obj,
        'search_q': search_q,
        'low_only': low_only,
    }


def _filtered_by_common(qs, search_q, start, end, employee_id):
    """فلاتر البحث/الفترة/الموظف المشتركة بين StockMovement وPriceChange —
    الاتنين عندهم نفس أسماء الحقول (inventory__product__..، created_at،
    created_by_id) فمفيش داعي نكرر نفس السلسلة مرتين."""
    if search_q:
        qs = qs.filter(
            Q(inventory__product__name_ar__icontains=search_q)
            | Q(inventory__product__name_en__icontains=search_q)
            | Q(inventory__product__code__icontains=search_q)
            | Q(inventory__product__barcode__iexact=search_q)
            | Q(inventory__product__barcode_2__iexact=search_q)
            | Q(inventory__product__barcode_3__iexact=search_q)
        )
    if start:
        qs = qs.filter(created_at__gte=start)
    if end:
        qs = qs.filter(created_at__lte=end)
    if employee_id:
        qs = qs.filter(created_by_id=employee_id)
    return qs


def _movements_tab_context(request):
    """
    سجل كل حركات المخزون (كل الأصناف مع بعض) قابل للبحث والفلترة بالنوع —
    بيغطي كل مصادر الحركة تلقائيًا (تسجيل يدوي، رصيد ابتدائي عند إضافة
    صنف/وحدة جديدة، استيراد Excel، تسليم الطلبات) لأنها كلها بتتسجّل عن
    طريق StockMovement.save() نفسها (راجع inventory/models.py)، فمفيش
    مصدر بيتفوت من هنا. مصدر كل حركة (وارد من المخزن، استيراد Excel،
    تسليم طلب...) واضح من عمود "ملاحظة" لأن كل مسار تلقائي بيسجّل ملاحظته
    الخاصة وقت الإنشاء.
    تغييرات السعر (PriceChange) بتتضاف جنب حركات الرصيد الفعلية في نفس
    السجل والفلتر (نوع "تغيير سعر") — راجع inventory.models.PriceChange
    وPRICE_CHANGE_KIND فوق.
    عكس inventory_detail اللي بيعرض آخر 20 حركة لصنف واحد بس، التبويب ده
    مخصص لمراجعة الحركة على مستوى المخزون كله.
    """
    search_q = request.GET.get('q', '').strip()

    movement_type = request.GET.get('type', '').strip()
    valid_types = set(StockMovement.MovementType.values) | {PRICE_CHANGE_KIND}
    if movement_type not in valid_types:
        movement_type = ''

    # نفس شريط الفلاتر الموحّد المستخدم في قسم التقارير (الفترة الزمنية
    # الجاهزة + فترة مخصصة + الموظف) — resolve_period هي نفس الدالة
    # المستخدمة في staff/reports_queries.py، فمفيش منطق تاريخ مكرر.
    start, end, period = resolve_period(request)
    employee_id = request.GET.get('employee', '').strip()

    rows = []

    # لو الفلتر محدد نوع حركة رصيد فعلي (IN/OUT/..)، أو مفيش فلتر خالص
    # (كل الأنواع)، نجيب StockMovement. لو الفلتر "تغيير سعر" بالظبط،
    # نتجاهل StockMovement تمامًا (مفيش داعي نستعلم عليه أصلًا).
    if movement_type != PRICE_CHANGE_KIND:
        movements_qs = StockMovement.objects.select_related(
            'inventory__product', 'unit', 'created_by'
        ).order_by('-created_at')
        movements_qs = _filtered_by_common(movements_qs, search_q, start, end, employee_id)
        if movement_type:
            movements_qs = movements_qs.filter(movement_type=movement_type)
        rows.extend(_stock_movement_to_row(m) for m in movements_qs)

    # لو الفلتر محدد "تغيير سعر" بالظبط، أو مفيش فلتر خالص، نجيب PriceChange.
    if not movement_type or movement_type == PRICE_CHANGE_KIND:
        price_changes_qs = PriceChange.objects.select_related(
            'inventory__product', 'unit', 'created_by'
        ).order_by('-created_at')
        price_changes_qs = _filtered_by_common(price_changes_qs, search_q, start, end, employee_id)
        rows.extend(_price_change_to_row(c) for c in price_changes_qs)

    # الاتنين اتجابوا مرتبين لوحدهم (كل واحد بترتيب -created_at خاص بيه)،
    # فبعد الدمج لازم نرتب تاني عشان يفضلوا متداخلين صح مع بعض بالتاريخ.
    rows.sort(key=lambda r: r.created_at, reverse=True)

    paginator = Paginator(rows, STAFF_LIST_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    return {
        'movements': page_obj,
        'page_obj': page_obj,
        'search_q': search_q,
        'movement_type': movement_type,
        'movement_types': ALL_MOVEMENT_TYPES,
        'period_choices': PERIOD_CHOICES,
        'selected_period': period,
        'date_from': request.GET.get('date_from', '').strip(),
        'date_to': request.GET.get('date_to', '').strip(),
        'employees': Employee.objects.all().order_by('username'),
        'selected_employee': employee_id,
    }


@perm_required('inventory.view_inventory')
def inventory_detail(request, pk):
    item = get_object_or_404(Inventory, pk=pk)
    movements_qs = item.movements.select_related('created_by', 'unit').order_by('-created_at')
    price_changes_qs = item.price_changes.select_related('created_by', 'unit').order_by('-created_at')

    # تاب "الحركات" هنا بيجمع حركات الرصيد الفعلية (StockMovement) وتغييرات
    # السعر (PriceChange) مع بعض في نفس القائمة — تعديل إعدادات الصنف (الحد
    # الأدنى/الإتاحة) هو بس اللي فضل ظاهر في تاب "سجل النشاط"
    # (activity/_panel.html) جنبه، مش هنا (راجع نفس القرار في
    # _movements_tab_context فوق).
    movements_count = movements_qs.count() + price_changes_qs.count()
    rows = [_stock_movement_to_row(m) for m in movements_qs[:20]]
    rows.extend(_price_change_to_row(c) for c in price_changes_qs[:20])
    rows.sort(key=lambda r: r.created_at, reverse=True)
    movements = rows[:20]
    units = list(item.product.units.all())

    return render(request, 'staff/inventory/detail.html', {
        'item': item,
        'movements': movements,
        'movements_count': movements_count,
        'units': units,
        # بيحافظ على رقم صفحة/بحث قائمة المخزون اللي جاي منها المستخدم،
        # عشان رابط "المخزون" في breadcrumb يرجعه لنفس المكان بدل صفحة 1.
        'back_url': url_with_qs(request, 'staff:inventory'),
        'list_qs': list_qs(request),
    })


@perm_required('inventory.change_inventory')
def update_settings(request, pk):
    """
    تعديل الحد الأدنى للصنف (min_quantity) وإتاحة ظهوره في المتجر
    (is_available) — كان ده بيتعدّل من لوحة أدمن دجانجو، ودلوقتي منقول
    لصفحة تفاصيل المخزون نفسها (المخزون فقط، مش أي موديل تاني).
    """
    item = get_object_or_404(Inventory, pk=pk)
    if request.method == 'POST':
        try:
            min_quantity = int(request.POST.get('min_quantity', item.min_quantity))
            if min_quantity < 0:
                raise ValueError
        except (TypeError, ValueError):
            messages.error(request, 'الحد الأدنى يجب أن يكون رقمًا صحيحًا غير سالب.')
            return redirect_with_qs(request, 'staff:inventory_detail', pk=pk)

        old_values = {f: getattr(item, f) for f in _TRACKED_INVENTORY_FIELDS}
        item.min_quantity = min_quantity
        item.is_available = request.POST.get('is_available') == 'on'
        item.save(update_fields=['min_quantity', 'is_available'])
        summary = diff_summary(old_values, item, _TRACKED_INVENTORY_FIELDS)
        if summary:
            log_activity(item, ActivityLog.Event.UPDATED, user=request.user, changes_summary=summary)
        messages.success(request, 'تم تحديث إعدادات الصنف بنجاح.')
    return redirect_with_qs(request, 'staff:inventory_detail', pk=pk)


@perm_required('inventory.add_stockmovement')
def add_movement(request, pk):
    item = get_object_or_404(Inventory, pk=pk)
    if request.method == 'POST':
        movement_type = request.POST.get('movement_type')
        note = request.POST.get('note', '')
        unit_id = request.POST.get('unit_id')

        manual_allowed_types = {
            StockMovement.MovementType.IN,
            StockMovement.MovementType.OUT,
            StockMovement.MovementType.RESERVE,
            StockMovement.MovementType.RELEASE,
        }
        if movement_type not in manual_allowed_types:
            messages.error(request, 'نوع الحركة غير صحيح')
            return redirect_with_qs(request, 'staff:inventory_detail', pk=pk)

        unit = ProductUnit.objects.filter(pk=unit_id, product_id=item.product_id).first()
        if not unit:
            messages.error(request, 'يرجى اختيار الوحدة (كرتونة/قطعة) التي سُجّلت بها الكمية')
            return redirect_with_qs(request, 'staff:inventory_detail', pk=pk)

        try:
            quantity = int(request.POST.get('quantity', 0))
        except (TypeError, ValueError):
            messages.error(request, 'الكمية غير صحيحة')
            return redirect_with_qs(request, 'staff:inventory_detail', pk=pk)

        # نقفل صف المخزون فعليًا (select_for_update) طول مدة التحقق والحفظ،
        # بنفس الأسلوب المستخدم في orders/models.py و orders/views.py.
        # ده بيمنع تعارض لو موظفين اتنين سجّلوا حركة يدوية على نفس الصنف
        # في نفس اللحظة: الاتنين كانوا ممكن يعدّوا فحص "الكمية كافية؟"
        # بنفس القيمة القديمة قبل ما أي حركة تتسجل فعليًا.
        with transaction.atomic():
            locked_item = Inventory.objects.select_for_update().get(pk=item.pk)

            movement = StockMovement(
                inventory=locked_item,
                unit=unit,
                movement_type=movement_type,
                quantity=quantity,
                note=note,
                created_by=request.user,
            )
            # StockMovement.save() بقت بتنادي full_clean() تلقائيًا (راجع
            # inventory/models.py)، فبنمسك ValidationError من هنا مباشرة
            # بدل ما ننادي full_clean() يدويًا قبلها.
            try:
                movement.save()
            except ValidationError as e:
                for err in e.messages:
                    messages.error(request, err)
                return redirect_with_qs(request, 'staff:inventory_detail', pk=pk)

        messages.success(request, 'تم تسجيل الحركة بنجاح')
        return redirect_with_qs(request, 'staff:inventory_detail', pk=pk)
    return redirect_with_qs(request, 'staff:inventory_detail', pk=pk)
