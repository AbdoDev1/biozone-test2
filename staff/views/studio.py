from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.core.validators import FileExtensionValidator
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import User
from staff.permissions import perm_required
from staff.utils import redirect_with_qs
from studio.models import StudioFolder, StudioImage
from studio.validators import ALLOWED_IMAGE_EXTENSIONS, validate_image_size

# نفس فكرة STAFF_LIST_PAGE_SIZE في staff/views/products/crud.py، بس بحجم
# أكبر شوية لأن المعرض بيعرض شبكة thumbnails صغيرة (مش صفوف جدول)، فمساحة
# الشاشة بتسمح بعدد أكبر لكل صفحة بلا ما تتزحلق كتير.
STUDIO_GALLERY_PAGE_SIZE = 40

# منتقي الصور جوه مودال فورم المنتج/القسم (المرحلة 8) — عدد أصغر من معرض
# الاستوديو الرئيسي لأن مساحة المودال نفسها أصغر بكتير من الشاشة كاملة.
STUDIO_PICKER_PAGE_SIZE = 20

_extension_validator = FileExtensionValidator(ALLOWED_IMAGE_EXTENSIONS)


def _usage_confirm_message(products_count, categories_count):
    """
    رسالة تأكيد الحذف (المرحلة 5) — بتتبني من عدد المنتجات/الأقسام
    المرتبطة (StudioImage.get_usage()). لو صفر في الاتنين بترجع رسالة
    عامة بلا تفاصيل عدد. مبنية كدالة منفصلة عشان تُستخدم لكل صورة على
    حدة (الحذف الفردي) ولمجموع صور محددة (الحذف الجماعي) بنفس المنطق.

    مفيش تفريق نحوي دقيق (مفرد/مثنى/جمع) هنا عمدًا — نفس مستوى البساطة
    المستخدم في باقي رسائل العدّ بالمشروع (زي "X صنف محدد").
    """
    parts = []
    if products_count:
        parts.append(f'{products_count} منتج')
    if categories_count:
        parts.append(f'{categories_count} قسم')

    if not parts:
        return 'متأكد من حذف هذه الصورة؟'

    return 'هذه الصورة مستخدمة في ' + ' و'.join(parts) + '، وسيتم حذفها منهم أيضًا. متأكد من الحذف؟'


@perm_required('studio.view_studioimage')
def studio(request):
    """
    معرض الاستوديو — thumbnails بس (مش الصورة الأصلية)، مع pagination
    حقيقي (نفس نمط staff/products/list.html). راجع STUDIO_PLAN.md، المرحلة 3.

    فلتر مستخدمة/غير مستخدمة (المرحلة 4) — بعد تنفيذ المرحلة 8 (ربط
    Product.image/Category.image فعليًا بـ ForeignKey)، StudioImage.get_usage()
    بقت بترجع الاستخدام الحقيقي. الفلترة هنا لسه بتترشّح في بايثون (مش
    queryset annotation)، لأن `.prefetch_related('products', 'categories')`
    تحت كافي لتفادي N+1 (باقي كل حاجة محسوبة مسبقًا في الذاكرة بلا أي
    استعلام إضافي لكل صورة)، ومفيش داعي عملي لـ annotate بعدد المرتبطين
    على مستوى قاعدة البيانات فوق كده حاليًا.

    فلتر المجلد (المرحلة 6) مختلف: `folder` عمود FK حقيقي من دلوقتي،
    فبيترشّح على مستوى الـ queryset (`.filter(folder_id=...)`) قبل حتى
    ما نجيب الصور، مش في بايثون زي فلتر الاستخدام فوق.
    """
    folder_filter = request.GET.get('folder', '')
    # prefetch_related('products', 'categories') — بلا ده، كل استدعاء
    # لـ img.get_usage() تحت (لكل صورة في الصفحة، لحد 40) كان هيعمل
    # استعلامين إضافيين (منتجات + أقسام)، يعني لحد 80 استعلام زيادة لكل
    # تحميل صفحة معرض واحدة. من المرحلة 8 (بعد ما get_usage() بقت بترجع
    # علاقات حقيقية بدل قوائم فاضية دايمًا)، الـ prefetch ده بقى ضروري —
    # مش تحسين اختياري.
    qs = StudioImage.objects.select_related('uploaded_by', 'folder').prefetch_related('products', 'categories').all()
    if folder_filter == 'none':
        qs = qs.filter(folder__isnull=True)
    elif folder_filter.isdigit():
        qs = qs.filter(folder_id=folder_filter)

    all_images = list(qs)

    # بنحسب عدد المنتجات/الأقسام المرتبطة لكل صورة مرة واحدة هنا (بدل ما
    # نستدعي get_usage() تاني في التمبليت لرسالة تأكيد الحذف، بعد ما
    # is_used أصلًا استدعتها فوق) — وبنحطها كخاصية عادية على الكائن
    # (مش حقل موديل) عشان الحذف الفردي (المرحلة 5) يقدر يبني رسالة
    # التأكيد بلا استعلام إضافي وقت الضغط على الزرار.
    for img in all_images:
        products, categories = img.get_usage()
        img.usage_products_count = len(products)
        img.usage_categories_count = len(categories)
        img.usage_confirm_message = _usage_confirm_message(
            img.usage_products_count, img.usage_categories_count,
        )

    usage_filter = request.GET.get('usage', '')
    if usage_filter == 'used':
        images = [img for img in all_images if img.usage_products_count or img.usage_categories_count]
    elif usage_filter == 'unused':
        images = [img for img in all_images if not (img.usage_products_count or img.usage_categories_count)]
    else:
        images = all_images

    paginator = Paginator(images, STUDIO_GALLERY_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'staff/studio/gallery.html', {
        'page_obj': page_obj,
        'images': page_obj,
        'total_images': paginator.count,
        'usage_filter': usage_filter,
        'folder_filter': folder_filter,
        'folders': StudioFolder.objects.all(),
    })


