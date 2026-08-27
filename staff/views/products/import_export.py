"""
استيراد/تصدير المنتجات من وإلى ملفات إكسل. منطق CRUD الأساسي (عرض/إضافة/
تعديل/حذف) منفصل في crud.py — راجع staff/views/products/__init__.py
للتوثيق الكامل لسبب الفصل.

منطق القراءة/التصنيف/الحفظ نفسه (parsing, fuzzy matching, commit) منقول
لـ products.services.import_export عشان يبقى قابل للاختبار من غير ما نمر
بـ request/session — هنا بس تنسيق الـ HTTP request/response وrenders.
"""
import os
import uuid

from django.conf import settings
from django.core.cache import cache
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect
from django.contrib import messages
from django.db.models import Q

from products.models import Category, Product
from products.matching import normalize_name
from products.new_arrivals import NEW_ARRIVALS_WINDOW_DAYS
from products.services import import_export as import_export_service
from staff.permissions import perm_required
from staff.excel_utils import XLSX_CONTENT_TYPE, workbook_response

IMPORT_ERRORS_SESSION_KEY = 'product_import_last_errors'
# حماية من ملف إكسل ضخم بالغلط (أو مقصود): الدفعة بالكامل بتتخزن مؤقتًا في
# الـ session (قاعدة البيانات) بين شاشة المراجعة وشاشة التأكيد، فملف بعشرات
# الآلاف من الصفوف كان بيعمل صف session ضخم ويشغل الـ worker وقت طويل في
# طلب واحد. الحدين دول سقف منطقي لأي استيراد حقيقي (لو المخزن عنده كتالوج
# أكبر فعلاً، يقسّم الملف على أكتر من دفعة).
IMPORT_MAX_FILE_SIZE_MB = 5
IMPORT_MAX_ROWS = 3000

# مجلد مؤقت مشترك بين هذه الحاوية (web-staff) وceleryworker عن طريق
# import_tmp volume في docker-compose.yml — بنحفظ فيه الملف المرفوع
# عشان مهمة Celery (products/tasks.py) تقدر توصله وتقراه في الخلفية.
IMPORT_TMP_DIR = os.path.join(settings.BASE_DIR, 'tmp_imports')

# قبل كده كانت شاشة المراجعة بترندر كل صفوف "هيتحدّث"/"هيتضاف" (لحد آلاف
# السطور مع ملف كبير) في قائمة واحدة من غير أي تقسيم — الحل: نفس Paginator
# المستخدم في قائمة المنتجات العادية (crud.py)، بس بيقرا رقم صفحة مختلف
# لكل قسم (?update_page=.. / ?create_page=..) عشان الاتنين يتقسموا صفحات
# مستقلة عن بعض في نفس الشاشة.
REVIEW_LIST_PAGE_SIZE = 50
# عدد مجموعات الإيرورز (بعد التجميع) المعروضة افتراضيًا قبل الحاجة لـ "عرض الكل".
REVIEW_ERRORS_PREVIEW_COUNT = 15

# صفحة اختيار الأصناف للتصدير (export_products_select) كانت بتسحب كل
# أصناف المتجر دفعة واحدة كـ JSON وتفلتر/تقسّم صفحات في المتصفح بالكامل —
# مع كتالوج كبير ده بيبطّئ فتح الصفحة (تحميل + parse لكل الأصناف حتى لو
# الموظف هيصدّر 5 بس). دلوقتي البحث/الفلترة/تقسيم الصفحات بيحصلوا في
# السيرفر (زي staff:product_list بالظبط) والصفحة الحالية بس اللي بتوصل
# للمتصفح — التحديد نفسه (IDs) لسه متراكم في Alpine مستقل عن أي صفحة
# ظاهرة حاليًا، عشان التنقل بين الصفحات أو تغيير الفلتر ميمسحش أي تحديد
# سابق (نفس السبب اللي كان خلّى التصميم الأصلي يحمّل كل حاجة مرة واحدة).
EXPORT_PICKER_PAGE_SIZE = 50

# Backward-compat: بعض الكود القديم (أو أي كود خارجي) كان بيستورد الثوابت
# دي من هنا مباشرة قبل الفصل — بتفضل متاحة كـ alias للمصدر الحقيقي.
FUZZY_MATCH_THRESHOLD = import_export_service.FUZZY_MATCH_THRESHOLD
DISCOUNT_COL_PREFIX = import_export_service.DISCOUNT_COL_PREFIX


