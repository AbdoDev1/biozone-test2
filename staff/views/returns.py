from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse

from orders.models import Order
from invoices.models import InvoiceReversal
from staff.permissions import perm_required


@perm_required('staff.create_returns')
def order_return_create(request, pk):
    """
    شاشة إنشاء إشعار مرتجع لطلب اتأكد بالفعل (وعنده فاتورة) — بتحوّل صفحة
    الطلب لصفحة اختيار أصناف/كميات للمرتجع (زي ما طلب Abdo بالظبط: "صفحة
    الطلب تتحول لصفحة اشعار عند الستاف"). محجوبة تمامًا خلف صلاحية
    'staff.create_returns' اللي الأدمن بس يقدر يمنحها (راجع staff/models.py
    ReturnsAccess وstaff/permissions.py).

    متاحة بس للطلبات اللي عندها فاتورة فعلًا (يعني مرّت بـ confirm()
    وخُصم مخزونها فعليًا) وحالتها CONFIRMED أو DELIVERED — قبل كده (PENDING/
    NEEDS_APPROVAL) مفيش حاجة اتخصمت أصلًا يترجع، وبعد الرفض (REJECTED)
    الطلب اتلغى بالكامل من قبل عن طريق مسار مختلف (راجع
    Order._reverse_confirmed_order_effects، stage=PRE_DELIVERY).
    """
    order = get_object_or_404(
        Order.objects.select_related('client', 'invoice').prefetch_related('invoice__items__reversal_items'),
        pk=pk,
    )

    if not hasattr(order, 'invoice'):
        messages.error(request, 'لا يوجد فاتورة لهذا الطلب، لا يمكن عمل مرتجع.')
        return redirect('staff:order_detail', pk=order.pk)

    if order.status not in (Order.Status.CONFIRMED, Order.Status.DELIVERED):
        messages.error(request, 'المرتجع متاح فقط للطلبات المؤكدة أو المُسلَّمة.')
        return redirect('staff:order_detail', pk=order.pk)

    invoice = order.invoice
    returnable_items = [
        item for item in invoice.items.all()
        if not (item.order_item and item.order_item.is_service_fee)
    ]

    if request.method == 'POST':
        note = request.POST.get('note', '').strip()
        items_to_return = []
        for item in returnable_items:
            raw_qty = request.POST.get(f'quantity_{item.pk}', '').strip()
            if not raw_qty:
                continue
            try:
                qty = int(raw_qty)
            except ValueError:
                messages.error(request, f'كمية غير صحيحة للصنف "{item.product_name}".')
                return redirect('staff:order_return_create', pk=order.pk)
            if qty > 0:
                items_to_return.append((item, qty))

        try:
            reversal = InvoiceReversal.create_post_delivery_return(
                invoice=invoice, items=items_to_return, actor=request.user, note=note,
            )
            messages.success(
                request,
                f'تم إنشاء إشعار المرتجع {reversal.return_number} بقيمة {reversal.amount} ج.م، '
                'وتم إرجاع الكميات للمخزون.',
            )
            return redirect('staff:order_detail', pk=order.pk)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect('staff:order_return_create', pk=order.pk)

    items_rows = [
        {'item': item, 'remaining': item.remaining_quantity}
        for item in returnable_items
    ]

    return render(request, 'staff/orders/return_create.html', {
        'order': order,
        'invoice': invoice,
        'items_rows': items_rows,
        'has_returnable_items': any(row['remaining'] > 0 for row in items_rows),
        'order_crumb_label': f'طلب #{order.pk}',
        'cancel_url': reverse('staff:order_detail', args=[order.pk]),
    })
