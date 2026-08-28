import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import CharField, Value
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse

from invoices.models import InvoiceReversal
from orders.models import Order, OrderItem
from staff.permissions import perm_required
from tags.services import tags_for_many

STAFF_LIST_PAGE_SIZE = 30
ITEMS_PER_PRINT_PAGE = 14  # لو الأصناف زادت عن كده، النسخة القابلة للطباعة بتتقسم لصفحات مرقّمة 1/ن، 2/ن...
ITEMS_PER_DETAIL_PAGE = 20  # ترقيم صفحات جدول الأصناف في تفاصيل الطلب (تفادي صفحة طويلة جدًا لو الطلب فيه أصناف كتير)


@perm_required('orders.view_order')
def order_list(request):
    """
    ملحوظة أداء مهمة (قبل التعديل ده كان بيجيب *كل* الطلبات + *كل* أصنافها
    + *كل* إشعارات المرتجع من أول يوم في الميموري في كل مرة الصفحة دي
    بتتفتح، بس عشان يعرض 30 صف. مع نمو عدد الطلبات ده كان بيبقى أبطأ
    وأبطأ تدريجيًا (ولحد timeout فعلي). الحل: الترتيب والـpagination
    بيحصلوا على مستوى قاعدة البيانات (مش Python list + sort يدوي)،
    والـselect_related/prefetch_related التقيلة (items، client، إلخ)
    بتتحصر بس على أقصى 30 صف (صفوف الصفحة الحالية)، مش كل الجدول.
    """
    status = request.GET.get('status', '')

    if status:
        # تبويب فلترة بحالة معينة: طلبات بس (بدون مرتجعات، زي الأول) —
        # الترتيب والـpagination بيحصلوا على مستوى قاعدة البيانات مباشرة
        # (LIMIT/OFFSET)، مش بجلب كل الطلبات المطابقة في الميموري الأول.
        ordered_qs = Order.objects.filter(status=status).order_by('-created_at')
        paginator = Paginator(ordered_qs, STAFF_LIST_PAGE_SIZE)
        page_obj = paginator.get_page(request.GET.get('page'))

        # جلب التفاصيل الكاملة (select_related/prefetch_related) بس
        # لطلبات الصفحة الحالية (30 كحد أقصى)، مش كل الطلبات المطابقة للفلتر.
        page_ids_in_order = [order.pk for order in page_obj]
        orders_by_id = {
            order.pk: order
            for order in Order.objects.filter(pk__in=page_ids_in_order)
            .select_related('client')
            .prefetch_related('items')
        }
        rows = [
            {'kind': 'order', 'obj': orders_by_id[pk], 'created_at': orders_by_id[pk].created_at}
            for pk in page_ids_in_order
        ]
    else:
        # تبويب "الكل": طلبات + إشعارات مرتجع مرتبين زمنيًا مع بعض (شوف
        # التعليق الأصلي تحت). عشان نرتب وندي صفحة من غير ما نجيب الجدولين
        # كاملين، بنعمل union خفيف (id + kind + created_at بس، من غير أي
        # select_related/prefetch_related تقيلة) على مستوى قاعدة البيانات،
        # ونطبّق الـpagination عليه — وبعدين نجيب التفاصيل الكاملة بس
        # لصفوف الصفحة الحالية (30 كحد أقصى)، مش كل السجلات.
        #
        # إشعارات المرتجع بتتحط في نفس قائمة الطلبات كصف مستقل (رقم الإشعار،
        # العميل، القيمة، التاريخ) — من غير تفاصيل الأصناف المرتجعة (دي موجودة
        # في صفحة طباعة الإشعار نفسها لو الستاف عايز يفتحها). بتظهر بس في تبويب
        # "الكل" لأن حالات الطلب (PENDING/CONFIRMED/...) مش منطبقة على إشعار مرتجع.
        #
        # .order_by() فاضي هنا مهم: InvoiceReversal.Meta.ordering معرّف
        # افتراضيًا (['-created_at']) وبيتسرّب كـORDER BY جوه كل استعلام
        # فرعي من استعلامات الـunion لو ماتعملهوش reset صراحة — وده ممنوع
        # قواعديًا في استعلامات UNION (SQLite بيرفضه بخطأ صريح، وPostgres
        # بيرفض الشكل ده كمان إلا لو ملفوف بقوسين مع LIMIT). الترتيب
        # الفعلي للنتيجة النهائية بيتحدد بـ.order_by('-created_at') على
        # الـcombined queryset تحت، مش هنا.
        orders_idx = Order.objects.order_by().annotate(
            kind=Value('order', output_field=CharField()),
        ).values('id', 'created_at', 'kind')
        reversals_idx = InvoiceReversal.objects.order_by().annotate(
            kind=Value('return', output_field=CharField()),
        ).values('id', 'created_at', 'kind')
        combined = orders_idx.union(reversals_idx).order_by('-created_at')

        paginator = Paginator(combined, STAFF_LIST_PAGE_SIZE)
        page_obj = paginator.get_page(request.GET.get('page'))
        page_index_rows = list(page_obj)

        page_order_ids = [r['id'] for r in page_index_rows if r['kind'] == 'order']
        page_reversal_ids = [r['id'] for r in page_index_rows if r['kind'] == 'return']

        orders_by_id = {
            order.pk: order
            for order in Order.objects.filter(pk__in=page_order_ids)
            .select_related('client')
            .prefetch_related('items')
        }
        reversals_by_id = {
            reversal.pk: reversal
            for reversal in InvoiceReversal.objects.filter(pk__in=page_reversal_ids)
            .select_related('invoice__order__client')
        }

        rows = []
        for r in page_index_rows:
            if r['kind'] == 'order':
                rows.append({'kind': 'order', 'obj': orders_by_id[r['id']], 'created_at': r['created_at']})
            else:
                rows.append({'kind': 'return', 'obj': reversals_by_id[r['id']], 'created_at': r['created_at']})

    # وسم كل طلب في الصفحة الحالية باستعلام واحد بدل ما نستدعي tags_for
    # لكل صف على حدة (N+1) — الوسوم بتتعرض صغيرة تحت رقم الطلب في الجدول.
    order_ids_on_page = [row['obj'].pk for row in rows if row['kind'] == 'order']
    tags_by_order_id = tags_for_many(Order, order_ids_on_page)
    for row in rows:
        if row['kind'] == 'order':
            row['obj'].tag_list = tags_by_order_id.get(row['obj'].pk, [])

    context = {
        'rows': rows,
        'page_obj': page_obj,
        'selected_status': status,
        'status_choices': Order.Status.choices,
    }
    return render(request, 'staff/orders/list.html', context)


