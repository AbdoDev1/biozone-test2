import os
from decimal import Decimal

from django.contrib import messages
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import F, Sum, Subquery, OuterRef, DecimalField, ExpressionWrapper
from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.utils import timezone

from inventory.models import Inventory
from orders.models import Order, OrderItem
from products.models import ProductUnit
from staff.permissions import perm_required
from staff.excel_utils import XLSX_CONTENT_TYPE
from staff import reports_queries as rq

STAFF_LIST_PAGE_SIZE = 50
MONEY = DecimalField(max_digits=14, decimal_places=2)


def _start_report_export(request, report_kind):
    """
    بتبدأ بناء ملف تصدير التقرير (report_kind — راجع staff/report_export.py
    — REPORT_KIND_BUILDERS) في الخلفية عبر Celery بدل ما تبنيه وترجّعه
    مباشرة جوه نفس طلب HTTP — نفس نقل تصدير المنتجات (products/tasks.py
    — export_products_task)، منقول من mg.

    request.GET.dict() بيتلقط هنا (لحظة الطلب) ويتخزن كـargs للـtask —
    ده بالظبط نفس فلاتر التقرير اللي كانت هتتستخدم لو التصدير حصل متزامن،
    فالنتيجة النهائية مطابقة تمامًا لما كانت هتطلع لو الموظف ضغط تصدير
    وهو شايف نفس الفلاتر دي على الشاشة.
    """
    from staff.report_export import REPORT_EXPORT_STATUS_TTL, report_export_status_cache_key
    from staff.tasks import build_report_export_task

    cache_key = report_export_status_cache_key(request.user.pk)
    cache.delete(cache_key)
    build_report_export_task.delay(report_kind, request.GET.dict(), request.user.pk)
    return render(request, 'staff/reports/export_processing.html')


# =====================================================================
# 1) لوحة المؤشرات (Executive Dashboard)
# =====================================================================
@perm_required('staff.view_reports')
def dashboard(request):
    now = timezone.localtime()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    from invoices.models import Invoice
    from accounts.models import ClientProfile

    today_summary = rq.sales_summary(Invoice.objects.filter(issued_at__gte=today_start))
    month_summary = rq.sales_summary(Invoice.objects.filter(issued_at__gte=month_start))

    month_item_qs = OrderItem.objects.filter(
        order__status=Order.Status.DELIVERED,
        order__invoice__isnull=False,
        order__invoice__issued_at__gte=month_start,
    )
    month_profit = rq.totals_for_items(month_item_qs)

    inv_qs = Inventory.objects.select_related('product')
    low_stock_count = inv_qs.low_stock().count()

    total_customers = ClientProfile.objects.filter(user__status='ACTIVE').count()
    active_customers = ClientProfile.objects.filter(
        user__status='ACTIVE', user__orders__status=Order.Status.DELIVERED,
    ).distinct().count()

    # قيمة المخزون الحالية = رصيد كل صنف (بالقطعة) × سعر جمهور أصغر وحدة له.
    # كان ده بيتحسب بلوب بايثون على كل أصناف المخزون (راجع نسخة قديمة في
    # الـ git history) — بطيء تدريجيًا مع نمو الكتالوج لآلاف الأصناف رغم إنه
    # مفيهوش N+1 (كان معمول له prefetch_related بالفعل). الاستعلام ده بيحسب
    # نفس القيمة بالظبط بس جوه قاعدة البيانات بضربة واحدة: subquery بياخد
    # unit_price لأصغر وحدة (أقل qty_in_small) لكل منتج، وSum() بيجمّع
    # (الرصيد × السعر) على مستوى الداتابيز.
    smallest_unit_price = Subquery(
        ProductUnit.objects.filter(product_id=OuterRef('product_id'))
        .order_by('qty_in_small')
        .values('unit_price')[:1],
        output_field=DecimalField(max_digits=10, decimal_places=2),
    )
    stock_value = inv_qs.annotate(smallest_unit_price=smallest_unit_price).aggregate(
        total=Sum(
            ExpressionWrapper(F('quantity') * F('smallest_unit_price'), output_field=MONEY)
        )
    )['total'] or Decimal('0')

    top_products = rq.products_sold_report(month_item_qs, order_by='revenue')[:5]
    top_customers = rq.top_customers_report(month_item_qs)[:5]

    context = {
        'today_summary': today_summary,
        'month_summary': month_summary,
        'month_profit': month_profit,
        'total_products': inv_qs.count(),
        'low_stock_count': low_stock_count,
        'total_customers': total_customers,
        'active_customers': active_customers,
        'stock_value': stock_value,
        'top_products': top_products,
        'top_customers': top_customers,
        'daily_series': rq.daily_sales_for_dashboard(days=14),
        'monthly_series': rq.monthly_profit_series(months=6),
    }
    return render(request, 'staff/reports/dashboard.html', context)


