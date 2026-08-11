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
    فاتورة — مستند Snapshot ثابت يتولد تلقائيًا عند Order.confirm() كمسودة
    (is_draft=True) برقمها الثابت النهائي، وتتحول لنهائية (is_draft=False)
    عند Order.mark_delivered() من غير ما رقمها أو أي حقل تاني يتغيّر.
    immutable تمامًا بعد الإصدار (عدا انتقال المسودة دي بالذات): أي تصحيح
    لاحق = مستند مرتجع منفصل (مرحلة 5).
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
                order_item=item,
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
    # رابط اختياري لصنف الطلب الأصلي (OrderItem) — مضاف مع نظام المرتجعات
    # (POST_DELIVERY) عشان لحظة عمل مرتجع نقدر نلاقي المنتج/الوحدة الفعلية
    # (product_unit) اللي نرجّع بيها الكمية للمخزون، من غير ما نعتمد على
    # product_name/unit_name النصية (Snapshot) اللي مالهاش FK حقيقي للمنتج.
    # null دايمًا للفواتير القديمة اللي اتصدرت قبل الحقل ده (مفيش مرتجع
    # ممكن يترجع مخزون تلقائي لأصنافها، بس التسوية المحاسبية اليدوية تفضل
    # ممكنة برضه لو احتاج الأدمن). on_delete=SET_NULL عشان حذف OrderItem
    # (لو حصل نظريًا) ميكسرش الفاتورة الثابتة نفسها.
    order_item = models.ForeignKey(
        'orders.OrderItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='invoice_items',
    )
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

    @property
    def returned_quantity(self):
        """
        إجمالي الكمية اللي اترجعت لهذا الصنف عبر كل إشعارات المرتجع
        (POST_DELIVERY) المرتبطة بيه لحد الآن — بيسمح بتعدد إشعارات
        مرتجع على نفس الفاتورة/الصنف بمرور الوقت طالما المجموع مايتعداش quantity.
        """
        return self.reversal_items.aggregate(total=models.Sum('quantity'))['total'] or 0

    @property
    def remaining_quantity(self):
        """الكمية المتبقية القابلة للإرجاع لهذا الصنف (الأصلية - اللي اترجعت فعلًا)."""
        return self.quantity - self.returned_quantity

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError('صنف الفاتورة immutable، مينفعش يتعدّل.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('صنف الفاتورة immutable، مينفعش يتحذف.')


class InvoiceReversal(models.Model):
    """
    إشعار إلغاء بسيط مرتبط بفاتورة (مرحلة 5) — آلية واحدة لتوثيق أي إلغاء
    محاسبي على فاتورة، بدل نظام مرتجعات كامل منفصل. الفاتورة نفسها **ماتتغيّرش
    ولا تتحذف أبدًا** بسبب الإلغاء (تفضل immutable زي ما هي دايمًا) — الإشعار
    ده مستند منفصل بجانبها بيوثّق إن قيمتها اتعكست محاسبيًا ووقتها وسببها.

    قرار صريح (راجع الخطة الأصلية، الجزء الخامس): مفيش موديلين أو مسارا كود
    منفصلين لتمييز "إلغاء قبل التسليم" عن "مرتجع بعد التسليم" — الفرق بينهم
    مجرد تسمية/توضيح لمكان توقف العملية (`stage`)، مش بنية مختلفة.

    نظام المرتجعات (POST_DELIVERY — راجع `create_post_delivery_return` تحت
    و`InvoiceReversalItem`): بيغطي مرتجع جزئي بالصنف (صنف معيّن أو الطلب
    كله، بكمية جزئية أو كلها)، بينشئه ستاف عنده صلاحية 'staff.create_returns'
    بس (الأدمن دايمًا عنده تلقائيًا، وهو الوحيد اللي يقدر يمنحها لموظف —
    راجع staff/permissions.py). كل إشعار مرتجع (سواء PRE أو POST) بيظهر
    للعميل/الستاف باسم "مرتجع" مش "تسوية" (راجع AccountTransaction.display_kind_label)،
    وبرقم إشعار مميز (`return_number`) بدل رقم الفاتورة العادي.
    """
    class Stage(models.TextChoices):
        PRE_DELIVERY = 'PRE_DELIVERY', 'قبل التسليم'
        POST_DELIVERY = 'POST_DELIVERY', 'بعد التسليم'

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='reversals')
    stage = models.CharField(max_length=20, choices=Stage.choices)
    # رقم إشعار المرتجع — مشتق من رقم الفاتورة + تسلسل داخلي لنفس الفاتورة
    # (RTN-2026-000006-01، RTN-2026-000006-02، ...)، بيتولد تلقائيًا في
    # save() تحت (مش عند الإنشاء يدويًا) عشان أي مكان ينشئ InvoiceReversal
    # (المسار القديم PRE_DELIVERY أو الجديد POST_DELIVERY) ياخد رقم فريد
    # تلقائيًا من غير ما يعرف تفاصيل التوليد. مقفول ضد التزامن بقفل صف
    # الفاتورة نفسها (select_for_update) وقت التوليد، مش قفل صفوف
    # الإشعارات الحالية (اللي ممكن تكون صفر أول مرة فمفيش حاجة تتقفل).
    return_number = models.CharField(max_length=30, blank=True, db_index=True, editable=False)
    note = models.TextField(blank=True)
    # قيمة الإلغاء بالجنيه — بتتسجّل موجبة دايمًا (بتمثّل إجمالي الفاتورة
    # اللي اتلغت، أو إجمالي الأصناف المرتجعة في حالة POST_DELIVERY)، عكس
    # AccountTransaction.amount اللي بيتسجّل سالب هناك عمدًا (هو حركة
    # "بتقلّل المديونية"، أما ده مجرد توثيق لقيمة الإلغاء نفسها بغض النظر
    # عن اتجاهها المحاسبي).
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'إشعار مرتجع'
        verbose_name_plural = 'إشعارات المرتجع'
        ordering = ['-created_at']

    def __str__(self):
        return self.return_number or f'إلغاء {self.invoice.invoice_number} — {self.get_stage_display()}'

    def save(self, *args, **kwargs):
        if not self.pk and not self.return_number:
            with transaction.atomic():
                locked_invoice = Invoice.objects.select_for_update().get(pk=self.invoice_id)
                existing_count = InvoiceReversal.objects.filter(invoice=locked_invoice).count()
                # invoice_number بصيغة INV-{سنة}-{رقم}؛ رقم الإشعار بياخد نفس
                # الجزء (سنة-رقم) بدل INV، زائد تسلسل بترتيب إنشائه لنفس الفاتورة.
                suffix = locked_invoice.invoice_number.split('-', 1)[1]
                self.return_number = f'RTN-{suffix}-{existing_count + 1:02d}'
                super().save(*args, **kwargs)
            return
        super().save(*args, **kwargs)

    @classmethod
    @transaction.atomic
    def create_post_delivery_return(cls, invoice, items, actor=None, note=''):
        """
        ينشئ إشعار مرتجع بعد التسليم/التأكيد (POST_DELIVERY) لصنف أو أكتر
        من فاتورة، بكمية جزئية أو الكمية كلها — الاستخدام الفعلي لنظام
        المرتجعات (راجع شرح الموديل فوق).

        items: قائمة (InvoiceItem instance, quantity) — بترجع صنف واحد أو
        أكتر بكميات مختلفة؛ أي عنصر بكمية صفر أو أقل بيتجاهل.

        الخطوات (كل واحدة جوه نفس الـ transaction):
        - قفل صف الفاتورة (select_for_update) ضد تزامن مرتجعين على نفس
          الفاتورة في نفس اللحظة (يحمي حساب remaining_quantity تحت من race).
        - فحص إن كل كمية مطلوبة ≤ remaining_quantity الفعلي وقت القفل.
        - إنشاء InvoiceReversal(stage=POST_DELIVERY) + InvoiceReversalItem
          لكل صنف (توثيق الكمية والسعر وقت الإرجاع).
        - إرجاع الكمية للمخزون الصالح للبيع (StockMovement IN) لكل صنف
          عنده order_item.product_unit فعلي (الأصناف الخدمية أو صنف
          فاتورة قديم من غير order_item بيتجاهل الإرجاع المخزني، بس
          المحاسبة بتترجع عادي).
        - تسجيل AccountTransaction (ADJUSTMENT سالبة) بقيمة إجمالي
          الأصناف المرتجعة، مربوطة بـ invoice_reversal عشان تتعرض باسم
          "مرتجع" في كشوف الحساب بدل "تسوية".
        - تسجيل OrderLog على الطلب المرتبط بالفاتورة.

        بترجع الـ InvoiceReversal الجديد. بترمي ValueError لأي مشكلة
        (مفيش كمية محددة، كمية أكبر من المتاح للإرجاع، صنف مش تابع لنفس
        الفاتورة).
        """
        from decimal import Decimal
        from inventory.models import Inventory, StockMovement
        from accounting.models import AccountTransaction
        from orders.models import OrderLog

        locked_invoice = cls._lock_invoice(invoice)

        prepared = []
        total_amount = Decimal('0')
        for invoice_item, quantity in items:
            quantity = int(quantity or 0)
            if quantity <= 0:
                continue
            if invoice_item.invoice_id != locked_invoice.pk:
                raise ValueError('صنف لا ينتمي لهذه الفاتورة.')
            remaining = invoice_item.remaining_quantity
            if quantity > remaining:
                raise ValueError(
                    f'الكمية المطلوب إرجاعها لـ "{invoice_item.product_name}" ({quantity}) '
                    f'أكبر من الكمية المتاحة للإرجاع ({remaining}).'
                )
            prepared.append((invoice_item, quantity))
            total_amount += invoice_item.unit_price * quantity

        if not prepared:
            raise ValueError('لازم تحدد كمية صنف واحد على الأقل لإنشاء إشعار مرتجع.')

        reversal = cls.objects.create(
            invoice=locked_invoice,
            stage=cls.Stage.POST_DELIVERY,
            amount=total_amount,
            note=note,
            created_by=actor,
        )

        order = locked_invoice.order
        locked_inventories = {}
        for invoice_item, quantity in prepared:
            InvoiceReversalItem.objects.create(
                reversal=reversal, invoice_item=invoice_item,
                quantity=quantity, unit_price=invoice_item.unit_price,
            )
            order_item = invoice_item.order_item
            if order_item is None or order_item.is_service_fee or order_item.product_unit_id is None:
                # صنف خدمي، أو صنف فاتورة قديم من غير ربط order_item —
                # مفيش مخزون فعلي يترجع (المحاسبة اترجعت عادي فوق).
                continue
            product_id = order_item.product_unit.product_id
            inv = locked_inventories.get(product_id)
            if inv is None:
                inv = Inventory.objects.select_for_update().filter(product_id=product_id).first()
                locked_inventories[product_id] = inv
            if inv is not None:
                StockMovement.objects.create(
                    inventory=inv, unit=order_item.product_unit,
                    movement_type=StockMovement.MovementType.IN, quantity=quantity,
                    note=f'مرتجع {reversal.return_number} على الفاتورة {locked_invoice.invoice_number}',
                    created_by=actor,
                )

        AccountTransaction.objects.create(
            client=order.client,
            kind=AccountTransaction.Kind.ADJUSTMENT,
            amount=-total_amount,
            invoice=locked_invoice,
            invoice_reversal=reversal,
            note=f'إشعار مرتجع {reversal.return_number} على الفاتورة {locked_invoice.invoice_number}.',
            created_by=actor,
        )

        OrderLog.objects.create(
            order=order,
            event=OrderLog.Event.NOTE,
            note=f'تم إنشاء إشعار مرتجع {reversal.return_number} بقيمة {total_amount} ج.م.',
            created_by=actor,
        )

        # تنبيه العميل بحركة المرتجع على طلبه (نقطة 4 من طلب المرتجعات) —
        # نفس أسلوب تنبيه "تم تسجيل دفعة على حسابك" في staff/views/accounting.py.
        # exclude_actor احتياطًا فقط (المرتجعات بتتنشأ من الستاف دايمًا حاليًا،
        # مش من العميل نفسه، فمفيش سيناريو فعلي بيستبعد حد هنا).
        from notifications.services import notify
        from notifications.models import Notification
        notify(
            order.client,
            kind=Notification.Kind.RETURN_CREATED,
            title='تم تسجيل مرتجع على طلبك',
            message=f'تم تسجيل إشعار مرتجع {reversal.return_number} بقيمة {total_amount} ج.م على طلب #{order.pk}.',
            url_name='orders:order_list',
            exclude_actor=actor,
        )

        return reversal

    @staticmethod
    def _lock_invoice(invoice):
        return Invoice.objects.select_for_update().get(pk=invoice.pk)

    @classmethod
    def rows_for_client(cls, client):
        """
        بترجع كل إشعارات المرتجع الخاصة بفواتير طلبات عميل معيّن — مستخدمة
        عشان نحطها كصف مستقل جنب طلباته في "طلباتي" (راجع orders/order_list.html
        و accounts/dashboard.html)، بنفس فلسفة merge_orders_with_returns تحت.
        """
        return cls.objects.filter(
            invoice__order__client=client,
        ).select_related('invoice__order')


