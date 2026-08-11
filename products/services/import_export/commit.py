"""
مرحلة الحفظ الفعلي بعد موافقة الموظف على قرارات المراجعة (parsing.py) —
الخطوة الأخيرة في استيراد إكسل. الـ transaction بيتحكم فيها المستدعي
(الـ view) عشان تفضل الدوال دي قابلة لإعادة الاستخدام برّة سياق request
لو احتجنا.
"""
from decimal import Decimal

from accounts.models import AccountType
from activity.models import ActivityLog
from activity.services import log_activity
from inventory.models import Inventory, StockMovement
from inventory.services import record_price_change
from products.models import Product, ProductUnit, UnitDiscount

from .common import get_or_create_category

__all__ = [
    'commit_product',
    'commit_import_batch',
]


def commit_product(row_data, target_pk, user, account_types_by_pk, category_cache=None,
                    product_cache=None, inventory_cache=None):
    """
    بيطبّق صنف واحد (وحدة أو وحدتين + خصوماته) فعليًا على قاعدة البيانات،
    بعد ما يبقى معروف بالظبط (من مرحلة المراجعة) هل ده تحديث لمنتج
    target_pk معين، ولا إضافة صنف جديد (target_pk=None). الكمية بتتسجل
    دايمًا كحركة "وارد" (IN) بتتضاف فوق الرصيد الحالي — مش استبدال له —
    سواء كانت "رصيد افتتاحي" لصنف جديد أو "تحديث كميات" لصنف موجود.
    بيرجّع (created, restocked).

    category_cache (اختياري): مُمرَّر من commit_import_batch ومُشترك بين
    كل صفوف نفس الدفعة، عشان لو قسم جديد اتكرر في أكتر من صف يتعمل مرة
    واحدة بس بدل ما كل صف يحاول ينشئه لوحده. راجع get_or_create_category.

    product_cache / inventory_cache (اختياريان): dict {pk: instance} مُجهّز
    مقدّمًا من commit_import_batch بجلب كل منتجات/مخزونات دفعة التحديث دفعة
    واحدة (بدل استعلام Product.get + Inventory.get_or_create لكل صف على
    حدة) — ده أكبر سبب لبطء الاستيراد مع ملفات كبيرة (مئات/آلاف الصفوف).
    لو معدّاش الاثنين (الاستخدام المباشر القديم، زي الاختبارات)، السلوك
    زي ما هو بالظبط: استعلام منفصل لكل صف.
    """
    category = None
    if row_data['category_slug']:
        category = get_or_create_category(row_data['category_slug'], cache=category_cache)

    if target_pk:
        product = (product_cache or {}).get(target_pk) or Product.objects.get(pk=target_pk)
        product.name_ar = row_data['name_ar']
        if category:
            product.category = category
        # الباركود مش موجود في ملف الإكسل خالص (راجع parsing.py) — فمش
        # بيتلمس هنا أبدًا، سواء عند إضافة صنف جديد أو تحديث صنف موجود.
        # لو الصنف الجديد جاله كود يدوي (row_data['code'])، Product.save()
        # بيحترمه ومش بيولّد كود تلقائي بدلًا منه.
        if row_data.get('code'):
            product.code = row_data['code']
        product.save()
        created = False
    else:
        if not category:
            raise ValueError(f'صنف جديد "{row_data["name_ar"]}" لازم يكون له قسم (category_slug)')
        product = Product.objects.create(
            name_ar=row_data['name_ar'], category=category, is_active=True,
            code=row_data.get('code') or '',
        )
        created = True

    # تسجيل النشاط (مرحلة 2) — كان ناقص تمامًا لمسار الاستيراد الجماعي لأنه
    # بيحفظ مباشرة عن طريق commit_product مش عن طريق product_add/product_edit
    # views، فمكنش بيمر على نفس أماكن التسجيل. ملخص عام (مش diff تفصيلي لكل
    # حقل) كافٍ هنا لأن السطر التالي في الاستيراد نفسه (اسم الملف) هو مصدر
    # الحقيقة التفصيلي، وده بس مؤشر "الصنف ده جه من استيراد Excel".
    if created:
        log_activity(product, ActivityLog.Event.CREATED, user=user, note='تم الإنشاء عبر استيراد ملف Excel')
    else:
        log_activity(product, ActivityLog.Event.UPDATED, user=user, changes_summary='تحديث بيانات/أسعار من ملف Excel')

    inventory = (inventory_cache or {}).get(product.pk)
    if inventory is None:
        inventory, _ = Inventory.objects.get_or_create(
            product=product, defaults={'quantity': 0, 'min_quantity': 0},
        )
        if inventory_cache is not None:
            inventory_cache[product.pk] = inventory

    restocked = False
    for size, unit_data in (('S', row_data['small']), ('L', row_data['large'])):
        if not unit_data:
            continue
        # لازم ناخد السعر القديم *قبل* update_or_create عشان نقدر نسجّله
        # كعنصر مستقل في سجل حركات المخزون لو اتغيّر فعليًا (راجع
        # inventory.services.record_price_change) — بعد update_or_create
        # القيمة القديمة بتبقى ضاعت خالص من الـ instance.
        existing_unit = ProductUnit.objects.filter(product=product, size=size).first()
        old_price = existing_unit.unit_price if existing_unit else None

        unit, _ = ProductUnit.objects.update_or_create(
            product=product, size=size,
            defaults={
                'name': unit_data['unit_name'],
                'unit_price': unit_data['unit_price'],
                'qty_in_small': unit_data['qty_in_small'],
            },
        )
        if old_price is not None:
            record_price_change(
                unit, old_price, unit.unit_price, user=user,
                note='تحديث من ملف Excel', inventory=inventory,
            )
        if unit_data['quantity'] > 0:
            StockMovement.objects.create(
                inventory=inventory, unit=unit, movement_type='IN',
                quantity=unit_data['quantity'], note='إضافة/تحديث من ملف Excel', created_by=user,
            )
            restocked = True

    # الخصم بيتحدد دايمًا على الوحدة "الأساسية" للتسعير: الصغرى لو موجودة
    # للصنف (حتى لو مكانتش في الملف ده تحديدًا، لأنها ممكن تكون اتضافت
    # قبل كده)، وإلا الوحدة الوحيدة المتاحة — راجع نفس القاعدة في
    # ProductUnit.get_pricing_breakdown_for_account_type.
    discount_unit = ProductUnit.objects.filter(product=product, size='S').first() \
        or ProductUnit.objects.filter(product=product, size='L').first()

    if discount_unit is not None:
        for at_pk_raw, pct_raw in row_data['discounts'].items():
            account_type = account_types_by_pk.get(int(at_pk_raw))
            if not account_type:
                continue
            if pct_raw is None:
                UnitDiscount.objects.filter(unit=discount_unit, account_type=account_type).delete()
            else:
                UnitDiscount.objects.update_or_create(
                    unit=discount_unit, account_type=account_type,
                    defaults={'discount_percent': Decimal(pct_raw)},
                )

    return created, restocked


