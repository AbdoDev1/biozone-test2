from django.db import models
from django.utils import timezone
from products.models import Product, ProductUnit


class InventoryQuerySet(models.QuerySet):
    def low_stock(self):
        """
        فلتر على مستوى قاعدة البيانات بنفس شرط الخاصية is_low بالظبط
        (الرصيد <= الحد الأدنى). الفرق إن is_low بيتحسب على مستوى الـ
        instance (بعد ما السجل يترجع من القاعدة)، أما الميثود دي بتسمح
        نفلتر/نعدّ في القاعدة نفسها (queryset) بدل ما نجيب كل المخزون
        ونفلتره في بايثون. اتعمل QuerySet method (مش Manager عادي) عشان
        تفضل قابلة للتسلسل (chainable) فوق فلاتر تانية، زي البحث في صفحة
        المخزون (staff/views/inventory.py?q=...&low=1). مستخدمة في اللوحة
        الرئيسية (staff/views/dashboard.py)، صفحة المخزون، ولوحة مؤشرات
        التقارير (staff/views/reports.py) — كانت العبارة دي متكررة يدويًا
        في التلات أماكن قبل كده.
        """
        return self.filter(quantity__lte=models.F('min_quantity'))


class Inventory(models.Model):
    """
    رصيد واحد لكل منتج (مش لكل وحدة) — المرجع الوحيد للحقيقة، ومحفوظ دايمًا
    بالقطعة (أصغر وحدة). أي وحدة تانية للمنتج (كرتونة مثلاً) هي مجرد "طريقة
    عرض/بيع" بمعامل تحويل (ProductUnit.qty_in_small)، مش رصيد منفصل.
    """
    objects = InventoryQuerySet.as_manager()

    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        related_name='inventory',
    )
    quantity = models.PositiveIntegerField(default=0, verbose_name='الرصيد (بالقطعة)')
    min_quantity = models.PositiveIntegerField(default=0, verbose_name='الحد الأدنى (بالقطعة)')
    is_available = models.BooleanField(
        default=True,
        verbose_name='متوفر في المتجر',
        help_text='يتم تحديثه تلقائياً عند انخفاض الكمية، أو يمكن التحكم فيه يدوياً'
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'مخزون'
        verbose_name_plural = 'المخزون'

    def __str__(self):
        return f"{self.product} — {self.quantity}"

    @property
    def available(self):
        return self.quantity

    @property
    def is_low(self):
        return self.available <= self.min_quantity

    @property
    def suggested_reorder_qty(self):
        """
        الكمية المقترح توريدها بالقطعة — الفرق بين الحد الأدنى والمتاح
        فعليًا، وصفر لو الصنف مش تحت الحد الأدنى أصلًا. بتُستخدم في صفحة
        "مقترحات التوريد" (مرحلة 7 من ROADMAP.md) — حساب بسيط على حقول
        محمّلة بالفعل (مفيش استعلام إضافي)، مش اقتراح ذكي أو تنبؤ بالطلب.
        """
        if not self.is_low:
            return 0
        return max(self.min_quantity - self.available, 0)

    @property
    def suggested_reorder_display(self):
        """الكمية المقترح توريدها معروضة بالوحدة الكبرى — لعرضها في صفحة مقترحات التوريد."""
        return self._format_in_large_unit(self.suggested_reorder_qty)

    def _format_in_large_unit(self, pieces):
        """
        بيحوّل رصيد بالقطعة لعرض بالوحدة الكبرى (كرتونة مثلًا) + الباقي
        بالوحدة الصغرى لو مش قسمة مضبوطة. لو المنتج مالوش وحدة كبرى أصلًا
        (أو وحدة واحدة بس)، بيرجع الرصيد بالقطعة زي ما هو.
        """
        large = self.product.largest_unit
        small = self.product.smallest_unit
        small_name = small.name if small else 'قطعة'
        if not large or large.qty_in_small <= 1:
            return f'{pieces} {small_name}'
        large_count, remainder = divmod(pieces, large.qty_in_small)
        text = f'{large_count} {large.name}'
        if remainder:
            text += f' + {remainder} {small_name}'
        return text

    @property
    def quantity_display(self):
        """الرصيد الكلي معروضًا بالوحدة الكبرى — للاستخدام في تقرير المخزون."""
        return self._format_in_large_unit(self.quantity)

    @property
    def available_display(self):
        """المتاح معروضًا بالوحدة الكبرى — للاستخدام في تقرير المخزون."""
        return self._format_in_large_unit(self.available)

    def sync_availability(self):
        if self.available <= 0:
            self.is_available = False
        elif self.is_low and self.min_quantity > 0:
            self.is_available = False
        else:
            self.is_available = True
        self.save(update_fields=['is_available'])


class StockMovement(models.Model):
    class MovementType(models.TextChoices):
        IN = 'IN', 'وارد'
        OUT = 'OUT', 'صادر'

    inventory = models.ForeignKey(
        Inventory,
        on_delete=models.CASCADE,
        related_name='movements',
    )
    unit = models.ForeignKey(
        ProductUnit,
        on_delete=models.PROTECT,
        verbose_name='الوحدة',
        help_text='الوحدة التي سُجّلت بها الحركة (كرتونة/قطعة) — الكمية أدناه بوحدة هذه الوحدة.',
    )
    # max_length فضل 13 حرف زي الأول (مش اتقصّر لـ 3) رغم إن أطول قيمة
    # متاحة دلوقتي في MovementType هي 'OUT' (3 أحرف بس) — عشان صفوف
    # StockMovement القديمة (قبل إلغاء نظام الحجز) ممكن يكون فيها قيم
    # زي 'RESERVE'، 'RELEASE'، 'OUT_RESERVED' لسه محفوظة في قاعدة
    # البيانات كسجل تاريخي (سجل حركات المخزون immutable، مفيش حذف).
    # تقصير العمود كان هيفشل فعليًا على أي قاعدة بيانات فيها بيانات
    # حقيقية (StringDataRightTruncation)، حتى لو شغال على قاعدة فاضية
    # في التطوير المحلي. القيم القديمة دي هتفضل موجودة كنص خام (مش هتظهر
    # لها ترجمة عربية جميلة في get_movement_type_display بعد النهاردة،
    # لأنها بقت مش موجودة في choices)، لكن من غير أي فقدان بيانات أو
    # فشل ميجريشن.
    movement_type = models.CharField(max_length=13, choices=MovementType.choices)
    quantity = models.PositiveIntegerField(
        verbose_name='الكمية (بوحدة الحركة)',
        help_text='بوحدة "الوحدة" المختارة أعلاه، وليس بالضرورة بالقطعة — يقوم النظام بتحويلها تلقائيًا.',
    )
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = 'حركة مخزون'
        verbose_name_plural = 'حركات المخزون'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.inventory.product.display_name}"

    @property
    def stock_qty(self):
        """الكمية الفعلية بالقطعة (بعد التحويل) — دي اللي بتتطبّق على رصيد المخزون."""
        return self.quantity * self.unit.qty_in_small

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.quantity is None or self.quantity <= 0:
            raise ValidationError('الكمية يجب أن تكون أكبر من صفر.')
        if self.unit_id and self.inventory_id and self.unit.product_id != self.inventory.product_id:
            raise ValidationError('الوحدة المختارة لا تنتمي لنفس منتج هذا المخزون.')
        if self.inventory_id and self.unit_id:
            stock_qty = self.quantity * self.unit.qty_in_small
            if self.movement_type == self.MovementType.OUT and stock_qty > self.inventory.available:
                raise ValidationError(
                    'الكمية المطلوبة أكبر من الكمية المتاحة في المخزون.'
                )

    def save(self, *args, **kwargs):
        # full_clean() تلقائي هنا (بنفس أسلوب AccountTransaction.save()) —
        # قبل كده كانت الحماية (منع OUT أكبر من المتاح، إلخ) شغالة بس لو
        # المكان اللي بينادي .create()/.save() فحص يدويًا الأول، فكانت الحماية
        # الحقيقية معتمدة على "كل مطوّر يتذكر يفحص" مش على validation مركزي.
        # دلوقتي أي إنشاء لحركة مخزون (حتى لو مسار جديد نسي الفحص اليدوي)
        # هيتوقف تلقائيًا لو مخالف لقواعد clean() تحت.
        self.full_clean()
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if not is_new:
            return
        from django.db.models import F
        stock_qty = self.stock_qty
        inv_qs = Inventory.objects.filter(pk=self.inventory_id)
        if self.movement_type == self.MovementType.IN:
            inv_qs.update(quantity=F('quantity') + stock_qty)
            # أي تزويد رصيد (وارد) بيخلي المنتج يظهر في "الوارد الجديد" للعملاء
            # لفترة معيّنة (راجع products.new_arrivals) — النقطة المركزية دي
            # بتغطي كل مسارات إضافة الرصيد (يدوي، استيراد إكسل، ...) تلقائيًا.
            Product.objects.filter(pk=self.inventory.product_id).update(new_arrival_at=timezone.now())
        elif self.movement_type == self.MovementType.OUT:
            inv_qs.update(quantity=F('quantity') - stock_qty)
        self.inventory.refresh_from_db()
        self.inventory.sync_availability()


