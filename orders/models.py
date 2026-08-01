from django.db import models
from django.db import transaction
from accounts.models import User
from products.models import ProductUnit


class SiteConfig(models.Model):
    """
    إعدادات عامة للموقع — سطر واحد بس (Singleton).
    يتم التعديل عليه من لوحة الأدمن.
    """
    min_order_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name='الحد الأدنى لقيمة الطلب',
        help_text='أقل قيمة إجمالية مسموح بها لإرسال الطلب (بالجنيه). اترك القيمة صفرًا في حال عدم الرغبة في تحديد حد أدنى.',
    )
    show_discounted_prices = models.BooleanField(
        default=False,
        verbose_name='إظهار سعر المخزن في المتجر',
        help_text=(
            'لو مفعّل، هيظهر للعميل في صفحات المتجر سعر المخزن (بعد خصم نوع حسابه) جنب سعر '
            'الجمهور. اتركه غير مفعّل لحين التأكد من صحة أسعار الخصم الجديدة — سعر الجمهور '
            'بيظهر دايمًا بغض النظر عن هذا الإعداد.'
        ),
    )

    class Meta:
        verbose_name = 'إعدادات الموقع'
        verbose_name_plural = 'إعدادات الموقع'

    def __str__(self):
        return 'إعدادات الموقع'

    def save(self, *args, **kwargs):
        # نضمن وجود سطر واحد بس دايمًا (pk=1)
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # منمنع حذف السطر الوحيد
        pass

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