def merge_orders_with_returns(orders_qs, client):
    """
    بتدمج قائمة طلبات عميل مع إشعارات المرتجع بتاعته في قائمة واحدة مرتبة
    بالتاريخ (الأحدث فوق) — كل عنصر dict فيه 'kind' ('order' أو 'return')
    و'obj' و'created_at'. مستخدمة في أكتر من مكان (orders:order_list
    وتبويب "طلباتي" في accounts:dashboard) عشان العميل يشوف حركة المرتجع
    في نفس قائمة طلباته، من غير تفاصيل الأصناف (زي أي طلب في القائمة) —
    تفاصيل الإشعار نفسه موجودة في صفحة طباعته لو حبّ يفتحها.
    """
    rows = [{'kind': 'order', 'obj': order, 'created_at': order.created_at} for order in orders_qs]
    rows += [
        {'kind': 'return', 'obj': reversal, 'created_at': reversal.created_at}
        for reversal in InvoiceReversal.rows_for_client(client)
    ]
    rows.sort(key=lambda row: row['created_at'], reverse=True)
    return rows


class InvoiceReversalItem(models.Model):
    """
    صنف داخل إشعار مرتجع (POST_DELIVERY) — توثيق ثابت للكمية والسعر
    اللي اترجعوا من صنف فاتورة معيّن (InvoiceItem). زي InvoiceItem بالظبط،
    immutable بعد الإنشاء. مجموع quantity لكل صنوف invoice_item الواحد عبر
    كل الإشعارات هو returned_quantity (راجع InvoiceItem.returned_quantity).
    """
    reversal = models.ForeignKey(InvoiceReversal, on_delete=models.CASCADE, related_name='items')
    invoice_item = models.ForeignKey(InvoiceItem, on_delete=models.PROTECT, related_name='reversal_items')
    quantity = models.PositiveIntegerField()
    # سعر الوحدة وقت الإرجاع — Snapshot من invoice_item.unit_price وقت
    # إنشاء الإشعار (نفس سعر البيع الأصلي دايمًا، مش سعر جديد).
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = 'صنف في إشعار المرتجع'
        verbose_name_plural = 'أصناف إشعارات المرتجع'

    def __str__(self):
        return f'{self.invoice_item.product_name} x{self.quantity}'

    @property
    def subtotal(self):
        return self.unit_price * self.quantity

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError('صنف إشعار المرتجع immutable، مينفعش يتعدّل.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('صنف إشعار المرتجع immutable، مينفعش يتحذف.')