@perm_required('studio.add_studioimage')
@require_POST
def studio_upload(request):
    """
    رفع صور فردي أو جماعي — كل ملف بياخد صف StudioImage منفصل بمعرّف جديد،
    بلا أي فحص تكرار اسم أو محتوى (قرار رقم 5 و6 في الخطة). لو ملف معيّن
    فشل التحقق (امتداد غير مسموح أو حجم أكبر من المسموح)، بيتجاهل هو بس
    مع رسالة تحذير — باقي الدفعة بتكمل عادي (نفس نمط التعامل مع أخطاء
    الصفوف في استيراد الإكسل).
    """
    files = request.FILES.getlist('images')
    if not files:
        messages.warning(request, 'لازم تختار صورة واحدة على الأقل.')
        return redirect('staff:studio')

    uploaded_count = 0
    for f in files:
        try:
            _extension_validator(f)
            validate_image_size(f)
        except ValidationError as e:
            messages.warning(request, f'{f.name}: {" ".join(e.messages)}')
            continue

        StudioImage.objects.create(image=f, uploaded_by=request.user)
        uploaded_count += 1

    if uploaded_count:
        messages.success(request, f'تم رفع {uploaded_count} صورة بنجاح.')

    return redirect('staff:studio')


@perm_required('studio.delete_studioimage')
@require_POST
def studio_delete(request):
    """
    حذف صورة أو أكتر من الاستوديو دفعة واحدة (المرحلة 5) — نفس الفورم
    مستخدم لزرار الحذف الفردي على كل كارت (image_ids بقيمة واحدة) ولشريط
    "حذف المحدد" الجماعي (image_ids بعدة قيم من checkboxes، نفس نمط
    product_bulk_action في staff/views/products/crud.py). التأكيد الفعلي
    (بعرض عدد المنتجات/الأقسام المرتبطة) بيحصل قبل ما الطلب ده يتبعت
    أصلًا، عن طريق data-confirm على الفورم في gallery.html — مفيش صفحة
    تأكيد وسيطة هنا لأن ده هيكسر حالة التحديد الجماعي في المعرض.

    الحذف نفسه .delete() عادي على queryset — الربط بأي Product/Category
    (لما المرحلة 8 تتنفذ) بيتصفّر تلقائيًا (SET_NULL)، بلا حاجة لأي منطق
    إضافي هنا (قرار رقم 8 في STUDIO_PLAN.md).
    """
    ids = [pk for pk in request.POST.getlist('image_ids') if pk.isdigit()]
    if not ids:
        messages.warning(request, 'لازم تحدد صورة واحدة على الأقل قبل الحذف.')
        return redirect_with_qs(request, 'staff:studio')

    images = StudioImage.objects.filter(pk__in=ids)
    deleted_count = images.count()
    images.delete()

    if deleted_count == 1:
        messages.success(request, 'تم حذف الصورة.')
    else:
        messages.success(request, f'تم حذف {deleted_count} صورة.')

    return redirect_with_qs(request, 'staff:studio')


