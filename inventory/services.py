"""
دوال مساعدة لتسجيل تغييرات السعر — الواجهة الموحّدة اللي أي مكان في
النظام (صفحة تعديل الصنف، التعديل السريع من الجدول، استيراد Excel)
المفروض يستخدمها بدل ما ينشئ PriceChange يدويًا في كل مكان، بنفس فكرة
activity.services.log_activity.
"""
from .models import Inventory, PriceChange


def record_price_change(unit, old_price, new_price, user=None, note='', inventory=None):
    """
    بتسجّل تغيير سعر وحدة واحدة كعنصر مستقل في سجل حركات المخزون —
    من غير أي تأثير على رصيد المخزون نفسه. بترجع None من غير ما تعمل
    حاجة لو السعر القديم والجديد نفس القيمة فعليًا (زي نفس فكرة
    diff_summary: مفيش داعي نسجّل "تغيير" وهمي).

    inventory (اختياري): يتمرر لو already محمّل (مثلاً من كاش دفعة
    استيراد Excel) بدل ما الدالة تعمل استعلام Inventory.get_or_create
    بنفسها لكل نداء.
    """
    if old_price == new_price:
        return None
    if inventory is None:
        inventory, _ = Inventory.objects.get_or_create(
            product_id=unit.product_id, defaults={'quantity': 0, 'min_quantity': 0},
        )
    return PriceChange.objects.create(
        inventory=inventory,
        unit=unit,
        old_price=old_price,
        new_price=new_price,
        note=note,
        created_by=user,
    )