# =====================================================================
# 2) تقرير المبيعات
# =====================================================================
@perm_required('staff.view_reports')
def sales_report(request):
    filters = rq.ReportFilters(request)
    invoice_qs = filters.base_invoices().order_by('-issued_at')
    summary = rq.sales_summary(invoice_qs)

    # الخصومات = الفرق بين سعر الجمهور والسعر الفعلي على كل صنف مبيع في نطاق الفلاتر.
    item_qs = filters.base_order_items()
    discount_total = item_qs.aggregate(
        d=Sum((F('public_price') - F('unit_price')) * F('quantity'))
    )['d'] or Decimal('0')

    if request.GET.get('export') == 'excel':
        return _start_report_export(request, 'sales')

    paginator = Paginator(invoice_qs, STAFF_LIST_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'invoices': page_obj,
        'page_obj': page_obj,
        'summary': summary,
        'discount_total': discount_total,
        'net_total': summary['total'] - discount_total,
    }
    context.update(filters.filter_context())
    return render(request, 'staff/reports/sales.html', context)


# =====================================================================
# 3) تقرير المنتجات المباعة (يشمل "الأكثر مبيعًا" عبر خيار الترتيب)
# =====================================================================
@perm_required('staff.view_reports')
def products_sold(request):
    filters = rq.ReportFilters(request)
    item_qs = filters.base_order_items()
    sort = request.GET.get('sort', 'revenue')
    if sort not in ('revenue', 'qty', 'profit'):
        sort = 'revenue'
    rows = rq.products_sold_report(item_qs, order_by=sort)

    if request.GET.get('export') == 'excel':
        return _start_report_export(request, 'products')

    paginator = Paginator(rows, STAFF_LIST_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'rows': page_obj,
        'page_obj': page_obj,
        'sort': sort,
        'totals': rq.totals_for_items(item_qs),
    }
    context.update(filters.filter_context())
    return render(request, 'staff/reports/products.html', context)