@perm_required('studio.add_studioimage')
@require_POST
def studio_folder_create(request):
    """
    إنشاء مجلد جديد (المرحلة 6). محمية بنفس صلاحية الرفع
    (`studio.add_studioimage`) — مفيش صلاحية منفصلة للمجلدات في
    الكتالوج (`staff/permissions.py`)، عمدًا على نفس نمط بساطة باقي
    الاستوديو (راجع "قرارات مفتوحة" في STUDIO_PLAN.md قسم 4).
    """
    name = request.POST.get('name', '').strip()
    if not name:
        messages.warning(request, 'لازم تكتب اسم للمجلد.')
        return redirect_with_qs(request, 'staff:studio')

    try:
        StudioFolder.objects.create(name=name)
    except IntegrityError:
        messages.error(request, f'فيه مجلد بالاسم "{name}" أصلًا.')
        return redirect_with_qs(request, 'staff:studio')

    messages.success(request, f'تم إنشاء مجلد "{name}".')
    return redirect_with_qs(request, 'staff:studio')


@perm_required('studio.add_studioimage')
@require_POST
def studio_folder_rename(request, pk):
    """إعادة تسمية مجلد موجود (المرحلة 6)."""
    folder = get_object_or_404(StudioFolder, pk=pk)
    name = request.POST.get('name', '').strip()
    if not name:
        messages.warning(request, 'لازم تكتب اسم للمجلد.')
        return redirect_with_qs(request, 'staff:studio')

    folder.name = name
    try:
        folder.save(update_fields=['name'])
    except IntegrityError:
        messages.error(request, f'فيه مجلد بالاسم "{name}" أصلًا.')
        return redirect_with_qs(request, 'staff:studio')

    messages.success(request, 'تم تعديل اسم المجلد.')
    return redirect_with_qs(request, 'staff:studio')


@perm_required('studio.delete_studioimage')
@require_POST
def studio_folder_delete(request, pk):
    """
    حذف مجلد (المرحلة 6) — الصور اللي جواه بترجع "بلا مجلد" بس
    (`SET_NULL`، راجع StudioFolder docstring)، مش بتتحذف. محمية بنفس
    صلاحية حذف الصور (`studio.delete_studioimage`) لأنها عملية حذف
    ضمن نفس نطاق الاستوديو.
    """
    folder = get_object_or_404(StudioFolder, pk=pk)
    name = folder.name
    folder.delete()
    messages.success(request, f'تم حذف مجلد "{name}" (الصور اللي كانت جواه بقت بلا مجلد).')
    return redirect_with_qs(request, 'staff:studio')


@perm_required('studio.add_studioimage')
@require_POST
def studio_move_to_folder(request):
    """
    نقل صورة أو أكتر لمجلد (أو لـ "بلا مجلد") دفعة واحدة (المرحلة 6) —
    نفس فورم/checkbox التحديد الجماعي المستخدم في studio_delete، بس
    بيغيّر `folder` بدل ما يحذف. محمية بصلاحية الرفع (مش الحذف) لأن
    النقل تعديل تنظيمي، مش حذف فعلي لأي حاجة.

    folder_id فاضي أو '0' يعني "شيل من المجلد الحالي" (بلا مجلد) —
    مش خطأ إدخال.
    """
    ids = [pk for pk in request.POST.getlist('image_ids') if pk.isdigit()]
    if not ids:
        messages.warning(request, 'لازم تحدد صورة واحدة على الأقل قبل النقل.')
        return redirect_with_qs(request, 'staff:studio')

    folder_id = request.POST.get('folder_id', '')
    if folder_id and folder_id != '0':
        folder = get_object_or_404(StudioFolder, pk=folder_id)
    else:
        folder = None

    moved_count = StudioImage.objects.filter(pk__in=ids).update(folder=folder)

    if folder:
        messages.success(request, f'تم نقل {moved_count} صورة لمجلد "{folder.name}".')
    else:
        messages.success(request, f'تم إخراج {moved_count} صورة من المجلد.')

    return redirect_with_qs(request, 'staff:studio')