def get_effective_min_order_amount(client_profile):
    """
    الحد الأدنى الفعلي لقيمة الطلب لعميل معيّن (مرحلة 6 من ROADMAP.md):
    لو ClientProfile.min_order_amount محدّد (مش None) يُستخدم هو، وإلا
    القيمة العامة SiteConfig.min_order_amount كـ fallback — بكده تعديل
    الحد الأدنى لعميل معيّن مايأثرش على باقي العملاء، وعميل من غير قيمة
    مخصّصة يفضل شغال بالقيمة العامة القديمة زي ما كان بالظبط.
    """
    if client_profile is not None and client_profile.min_order_amount is not None:
        return client_profile.min_order_amount
    return SiteConfig.get_solo().min_order_amount


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING         = 'PENDING',         'في الانتظار'
        NEEDS_APPROVAL  = 'NEEDS_APPROVAL',   'بانتظار موافقتك على التعديل'
        CONFIRMED       = 'CONFIRMED',        'مؤكد'
        REJECTED        = 'REJECTED',         'مرفوض'
        DELIVERED       = 'DELIVERED',        'تم التسليم'

    client      = models.ForeignKey(User, on_delete=models.PROTECT, related_name='orders')
    status      = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    notes       = models.TextField(blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)
    # بيتحدد True أول ما أي موظف/أدمن يفتح صفحة تفاصيل الطلب (staff:order_detail).
    # بيُستخدم في الصفحة الرئيسية للوحة التحكم لعرض عدد الطلبات "لسه ماتفتحتش"،
    # عشان الموظف يعرف بسرعة إيه الجديد من غير ما يفوّته وسط باقي الطلبات.
    viewed_by_staff = models.BooleanField(default=False, db_index=True)
    # نسخة (snapshot) من الحد الأدنى الفعلي لإجمالي الطلب وقت إنشائه
    # (get_effective_min_order_amount وقت checkout) — null لو مفيش حد أدنى
    # مفعّل أصلًا وقتها. الطلب مبقاش بيترفض تلقائيًا لو إجماليه أقل من الحد
    # الأدنى (زي ما كان قبل كده)؛ بدل كده بيتبعت عادي للمخزن مع تنبيه واضح
    # (راجع is_below_min_order)، والمخزن يقدر يكمّل الطلب زي ما هو أو يضيف
    # "مصاريف توصيل" (add_service_fee) لتغطية الفرق. القيمة بتفضل ثابتة حتى
    # لو الحد الأدنى للعميل اتغيّر بعد إنشاء الطلب، عشان التنبيه يفضل معبّر
    # عن الوضع وقت إرسال الطلب فعليًا.
    min_order_amount_snapshot = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        verbose_name='الحد الأدنى وقت إرسال الطلب',
    )

    class Meta:
        verbose_name = 'طلب'
        verbose_name_plural = 'الطلبات'
        ordering = ['-created_at']

    def __str__(self):
        return f'طلب #{self.pk} — {self.client.username}'

    @property
    def total(self):
        return sum(item.subtotal for item in self.items.all())

    @property
    def original_total(self):
        return sum(item.original_subtotal for item in self.items.all())

    @property
    def is_amended(self):
        return any(item.is_amended for item in self.items.all())

    @property
    def has_service_fee(self):
        """True لو الطلب عنده أي صنف خدمي (زي "مصاريف توصيل") مُضاف بالفعل."""
        return any(item.is_service_fee for item in self.items.all())

    @property
    def is_below_min_order(self):
        """
        True لو إجمالي الطلب الحالي لسه أقل من الحد الأدنى المسجّل وقت
        الإرسال، *ومفيش* أي صنف خدمي (مصاريف توصيل) اتضاف للطلب لسه.

        الغرض الوحيد من هذا التنبيه هو التأكد إن المخزن "اطّلع" على إن
        الطلب أقل من الحد الأدنى وقرر بشأنه — مش فرض إن القيمة المضافة
        تساوي أو تغطي الفرق بالكامل. المخزن ممكن يضيف مصاريف توصيل بقيمة
        أقل من الفرق عمدًا (أو حتى لا تغطيه خالص) ويكون ده قراره، فأول ما
        أي صنف خدمي يتضاف، التنبيه لازم يختفي فورًا بدل ما يفضل ملّح على
        المخزن يزوّد القيمة أكتر. لو المخزن شال الصنف الخدمي تاني
        (remove_service_fee) والطلب لسه أقل من الحد الأدنى، التنبيه
        هيرجع يظهر تلقائيًا لأن الغرض (التأكد من وجود قرار) لسه ماتحققش.
        """
        return (
            self.min_order_amount_snapshot is not None
            and self.total < self.min_order_amount_snapshot
            and not self.has_service_fee
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._old_status = self.status

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        status_changed = (not is_new) and (self.status != self._old_status)
        actor = getattr(self, '_actor', None)
        super().save(*args, **kwargs)

        if is_new:
            OrderLog.objects.create(
                order=self,
                event=OrderLog.Event.CREATED,
                note='تم إنشاء الطلب.',
                created_by=actor,
            )
            from orders.notifications import notify_new_order
            notify_new_order(self)
        elif status_changed:
            OrderLog.objects.create(
                order=self,
                event=OrderLog.Event.STATUS_CHANGED,
                note=f'تم تغيير حالة الطلب إلى "{self.get_status_display()}".',
                new_status=self.status,
                created_by=actor,
            )
            # _old_status لسه بيحمل الحالة القديمة هنا (بنحدّثها في آخر
            # سطر تحت) — orders/notifications.py::notify_status_change
            # بيعتمد عليها لتمييز "العميل لغى قبل المراجعة" عن "رفض تعديل".
            from orders.notifications import notify_status_change
            notify_status_change(self, actor)
        self._old_status = self.status

    # ---------- منطق سير العمل (المرحلة 8) ----------

    def confirm(self, actor=None):
        """المخزن بيأكد الطلب من غير أي تعديل في الكميات."""
        self._actor = actor
        self.status = self.Status.CONFIRMED
        self.save()

    @transaction.atomic
    def reject(self, actor=None, reason=''):
        """رفض الطلب (من المخزن أو من العميل) — الطلبات لا تحجز أي كمية من
        المخزون أصلًا، فمفيش أي حجز يتفك هنا."""
        if self.status == self.Status.DELIVERED:
            raise ValueError('الطلب ده اتسلّم بالفعل، مينفعش يترفض.')
        if self.status == self.Status.REJECTED:
            raise ValueError('الطلب ده مرفوض بالفعل.')

        self._actor = actor
        self.status = self.Status.REJECTED
        if reason:
            OrderLog.objects.create(
                order=self, event=OrderLog.Event.NOTE, note=reason, created_by=actor,
            )
        self.save()

    @transaction.atomic
    def mark_delivered(self, actor=None):
        """
        تسليم الطلب — الطلبات مش بتحجز أي كمية وقت الإرسال، فالخصم الفعلي من
        المخزون بيحصل هنا بس (لحظة التسليم): حركة "صادر (مباشر)" واحدة لكل
        صنف. لو الكمية بقت غير متوفرة فعليًا وقت التسليم (اتباعت لعميل تاني
        مثلاً في الفترة من إرسال الطلب لحد المراجعة)، الحركة هترفض تلقائيًا
        (StockMovement.clean()) وهيرجع ValidationError للموظف.

        حماية ضد double-submit/سباق: الفحص "الطلب لسه CONFIRMED" في الـ view
        بيحصل *قبل* الدخول هنا (شرط مستوى الـ view بس، مش شرط هنا في الموديل —
        الميثود دي أصلًا مصممة تتنادى من أي حالة، شوف اختبارات orders/tests.py).
        المشكلة الفعلية: لو طلبين POST جم مع بعض (دبل كليك، أو تابين لموظفين
        مختلفين) ممكن الاتنين يعدّوا فحص الـ view قبل ما أي واحد يغيّر الحالة
        فعليًا، فالاتنين ينادوا mark_delivered() ويخصموا من المخزون مرتين لنفس
        الطلب. عشان كده لازم نقفل صف الطلب نفسه (select_for_update) ونتأكد إنه
        مش DELIVERED بالفعل *بعد* أخذ القفل — الطلب التاني هيستنى القفل، ولما
        ياخده هيلاقي الحالة بقت DELIVERED فيتوقف بدل ما يسجّل حركة مخزون
        تانية (خصم مزدوج) على نفس الطلب. الفاتورة كانت محمية أصلًا (hasattr
        check في Invoice.issue_for_order)، لكن حركة المخزون ماكانتش.
        """
        from django.core.exceptions import ValidationError
        from inventory.models import Inventory, StockMovement

        locked_self = Order.objects.select_for_update().get(pk=self.pk)
        if locked_self.status == self.Status.DELIVERED:
            raise ValidationError('الطلب ده اتسلّم بالفعل، لا يمكن تكرار التسليم.')

        items = list(self.items.select_related('product_unit').all())
        product_ids = [item.product_unit.product_id for item in items if not item.is_service_fee]
        locked_inventories = {
            inv.product_id: inv
            for inv in Inventory.objects.select_for_update().filter(product_id__in=product_ids)
        }

        for item in items:
            if item.is_service_fee:
                # صنف خدمي (زي مصاريف التوصيل) — مالوش منتج ولا تأثير على
                # المخزون خالص، فبيتجاوز هنا تمامًا.
                continue
            inv = locked_inventories.get(item.product_unit.product_id)
            if inv:
                out_movement = StockMovement(
                    inventory=inv,
                    unit=item.product_unit,
                    movement_type=StockMovement.MovementType.OUT,
                    quantity=item.quantity,
                    note=f'تسليم طلب #{self.pk}',
                    created_by=actor,
                )
                # StockMovement.save() بقت بتنادي full_clean() تلقائيًا
                # (راجع inventory/models.py)، فمفيش داعي نناديها هنا يدويًا.
                out_movement.save()
        self._actor = actor
        self.status = self.Status.DELIVERED
        self.save()

        from invoices.models import Invoice
        Invoice.issue_for_order(self, actor=actor)

    @transaction.atomic
    def amend_item_quantity(self, item, new_quantity, actor=None):
        """
        المخزن بيعدّل كمية صنف في الطلب (لو الكمية المتاحة أقل من المطلوب، أو
        لأي سبب تاني)، وبيعيد حساب السعر حسب الكمية الجديدة. التعديل هنا
        بيغيّر بس بيانات الطلب — مفيش أي تأثير على المخزون (لا حجز ولا فك)،
        لأن الخصم الفعلي بيحصل بس وقت التسليم (mark_delivered).
        """
        if item.is_service_fee:
            raise ValueError('مينفعش تتعدّل كمية صنف خدمي زي "مصاريف التوصيل".')
        if self.status not in (self.Status.PENDING, self.Status.NEEDS_APPROVAL):
            # بعد التأكيد (CONFIRMED) وقبل التسليم، الطلب بيتحوّل لمرحلة
            # "فاتورة قبل نهائية" (راجع staff:order_confirmed_invoice_print)
            # ومينفعش تتعدّل كمياته تاني — أي تصحيح لازم يبقى برفض الطلب
            # وإنشاء واحد جديد، مش تعديل صامت على طلب اتأكد بالفعل.
            raise ValueError('لا يمكن تعديل كميات طلب تم تأكيده بالفعل.')

        from inventory.models import Inventory
        old_quantity = item.quantity
        diff = new_quantity - old_quantity
        unit = item.product_unit

        if diff > 0:
            # فحص إرشادي بس (تنبيه للموظف) — مش قفل فعلي على المخزون.
            inv = Inventory.objects.filter(product_id=unit.product_id).first()
            available = inv.available if inv else 0
            if diff * unit.qty_in_small > available:
                raise ValueError('الكمية المطلوبة أكبر من المتاح حاليًا في المخزون.')

        item.quantity = new_quantity
        if new_quantity > 0:
            # بنجيب سعر الجمهور ونسبة الخصم مع سعر الوحدة الفعلي مع بعض من
            # نفس المصدر (get_pricing_breakdown_for_client)، ونحدّث التلاتة
            # حقول مع بعض — لو حدّثنا unit_price بس (زي ما كان قبل كده)،
            # public_price/discount_percent كانوا بيفضلوا واقفين على قيمة
            # وقت إنشاء الطلب حتى لو الأدمن غيّر نسبة الخصم بعد كده، فيبقى
            # كشف السعر (سعر جمهور + نسبة خصم + سعر نهائي) متضارب مع بعضه
            # ومايطلعش صح في تفاصيل الطلب ولا الفاتورة.
            public_price, discount_percent, unit_price = item.product_unit.get_pricing_breakdown_for_client(self.client)
            item.public_price = public_price
            item.discount_percent = discount_percent
            item.unit_price = unit_price
        item.save()

        direction_word = 'بالزيادة' if new_quantity > old_quantity else 'بالنقص'
        OrderLog.objects.create(
            order=self,
            event=OrderLog.Event.NOTE,
            note=(
                f'تم تعديل كمية "{item.product_unit.product.display_name} — '
                f'{item.product_unit.name}" {direction_word} من {old_quantity} إلى {new_quantity}.'
            ),
            created_by=actor,
        )

    def send_for_client_approval(self, actor=None):
        self._actor = actor
        self.status = self.Status.NEEDS_APPROVAL
        self.save()

    @transaction.atomic
    def client_approve_amendment(self, actor=None):
        """العميل وافق على التعديل — يثبّت الكميات الجديدة كأصل ويأكد الطلب."""
        for item in self.items.all():
            item.original_quantity = item.quantity
            item.original_unit_price = item.unit_price
            item.save(update_fields=['original_quantity', 'original_unit_price'])
        self._actor = actor
        self.status = self.Status.CONFIRMED
        self.save()

    def client_reject_amendment(self, actor=None):
        """العميل رفض التعديل — الطلب بالكامل يترفض."""
        self.reject(actor=actor, reason='العميل رفض التعديل المقترح من المخزن.')

    DEFAULT_SERVICE_FEE_NAME = 'مصاريف توصيل'

    @transaction.atomic
    def add_service_fee(self, amount, actor=None, name=None):
        """
        إضافة صنف خدمي للطلب (افتراضيًا "مصاريف توصيل") — بدون منتج ولا أي
        تأثير على المخزون، وبدون خصم أو كمية (بتتثبت على قطعة واحدة). القيمة
        بتدخل يدويًا من المخزن، وربحيتها 100% لأنها مالهاش سعر تكلفة أصلًا.
        بيُستخدم عادة لتغطية الفرق لما إجمالي الطلب أقل من الحد الأدنى
        المسموح للعميل (Order.is_below_min_order) بدل رفض الطلب بالكامل، لكنه
        مش مقصور على الحالة دي — أي طلب لسه مش DELIVERED/REJECTED ممكن يتضاف
        له.
        """
        if self.status not in (self.Status.PENDING, self.Status.NEEDS_APPROVAL):
            # بعد التأكيد (CONFIRMED)، الطلب بقى في مرحلة "فاتورة قبل نهائية"
            # ومينفعش يتعدّل خالص (لا كميات ولا صنف خدمي) — لازم القرار بشأن
            # مصاريف التوصيل يتاخد قبل التأكيد، مش بعده.
            raise ValueError('لا يمكن إضافة مصاريف توصيل بعد تأكيد الطلب.')
        if amount is None or amount <= 0:
            raise ValueError('قيمة مصاريف التوصيل لازم تكون أكبر من صفر.')

        fee_name = name or self.DEFAULT_SERVICE_FEE_NAME
        item = OrderItem.objects.create(
            order=self,
            product_unit=None,
            is_service_fee=True,
            service_name=fee_name,
            quantity=1,
            public_price=amount,
            discount_percent=0,
            unit_price=amount,
        )
        OrderLog.objects.create(
            order=self,
            event=OrderLog.Event.NOTE,
            note=f'تمت إضافة "{fee_name}" بقيمة {amount} ج.م للطلب.',
            created_by=actor,
        )
        return item

    @transaction.atomic
    def remove_service_fee(self, item, actor=None):
        """حذف صنف خدمي (زي مصاريف التوصيل) اتضاف بالغلط أو محتاج يتشال."""
        if self.status not in (self.Status.PENDING, self.Status.NEEDS_APPROVAL):
            raise ValueError('لا يمكن تعديل مصاريف التوصيل بعد تأكيد الطلب.')
        if not item.is_service_fee or item.order_id != self.pk:
            raise ValueError('الصنف ده مش صنف خدمي تابع للطلب ده.')

        fee_name = item.service_name
        amount = item.unit_price
        item.delete()
        OrderLog.objects.create(
            order=self,
            event=OrderLog.Event.NOTE,
            note=f'تم حذف "{fee_name}" (كانت بقيمة {amount} ج.م) من الطلب.',
            created_by=actor,
        )


class OrderItem(models.Model):
    order        = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    # null بس لما is_service_fee=True (صنف خدمي زي "مصاريف توصيل" — مالوش
    # منتج فعلي، فمفيش داعي لسجل ProductUnit؛ راجع service_name تحت).
    product_unit = models.ForeignKey(ProductUnit, on_delete=models.PROTECT, null=True, blank=True)
    # صنف خدمي (بدون منتج/مخزون) — بيتضاف من المخزن بس (مثلاً "مصاريف
    # توصيل" لما إجمالي الطلب أقل من الحد الأدنى)، ربحيته 100% لأنه مالوش
    # سعر تكلفة، وبدون خصم ولا تعديل كمية. راجع Order.add_service_fee.
    is_service_fee = models.BooleanField(default=False, verbose_name='صنف خدمي (بدون مخزون)')
    service_name = models.CharField(max_length=150, blank=True, verbose_name='اسم الخدمة')
    quantity     = models.PositiveIntegerField()
    # سعر الجمهور ونسبة الخصم وقت الطلب — Snapshot لا يتغيّر حتى لو الأدمن
    # عدّل قائمة الخصومات بعد كده. unit_price = السعر الفعلي بعد الخصم
    # (سعر الجمهور × (1 - نسبة الخصم/100))، وهو المستخدم في كل الحسابات.
    public_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    unit_price   = models.DecimalField(max_digits=10, decimal_places=2)
    original_quantity   = models.PositiveIntegerField(null=True, blank=True)
    original_unit_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        verbose_name = 'صنف في الطلب'
        verbose_name_plural = 'أصناف الطلب'

    def __str__(self):
        if self.is_service_fee:
            return f'{self.service_name} x{self.quantity}'
        return f'{self.product_unit.name} x{self.quantity}'

    @property
    def display_name(self):
        """اسم الصنف المعروض — اسم المنتج للأصناف العادية، أو اسم الخدمة (زي "مصاريف توصيل") للأصناف الخدمية."""
        if self.is_service_fee:
            return self.service_name or 'خدمة'
        return self.product_unit.product.display_name

    @property
    def stock_qty(self):
        """
        الكمية الفعلية بالقطعة اللي اتحجزت/اتطرحت من رصيد المخزون — تحويل
        quantity (بوحدة الطلب: كرتونة للجملة أو قطعة للقطاعي) بمعامل qty_in_small.
        صفر دايمًا للأصناف الخدمية لأنها مالهاش تأثير على المخزون أصلًا.
        """
        if self.is_service_fee:
            return 0
        return self.quantity * self.product_unit.qty_in_small

    @property
    def unit_display_label(self):
        if self.is_service_fee:
            return '—'
        return self.product_unit.name

    def save(self, *args, **kwargs):
        # سطر واحد بس إما صنف منتج فعلي (له product_unit ومفيش service_name)
        # أو صنف خدمي (is_service_fee=True، مالوش product_unit) — مايتلخبطش.
        if self.is_service_fee:
            if self.product_unit_id is not None:
                raise ValueError('صنف خدمي مينفعش يتربط بمنتج فعلي (product_unit).')
        elif self.product_unit_id is None:
            raise ValueError('صنف الطلب لازم يكون له منتج فعلي (product_unit) إلا لو صنف خدمي.')
        # أول مرة بس بنحفظ نسخة من الكمية/السعر الأصلي قبل أي تعديل من المخزن
        if self.original_quantity is None:
            self.original_quantity = self.quantity
        if self.original_unit_price is None:
            self.original_unit_price = self.unit_price
        super().save(*args, **kwargs)

    @property
    def subtotal(self):
        return self.unit_price * self.quantity

    @property
    def original_subtotal(self):
        return (self.original_unit_price or self.unit_price) * (self.original_quantity or self.quantity)

    @property
    def is_amended(self):
        return (
            self.original_quantity is not None and self.quantity != self.original_quantity
        ) or (
            self.original_unit_price is not None and self.unit_price != self.original_unit_price
        )

    @property
    def quantity_diff(self):
        """الفرق بين الكمية الحالية والأصلية (موجب = زيادة، سالب = نقص، صفر = مفيش تغيير في الكمية)."""
        if self.original_quantity is None:
            return 0
        return self.quantity - self.original_quantity

    @property
    def amendment_direction(self):
        """
        'increase' لو المخزن زوّد الكمية، 'decrease' لو قلّلها، None لو مفيش
        تعديل على الكمية أصلًا (مفيد للتمبليت عشان يوضّح للعميل والمخزن
        بوضوح اتجاه التعديل، مش بس إنه "اتغيّر").
        """
        diff = self.quantity_diff
        if diff > 0:
            return 'increase'
        if diff < 0:
            return 'decrease'
        return None


class Cart(models.Model):
    """
    سلة مشتريات — بقت متخزنة في الداتابيز (مش السيشن) عشان تفضل موجودة
    حتى لو العميل قفل المتصفح أو غيّر جهازه، وعشان نسمح للعميل يفتح أكتر
    من سلة في نفس الوقت (مثلاً "طلبية عادية" و"طلبية عاجلة") من غير ما
    إضافة صنف في واحدة تأثر على التانية، ويرجع يكمل أي سلة وهو مطمن إنها
    محفوظة له.

    في أي لحظة، سلة واحدة بس من سلال العميل تبقى "نشطة" (is_active) —
    هي اللي بتتعرض له افتراضيًا في صفحة السلة، وهي اللي بيتم التعامل
    معاها عند "أضف للسلة". العميل يقدر يبدّل السلة النشطة من نفس الصفحة.

    مهم: مفيش أي سلة بتتنشئ تلقائيًا لمجرد ما العميل يفتح صفحة السلة —
    السلة الأولى بتتنشئ بس لحظة إضافة أول صنف فعليًا (get_or_create_active)،
    عشان العميل يعرف بوضوح إنه مفيش عنده أي طلبية مفتوحة لو مسحهم كلهم.
    """
    client     = models.ForeignKey(User, on_delete=models.CASCADE, related_name='order_carts')
    name       = models.CharField(max_length=100, blank=True, verbose_name='اسم الطلبية')
    is_active  = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'سلة'
        verbose_name_plural = 'السلال'
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.display_name} — {self.client.username}'

    @property
    def display_name(self):
        return self.name or f'سلة بدون اسم #{self.pk}'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # سلة واحدة نشطة بس لكل عميل — أي سلة تتفعّل تلغي تفعيل الباقي.
        if self.is_active:
            Cart.objects.filter(client=self.client, is_active=True).exclude(pk=self.pk).update(is_active=False)

    @classmethod
    def get_active(cls, client):
        """يرجع السلة النشطة للعميل، أو None لو مفيش عنده أي سلة مفتوحة أصلًا (بدون إنشاء أي حاجة)."""
        cart = cls.objects.filter(client=client, is_active=True).first()
        if cart is not None:
            return cart
        return cls.objects.filter(client=client).order_by('-updated_at').first()

    @classmethod
    def get_or_create_active(cls, client):
        """
        زي get_active، لكن لو العميل مالوش أي سلة خالص، بينشئ واحدة جديدة —
        يُستخدم بس لحظة إضافة أول صنف فعليًا (orders.cart.Cart.add)، مش عند
        مجرد فتح صفحة السلة أو حذف سلة موجودة.
        """
        cart = cls.get_active(client)
        if cart is not None:
            if not cart.is_active:
                cart.is_active = True
                cart.save(update_fields=['is_active'])
            return cart
        return cls.objects.create(client=client, is_active=True)


