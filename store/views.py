from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render, get_object_or_404
from django.template.loader import render_to_string
from products.models import Category, Company, Product, ProductUnit
from products.matching import normalize_name
from products.new_arrivals import new_arrival_filter, NEW_ARRIVALS_WINDOW_DAYS
from inventory.models import Inventory
from orders.cart import Cart
from django.db.models import Q, Case, When, Value, BooleanField, Count


def _cart_quantities(request):
    """
    قاموس {unit_id: quantity} للسلة النشطة الحالية — بيتحسب مرة واحدة
    وبيتحقن في شبكة المنتجات عشان بطاقة المنتج تعرف تعرض الـ stepper
    (+/-) للأصناف الموجودة فعلاً بالسلة بدل زرار "أضف" دايمًا. فاضل
    فاضي لغير العميل المسجّل (زائر/موظف) لأنه مالوش سلة أصلًا.
    """
    if request.user.is_authenticated and request.user.role == 'CLIENT':
        return Cart(request).get_quantities()
    return {}


PRODUCTS_PER_PAGE = 24

# مدة كاش Redis لنتيجة _categories_list()/_manufacturers_list() (المرحلة
# 4.ب من خطة نقل تعديلات mg) — الدالتين دول كانتا بتعملوا aggregate
# (annotate + Count) على الكتالوج كله في *كل* طلب لـ store_home/
# new_arrivals، رغم إن عدد المنتجات لكل قسم/شركة بيتغيّر ببطء شديد مقارنة
# بمعدل الزيارات، وده كان بيتفاقم تحت حمل متزامن (راجع تقرير اختبار
# المرحلة 0 — / و/store/ من أبطأ صفحتين حتى من غير أي حمل تاني). TTL
# قصير (60 ثانية) — يعني تحديث عدد المنتجات في الفلتر (بعد استيراد كبير
# مثلًا) ممكن ياخد لحد دقيقة يظهر، قرار مقصود بدل تعقيد الموضوع بـ
# invalidation عند كل تعديل صنف/استيراد.
CATALOG_FILTERS_CACHE_TTL = 60


def _base_products_queryset():
    """
    كل المنتجات النشطة، معلَّم عليها is_new_arrival (badge "وارد جديد" في
    الكارت) — من غير أي استبعاد؛ الصنف الوارد فاضل ظاهر هنا زي أي منتج
    عادي بالظبط (في الشبكة، البحث، والأقسام)، وده الفرق الأساسي عن
    التصميم القديم اللي كان بيشيل الصنف من هنا لحد ما يخرج من "الوارد".
    """
    return (
        Product.objects.filter(is_active=True)
        .annotate(
            is_new_arrival=Case(
                When(new_arrival_filter(), then=Value(True)),
                default=Value(False),
                output_field=BooleanField(),
            )
        )
        .select_related('category', 'inventory', 'image')
        # 'image' زودت من المرحلة 8 (STUDIO_PLAN.md) — كارت المنتج
        # (product_card.html) بيوصل لـ product.image.thumbnail/.image،
        # وده شبكة كاملة (PRODUCTS_PER_PAGE=24 منتج) فمن غيرها N+1 واضح.
        # 'units__discounts' (مش 'units' بس) — لأن كارت المنتج بيحسب السعر
        # بعد الخصم لكل صنف لو site_config.show_discounted_prices مفعّل،
        # وده بيوصل لـ unit.discounts.all() لكل وحدة. من غير الـ prefetch
        # ده، كل منتج في الصفحة (24) كان بيعمل استعلام إضافي منفصل (N+1).
        .prefetch_related('units__discounts')
    )


