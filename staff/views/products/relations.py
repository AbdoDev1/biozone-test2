"""
مرحلة 5 (ROADMAP.md) — منتجات مشابهة/مكمّلة (Cross-sell).

هذا الملف مسؤول عن:
- Product Picker العام (بحث + شرائح htmx) — نفس المكوّن المُعاد استخدامه
  للعلاقتين (مشابه/مكمّل)، بدل ما يتبني من الصفر لكل واحدة على حدة
  (راجع قاعدة "موديلات/مكوّنات عامة بدل مكررة" في ROADMAP.md قسم 2-ج).
- إضافة/إزالة منتجات مشابهة ومكمّلة (M2M بسيط).

ملحوظة: ميزة "مقاسات/تنويعات المنتج" (ProductVariantGroup) اتشالت بالكامل
من هنا — كانت هي المسؤولة عن ربط/فك ربط مقاسات بديلة، شوف Git history لو
احتجت ترجع لها لاحقًا.
"""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from products.matching import normalize_name
from products.models import Product
from staff.permissions import perm_required
from staff.utils import redirect_with_qs
from activity.services import log_activity
from activity.models import ActivityLog

# العلاقات المسموح البحث/الإضافة فيها عبر product_relation_search/_add —
# مفتاح واحد يوصف كل علاقة (اسم الحقل + تسمية عربية للرسائل).
RELATION_FIELDS = {
    'similar': ('similar_products', 'منتج مشابه'),
    'complementary': ('complementary_products', 'منتج مكمّل'),
}

PICKER_RESULTS_LIMIT = 8


def _search_products(query, exclude_ids):
    """
    بحث عام لأي Product Picker (بالاسم العربي/الإنجليزي أو الاسم المُطبَّع)،
    مستبعد منه أي id في exclude_ids (المنتج نفسه + المرتبطين بالفعل).
    """
    query = (query or '').strip()
    if not query:
        return Product.objects.none()
    normalized_q = normalize_name(query)
    from django.db.models import Q
    return (
        Product.objects.filter(
            Q(name_ar__icontains=query) | Q(name_key__icontains=normalized_q) | Q(name_en__icontains=query)
        )
        .exclude(pk__in=exclude_ids)
        .select_related('category')[:PICKER_RESULTS_LIMIT]
    )


@perm_required('products.change_product')
def product_relation_search(request, pk, relation):
    """
    نتيجة البحث (htmx) لعلاقة معيّنة (similar/complementary) — بيرجّع
    partial فيه أزرار قابلة للضغط لكل نتيجة، ماعداش المنتج نفسه والمرتبطين
    فعلًا بنفس العلاقة (منعًا لإضافة مكررة).
    """
    if relation not in RELATION_FIELDS:
        return render(request, 'staff/products/partials/relation_picker_results.html', {'results': []})
    product = get_object_or_404(Product, pk=pk)
    field_name, _ = RELATION_FIELDS[relation]
    already_linked_ids = list(getattr(product, field_name).values_list('pk', flat=True))
    results = _search_products(request.GET.get('q', ''), exclude_ids=already_linked_ids + [product.pk])
    return render(request, 'staff/products/partials/relation_picker_results.html', {
        'results': results, 'relation': relation, 'product': product,
    })


@perm_required('products.change_product')
@require_POST
def product_relation_add(request, pk, relation):
    if relation not in RELATION_FIELDS:
        messages.error(request, 'نوع علاقة غير معروف.')
        return redirect_with_qs(request, 'staff:product_edit', pk=pk)

    product = get_object_or_404(Product, pk=pk)
    target = get_object_or_404(Product, pk=request.POST.get('target_id'))
    field_name, label = RELATION_FIELDS[relation]

    if target.pk == product.pk:
        messages.error(request, 'لا يمكن ربط المنتج بنفسه.')
        return redirect_with_qs(request, 'staff:product_edit', pk=pk)

    getattr(product, field_name).add(target)
    log_activity(
        product, ActivityLog.Event.UPDATED, user=request.user,
        changes_summary=f'إضافة {label}: {target.name_ar}',
    )
    return redirect_with_qs(request, 'staff:product_edit', pk=pk)


@perm_required('products.change_product')
@require_POST
def product_relation_remove(request, pk, relation):
    if relation not in RELATION_FIELDS:
        messages.error(request, 'نوع علاقة غير معروف.')
        return redirect_with_qs(request, 'staff:product_edit', pk=pk)

    product = get_object_or_404(Product, pk=pk)
    target = get_object_or_404(Product, pk=request.POST.get('target_id'))
    field_name, label = RELATION_FIELDS[relation]

    getattr(product, field_name).remove(target)
    log_activity(
        product, ActivityLog.Event.UPDATED, user=request.user,
        changes_summary=f'إزالة {label}: {target.name_ar}',
    )
    return redirect_with_qs(request, 'staff:product_edit', pk=pk)