class CartItem(models.Model):
    cart         = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='items')
    product_unit = models.ForeignKey(ProductUnit, on_delete=models.CASCADE)
    quantity     = models.PositiveIntegerField(default=1)
    added_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'صنف في السلة'
        verbose_name_plural = 'أصناف السلة'
        constraints = [
            models.UniqueConstraint(fields=['cart', 'product_unit'], name='unique_product_unit_per_cart'),
        ]

    def __str__(self):
        return f'{self.product_unit.name} x{self.quantity}'


class OrderLog(models.Model):
    """
    سجل عمليات الطلب — كل حدث بيحصل على الطلب (إنشاء، تغيير حالة، ملاحظة).
    العميل يشوفه كـ تايم لاين في صفحة تفاصيل الطلب.
    """
    class Event(models.TextChoices):
        CREATED        = 'CREATED',        'تم إنشاء الطلب'
        STATUS_CHANGED = 'STATUS_CHANGED',  'تغيير الحالة'
        NOTE           = 'NOTE',            'ملاحظة'

    order      = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='logs')
    event      = models.CharField(max_length=20, choices=Event.choices)
    new_status = models.CharField(max_length=20, choices=Order.Status.choices, blank=True)
    note       = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='order_logs',
    )

    class Meta:
        verbose_name = 'سجل عملية'
        verbose_name_plural = 'سجل العمليات'
        ordering = ['created_at']

    def __str__(self):
        return f'{self.get_event_display()} — طلب #{self.order_id}'