def _apply_filters(products, request):
    """
    بحث + فلترة (قسم/شركة مصنعة) مشتركة بين المتجر العادي وصفحة الوارد،
    عشان صفحة الوارد تدعم نفس البحث والفلاتر بالظبط (كانت ناقصة قبل كده).

    selected_manufacturer: بقى slug (زي selected_category بالظبط) بدل
    النص الخام القديم — من أغسطس 2026، Product.manufacturer بقى
    ForeignKey على Company (راجع Company docstring في products/models.py)،
    فالفلتر بيترشّح على manufacturer__slug مش على نص حر.
    """
    selected_category = request.GET.get('category', '')
    selected_manufacturer = request.GET.get('manufacturer', '')
    search_q = request.GET.get('q', '').strip()

    if selected_category:
        products = products.filter(category__slug=selected_category)
    if selected_manufacturer:
        products = products.filter(manufacturer__slug=selected_manufacturer)
    if search_q:
        normalized_q = normalize_name(search_q)
        products = products.filter(
            Q(name_ar__icontains=search_q)
            | Q(name_en__icontains=search_q)
            | Q(name_key__icontains=normalized_q)
        )

    return products, selected_category, selected_manufacturer, search_q


def _categories_list():
    """
    الأقسام النشطة اللي عندها منتج نشط واحد على الأقل بس (نفس منطق
    _manufacturers_list() بالظبط) — قبل كده كان store_home/new_arrivals
    بيستخدموا Category.objects.filter(is_active=True) من غير استبعاد
    الأقسام الفاضية، فكان جزء من الأقسام الظاهرة في الفلتر مالوش أي
    منتج فعلي. مرتبة تنازليًا بعدد المنتجات النشطة (الأكثر بضاعة فوق)
    بدل الترتيب الأبجدي، عشان أهم الأقسام تبقى أول حاجة يشوفها العميل.

    مكاشة (CATALOG_FILTERS_CACHE_TTL ثانية، راجع تعليق الثابت فوق) —
    بترجّع list مُقيَّمة (list(...)) مش QuerySet كسول، عشان تتخزن في
    الكاش كقيمة عادية وترجع منها زي ما هي من غير ما تعمل أي استعلام
    إضافي وقت القراءة من الكاش.
    """
    cached = cache.get('store:categories_list')
    if cached is not None:
        return cached
    categories = list(
        Category.objects.filter(is_active=True, products__is_active=True)
        .annotate(active_product_count=Count(
            'products', filter=Q(products__is_active=True), distinct=True
        ))
        .distinct()
        .order_by('-active_product_count', 'name')
    )
    cache.set('store:categories_list', categories, CATALOG_FILTERS_CACHE_TTL)
    return categories


def _manufacturers_list():
    # كانت .values_list('manufacturer', flat=True).distinct() على نص خام
    # — بيرجّع أي اختلاف كتابة بسيط كشركة منفصلة (عدد غير منطقي في
    # الفلتر). دلوقتي Company موديل مستقل، فمفيش داعي لـ distinct() لتفادي
    # تكرار الأسماء، بس لسه محتاجينها هنا عشان annotate() مع filter بيعمل
    # JOIN بيتكرر لكل منتج نشط. مرتبة تنازليًا بعدد المنتجات النشطة (نفس
    # منطق الأقسام بالظبط) بدل الأبجدي.
    #
    # مكاشة (نفس سبب/TTL _categories_list() فوق بالظبط).
    cached = cache.get('store:manufacturers_list')
    if cached is not None:
        return cached
    manufacturers = list(
        Company.objects.filter(is_active=True, products__is_active=True)
        .annotate(active_product_count=Count(
            'products', filter=Q(products__is_active=True), distinct=True
        ))
        .distinct()
        .order_by('-active_product_count', 'name')
    )
    cache.set('store:manufacturers_list', manufacturers, CATALOG_FILTERS_CACHE_TTL)
    return manufacturers


