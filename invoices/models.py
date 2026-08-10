from django.db import models, transaction
from django.core.exceptions import ValidationError
from accounts.models import User


class InvoiceSequence(models.Model):
    """
    عداد تسلسلي لكل سنة ميلادية — يضمن عدم تكرار رقم الفاتورة
    حتى لو 2 موظفين سلّموا طلبين في نفس اللحظة بالظبط.
    صف واحد لكل سنة، بيتقفل بـ select_for_update وقت توليد الرقم.
    """
    year = models.PositiveIntegerField(unique=True)
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = 'عداد الفواتير'
        verbose_name_plural = 'عدادات الفواتير'

    def __str__(self):
        return f'عداد {self.year} — آخر رقم: {self.last_number}'

    @classmethod
    @transaction.atomic
    def next_number(cls, year):
        """يرجع الرقم التسلسلي التالي لسنة معيّنة، مقفول ضد التزامن."""
        seq, _ = cls.objects.select_for_update().get_or_create(
            year=year, defaults={'last_number': 0},
        )
        seq.last_number += 1
        seq.save(update_fields=['last_number'])
        return seq.last_number


class Invoice(models.Model):
    """
    فاتورة — مستند Snapshot ثابت يتولد تلقائيًا عند Order.mark_delivered().
    immutable تمامًا بعد الإصدار: أي تصحيح لاحق = مستند مرتجع منفصل (مرحلة 11).
    """
    invoice_number = models.CharField(max_length=20, unique=True, editable=False)
    order = models.OneToOneField(
        'orders.Order',
        on_delete=models.PROTECT,
        related_name='invoice',
    )
    # مؤقتًا (مرحلة 1) الافتراضي False عمدًا — issue_for_order لسه بتتنادى من
    # mark_delivered() زي ما هي، فأي فاتورة جديدة دلوقتي بتتولد لحظة التسليم
    # الفعلي، يعني هي "نهائية" من ساعة ما اتعملت. لما issue_for_order تتنقل
    # لـ confirm() (مرحلة 2) هتحدد is_draft=True صراحة وقت الإنشاء، من غير ما
    # نغيّر default الحقل هنا — كده الفواتير القديمة والجديدة في المرحلة دي
    # (لسه من مسار mark_delivered) تفضل is_draft=False زي سلوكها الفعلي.
    is_draft = models.BooleanField(default=False, verbose_name='مسودة')

    # --- Snapshot بيانات العميل وقت الإصدار (مش قراءة حية من Order/ClientProfile) ---
    client_name = models.CharField(max_length=255)
    client_business_name = models.CharField(max_length=255, blank=True)
    client_address = models.TextField(blank=True)
    client_phone = models.CharField(max_length=20, blank=True)

    # --- المجاميع وقت الإصدار ---
    total = models.DecimalField(max_digits=12, decimal_places=2)

    # --- Audit ---
    issued_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='issued_invoices',
    )
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'فاتورة'
        verbose_name_plural = 'الفواتير'
        ordering = ['-issued_at']

    def __str__(self):
        return self.invoice_number

    def save(self, *args, **kwargs):
        if self.pk is not None:
            # الفاتورة immutable بعد الإصدار، ما عدا استثناء واحد مقصود:
            # تحويلها من مسودة (is_draft=True) لنهائية (is_draft=False) —
            # وبس، بدون أي تغيير على أي حقل تاني، وبدون الرجوع من False
            # لـ True تاني. أي تعديل خارج الحالة دي بالظبط يترفض زي الأول.
            try:
                previous = Invoice.objects.get(pk=self.pk)
            except Invoice.DoesNotExist:
                previous = None

            is_draft_to_final = (
                previous is not None
                and previous.is_draft is True
                and self.is_draft is False
            )
            other_fields_unchanged = previous is not None and all(
                getattr(self, field.attname) == getattr(previous, field.attname)
                for field in self._meta.concrete_fields
                if field.attname not in ('id', 'is_draft')
            )

            if not (is_draft_to_final and other_fields_unchanged):
                raise ValidationError(
                    'الفاتورة مستند ثابت بعد الإصدار، مينفعش تتعدّل (عدا تحويلها من مسودة لنهائية).'
                )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('الفاتورة مستند ثابت، مينفعش تتحذف.')

    @classmethod
    @transaction.atomic
    def issue_for_order(cls, order, actor=None):
        """
        يولّد فاتورة من طلب — Snapshot ثابت لبيانات العميل والأصناف والأسعار
        وقت الإصدار. بتتنادى تلقائيًا من Order.confirm() (مرحلة 2)، يعني وقت
        التأكيد مش التسليم — بتتولد كـ "مسودة" (is_draft=True) فورًا، ورقم
        الفاتورة بيثبت من هنا للأبد. mark_delivered() بعد كده مش بتصدر
        فاتورة جديدة، بس بتحوّل نفس الفاتورة من مسودة لنهائية.
        """
        if hasattr(order, 'invoice'):
            return order.invoice

        year = order.updated_at.year
        number = InvoiceSequence.next_number(year)
        invoice_number = f'INV-{year}-{number:06d}'

        profile = getattr(order.client, 'client_profile', None)

        invoice = cls(
            invoice_number=invoice_number,
            order=order,
            client_name=order.client.get_full_name() or order.client.username,
            client_business_name=getattr(profile, 'business_name', ''),
            client_address=getattr(profile, 'address', ''),
            client_phone=getattr(profile, 'phone', ''),
            total=order.total,
            issued_by=actor,
            # بتتولد كمسودة دايمًا هنا — issue_for_order بقت بتتنادى من
            # confirm() (مرحلة 2)، يعني الفاتورة لسه مش نهائية لحد التسليم.
            # ده صراحةً هنا مش اعتمادًا على default الحقل (اللي فضل False
            # عمدًا — راجع تعليق الحقل نفسه في تعريف الموديل فوق).
            is_draft=True,
        )
        invoice.save()

        for item in order.items.all():
            # الصنف الخدمي (زي "مصاريف توصيل") مالوش product_unit خالص —
            # بناخد الاسم والوحدة من item.display_name/service_name بدل ما
            # نعتمد على المنتج (راجع OrderItem.is_service_fee).
            InvoiceItem.objects.create(
                invoice=invoice,
                product_name=item.display_name,
                unit_name='—' if item.is_service_fee else item.product_unit.name,
                quantity=item.quantity,
                public_price=item.public_price,
                discount_percent=item.discount_percent,
                unit_price=item.unit_price,
            )

        # نسجّل حركة "فاتورة" في دفتر حساب العميل تلقائيًا — دي اللي بتزوّد مديونيته.
        from accounting.models import AccountTransaction
        AccountTransaction.objects.create(
            client=order.client,
            kind=AccountTransaction.Kind.INVOICE,
            amount=invoice.total,
            invoice=invoice,
            created_by=actor,
        )

        return invoice