@perm_required('products.add_product')
def import_products(request):
    """
    المرحلة الأولى: قراءة الملف وتصنيف كل صف (تحديث أكيد / إضافة جديدة
    أكيدة / يحتاج مراجعة) من غير أي حفظ فعلي، ثم عرض شاشة مراجعة. الحفظ
    الفعلي بيحصل بس في import_products_confirm بعد موافقة الموظف على أي
    صف يحتاج قرار بشري (اسم قريب من صنف موجود).
    """
    if request.method == 'POST':
        excel_file = request.FILES.get('excel_file')
        if not excel_file:
            messages.error(request, 'يرجى اختيار ملف Excel أولاً.')
            return redirect('staff:import_products')
        if not excel_file.name.endswith('.xlsx'):
            messages.error(request, 'يجب أن يكون الملف بصيغة .xlsx')
            return redirect('staff:import_products')
        if excel_file.size > IMPORT_MAX_FILE_SIZE_MB * 1024 * 1024:
            messages.error(
                request,
                f'حجم الملف أكبر من الحد المسموح ({IMPORT_MAX_FILE_SIZE_MB} ميجا). '
                f'يرجى تقسيم الملف على أكتر من دفعة استيراد.'
            )
            return redirect('staff:import_products')

        # قراءة وتصنيف الملف (خصوصًا مع 3000 صف) كانت بتتنفذ هنا مباشرة
        # جوه نفس طلب الـ HTTP — بتاخد Gunicorn worker كامل لمدة طويلة،
        # وده اللي كان بيسبب 504 Gateway Timeout من nginx (راجع تقرير
        # اختبار المرحلة 0). دلوقتي بنحفظ الملف على القرص المشترك
        # (import_tmp) وبنبعت المعالجة لـ Celery في الخلفية، ونرجع
        # فورًا — مفيش أصلًا طلب HTTP طويل يتقاس ضده أي timeout.
        os.makedirs(IMPORT_TMP_DIR, exist_ok=True)
        tmp_path = os.path.join(IMPORT_TMP_DIR, f'{uuid.uuid4().hex}.xlsx')
        with open(tmp_path, 'wb') as dest:
            for chunk in excel_file.chunks():
                dest.write(chunk)

        from products.tasks import import_result_cache_key, parse_import_file
        # نظّف أي نتيجة استيراد سابقة للموظف ده (لو رفع ملف قبل كده ولسه
        # فاتحة الشاشة) عشان شاشة الانتظار متتأكدش من نتيجة قديمة غلط.
        cache.delete(import_result_cache_key(request.user.pk))
        parse_import_file.delay(tmp_path, IMPORT_MAX_ROWS, request.user.pk)

        return render(request, 'staff/products/import_processing.html')
    return render(request, 'staff/products/import.html')


@perm_required('products.add_product')
def import_products_status(request):
    """
    Endpoint خفيف بتستدعيه شاشة الانتظار (import_processing.html) كل
    ثانيتين عن طريق JS بسيط — بيتأكد هل مهمة Celery خلصت (النتيجة موجودة
    في الكاش) ولا لسه. مفيش حاجة تقيلة هنا، مجرد قراءة كاش.
    """
    from products.tasks import import_result_cache_key
    cached = cache.get(import_result_cache_key(request.user.pk))
    return JsonResponse({'ready': cached is not None})


