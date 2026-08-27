"""
بناء ملفات تصدير Excel لتقارير قسم staff/reports.py — منقول للخلفية عبر
Celery (راجع staff/tasks.py — build_report_export_task) بنفس فلسفة تصدير
المنتجات (products/tasks.py — export_products_task): بناء كل تقرير
(المبيعات، المنتجات المباعة، أفضل العملاء، الأرباح، الراكدة، مقترحات
التوريد) كان بيحصل بشكل متزامن جوه نفس طلب HTTP على web-staff — مع فترة
"كل الفترات" أو كتالوج/قاعدة عملاء كبيرة، ده معناه تحميل آلاف الصفوف في
الذاكرة وبناء ملف إكسل كامل قبل ما الاستجابة ترجع، ومعرّض لنفس فئة مشكلة
الـ504 اللي اتحلت للاستيراد/تصدير المنتجات قبل كده (منقول من mg).

كل دالة *_workbook هنا بترجع Workbook (مش HttpResponse) عشان الـtask تقدر
تحفظها كملف على القرص بدل ما ترجّعها مباشرة كتحميل — نفس شكل
export_products_task بالظبط (راجع products/tasks.py).

params هنا هو نسخة من request.GET.dict() وقت الطلب الأصلي (محفوظة قبل
الـdelay، راجع staff/views/reports.py). _ParamsRequest محول خفيف يخلي
الـdict ده يتقرا بنفس طريقة request.GET.get(...)، عشان ReportFilters/
resolve_period (staff/reports_queries.py) يشتغلوا من جوه celery-worker
بالظبط زي ما بيشتغلوا من جوه request حقيقي، من غير أي تكرار لمنطق
الفلترة نفسه.
"""
from staff import reports_queries as rq
from staff.excel_utils import build_simple_workbook


class _ParamsRequest:
    """راجع docstring الملف فوق — بديل خفيف لـ HttpRequest يكفي لأي كود
    بينادي request.GET.get(...) بس (ReportFilters)."""

    def __init__(self, params):
        self.GET = params


def _sales_workbook(params):
    from django.utils import timezone

    filters = rq.ReportFilters(_ParamsRequest(params))
    invoice_qs = filters.base_invoices().order_by('-issued_at').select_related(
        'order__client', 'issued_by',
    )
    data_rows = [
        [
            inv.invoice_number,
            timezone.localtime(inv.issued_at).strftime('%Y-%m-%d %H:%M'),
            inv.client_name,
            inv.issued_by.username if inv.issued_by else '—',
            float(inv.total),
        ]
        for inv in invoice_qs
    ]
    return build_simple_workbook(
        sheet_title='تقرير المبيعات',
        headers=['رقم الفاتورة', 'التاريخ', 'العميل', 'الموظف', 'الإجمالي (ج.م)'],
        rows=data_rows,
    )


def _products_workbook(params):
    filters = rq.ReportFilters(_ParamsRequest(params))
    item_qs = filters.base_order_items()
    sort = params.get('sort', 'revenue')
    if sort not in ('revenue', 'qty', 'profit'):
        sort = 'revenue'
    rows = rq.products_sold_report(item_qs, order_by=sort)
    data_rows = [
        [
            r['product_unit__product__code'],
            r['display_name'],
            r['product_unit__product__category__name'] or '—',
            r['total_qty'],
            float(r['total_revenue'] or 0),
            float(r['total_profit'] or 0),
            round(float(r['share_percent'] or 0), 2),
        ]
        for r in rows
    ]
    return build_simple_workbook(
        sheet_title='المنتجات المباعة',
        headers=[
            'كود الصنف', 'اسم المنتج', 'القسم', 'الكمية المباعة',
            'الإيراد (ج.م)', 'الربح (ج.م)', 'نسبة المساهمة %',
        ],
        rows=data_rows,
    )


def _customers_workbook(params):
    from django.utils import timezone

    filters = rq.ReportFilters(_ParamsRequest(params))
    item_qs = filters.base_order_items()
    rows = rq.top_customers_report(item_qs)
    data_rows = [
        [
            r['display_name'],
            float(r['total_revenue'] or 0),
            r['invoices_count'],
            float(r['avg_invoice'] or 0),
            timezone.localtime(r['last_purchase']).strftime('%Y-%m-%d') if r['last_purchase'] else '—',
        ]
        for r in rows
    ]
    return build_simple_workbook(
        sheet_title='أفضل العملاء',
        headers=['العميل', 'إجمالي المشتريات (ج.م)', 'عدد الفواتير', 'متوسط الفاتورة (ج.م)', 'آخر عملية شراء'],
        rows=data_rows,
        column_width=24,
    )