class InvoiceItem(models.Model):
    """صنف داخل الفاتورة — Snapshot ثابت لاسم المنتج/الوحدة/الكمية/السعر وقت الإصدار."""
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items')
    product_name = models.CharField(max_length=255)
    unit_name = models.CharField(max_length=100)
    quantity = models.PositiveIntegerField()
    # سعر الجمهور ونسبة الخصم وقت إصدار الفاتورة — هما اللي بيظهروا للعميل،
    # مش سعر القطعة الفعلي بعد الخصم (unit_price) اللي يفضل داخلي/للموظفين بس.
    public_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = 'صنف في الفاتورة'
        verbose_name_plural = 'أصناف الفاتورة'

    def __str__(self):
        return f'{self.product_name} x{self.quantity}'

    @property
    def subtotal(self):
        return self.unit_price * self.quantity

    @property
    def public_subtotal(self):
        """إجمالي سعر الجمهور قبل الخصم (الكمية × سعر الجمهور)."""
        return self.public_price * self.quantity

    @property
    def discount_amount(self):
        """قيمة الخصم بالجنيه (إجمالي سعر الجمهور - الإجمالي بعد الخصم)."""
        return self.public_subtotal - self.subtotal

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError('صنف الفاتورة immutable، مينفعش يتعدّل.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('صنف الفاتورة immutable، مينفعش يتحذف.')