@perm_required('products.add_product')
def import_products_review(request):
    """
    شاشة المراجعة: بتعرض عدد الأصناف اللي هتتحدّث/هتتضاف بثقة تلقائيًا،
    وبتوقف عند أي صف اسمه قريب من صنف موجود وتسأل الموظف صراحةً هل ده
    نفس الصنف (تحديث) ولا صنف جديد فعلًا — قبل أي حفظ في قاعدة البيانات.
    """
    from products.tasks import (
        import_result_cache_key,
        import_review_batch_cache_key,
        IMPORT_REVIEW_BATCH_TTL,
    )
    review_key = import_review_batch_cache_key(request.user.pk)
    batch = cache.get(review_key)
    if not batch:
        # أول ما يوصل هنا (من رابط الإشعار أو تحويلة شاشة الانتظار)،
        # النتيجة لسه في كاش النتيجة الخام (Celery حطها هناك — راجع
        # products/tasks.py). ننقلها لمفتاح كاش المراجعة مرة واحدة عشان
        # باقي شاشات الاستيراد (التأكيد، الأخطاء) تفضل شغالة زي ما هي
        # بالظبط.
        cached = cache.get(import_result_cache_key(request.user.pk))
        if cached and cached.get('status') == 'done' and not cached.get('error_message'):
            batch = {'rows': cached['rows'], 'errors': cached['errors']}
            cache.delete(import_result_cache_key(request.user.pk))
        elif cached:
            messages.error(request, cached.get('error_message') or 'حصل خطأ أثناء قراءة الملف.')
            cache.delete(import_result_cache_key(request.user.pk))
            return redirect('staff:import_products')

    if not batch:
        messages.error(request, 'مفيش عملية استيراد جارية. من فضلك ارفع الملف تاني.')
        return redirect('staff:import_products')

    # تجديد الصلاحية في كل مرة الشاشة دي بتترندر (أول وصول أو أي تنقل
    # صفحات لاحق) — عشان مراجعة طويلة (كتالوج كبير، موظف بياخد وقته)
    # متنتهيش صلاحيتها لوحدها من نص الطريق.
    cache.set(review_key, batch, timeout=IMPORT_REVIEW_BATCH_TTL)

    rows = batch['rows']
    all_update_rows = [r for r in rows if r['action'] == 'update']
    all_create_rows = [r for r in rows if r['action'] == 'create']
    # review_rows (فيها radio inputs لازم تتبعت كلها في الفورم) مش بتتقسّم —
    # طبيعتها محدودة أصلًا (بس الصفوف اللي محتاجة قرار بشري لتشابه الاسم).
    review_rows = [r for r in rows if r['action'] == 'review']

    update_paginator = Paginator(all_update_rows, REVIEW_LIST_PAGE_SIZE)
    update_page = update_paginator.get_page(request.GET.get('update_page'))
    create_paginator = Paginator(all_create_rows, REVIEW_LIST_PAGE_SIZE)
    create_page = create_paginator.get_page(request.GET.get('create_page'))

    grouped_errors = import_export_service.group_import_errors(batch['errors'])

    context = {
        'grouped_errors': grouped_errors,
        'errors_preview_count': REVIEW_ERRORS_PREVIEW_COUNT,
        'update_rows': update_page,
        'update_rows_total': len(all_update_rows),
        'create_rows': create_page,
        'create_rows_total': len(all_create_rows),
        'review_rows': review_rows,
        'new_arrivals_window_days': NEW_ARRIVALS_WINDOW_DAYS,
    }
    return render(request, 'staff/products/import_review.html', context)


@perm_required('products.add_product')
def import_products_confirm(request):
    """
    المرحلة التانية: بتاخد قرارات الموظف على صفوف "المراجعة" (اتحدد لكل
    واحد منها إما تحديث صنف بعينه أو إضافته كصنف جديد فعلًا)، وبدل ما
    تنفّذ الحفظ الفعلي هنا مباشرة (زي قبل كده)، بتخزّن الدفعة في الكاش
    وتبعتها لـcelery-worker (commit_import_batch_task) وتوجّه الموظف
    لشاشة انتظار.

    السبب: الحفظ الفعلي لدفعة كبيرة (لحد 3000 صف) جوه transaction واحدة
    كان بياخد وقت طويل نسبيًا (مئات/آلاف query منفصلة)، وكان شغال جوه
    نفس طلب HTTP في container web-staff (0.5 CPU فقط — نصف تخصيص
    web-store) — يعني بيقفل الـworker طول مدة الحفظ ومعرّض لـtimeout من
    nginx/gunicorn لو الملف كبير كفاية. نفس السبب اللي خلّى مرحلة القراءة
    (parse_import_file) تتنقل لـCelery قبل كده — دلوقتي مرحلة التأكيد
    بتتبع نفس النمط بالظبط.

    الدفعة بتتقرا من نفس مفتاح كاش المراجعة (import_review_batch_cache_key)
    بدل الجلسة — نفس أسلوب باقي مراحل الاستيراد المؤقتة (مفتاح مبني على
    user_id في الكاش)، بدل الاعتماد على سيشن الطلب الأصلي.
    """
    if request.method != 'POST':
        return redirect('staff:import_products')

    from products.tasks import import_review_batch_cache_key
    review_key = import_review_batch_cache_key(request.user.pk)
    batch = cache.get(review_key)
    if not batch:
        messages.error(request, 'انتهت صلاحية عملية الاستيراد دي. من فضلك ارفع الملف تاني.')
        return redirect('staff:import_products')

    rows = batch['rows']
    decisions = {
        row['row_num']: request.POST.get(f"decision_{row['row_num']}", 'new')
        for row in rows if row['action'] == 'review'
    }

    from products.tasks import (
        commit_import_batch_task,
        import_commit_payload_cache_key,
        import_commit_result_cache_key,
    )
    # نظّف أي نتيجة تأكيد سابقة لنفس الموظف (لو أعاد الضغط على تأكيد قبل
    # كده ولسه فاتح الشاشة) — نفس فكرة تنظيف نتيجة القراءة القديمة في
    # import_products قبل الـdelay.
    cache.delete(import_commit_result_cache_key(request.user.pk))
    payload = {
        'rows': rows,
        'decisions': decisions,
        'errors': batch.get('errors') or [],
        'notify_clients': request.POST.get('notify_clients') == 'on',
    }
    cache.set(import_commit_payload_cache_key(request.user.pk), payload, timeout=60 * 30)
    cache.delete(review_key)
    commit_import_batch_task.delay(request.user.pk)

    return render(request, 'staff/products/import_committing.html')