def _profit_workbook(params):
    filters = rq.ReportFilters(_ParamsRequest(params))
    item_qs = filters.base_order_items()
    totals = rq.totals_for_items(item_qs)
    monthly_series = rq.monthly_profit_series(months=12)
    data_rows = [[m['label'], float(m['revenue']), float(m['profit'])] for m in monthly_series]
    data_rows.append(['', '', ''])
    data_rows.append(['الإجمالي (الفترة المختارة)', float(totals['revenue']), float(totals['profit'])])
    return build_simple_workbook(
        sheet_title='تقرير الأرباح',
        headers=['الشهر', 'الإيرادات (ج.م)', 'الربح الإجمالي (ج.م)'],
        rows=data_rows,
    )


def _stagnant_workbook(params):
    from django.utils import timezone

    days_custom = (params.get('days_custom') or '').strip()
    days_preset = (params.get('days') or '30').strip()
    raw = days_custom or days_preset
    try:
        days = int(raw)
    except ValueError:
        days = 30
    days = max(1, min(days, 3650))
    category_id = (params.get('category') or '').strip() or None
    product_id = (params.get('product') or '').strip() or None
    rows = rq.stagnant_products_report(days=days, category_id=category_id, product_id=product_id)
    data_rows = []
    for r in rows:
        product = r['product']
        data_rows.append([
            product.code,
            product.display_name,
            product.category.name if product.category_id else '—',
            r['inventory'].quantity_display,
            timezone.localtime(r['last_sale']).strftime('%Y-%m-%d') if r['last_sale'] else 'لم يُبع من قبل',
        ])
    return build_simple_workbook(
        sheet_title='منتجات راكدة',
        headers=['كود الصنف', 'اسم المنتج', 'القسم', 'الرصيد الحالي', 'آخر عملية بيع'],
        rows=data_rows,
        column_width=24,
    )


def _supply_suggestions_workbook(params):
    from inventory.models import Inventory

    items_qs = Inventory.objects.low_stock().select_related('product__category').order_by('quantity')
    category_id = (params.get('category') or '').strip() or None
    if category_id:
        items_qs = items_qs.filter(product__category_id=category_id)
    data_rows = []
    for item in items_qs:
        data_rows.append([
            item.product.code,
            item.product.display_name,
            item.product.category.name if item.product.category_id else '—',
            item.available_display,
            item.min_quantity,
            item.suggested_reorder_display,
        ])
    return build_simple_workbook(
        sheet_title='مقترحات التوريد',
        headers=['كود الصنف', 'اسم المنتج', 'القسم', 'المتاح حاليًا', 'الحد الأدنى (بالقطعة)', 'الكمية المقترح توريدها'],
        rows=data_rows,
        column_width=24,
    )


# مفتاح report_kind (راجع staff/urls.py — reports/export/...) مربوط
# بدالة بناء الـworkbook واسم ملف التحميل. أي تقرير جديد يحتاج تصدير
# إكسل يتضاف هنا بس — الـview وtask عامتين ومبنيتين على الجدول ده.
REPORT_KIND_BUILDERS = {
    'sales': _sales_workbook,
    'products': _products_workbook,
    'customers': _customers_workbook,
    'profit': _profit_workbook,
    'stagnant': _stagnant_workbook,
    'supply_suggestions': _supply_suggestions_workbook,
}

REPORT_KIND_FILENAMES = {
    'sales': 'biozone_sales_report.xlsx',
    'products': 'biozone_products_sold.xlsx',
    'customers': 'biozone_top_customers.xlsx',
    'profit': 'biozone_profit_report.xlsx',
    'stagnant': 'biozone_stagnant_products.xlsx',
    'supply_suggestions': 'biozone_supply_suggestions.xlsx',
}

# حالة بناء ملف تصدير التقرير في الخلفية — {'status': 'done'|'failed',
# 'file_path': '...' (لو done)، 'download_filename': '...' (لو done)،
# 'error_message': '...' (لو failed)}. مخزّنة في الكاش (مفتاح مبني على
# user_id) بدل الجلسة — نفس أسلوب export_result_cache_key في
# products/tasks.py بالظبط (SESSION_ENGINE الفعلي cached_db).
REPORT_EXPORT_STATUS_PREFIX = 'report_export_status:'
REPORT_EXPORT_STATUS_TTL = 60 * 30  # 30 دقيقة — كفاية للموظف يفتح شاشة التحميل


def report_export_status_cache_key(user_id):
    return f'{REPORT_EXPORT_STATUS_PREFIX}{user_id}'