def commit_import_batch(rows, decisions, user):
    """
    بتاخد قرارات الموظف على صفوف "المراجعة" (decisions: dict بمفتاح
    row_num وقيمة إما 'new' أو pk المنتج المستهدف) وتنفّذ الحفظ الفعلي
    لكل صفوف الدفعة. بترجّع (created_count, updated_count, restocked_count).
    """
    account_types_by_pk = {at.pk: at for at in AccountType.objects.all()}
    # كاش مشترك بين كل صفوف الدفعة: لو أكتر من صف بيحتاج نفس القسم الجديد
    # (زي صنف بوحدتين، أو أكتر من صنف واقع في نفس القسم غير الموجود)،
    # القسم بينشأ مرة واحدة بس ويتعاد استخدامه، بدل ما كل صف يحاول ينشئه
    # بنفسه ويضرب IntegrityError من تعارض الـslug مع الصف اللي قبله.
    category_cache = {}

    # تحديد كل الصفوف اللي هتتحدّث (target_pk معروف مسبقًا) عشان نجيب
    # منتجاتها ومخزونها بدفعة واحدة (Product.objects.filter(pk__in=..))
    # بدل ما كل صف يعمل استعلام Product.get + Inventory.get_or_create
    # لوحده — ده كان السبب الأساسي وراء بطء استيراد الملفات الكبيرة (كل
    # صف بيعمل ٦-٨ استعلامات منفصلة، فملف بـ٥٠٠ صف = آلاف الاستعلامات
    # المتتالية جوه نفس الطلب).
    target_pks = []
    for row_data in rows:
        if row_data['action'] == 'review':
            decision = decisions.get(row_data['row_num'], 'new')
            pk = int(decision) if decision != 'new' else None
        else:
            pk = row_data.get('match_pk')
        if pk:
            target_pks.append(pk)

    product_cache = Product.objects.select_related('category').in_bulk(target_pks)
    inventory_cache = {
        inv.product_id: inv
        for inv in Inventory.objects.filter(product_id__in=target_pks)
    }

    created_count = updated_count = restocked_count = 0
    for row_data in rows:
        if row_data['action'] == 'review':
            decision = decisions.get(row_data['row_num'], 'new')
            target_pk = int(decision) if decision != 'new' else None
        else:
            target_pk = row_data.get('match_pk')
        created, restocked = commit_product(
            row_data, target_pk, user, account_types_by_pk,
            category_cache=category_cache,
            product_cache=product_cache, inventory_cache=inventory_cache,
        )
        if created:
            created_count += 1
        else:
            updated_count += 1
            if restocked:
                restocked_count += 1
    return created_count, updated_count, restocked_count