@perm_required('products.add_product')
def import_products_commit_status(request):
    """
    نظير import_products_status بالظبط بس لمرحلة التأكيد/الحفظ — endpoint
    خفيف بتستدعيه شاشة الانتظار (import_committing.html) كل ثانيتين،
    بيتأكد هل commit_import_batch_task خلصت (النتيجة موجودة في الكاش).
    """
    from products.tasks import import_commit_result_cache_key
    cached = cache.get(import_commit_result_cache_key(request.user.pk))
    return JsonResponse({'ready': cached is not None})


@perm_required('products.add_product')
def import_products_commit_result(request):
    """
    بتقرا نتيجة الحفظ (اللي commit_import_batch_task خزّنتها في الكاش)
    وتحوّلها لـmessages حقيقية — ده لازم يحصل جوه request فعلي (messages
    framework مرتبط بالـsession، مش متاح جوه Celery task)، نفس فكرة
    import_products_review بالظبط بالنسبة لنتيجة مرحلة القراءة.
    """
    from products.tasks import import_commit_result_cache_key
    cached = cache.get(import_commit_result_cache_key(request.user.pk))
    if not cached:
        messages.error(request, 'مفيش نتيجة حفظ جاهزة. من فضلك ارفع الملف تاني.')
        return redirect('staff:import_products')
    cache.delete(import_commit_result_cache_key(request.user.pk))

    if cached['status'] != 'done':
        messages.error(request, cached.get('error_message') or 'حصل خطأ أثناء الحفظ ولم يتم حفظ أي صنف.')
        return redirect('staff:import_products')

    created_count = cached['created_count']
    updated_count = cached['updated_count']
    if created_count:
        messages.success(request, f'تم إضافة {created_count} صنف جديد.')
    if updated_count:
        messages.success(request, f'تم تحديث {updated_count} صنف موجود.')

    errors = cached.get('errors') or []
    if errors:
        request.session[IMPORT_ERRORS_SESSION_KEY] = errors
        messages.warning(request, f'تم تجاهل {len(errors)} صف فيهم مشكلة أثناء القراءة — التفاصيل تحت.')
        return redirect('staff:import_products_errors')

    return redirect('staff:product_list')


@perm_required('products.add_product')
def import_products_errors(request):
    """
    تفاصيل التحذيرات (صفوف اتجاهلت) بعد آخر عملية استيراد اتأكّدت — بديل
    عن رميهم كـ toasts منفصلة. مجمّعة حسب نوع المشكلة (راجع
    group_import_errors) ومقسّمة صفحات لو كانت المجموعات كتير.
    """
    errors = request.session.get(IMPORT_ERRORS_SESSION_KEY, [])
    grouped_errors = import_export_service.group_import_errors(errors)
    paginator = Paginator(grouped_errors, REVIEW_LIST_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'staff/products/import_errors.html', {
        'grouped_errors': page_obj,
        'total_errors': len(errors),
        'total_groups': paginator.count,
    })