class PriceChange(models.Model):
    """
    تسجيل مستقل لتغيير سعر وحدة (قطعة/كرتونة) — عنصر مستقل في سجل حركات
    الصنف (زي StockMovement بالظبط)، لكن من غير أي أثر على رصيد المخزون
    نفسه (السعر مش كمية). كان قبل كده بيتسجّل كملخص عام في ActivityLog
    بس، ومستثنى من العرض تمامًا (راجع ActivityLogQuerySet.exclude_pricing_details)
    بطلب صاحب النظام وقتها — القرار اتغيّر: تغيير السعر لازم يبقى ظاهر
    كعنصر مستقل في سجل حركات المخزون (بالتفصيل: من سعر/لسعر)، وقابل
    للفلترة زي أي نوع حركة تاني. راجع inventory.services.record_price_change
    للطريقة الموحّدة لإنشاء سجل من هنا (بدل ما كل مكان يستخدم .create()
    مباشرة ويفوّت مثلاً حالة "السعر لم يتغيّر فعليًا").
    """
    inventory = models.ForeignKey(
        Inventory,
        on_delete=models.CASCADE,
        related_name='price_changes',
    )
    unit = models.ForeignKey(
        ProductUnit,
        on_delete=models.PROTECT,
        verbose_name='الوحدة',
    )
    old_price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='السعر القديم')
    new_price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='السعر الجديد')
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = 'تغيير سعر'
        verbose_name_plural = 'تغييرات الأسعار'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.inventory.product.display_name}: {self.old_price} → {self.new_price}"
