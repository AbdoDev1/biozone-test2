from django.contrib import admin

# Register your models here.
from django.contrib import admin
from .models import Category, Product, ProductUnit
from .forms import BaseProductUnitFormSet


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'created_at')
    list_filter = ('is_active',)
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ('name',)
    # raw_id_fields بدل <select> عادي — من المرحلة 8 (STUDIO_PLAN.md)، image
    # بقى ForeignKey على studio.StudioImage، ومعرض الاستوديو ممكن يبقى فيه
    # آلاف الصور؛ <select> عادي هيحمّلهم كلهم كـ <option> واحدة واحدة.
    raw_id_fields = ('image',)


class ProductUnitInline(admin.TabularInline):
    model = ProductUnit
    formset = BaseProductUnitFormSet  # نفس فحص الوحدة الكبرى/الصغرى المُصحّح المستخدم في فورم الستاف
    extra = 3
    fields = ('size', 'name', 'qty_in_small', 'unit_price')


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'code', 'barcode', 'category', 'manufacturer', 'is_active', 'created_at')
    list_filter = ('category', 'is_active')
    search_fields = ('name_ar', 'name_en', 'manufacturer', 'code', 'barcode', 'barcode_2', 'barcode_3')
    inlines = [ProductUnitInline]
    # نفس ملاحظة CategoryAdmin.raw_id_fields فوق بالظبط.
    raw_id_fields = ('image',)