@perm_required('products.view_product')
def download_template(request):
    """
    قالب فيه صف لكل وحدة (مش صف لكل صنف) — راجع
    products.services.import_export.build_import_template_workbook لتفاصيل الصيغة.
    """
    wb = import_export_service.build_import_template_workbook()
    return workbook_response(wb, 'biozone_products_template.xlsx')


@perm_required('products.view_product')
def export_products(request):
    """
    تصدير كل الأصناف الحالية دفعة واحدة (بدون اختيار) — كان بيبني ملف
    الإكسل متزامن جوه نفس طلب الـ HTTP على web-staff (بدون أي حد أقصى
    لعدد الصفوف)، وده بيتفاقم تلقائيًا مع نمو الكتالوج (راجع ADR-001).
    دلوقتي بنبعت المعالجة لـ Celery في الخلفية (نفس نمط
    import_products/parse_import_file) ونرجّع فورًا شاشة انتظار
    بسيطة بتعمل polling على export_products_status.
    """
    from products.tasks import export_products_task, export_result_cache_key
    # نظّف أي نتيجة تصدير سابقة للموظف ده (لو كان طلب تصدير قبل كده ولسه
    # فاتح الشاشة) عشان شاشة الانتظار متتأكدش من نتيجة قديمة غلط.
    cache.delete(export_result_cache_key(request.user.pk))
    export_products_task.delay(request.user.pk)
    return render(request, 'staff/products/export_processing.html')


@perm_required('products.view_product')
def export_products_status(request):
    """
    Endpoint خفيف بتستدعيه شاشة الانتظار (export_processing.html) كل
    ثانيتين — بيتأكد هل مهمة Celery خلصت (نتيجة موجودة في الكاش) ولا لسه،
    وهل خلصت بنجاح ولا بفشل. نفس فكرة import_products_status بالظبط.
    """
    from products.tasks import export_result_cache_key
    cached = cache.get(export_result_cache_key(request.user.pk))
    return JsonResponse({
        'ready': cached is not None,
        'failed': bool(cached and cached.get('status') == 'failed'),
    })


@perm_required('products.view_product')
def export_products_download(request):
    """
    بتقرا ملف التصدير الجاهز من القرص المشترك (export_products_task حفظه
    هناك) وترجعه للموظف كتحميل، وبعدين تمسحه فورًا — رابط استخدام واحد.
    لو الملف مش موجود (اتحمّل قبل كده، أو الكاش انتهت صلاحيته) بترجّع
    الموظف لصفحة التصدير تاني بدل خطأ 500.
    """
    from products.tasks import export_result_cache_key
    cached = cache.get(export_result_cache_key(request.user.pk))
    if not cached:
        messages.error(request, 'مفيش ملف تصدير جاهز حاليًا (ممكن يكون اتحمّل قبل كده أو انتهت صلاحيته). جرّب تاني.')
        return redirect('staff:export_products')

    if cached.get('status') != 'done':
        messages.error(request, cached.get('error_message') or 'حصل خطأ أثناء تجهيز ملف التصدير.')
        cache.delete(export_result_cache_key(request.user.pk))
        return redirect('staff:export_products')

    file_path = cached['file_path']
    if not os.path.exists(file_path):
        messages.error(request, 'ملف التصدير مش موجود على السيرفر (اتحمّل قبل كده على الأرجح). جرّب تاني.')
        cache.delete(export_result_cache_key(request.user.pk))
        return redirect('staff:export_products')

    with open(file_path, 'rb') as f:
        data = f.read()
    try:
        os.remove(file_path)
    except OSError:
        pass
    cache.delete(export_result_cache_key(request.user.pk))

    response = HttpResponse(data, content_type=XLSX_CONTENT_TYPE)
    download_filename = cached.get('download_filename', 'biozone_products_export.xlsx')
    response['Content-Disposition'] = f'attachment; filename="{download_filename}"'
    return response