@perm_required('orders.view_order')
def order_print(request, pk):
    """
    نسخة قابلة للطباعة من الطلب — لتسهيل المراجعة اليدوية على المخزن
    أثناء التحضير أو قبل اتخاذ قرار التأكيد/الرفض. بدون WeasyPrint،
    بنفس أسلوب invoices/print.html (window.print() من المتصفح).

    لو عدد الأصناف أكتر من ITEMS_PER_PRINT_PAGE، بتتقسم لصفحات طباعة
    منفصلة مرقّمة "1/ن"، "2/ن"...، والإجمالي بيظهر في آخر صفحة بس.
    """
    order = get_object_or_404(
        Order.objects.select_related('client', 'client__client_profile').prefetch_related(
            'items__product_unit__product__inventory'
        ),
        pk=pk,
    )
    all_items = list(order.items.all())
    for idx, item in enumerate(all_items, start=1):
        item.display_index = idx
    item_pages = [
        all_items[i:i + ITEMS_PER_PRINT_PAGE]
        for i in range(0, len(all_items), ITEMS_PER_PRINT_PAGE)
    ] or [[]]
    return render(request, 'staff/orders/print.html', {
        'order': order,
        'item_pages': item_pages,
        'item_count': len(all_items),
    })


@perm_required('orders.view_order')
def order_detail(request, pk):
    order = get_object_or_404(
        Order.objects.select_related('client').prefetch_related('items__product_unit__product__inventory'),
        pk=pk,
    )

    # أول ما الموظف/الأدمن يفتح تفاصيل الطلب، بيتحدد "متفتح" فورًا — بيُستخدم
    # في عداد "طلبات لسه ماتفتحتش" على الصفحة الرئيسية للوحة التحكم.
    if not order.viewed_by_staff:
        order.viewed_by_staff = True
        order.save(update_fields=['viewed_by_staff'])

    if request.method == 'POST':
        # الإجراءات دي بتعدّل حالة الطلب فعليًا (تأكيد/رفض/تسليم/تعديل كمية)
        # فمحتاجة صلاحية "تعديل" مش "عرض" بس.
        if not request.user.has_perm('orders.change_order'):
            messages.error(request, 'ليس لديك صلاحية تعديل الطلبات. تواصل مع الأدمن.')
            return redirect('staff:order_detail', pk=order.pk)

        action = request.POST.get('action')

        if action == 'update_quantities':
            if order.status not in (Order.Status.PENDING, Order.Status.NEEDS_APPROVAL):
                messages.error(request, 'لا يمكن تعديل كميات طلب تم تأكيده بالفعل.')
                return redirect('staff:order_detail', pk=order.pk)
            any_changed = False
            for item in order.items.all():
                field_name = f'quantity_{item.pk}'
                if field_name not in request.POST:
                    continue
                try:
                    new_qty = int(request.POST.get(field_name))
                except (TypeError, ValueError):
                    continue
                if new_qty == item.quantity or new_qty < 0:
                    continue
                if new_qty == 0:
                    messages.error(request, 'لا يمكن تصفير كمية صنف من هنا، استخدم خيار رفض الطلب إذا أردت إزالته بالكامل.')
                    continue
                try:
                    order.amend_item_quantity(item, new_qty, actor=request.user)
                    any_changed = True
                except ValueError as e:
                    messages.error(request, str(e))

            if any_changed:
                order.send_for_client_approval(actor=request.user)
                messages.success(request, 'تم تعديل الكميات وإرسال الطلب للعميل للموافقة على التعديل.')
            else:
                messages.info(request, 'لم يتم تطبيق أي تعديلات.')
            return redirect('staff:order_detail', pk=order.pk)

        elif action == 'confirm':
            if order.is_amended and order.status != Order.Status.NEEDS_APPROVAL:
                messages.error(request, 'يحتوي الطلب على تعديلات بانتظار موافقة العميل، ولا يمكن تأكيده مباشرة.')
            elif order.status not in (Order.Status.PENDING, Order.Status.NEEDS_APPROVAL):
                # بعد مرحلة 3، confirm() بيخصم من المخزون فعليًا، فمهم نمنع
                # نداء تاني على طلب اتأكد بالفعل من هنا في الـ view (مش بس
                # نعتمد على الحماية جوه الموديل) — زي بالظبط شرط 'deliver' تحت.
                messages.error(request, 'الطلب ده اتأكد بالفعل.')
            else:
                try:
                    order.confirm(actor=request.user)
                    messages.success(request, f'تم تأكيد الطلب #{order.pk} وخصم الكميات من المخزون.')
                except ValidationError as e:
                    messages.error(request, f'تعذّر تأكيد الطلب: {"، ".join(e.messages)}')
            return redirect('staff:order_detail', pk=order.pk)

        elif action == 'add_service_fee':
            raw_amount = request.POST.get('amount', '').strip()
            try:
                amount = Decimal(raw_amount)
            except (InvalidOperation, TypeError):
                messages.error(request, 'قيمة مصاريف التوصيل غير صحيحة.')
                return redirect('staff:order_detail', pk=order.pk)
            try:
                order.add_service_fee(amount, actor=request.user)
                messages.success(request, 'تمت إضافة مصاريف التوصيل للطلب.')
            except ValueError as e:
                messages.error(request, str(e))
            return redirect('staff:order_detail', pk=order.pk)

        elif action == 'remove_service_fee':
            item_id = request.POST.get('item_id')
            item = get_object_or_404(OrderItem, pk=item_id, order=order)
            try:
                order.remove_service_fee(item, actor=request.user)
                messages.success(request, 'تم حذف الصنف الخدمي من الطلب.')
            except ValueError as e:
                messages.error(request, str(e))
            return redirect('staff:order_detail', pk=order.pk)

        elif action == 'reject':
            reason = request.POST.get('reason', '')
            try:
                order.reject(actor=request.user, reason=reason)
                messages.success(request, f'تم رفض الطلب #{order.pk}.')
            except ValueError as e:
                messages.error(request, str(e))
            return redirect('staff:order_detail', pk=order.pk)

        elif action == 'deliver':
            if order.status != Order.Status.CONFIRMED:
                messages.error(request, 'يجب تأكيد الطلب أولًا قبل التسليم.')
            else:
                try:
                    with transaction.atomic():
                        order.mark_delivered(actor=request.user)
                    messages.success(request, f'تم تسليم الطلب #{order.pk} واعتماد الفاتورة نهائيًا.')
                except ValidationError as e:
                    messages.error(request, f'تعذّر تسليم الطلب: {"، ".join(e.messages)}')
            return redirect('staff:order_detail', pk=order.pk)

    items_qs = order.items.select_related('product_unit__product__inventory').order_by('pk')
    items_paginator = Paginator(items_qs, ITEMS_PER_DETAIL_PAGE)
    items_page = items_paginator.get_page(request.GET.get('page'))

    # قائمة الإجراءات الموحدة (مرحلة 4) — كانت روابط طباعة متفرقة
    # (نسخة المراجعة اليدوية + الفاتورة) متبعثرة في أماكن مختلفة من
    # الصفحة، دلوقتي مجمّعة في قائمة منسدلة واحدة.
    order_actions = []
    if order.status in (Order.Status.PENDING, Order.Status.NEEDS_APPROVAL):
        order_actions.append({
            'label': 'طباعة الطلب للمراجعة اليدوية',
            'href': reverse('staff:order_print', args=[order.pk]),
            'icon': 'printer',
            'target': '_blank',
        })
    if hasattr(order, 'invoice'):
        # الفاتورة (مرحلة 2) بتتولد فورًا وقت التأكيد كمسودة (is_draft=True)
        # برقمها الثابت النهائي، وبتتحول لنهائية (is_draft=False) لحظة
        # التسليم من غير ما رقمها يتغيّر — يعني نفس المستند بالظبط من التأكيد
        # لحد التسليم، مفيش مستند مؤقت منفصل ("قبل نهائي") تاني. القالب نفسه
        # (invoices/print.html) بيوضّح حالة المسودة بشريط تنبيه لما is_draft.
        order_actions.append({
            'label': (
                f'طباعة الفاتورة ({order.invoice.invoice_number} — مسودة)'
                if order.invoice.is_draft
                else f'عرض/طباعة الفاتورة ({order.invoice.invoice_number})'
            ),
            'href': reverse('invoices:print', args=[order.invoice.pk]),
            'icon': 'printer',
            'target': '_blank',
        })

    return render(request, 'staff/orders/detail.html', {
        'order': order,
        'items_page': items_page,
        'order_actions': order_actions,
        'hasattr_invoice': hasattr(order, 'invoice'),
    })


