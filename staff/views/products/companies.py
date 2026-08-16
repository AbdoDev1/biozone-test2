"""
إدارة الشركات المصنّعة (Company) من لوحة الموظفين: عرض/إضافة/تعديل/حذف.
نفس باترن staff/views/products/categories.py بالظبط — Company اتضافت
(أغسطس 2026) كموديل مستقل بدل Product.manufacturer نص حر، عشان تتحل
مشكلة تكرار أسماء الشركات الشكلي (راجع Company docstring في
products/models.py).
"""
from django.core.paginator import Paginator
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import ProtectedError, Count
from django.contrib.contenttypes.models import ContentType

from products.models import Company
from products.forms import CompanyForm
from staff.permissions import perm_required
from activity.models import ActivityLog
from activity.services import log_activity, diff_summary

COMPANY_LIST_PAGE_SIZE = 30
COMPANY_TRACKED_FIELDS = ['name', 'is_active']


@perm_required('products.view_company')
def company_list(request):
    companies = Company.objects.annotate(products_count=Count('products')).order_by('name')
    search_q = request.GET.get('q', '').strip()
    if search_q:
        companies = companies.filter(name__icontains=search_q)

    paginator = Paginator(companies, COMPANY_LIST_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'staff/companies/list.html', {
        'companies': page_obj,
        'page_obj': page_obj,
        'total_companies': paginator.count,
        'search_q': search_q,
    })


@perm_required('products.add_company')
def company_add(request):
    if request.method == 'POST':
        form = CompanyForm(request.POST)
        if form.is_valid():
            company = form.save()
            log_activity(company, ActivityLog.Event.CREATED, user=request.user)
            messages.success(request, f'تم إضافة الشركة "{company.name}" بنجاح.')
            return redirect('staff:company_list')
    else:
        form = CompanyForm()
    return render(request, 'staff/companies/form.html', {
        'form': form,
        'title': 'إضافة شركة جديدة',
        'is_edit': False,
    })


@perm_required('products.change_company')
def company_edit(request, pk):
    company = get_object_or_404(Company, pk=pk)
    if request.method == 'POST':
        old_values = {f: getattr(company, f) for f in COMPANY_TRACKED_FIELDS}
        form = CompanyForm(request.POST, instance=company)
        if form.is_valid():
            form.save()
            summary = diff_summary(old_values, company, COMPANY_TRACKED_FIELDS)
            if summary:
                log_activity(company, ActivityLog.Event.UPDATED, user=request.user, changes_summary=summary)
            messages.success(request, f'تم تعديل الشركة "{company.name}" بنجاح.')
            return redirect('staff:company_list')
    else:
        form = CompanyForm(instance=company)
    return render(request, 'staff/companies/form.html', {
        'form': form,
        'title': f'تعديل: {company.name}',
        'is_edit': True,
        'company': company,
    })


@perm_required('products.delete_company')
def company_delete(request, pk):
    company = get_object_or_404(Company, pk=pk)
    # عكس Category (PROTECT)، Product.manufacturer معمول عليه SET_NULL —
    # يعني حذف الشركة مش هيرمي ProtectedError تلقائيًا حتى لو ليها أصناف
    # مرتبطة (هيتصفّر الربط بس بصمت). بنعمل نفس فحص has_products يدويًا
    # هنا (زي category_delete) عشان الموظف يشوف تحذير واضح ويختار
    # "تعطيل" بدل ما نسيب المنتجات تفقد شركتها المصنّعة بصمت.
    has_products = company.products.exists()

    if request.method == 'POST':
        name = company.name
        if has_products:
            company.is_active = False
            company.save()
            log_activity(company, ActivityLog.Event.UPDATED, user=request.user, changes_summary='تم تعطيل الشركة')
            messages.warning(request, f'الشركة "{name}" لها أصناف مرتبطة بها — تم تعطيلها بدل الحذف.')
        else:
            company_pk = company.pk
            try:
                company.delete()
            except ProtectedError:
                company.is_active = False
                company.save()
                log_activity(company, ActivityLog.Event.UPDATED, user=request.user, changes_summary='تم تعطيل الشركة')
                messages.warning(request, f'الشركة "{name}" مرتبطة بأصناف — تم تعطيلها بدل الحذف.')
            else:
                ActivityLog.objects.create(
                    content_type=ContentType.objects.get_for_model(Company),
                    object_id=company_pk,
                    event=ActivityLog.Event.DELETED,
                    created_by=request.user,
                )
                messages.success(request, f'تم حذف الشركة "{name}".')
        return redirect('staff:company_list')

    return render(request, 'staff/companies/delete.html', {
        'company': company,
        'has_products': has_products,
    })