def studio_picker(request):
    """
    منتقي صور الاستوديو (htmx) — بيتستخدم جوه مودال اختيار صورة منتج/قسم
    (المرحلة 8 في STUDIO_PLAN.md، staff/products/partials/image_picker.html)،
    مش شاشة الاستوديو الرئيسية نفسها (studio() فوق). بيرجع partial بس (بلا
    base.html)، بحث بالاسم + pagination حقيقي، thumbnails بس (نفس فلسفة
    الأداء في المرحلة 3 — الملف الأصلي مش بيتحمّل هنا خالص).

    عمدًا **بلا** @perm_required('studio.view_studioimage') زي باقي شاشات
    الاستوديو: صلاحية "الاستوديو" (قرار رقم 3 في الخطة) بتتحكم في الوصول
    لشاشة إدارة الصور نفسها (رفع/حذف/تنظيم في مجلدات)، لكن اختيار صورة
    موجودة أصلًا من جوه فورم منتج/قسم حاجة مختلفة تمامًا — لو ربطناها
    بنفس الصلاحية، أي موظف عنده صلاحية تعديل منتجات بس (بلا صلاحية
    استوديو منفصلة ماحدش قرر يديله إياها) هيوصل لفورم المنتج عادي لكن
    خانة الصورة فيه هتفضل معطّلة فعليًا بلا أي رسالة واضحة ليه — تعارض
    مباشر مع كونه أصلًا معدّى صلاحية products.add_product/change_product
    (أو مقابلها للأقسام) اللي هي المتطلب الحقيقي للوصول للفورم ده من
    الأساس. الوصول هنا مقصور على تسجيل الدخول كموظف (أدمن/مخزن) بس —
    نفس المستوى الأساسي اللي perm_required بيفرضه قبل حتى ما يوصل لفحص
    أي صلاحية تفصيلية.
    """
    if not request.user.is_authenticated or request.user.role not in (User.Role.ADMIN, User.Role.WAREHOUSE):
        return redirect('staff:login')

    search_q = request.GET.get('q', '').strip()
    qs = StudioImage.objects.all()
    if search_q:
        qs = qs.filter(original_filename__icontains=search_q)

    paginator = Paginator(qs, STUDIO_PICKER_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    # نفس فكرة picker_url المحسوبة مرة واحدة هنا (مش في التمبليت) اللي
    # studio() فوق بيستخدمها لـ usage — بيتفادى تكرار شرط
    # {% if image.thumbnail %}...{% else %}...{% endif %} في التمبليت،
    # وأهم من كده بيتفادى محاولة الوصول لـ .url على حقل ImageField فاضي
    # (image.thumbnail) لو اتكتب غلط بـ |default بدل {% if %} — راجع
    # الملاحظة في gallery.html.
    # full_url (الأصل، مش الـ thumbnail) بيتحسب هنا مرة واحدة كمان — بيتستخدم
    # في زرار المعاينة (lightbox) في picker_results.html بلا أي طلب lookup
    # إضافي وقت الضغط عليه (الـ URL جاهز أصلًا في التمبليت).
    for img in page_obj:
        img.picker_url = img.thumbnail.url if img.thumbnail else img.image.url
        img.full_url = img.image.url

    return render(request, 'staff/studio/partials/picker_results.html', {
        'page_obj': page_obj,
        'images': page_obj,
    })


def studio_image_lookup(request):
    """
    مطابقة معرّف صورة استوديو واحد → JSON، بيتستخدم من:
    1) خانة "أدخل المعرف يدويًا" في منتقي صورة المنتج/القسم
       (image_picker.html) — البديل التاني المطلوب عن الاختيار البصري،
       الموظف بينسخ رقم معرّف من مكان تاني (شيت إكسل، أو استوديو مفتوح
       في تبويب تاني) ويلزقه هنا مباشرة بلا ما يفتح مودال الاختيار خالص.
    2) نافذة المعاينة (lightbox) في نفس المنتقي وفي معرض الاستوديو نفسه —
       بترجع مسار الصورة الأصلية (مش الـ thumbnail) لعرضها بحجمها الكامل.

    نفس مستوى الحماية بالظبط المستخدم في studio_picker فوق (تسجيل دخول
    كموظف أدمن/مخزن بس، بلا اشتراط صلاحية studio.view_studioimage
    المنفصلة) — لنفس السبب الموثّق في docstring studio_picker: الوصول من
    جوه فورم منتج/قسم مرتبط بصلاحية تعديل المنتج نفسه، مش بصلاحية
    الاستوديو الكاملة.
    """
    if not request.user.is_authenticated or request.user.role not in (User.Role.ADMIN, User.Role.WAREHOUSE):
        return JsonResponse({'found': False}, status=403)

    raw_id = request.GET.get('id', '').strip()
    if not raw_id.isdigit():
        return JsonResponse({'found': False})

    image = StudioImage.objects.filter(pk=raw_id).first()
    if not image:
        return JsonResponse({'found': False})

    return JsonResponse({
        'found': True,
        'id': image.pk,
        'thumb_url': image.thumbnail.url if image.thumbnail else image.image.url,
        'full_url': image.image.url,
        'filename': image.original_filename,
    })
