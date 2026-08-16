# يدوي (أغسطس 2026) — راجع نقاش "الشركة المصنّعة كموديل + شيل صورة القسم".
#
# 1) موديل Company جديد (زي Category بالظبط) — الخطوة الأولى في تحويل
#    Product.manufacturer من نص حر لـ ForeignKey. الحقل الفعلي في Product
#    لسه CharField هنا (هنضيف عمود مؤقت manufacturer_fk في نفس الـ
#    migration، ونعمل data migration في migration منفصلة بعد كده تملأه من
#    النص القديم، وبعدين نشيل القديم ونرجّع تسمية الجديد لـ manufacturer —
#    3 خطوات منفصلة عمدًا عشان القيمة الفعلية للنص القديم متتفقدش أثناء
#    التحويل).
#
# 2) Category.image اتشال بالكامل — القسم مالوش أي عرض بصري في المتجر
#    (كان بيظهر كنص بس في فلتر الأقسام)، والحقل نفسه مفيش صور متربطة بيه
#    فعليًا (زي ملحوظة migration 0022 الأصلية). حذف مباشر بلا data
#    migration لنفس السبب.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0022_category_image_product_image_studio_fk'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='category',
            name='image',
        ),
        migrations.CreateModel(
            name='Company',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, unique=True, verbose_name='اسم الشركة')),
                ('name_key', models.CharField(blank=True, db_index=True, editable=False, max_length=255, unique=True)),
                ('slug', models.SlugField(allow_unicode=True, unique=True)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'شركة مصنّعة',
                'verbose_name_plural': 'الشركات المصنّعة',
                'ordering': ['name'],
            },
        ),
        migrations.AddField(
            model_name='product',
            name='manufacturer_fk',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='products',
                to='products.company',
                verbose_name='الشركة المصنعة',
            ),
        ),
    ]