def _category_options(categories):
    """
    نفس فكرة _manufacturer_options() بس للأقسام — شكل {value, label, count}
    عشان يتحقن كـ JSON جوه store_filters.html (البحث الداخلي في الدروب
    داون شغال بـ Alpine.js على القايمة دي، مش على كائنات Category
    الخام). count = عدد المنتجات النشطة في القسم، بيتعرض جنب الاسم في
    الفلتر ("المستلزمات الجراحية (128)") عشان العميل يقرر يفتح القسم ده
    من غيره من غير ما يدخله. القالب نفسه لسه بياخد `categories` (الـ
    queryset) زي ما هو لأي استخدام تاني يحتاجه مستقبلًا.
    """
    return [
        {'value': c.slug, 'label': c.name, 'count': c.active_product_count}
        for c in categories
    ]


def _manufacturer_options():
    """
    نفس _manufacturers_list() لكن بشكل موحّد {value, label, count} بدل
    كائنات Company الخام — القالب (store_filters.html) محتاج يتعامل مع
    الأقسام والشركات المصنّعة بنفس الشكل من غير ما "يعرف" إن الشركة
    المصنّعة جوّه Company.slug/.name تحديدًا (راجع STORE_FILTERS
    redesign). value = company slug، label = company name، زي
    category.slug/.name بالظبط.
    """
    return [
        {'value': c.slug, 'label': c.name, 'count': c.active_product_count}
        for c in _manufacturers_list()
    ]


def _selected_label(options, selected_value):
    """
    بيدوّر على label الخيار المختار (قسم أو شركة مصنّعة) عشان الفلتر
    المدمج (store_filters.html) يعرضه كـ "chip" من غير ما يحتاج يلف على
    القايمة تاني جوه التمبليت. options ممكن تكون queryset (Category،
    فيها .slug/.name) أو list من dicts (manufacturer_options، فيها
    value/label) — الاتنين مدعومين هنا.
    """
    if not selected_value:
        return ''
    for opt in options:
        if isinstance(opt, dict):
            if opt['value'] == selected_value:
                return opt['label']
        elif opt.slug == selected_value:
            return opt.name
    return ''


FILTER_GROUP_SEARCH_MIN_OPTIONS = 8
# خانة البحث الداخلي جوه مجموعة الفلتر (بحث في الأقسام/الشركات) زودة لو
# عدد الخيارات قليل — بتتخفي تلقائيًا لو العدد أقل من العتبة دي (راجع
# ملاحظات مراجعة الفلتر).


def store_home(request):
    categories = _categories_list()
    products, selected_category, selected_manufacturer, search_q = _apply_filters(
        _base_products_queryset(), request
    )
    manufacturer_options = _manufacturer_options()
    category_options = _category_options(categories)

    paginator = Paginator(products, PRODUCTS_PER_PAGE)
    # لو فلتر (فئة/بحث) اتغيّر ورجع صفحة مش موجودة (مثلاً كنت في صفحة 5
    # وبقى الناتج صفحتين بس)، get_page بترجع آخر صفحة صالحة بدل ما تطلع خطأ.
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'products': page_obj,
        'page_obj': page_obj,
        'total_products': paginator.count,
        'categories': categories,
        'category_options': category_options,
        'manufacturer_options': manufacturer_options,
        'category_search_enabled': len(category_options) >= FILTER_GROUP_SEARCH_MIN_OPTIONS,
        'manufacturer_search_enabled': len(manufacturer_options) >= FILTER_GROUP_SEARCH_MIN_OPTIONS,
        'selected_category': selected_category,
        'selected_manufacturer': selected_manufacturer,
        'selected_category_label': _selected_label(categories, selected_category),
        'selected_manufacturer_label': _selected_label(manufacturer_options, selected_manufacturer),
        'search_q': search_q,
        'grid_url': 'store:home',
        'cart_quantities': _cart_quantities(request),
    }

    if request.headers.get('HX-Request'):
        return HttpResponse(render_to_string('store/partials/product_grid.html', context, request=request))

    return render(request, 'store/home.html', context)


def store_search(request):
    return store_home(request)


