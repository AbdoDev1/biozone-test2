import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse

from orders.models import Order, OrderItem
from staff.permissions import perm_required
from tags.services import tags_for_many

STAFF_LIST_PAGE_SIZE = 30
ITEMS_PER_PRINT_PAGE = 14  # لو الأصناف زادت عن كده، النسخة القابلة للطباعة بتتقسم لصفحات مرقّمة 1/ن، 2/ن...
ITEMS_PER_DETAIL_PAGE = 20  # ترقيم صفحات جدول الأصناف في تفاصيل الطلب (تفادي صفحة طويلة جدًا لو الطلب فيه أصناف كتير)


@perm_required('orders.view_order')
def order_list(request):
    status = request.GET.get('status', '')
    orders = Order.objects.select_related('client').prefetch_related('items')

    if status:
        orders = orders.filter(status=status)

    paginator = Paginator(orders, STAFF_LIST_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    # وسم كل طلب في الصفحة الحالية باستعلام واحد بدل ما نستدعي tags_for
    # لكل صف على حدة (N+1) — الوسوم بتتعرض صغيرة تحت رقم الطلب في الجدول.
    tags_by_order_id = tags_for_many(Order, [order.pk for order in page_obj])
    for order in page_obj:
        order.tag_list = tags_by_order_id.get(order.pk, [])

    context = {
        'orders': page_obj,
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
