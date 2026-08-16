# يدوي (أغسطس 2026) — الخطوة الأخيرة في تحويل manufacturer لـ FK: بعد ما
# migration 0024 ملت manufacturer_fk من كل قيم النص القديم، دلوقتي نشيل
# العمود النصي القديم (manufacturer) خالص، ونرجّع تسمية manufacturer_fk
# لـ manufacturer عشان أي كود بيستخدم product.manufacturer يفضل شغال من
# غير أي تغيير تاني (دلوقتي بيرجّع Company instance بدل str).

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0024_migrate_manufacturer_text_to_company'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='product',
            name='manufacturer',
        ),
        migrations.RenameField(
            model_name='product',
            old_name='manufacturer_fk',
            new_name='manufacturer',
        ),
    ]