def _scan_panel_context(order):
    """سياق الجزء المتغيّر من شاشة المراجعة بالسكانر (مرحلة 6) — بيتحسب من
    جديد بعد أي إجراء (مسح باركود أو تعليم يدوي) عشان يترجع في الرد الجزئي
    لـ htmx."""
    items = list(
        order.items.filter(is_service_fee=False)
        .select_related('product_unit__product')
        .order_by('pk')
    )
    found_count = sum(1 for item in items if item.scanned)
    return {
        'order': order,
        'items': items,
        'found_count': found_count,
        'total_count': len(items),
    }


@perm_required('orders.view_order')
def order_scan_review(request, pk):
    """
    مرحلة 6 — شاشة مراجعة تفاعلية بالسكانر لطلبات لسه ما اتأكدتش (PENDING/
    NEEDS_APPROVAL، بنفس شرط توفر زر الطباعة اليدوية فوق). واجهة مساعدة
    بحتة للمخزن قبل قرار التأكيد/الرفض — مفيش أي لمسة لمنطق حالة الطلب ولا
    المخزون ولا الفاتورة هنا خالص (راجع OrderItem.set_scanned/Order.find_item_by_barcode).
    الأصناف الخدمية (زي مصاريف التوصيل) مستبعدة تمامًا من الشاشة دي.
    """
    order = get_object_or_404(Order.objects.select_related('client'), pk=pk)

    if order.status not in (Order.Status.PENDING, Order.Status.NEEDS_APPROVAL):
        messages.error(request, 'شاشة المراجعة بالسكانر متاحة بس للطلبات اللي لسه ما اتأكدتش.')
        return redirect('staff:order_detail', pk=order.pk)

    is_htmx = bool(request.headers.get('HX-Request'))
    feedback = None

    if request.method == 'POST':
        if not request.user.has_perm('orders.change_order'):
            if is_htmx:
                feedback = {'status': 'error', 'message': 'ليس لديك صلاحية تعديل الطلبات.'}
            else:
                messages.error(request, 'ليس لديك صلاحية تعديل الطلبات. تواصل مع الأدمن.')
                return redirect('staff:order_scan_review', pk=order.pk)
        else:
            action = request.POST.get('action')

            if action == 'scan_barcode':
                barcode = request.POST.get('barcode', '')
                item = order.find_item_by_barcode(barcode)
                if item is None:
                    feedback = {'status': 'not_found', 'message': f'الباركود "{barcode.strip()}" مش موجود في هذا الطلب.', 'barcode': barcode.strip()}
                elif item.scanned:
                    feedback = {'status': 'already', 'message': f'{item.display_name} — كان اتفحص بالفعل.', 'barcode': barcode.strip()}
                else:
                    item.set_scanned(True)
                    feedback = {'status': 'found', 'message': f'تم تسجيل: {item.display_name}', 'barcode': barcode.strip()}

            elif action == 'toggle_manual':
                item = get_object_or_404(OrderItem, pk=request.POST.get('item_id'), order=order, is_service_fee=False)
                item.set_scanned(not item.scanned)
                feedback = {
                    'status': 'found' if item.scanned else 'reset',
                    'message': f'{item.display_name} — {"اتعلّم يدويًا" if item.scanned else "اتلغى تعليمه"}.',
                }

            if not is_htmx:
                if feedback:
                    level = messages.SUCCESS if feedback['status'] in ('found', 'reset') else messages.WARNING
                    messages.add_message(request, level, feedback['message'])
                return redirect('staff:order_scan_review', pk=order.pk)

    context = _scan_panel_context(order)
    context['feedback'] = feedback

    if is_htmx:
        response = render(request, 'staff/orders/partials/scan_panel.html', context)
        if feedback and 'barcode' in feedback:
            # بنبعت حدث للفرونت-إند فيه حالة آخر باركود اتقرا (وقيمته)، عشان
            # حقل الإدخال يقرر يمسح نفسه (لو الصنف تابع للفاتورة) أو يفضّل
            # محتفظ بالرقم مع تنبيه (لو مش تابع لها) — راجع scan_review.html.
            response['HX-Trigger'] = json.dumps({
                'scan-result': {'status': feedback['status'], 'barcode': feedback['barcode']},
            })
        return response

    context['order_label'] = f'طلب #{order.pk}'
    return render(request, 'staff/orders/scan_review.html', context)
