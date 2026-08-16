# يدوي (أغسطس 2026) — data migration: بتاخد كل قيمة manufacturer (النص
# القديم) الموجودة فعليًا على المنتجات، بتجمّعها حسب normalize_name
# (نفس دالة تطبيع اسم الصنف — بتوحّد الفراغات الزيادة وحالة الحروف
# واختلافات الهمزة الشكلية)، وبتعمل صف Company واحد لكل مجموعة (الاسم
# المعروض = أول نص شُوهد في المجموعة، بعد trim بس)، وبعدين تربط كل منتج
# بالـ Company المناسبة عن طريق العمود المؤقت manufacturer_fk (اتضاف في
# migration اللي قبل دي).
#
# reverse بسيط: بيصفّر manufacturer_fk بس (مفيش رجوع لنص حر تاني — الحقل
# القديم نفسه لسه موجود في هذه المرحلة ومحتفظ بقيمته الأصلية، فمفيش فقدان
# بيانات حتى لو الـ migration اترجعت).

from django.db import migrations

from products.matching import normalize_name


def migrate_forward(apps, schema_editor):
    Product = apps.get_model('products', 'Product')
    Company = apps.get_model('products', 'Company')
    from django.utils.text import slugify

    products = Product.objects.exclude(manufacturer='').only('id', 'manufacturer')

    # key مُطبَّع -> Company instance
    companies_by_key = {}
    for product in products.iterator():
        raw_name = (product.manufacturer or '').strip()
        if not raw_name:
            continue
        key = normalize_name(raw_name)
        if not key:
            continue

        company = companies_by_key.get(key)
        if company is None:
            base_slug = slugify(raw_name, allow_unicode=True) or 'company'
            slug = base_slug
            i = 1
            while Company.objects.filter(slug=slug).exists():
                i += 1
                slug = f'{base_slug}-{i}'
            company = Company.objects.create(
                name=raw_name, name_key=key, slug=slug, is_active=True,
            )
            companies_by_key[key] = company

        product.manufacturer_fk_id = company.id
        product.save(update_fields=['manufacturer_fk'])


def migrate_backward(apps, schema_editor):
    Product = apps.get_model('products', 'Product')
    Product.objects.exclude(manufacturer_fk__isnull=True).update(manufacturer_fk=None)
    apps.get_model('products', 'Company').objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0023_company_remove_category_image'),
    ]

    operations = [
        migrations.RunPython(migrate_forward, migrate_backward),
    ]