# =====================================================================
# 4) تقرير أفضل العملاء
# =====================================================================
@perm_required('staff.view_reports')
def top_customers(request):
    filters = rq.ReportFilters(request)
    item_qs = filters.base_order_items()
    rows = rq.top_customers_report(item_qs)

    if request.GET.get('export') == 'excel':
        return _start_report_export(request, 'customers')

    paginator = Paginator(rows, STAFF_LIST_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {'rows': page_obj, 'page_obj': page_obj}
    context.update(filters.filter_context())
    return render(request, 'staff/reports/customers.html', context)


# =====================================================================
# 5) تقرير الأرباح
# =====================================================================
@perm_required('staff.view_reports')
def profit_report(request):
    filters = rq.ReportFilters(request)
    item_qs = filters.base_order_items()
    totals = rq.totals_for_items(item_qs)
    monthly_series = rq.monthly_profit_series(months=12)

    if request.GET.get('export') == 'excel':
        return _start_report_export(request, 'profit')

    context = {'totals': totals, 'monthly_series': monthly_series}
    context.update(filters.filter_context())
    return render(request, 'staff/reports/profit.html', context)


# =====================================================================
# 6) تقرير المنتجات الراكدة
# =====================================================================
@perm_required('staff.view_reports')
def stagnant_products(request):
    days_custom = request.GET.get('days_custom', '').strip()
    days_preset = request.GET.get('days', '30').strip()
    raw = days_custom or days_preset
    try:
        days = int(raw)
    except ValueError:
        days = 30
    days = max(1, min(days, 3650))

    category_id = request.GET.get('category', '').strip() or None
    product_id = request.GET.get('product', '').strip() or None
    rows = rq.stagnant_products_report(days=days, category_id=category_id, product_id=product_id)

    if request.GET.get('export') == 'excel':
        return _start_report_export(request, 'stagnant')

    paginator = Paginator(rows, STAFF_LIST_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    from products.models import Category, Product
    context = {
        'rows': page_obj,
        'page_obj': page_obj,
        'days': days,
        'categories': Category.objects.filter(is_active=True).order_by('name'),
        'products': Product.objects.filter(is_active=True).order_by('name_ar').only('id', 'name_ar', 'name_en'),
        'selected_category': category_id or '',
        'selected_product': product_id or '',
    }
    return render(request, 'staff/reports/stagnant.html', context)


# =====================================================================
# مقترحات التوريد (مرحلة 7 من ROADMAP.md)
# =====================================================================
@perm_required('inventory.view_inventory')
def supply_suggestions(request):
    """
    تجميع كل الأصناف تحت الحد الأدنى (min_quantity) في صفحة واحدة بدل
    تنبيه متفرق (كارت "مخزون منخفض" في لوحة التحكم، فلتر "?low=1" في
    صفحة المخزون) — البيانات جاهزة أصلًا (Inventory.low_stock() من
    مرحلة 3)، والإضافة هنا كمية مقترح توريدها لكل صنف
    (Inventory.suggested_reorder_qty) عشان الصفحة تبقى قابلة للتنفيذ
    مباشرة (كام قطعة يتوّرد) مش بس تنبيه.
    """
    items_qs = Inventory.objects.low_stock().select_related(
        'product__category'
    ).prefetch_related('product__units').order_by('quantity')

    category_id = request.GET.get('category', '').strip() or None
    if category_id:
        items_qs = items_qs.filter(product__category_id=category_id)

    if request.GET.get('export') == 'excel':
        return _start_report_export(request, 'supply_suggestions')

    paginator = Paginator(items_qs, STAFF_LIST_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    from products.models import Category
    return render(request, 'staff/reports/supply_suggestions.html', {
        'items': page_obj,
        'page_obj': page_obj,
        'categories': Category.objects.filter(is_active=True).order_by('name'),
        'selected_category': category_id or '',
        'total_low_count': items_qs.count(),
    })


@perm_required('staff.view_reports')
def reports_export_processing(request):
    """
    شاشة انتظار بينما build_report_export_task شغالة في celery-worker —
    نظير export_products_processing (staff/views/products/import_export.py)
    بالظبط، بس لتقارير قسم التقارير بدل تصدير المنتجات (منقول من mg —
    راجع staff/report_export.py لتفاصيل النقل الكامل).
    """
    from staff.report_export import report_export_status_cache_key

    cached = cache.get(report_export_status_cache_key(request.user.pk))
    if cached is None:
        return redirect('staff:reports_dashboard')
    if cached.get('status') == 'failed':
        cache.delete(report_export_status_cache_key(request.user.pk))
        messages.error(request, cached.get('error_message') or 'حصل خطأ أثناء بناء ملف التقرير.')
        return redirect('staff:reports_dashboard')
    return render(request, 'staff/reports/export_processing.html')


@perm_required('staff.view_reports')
def reports_export_status(request):
    """
    Endpoint خفيف بتستدعيه شاشة الانتظار (export_processing.html) كل
    ثانيتين — بيتأكد هل build_report_export_task خلصت (نتيجة موجودة في
    الكاش) ولا لسه، وهل خلصت بنجاح ولا بفشل. نفس فكرة
    export_products_status بالظبط (products/tasks.py).
    """
    from django.http import JsonResponse

    from staff.report_export import report_export_status_cache_key
    cached = cache.get(report_export_status_cache_key(request.user.pk))
    return JsonResponse({
        'ready': cached is not None,
        'failed': bool(cached and cached.get('status') == 'failed'),
    })


@perm_required('staff.view_reports')
def reports_export_download(request):
    """
    بتقرا ملف تصدير التقرير الجاهز من القرص المشترك (build_report_export_task
    حفظه هناك) وترجعه للموظف كتحميل، وبعدين تمسحه فورًا — رابط استخدام واحد.
    نظير export_products_download بالظبط (products/views، منقول من mg).
    """
    from staff.report_export import report_export_status_cache_key
    cached = cache.get(report_export_status_cache_key(request.user.pk))
    if not cached:
        messages.error(request, 'مفيش ملف تصدير جاهز حاليًا (ممكن يكون اتحمّل قبل كده أو انتهت صلاحيته). جرّب تاني.')
        return redirect('staff:reports_dashboard')

    if cached.get('status') != 'done':
        messages.error(request, cached.get('error_message') or 'حصل خطأ أثناء تجهيز ملف التقرير.')
        cache.delete(report_export_status_cache_key(request.user.pk))
        return redirect('staff:reports_dashboard')

    file_path = cached['file_path']
    if not os.path.exists(file_path):
        messages.error(request, 'ملف التقرير مش موجود على السيرفر (اتحمّل قبل كده على الأرجح). جرّب تاني.')
        cache.delete(report_export_status_cache_key(request.user.pk))
        return redirect('staff:reports_dashboard')

    with open(file_path, 'rb') as f:
        data = f.read()
    try:
        os.remove(file_path)
    except OSError:
        pass
    cache.delete(report_export_status_cache_key(request.user.pk))

    response = HttpResponse(data, content_type=XLSX_CONTENT_TYPE)
    download_filename = cached.get('download_filename', 'biozone_report.xlsx')
    response['Content-Disposition'] = f'attachment; filename="{download_filename}"'
    return response