@login_required
def new_arrivals(request):
    """
    صفحة "الوارد الجديد" — كل منتج جديد أو اتزوّد رصيده خلال آخر
    NEW_ARRIVALS_WINDOW_DAYS يوم (نفس شرط badge الوارد في المتجر العادي
    بالظبط). خاصة بالعملاء المسجّلين فقط (login_required)، وبتدعم نفس
    بحث/فلاتر المتجر العادي (قسم، شركة مصنعة، بحث بالاسم) — الصنف هنا
    فاضل موجود في المتجر العادي كمان، الصفحة دي مجرد تجميعة مفلترة.
    """
    categories = _categories_list()
    base_qs = _base_products_queryset().filter(new_arrival_filter())
    products, selected_category, selected_manufacturer, search_q = _apply_filters(base_qs, request)
    manufacturer_options = _manufacturer_options()
    category_options = _category_options(categories)

    paginator = Paginator(products.order_by('-new_arrival_at'), PRODUCTS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'products': page_obj,
        'page_obj': page_obj,
        'total_products': paginator.count,
        'categories': categories,
        'category_options': category_options,
        'manufacturer_options': manufacturer_options,
        'category_search_enabled': len(category_options) >= FILTER_GROUP_SEARCH_MIN_OPTIONS,
        'manufacturer_search_enabled': len(manufacturer_options) >= FILTER_GROUP_SEARCH_MIN_OPTIONS,
        'selected_category': selected_category,
        'selected_manufacturer': selected_manufacturer,
        'selected_category_label': _selected_label(categories, selected_category),
        'selected_manufacturer_label': _selected_label(manufacturer_options, selected_manufacturer),
        'search_q': search_q,
        'window_days': NEW_ARRIVALS_WINDOW_DAYS,
        'grid_url': 'store:new_arrivals',
        'cart_quantities': _cart_quantities(request),
    }

    if request.headers.get('HX-Request'):
        return HttpResponse(render_to_string('store/partials/product_grid.html', context, request=request))

    return render(request, 'store/new_arrivals.html', context)


def product_detail(request, pk):
    product = get_object_or_404(
        Product.objects.filter(is_active=True)
        .select_related('category', 'image', 'manufacturer')
        .prefetch_related(
            'units__discounts',
            'similar_products__units__discounts', 'similar_products__category', 'similar_products__inventory', 'similar_products__image',
            'complementary_products__units__discounts', 'complementary_products__category', 'complementary_products__inventory', 'complementary_products__image',
        ),
        pk=pk,
    )
    # صفحة منتج واحد بس (مش شبكة متجر)، فمفيش قلق أداء من استعلام إضافي هنا.
    # units_for_client بيحدد الوحدة (أو الوحدات) اللي تظهر لنوع الحساب ده:
    # قطاعي = أصغر وحدة، جملة = أكبر وحدة.
    # ملحوظة: المخزون بقى على مستوى المنتج (product.inventory) مش الوحدة —
    # ما بنعملش وصول مباشر ليه هنا في كود بايثون، لأن منتج جديد لسه ماتفتحش
    # له مخزون هيعمل RelatedObjectDoesNotExist. القالب بيوصل لـ product.inventory
    # بأمان (Django بيتعامل مع الغياب ده silently جوه التمبليت).
    client = request.user if request.user.is_authenticated else None
    units = product.units_for_client(client)
    # نفلتر على is_active بايثونيًا (مش .filter() جديد) عشان نستفيد من
    # الـ prefetch_related الجاهز فوق بدل ما نضرب استعلام إضافي لكل قسم.
    similar_products = [p for p in product.similar_products.all() if p.is_active][:6]
    complementary_products = [p for p in product.complementary_products.all() if p.is_active][:6]
    return render(request, 'store/product_detail.html', {
        'product': product,
        'units': units,
        'cart_quantities': _cart_quantities(request),
        'similar_products': similar_products,
        'complementary_products': complementary_products,
    })