def _export_picker_queryset(request):
    """
    الاستعلام المشترك بين صفحة الاختيار (أول تحميل) وendpoint الجدول
    (htmx بعد أي بحث/فلتر/تنقل صفحات) — عشان الاتنين يفلتروا بنفس
    المنطق بالظبط. نفس حقول البحث المستخدمة في staff:product_list
    (name_ar/name_en/name_key المُطبَّع/code) عشان سلوك متسق في كل
    شاشات المنتجات.
    """
    q = request.GET.get('q', '').strip()
    category_slug = request.GET.get('category', '').strip()
    products = Product.objects.select_related('category').order_by('name_ar')
    if category_slug:
        products = products.filter(category__slug=category_slug)
    if q:
        normalized_q = normalize_name(q)
        products = products.filter(
            Q(name_ar__icontains=q)
            | Q(name_en__icontains=q)
            | Q(name_key__icontains=normalized_q)
            | Q(code__icontains=q)
        )
    return products, q, category_slug


def _export_picker_page_context(request):
    products, q, category_slug = _export_picker_queryset(request)
    paginator = Paginator(products, EXPORT_PICKER_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))
    return {
        'page_obj': page_obj,
        'page_ids': [p.pk for p in page_obj],
        'query': q,
        'category_slug': category_slug,
    }


@perm_required('products.view_product')
def export_products_select(request):
    """
    صفحة اختيار الأصناف قبل التصدير: تقدر تبحث وتحدد أصناف بعينها، أو
    تحدد قسم كامل (وبعدين تشيل منه أي صنف مش عايزه)، والنظام هيصدّر بس
    اللي محدد فعليًا لملف إكسل بنفس صيغة "تصدير الأصناف الحالية".

    الجدول نفسه (صفحة 1 افتراضيًا) بيترندر هنا بنفس الـ partial اللي
    endpoint الـ htmx (export_products_table) بيستخدمه، عشان الصفحة
    الأولى تظهر فورًا من غير طلب htmx إضافي بعد التحميل.
    """
    categories = Category.objects.filter(is_active=True).order_by('name')
    context = _export_picker_page_context(request)
    context['categories'] = categories
    return render(request, 'staff/products/export_select.html', context)


@perm_required('products.view_product')
def export_products_table(request):
    """
    partial الجدول + التقسيم لصفحات — بيترجع نفس الجزء اللي export_products_select
    بيرندره أول مرة، بس مستدعى عن طريق htmx بعد أي تغيير في البحث/الفلتر/
    رقم الصفحة (شوف hx-get في export_select.html).
    """
    context = _export_picker_page_context(request)
    return render(request, 'staff/products/partials/export_table.html', context)


@perm_required('products.view_product')
def export_products_category_ids(request):
    """
    كل IDs الأصناف في قسم معيّن (مش بس اللي ظاهرة في الصفحة الحالية) —
    بيُستخدم لزرار "تحديد القسم كامل" عشان يضيف القسم بالكامل للتحديد
    المتراكم حتى لو القسم بيمتد على أكتر من صفحة. زي التصميم الأصلي،
    مش بيتأثر بخانة البحث — تحديد قسم كامل يعني القسم كله بغض النظر عن
    أي فلتر بحث حالي.
    """
    category_slug = request.GET.get('category', '').strip()
    if not category_slug:
        return JsonResponse({'ids': []})
    ids = list(
        Product.objects.filter(category__slug=category_slug).values_list('pk', flat=True)
    )
    return JsonResponse({'ids': ids})


@perm_required('products.view_product')
def export_products_selected(request):
    """
    يستقبل قائمة IDs من صفحة الاختيار ويصدّرها كملف إكسل واحد.

    كانت لسه بتبني الملف متزامن جوه الـ request/response (فجوة حقيقية
    لقيناها أثناء مراجعة الفرق مع mg — export_products نفسها كانت أصلاً
    منقولة لـCelery من زمان، بس النسخة "المحددة" دي اتنستيت). دلوقتي
    بتستخدم نفس export_products_task المعمَّمة (product_ids بدل None) —
    نفس النمط، نفس شاشة الانتظار، من غير أي تكرار منطق.
    """
    if request.method != 'POST':
        return redirect('staff:export_products_select')

    ids = [pk for pk in request.POST.getlist('product_ids') if pk.isdigit()]
    if not ids:
        messages.warning(request, 'لازم تحدد صنف واحد على الأقل قبل التصدير.')
        return redirect('staff:export_products_select')

    from products.tasks import export_products_task, export_result_cache_key
    cache.delete(export_result_cache_key(request.user.pk))
    export_products_task.delay(request.user.pk, product_ids=ids, download_filename='biozone_products_export_selected.xlsx')
    return render(request, 'staff/products/export_processing.html')
