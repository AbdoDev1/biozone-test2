# BioZone — تتبع تنفيذ ROADMAP.md

> **الغرض:** ملف واحد يوضح فين وصلنا بالظبط في كل مرحلة، عشان محدش (ولا حتى Claude في محادثة جديدة) يحتاج يتقال له الحالة من الأول كل مرة. حدّث الجدول ده مع كل تسليم فعلي (مش تخطيط).

**آخر تحديث:** 30 يوليو 2026 (مرحلة 7 — المرحلة الأخيرة في الخطة الأصلية)

---

## نظرة سريعة

| المرحلة | الحالة | ملاحظة |
|---|---|---|
| 1 — تعميم الـ Pattern الموجود | ✅ **مكتملة** | breadcrumbs + tabs + عدادات على المنتجات والمخزون. لا يوجد app موردين في المشروع أصلًا. |
| 2 — بنية تحتية (Audit + Chatter + Relations) | ✅ **مكتملة** | البنية التحتية + منتجات + عملاء + الـ migration اتطبقت فعليًا على السيرفر (Postgres). |
| 3 — ترقية الجداول | ✅ **مكتملة (لجدول المنتجات)** | ترتيب/فلترة/تجميع/bulk activate-deactivate/Quick Edit سعر — كله مطبّق ومفحوص. باقي فقط لو حبيتوا تعمّموا نفس النمط لجداول تانية (العملاء، المخزون...). |
| 4 — Lifecycle + Action Bar | ✅ **مكتملة** | شريط حالة مرئي للطلبات + قائمة إجراءات موحدة + تكرار منتج + نظام وسوم عام (Tags). |
| 5 — تحسينات واجهة المتجر | ✅ **مكتملة** | منتجات مشابهة/مكمّلة + hover hint + Reorder + Product Variants + data migration لاستخراج المقاس من الاسم. migration (0016+0017) اتطبّقت فعليًا على السيرفر. |
| 6 — الحد الأدنى للطلب لكل عميل | ✅ **مكتملة** | `ClientProfile.min_order_amount` (مخصّص لكل عميل) + fallback للقيمة العامة. راجع تعليمات `APPLY_INSTRUCTIONS_phase6.md` لتطبيق الـ migration لو لسه ماتمّش. |
| 7 — أتمتة ومتابعة | ✅ **مكتملة** | متابعات مجدولة عامة (`followups/`) + صفحة "مقترحات التوريد" الموحّدة. آخر مرحلة في الخطة الأصلية. |

---

## مرحلة 1 — تعميم الـ Pattern الموجود
**الحالة: ✅ مكتملة**

- تأكيد: `staff/templates/staff/products/form.html` و `staff/templates/staff/inventory/detail.html` فيهم نفس نمط breadcrumbs + notebook tabs + عدادات زي `clients/detail.html`.
- لا يوجد app/موديل للموردين في المشروع حاليًا، فبند "الموردين" في المرحلة غير منطبق (مفيش حاجة تتعمم عليها).
- معيار القبول محقق: كل صفحة تفاصيل موجودة (منتج، صنف مخزون) عندها نفس بنية التابات والعدادات.

---

## مرحلة 2 — بنية تحتية (Audit + Chatter + Relations)
**الحالة: ✅ مكتملة**

### ✅ اتعمل

**App جديد `activity/`** (Generic Audit Log + Chatter، ContentType-based):
- `activity/models.py` — موديل `ActivityLog` واحد يشتغل على أي كيان (CREATED / UPDATED / NOTE) بدل ما يفضل `OrderLog` مقفول على الطلبات.
- `activity/services.py` — `log_activity()`, `log_created()`, `log_note()`, `diff_summary()` (الواجهة الوحيدة المفروض أي view يستخدمها).
- `activity/views.py` + `activity/urls.py` — endpoint عام واحد `activity:add_note` لإضافة ملاحظة (Chatter) على أي سجل، بدل view منفصل لكل قسم.
- `activity/templatetags/activity_tags.py` + `activity/templates/activity/_panel.html` — تاج `{% activity_panel obj %}` قابل لإعادة الاستخدام (تايم لاين + فورم ملاحظة).
- `activity/admin.py` — سجل للمراجعة، read-only (الإدخال من الكود بس، مش يدوي من admin).
- Migration: `activity/migrations/0001_initial.py` (اتولّدت واتفحصت بـ `makemigrations --check` — **لسه محتاجة تتطبّق فعليًا بـ `migrate` على قاعدة بيانات المشروع الحقيقية**، الصلاحيات دي مش متاحة من بيئة التطوير الحالية).
- مسجّلة في `INSTALLED_APPS` (`config/settings.py`) و `config/urls.py` (`path('activity/', include('activity.urls'))`).

**تطبيق على المنتجات** (`staff/views/products/crud.py`, `staff/templates/staff/products/form.html`):
- `product_add`: يسجّل `CREATED`.
- `product_edit`: ياخد نسخة من القيم المتابَعة (`PRODUCT_TRACKED_FIELDS`) قبل الحفظ، ويسجّل `UPDATED` بملخص التغيير الفعلي (`diff_summary`) — بس لو فيه تغيير حقيقي.
- تاب "النشاط" جديد في `form.html` (وضع التعديل بس) بيعرض `{% activity_panel product %}`.
- **Related Documents:** قسم "مستندات ذات صلة" في تاب "بيانات المنتج" — رابط لسجل المخزون (`inventory_item`) وآخر 8 طلبات فيها صنف من المنتج (`related_orders`، عبر `OrderItem.product_unit__product`).

**تطبيق على العملاء** (`staff/views/clients.py`, `staff/templates/staff/clients/detail.html`):
- `client_approve` / `client_reject`: يسجّلوا `UPDATED` بملخص ("تفعيل الحساب" / "رفض الحساب").
- تاب "النشاط" جديد في `clients/detail.html` بيعرض `{% activity_panel profile %}`.
- ملاحظة تصميم: عمليات الدفع/التسوية المالية (`client_add_payment`/`client_add_adjustment`) **ماتسجلش** في `ActivityLog` عن قصد — عندها تايم لاين خاص بيها بالفعل في تاب "حسابي المالي" (`AccountTransaction`)، والتكرار هيبقى ضوضاء زيادة من غير فايدة.

### ✅ الباقي اللي كان معلّق اتقفل

1. **الـ migration اتطبقت فعليًا** على السيرفر الحقيقي (Postgres) — تأكيد من صاحب المشروع (27 يوليو).
2. معيار القبول **متحقق بالكامل** الآن: فتح منتج أو عميل يوريك نشاطه (مين عدّل إيه وإمتى) وتقدر تسيب ملاحظة داخلية.
3. لو حبيتوا تعمموا نفس النمط على كيانات تانية غير منتج/عميل (لو ظهرت لاحقًا)، الخطوات صارت مكرورة وسريعة:
   - `from activity.services import log_activity, diff_summary` في الـ view.
   - `{% load activity_tags %}{% activity_panel obj %}` في التمبلت.
   - إضافة `activity_count` للـ context لو عايز تاب فيه عداد.

### 🔍 ثغرات اتلقت واتصلحت بعد مراجعة إضافية (27 يوليو)

مراجعة شاملة على كل مسار ممكن يعدّل منتج أو عميل، مش بس اللي كان اتجرب الأول:

1. **الاستيراد الجماعي من Excel كان مايسجّلش نشاط خالص** — `products/services/import_export/commit.py` بيحفظ المنتج مباشرة (`Product.objects.create`) مش عن طريق `product_add`/`product_edit`، فمكنش بيمر على تسجيل النشاط. **اتصلح:** بيسجّل CREATED للصنف الجديد، UPDATED (بملاحظة عامة "تحديث من ملف Excel") للصنف الموجود.
2. **حذف وحدة (Unit) أثناء تعديل منتج كان بيحصل بصمت** — الفورمست بيسمح بحذف وحدة (`can_delete=True`) بس الكود الأول كان بيقارن بس الوحدات الموجودة بعد الحفظ. **اتصلح:** `_unit_prices_diff_summary` دلوقتي بيكتشف الوحدات المحذوفة ويسجّل "تم حذف وحدة (اسمها)".
3. **حذف منتج كامل كان بيسيب سجلات نشاط يتيمة** — الربط بـ `ActivityLog` عن طريق ContentType عام (object_id) مش FK حقيقي، فمفيش CASCADE تلقائي وقت حذف المنتج. **اتصلح:** أضيفت `activity.services.delete_activity_logs_for(instance)` وبتتنادى في `product_delete` قبل `product.delete()` — أي delete view جديد لازم يستخدمها بنفس الطريقة (اتوثقت في الدالة نفسها).

الثلاثة إصلاحات دي اتفحصت فعليًا (functional test عبر Django test client + سيناريوهات حقيقية) مش بس نظريًا — النتائج موثقة في نفس المحادثة.

### خارج نطاق مرحلة 2 عن قصد (مش ثغرات)
- الأقسام (`Category`) مش متتبّعة — معيار القبول في ROADMAP.md حدد "عميل/منتج" بس.
- مفيش view لتعديل بيانات العميل نفسه (اسم النشاط، نوع الحساب) في الكود أصلًا، فمفيش حاجة تتراقب هناك حاليًا.

### 🆕 إضافة صغيرة خارج المراحل — دعم الباركود في استيراد/تصدير Excel (27 يوليو)

طلب مباشر من صاحب المنتج (مش جزء من مرحلة معينة في ROADMAP.md، بس مربوط بنفس نقاش كود الصنف/الباركود):

- عمود `barcode` جديد جنب `code` في: التصدير (`export.py`)، القالب الفارغ (`build_import_template_workbook`)، والقراءة (`parsing.py`).
- عمود اختياري تمامًا (مش ضمن `REQUIRED_IMPORT_HEADERS`) — الملفات القديمة من غيره تفضل شغالة عادي.
- **عند التحديث:** لو الخلية فاضية، الباركود المسجّل يفضل زي ما هو (مش بيتمسح) — عكس `name_ar`/`category` اللي بيتكتبوا زي ما هما في الملف دايمًا.
- **حماية من التعارض:** لو الباركود متكرر في نفس الملف، أو مستخدم بالفعل لصنف تاني في القاعدة، الصف نفسه بيتحفظ عادي بس من غير الباركود ده + تحذير واضح في صفحة الأخطاء بعد الاستيراد (بدل ما `IntegrityError` توقف الدفعة *كلها* زي ما كانت هتعمل قبل الإصلاح ده).
- واجهة صفحة الاستيراد (`import.html`) اتحدثت بشرح العمود الجديد.
- اتفحص functional (round-trip كامل: تصدير قالب → استيراد → تعارض باركود موجود وتعارض داخل نفس الملف) — النتائج موثقة في المحادثة.

### ملفات اتلمست في مرحلة 2

```
activity/                              (جديد بالكامل)
├── models.py, services.py, views.py, urls.py, admin.py, apps.py
├── migrations/0001_initial.py
└── templatetags/activity_tags.py
    templates/activity/_panel.html
config/settings.py                     (+ 'activity' في INSTALLED_APPS)
config/urls.py                         (+ include('activity.urls'))
staff/views/products/crud.py           (+ log CREATED/UPDATED + related docs helpers)
staff/templates/staff/products/form.html   (+ تاب النشاط + قسم مستندات ذات صلة)
staff/views/clients.py                 (+ log UPDATED عند approve/reject + activity_count)
staff/templates/staff/clients/detail.html  (+ تاب النشاط)
```

---

## مرحلة 3 — ترقية الجداول
**الحالة: ✅ مكتملة (على جدول المنتجات — النطاق المحدد في معيار القبول)**

### ✅ اتعمل — على `staff/products/list.html` و `staff/views/products/crud.py`

- **ترتيب (Sortable columns):** `?sort=name|category|price|stock|status&dir=asc|desc`. الترتيب بيحصل على مستوى الاستعلام (annotate + Subquery لسعر أصغر وحدة، annotate للمخزون المتاح) مش في بايثون بعد التقطيع — عشان الترتيب يكون صحيح عبر كل الصفحات مش بس الصفحة الحالية. تاج تمبلت جديد وقابل لإعادة الاستخدام `{% sortable_th %}` (في `staff_ui.py`) بيرسم رابط الترتيب + سهم الاتجاه، وأي جدول تاني هيحتاج نفس الميزة بعدين هيستخدم نفس التاج.
- **Sticky header:** حاوية الجدول بقت `max-h-[70vh] overflow-y-auto` و`<thead>` عليه `sticky top-0` — العنوان يفضل ظاهر أثناء اسكرول الجدول نفسه (مش الصفحة كلها).
- **Pagination:** كان موجود قبل كده على المنتجات، اتأكد إنه لسه شغال مع الفلاتر/الترتيب الجديد (روابط الصفحات بقت بتحافظ على كل querystring عن طريق فلتر `without_page` الموجود أصلًا).
- **فلاتر إضافية:** حالة (نشط/معطل) ومخزون (منخفض/نافذ — نفس منطق `Inventory.is_low`/`low_stock()` بس كـ query filter).
- **تجميع حسب القسم (Grouping):** checkbox `?group=1` — بيرتب حسب القسم كمفتاح أساسي ويعرض صف عنوان القسم (`{% ifchanged %}`) بين المجموعات.
- **Bulk activate/deactivate:** checkboxes على كل صف (مربوطة بـ `form="bulk-products-form"` بدل ما نحوّط الجدول كله في `<form>` — كان هيتعارض مع فورم التعديل السريع جوه خلية السعر، والفورمز مينفعش تتعشش في HTML) + شريط إجراءات جماعي بيظهر بس لو فيه تحديد. view جديد `product_bulk_action` بيستخدم `.update()` (تحديث واحد، مش حفظ كل منتج لوحده) ويسجّل نشاط في ActivityLog (مرحلة 2) لكل منتج اتغيّرت حالته فعليًا.
- **Quick Edit inline (السعر):** الضغط على سعر القطعة في الجدول بيفتح خانة تعديل مكانها (Alpine، من غير أي reload)، والحفظ بيتم عبر htmx (`product_quick_update_price`) برجّع partial واحد بس (الخلية نفسها) — التمبلت (`partials/price_cell.html`) مصدر واحد مستخدم في الرندر الأول وفي رد الـ htmx. أي تعديل فعلي بيتسجل في ActivityLog بنفس أسلوب مرحلة 2.
- **صلاحيات:** الإجراءين الجماعي والتعديل السريع محميين بنفس صلاحية `products.change_product` بتاعة `product_edit` العادي (اتفحص إن مخزن من غير الصلاحية دي ميقدرش ينفذهم).

### 🔍 إصلاح جانبي (اتلقى أثناء تشغيل test suite الكامل، مش جزء من مرحلة 3)

`products/services/import_export/parsing.py` — `group_unit_rows` كان بيفترض إن كل صف قاموس فيه مفتاح `'barcode'` دايمًا (`r['barcode']`)، وده كان بيكسر جزء من test suite الأصلي (`KeyError`) لأن الباركود عمود اختياري بالتصميم. اتصلح لـ `.get('barcode', '')`.

### 🧪 الفحص

- test suite الكامل للمشروع (134 اختبار بعد الإضافة، كانوا 112 قبلها) شغّال بالكامل ونجح (`OK`) على بيئة SQLite محلية (Postgres مش متاح من بيئة التطوير الحالية — نفس القيد اللي واجهناه في مرحلة 2).
- 22 اختبار functional جديد (`staff/tests_products_list.py`) بيغطوا: الترتيب بكل الاتجاهات، فلاتر الحالة/المخزون، التجميع، الإجراء الجماعي (بما فيه: عدم تكرار تسجيل نشاط لو الحالة متطابقة أصلًا، رفض GET، رفض من غير صلاحية)، والتعديل السريع للسعر (قيمة صحيحة/غير صحيحة/سالبة، تسجيل النشاط، رفض GET، رفض من غير صلاحية).
- `npm run build:css` اتشغّل فعليًا بعد كل تعديلات التمبلت (Tailwind مبني من ملف، مش CDN) — اتأكد إن كل كلاس جديد مستخدم (زي `max-h-[70vh]`) دخل فعليًا في `static/css/tailwind.css` المُصدَّر.

### خارج نطاق مرحلة 3 عن قصد (مش نواقص)

- الترتيب/الفلترة/التجميع/bulk actions/Quick Edit اتطبقوا على **جدول المنتجات بس** (ده اللي معيار القبول في ROADMAP.md حدده صراحةً بالاسم). باقي الجداول (العملاء، المخزون، الطلبات، الفئات...) لسه بالشكل القديم — التاج `{% sortable_th %}` والنمط العام (annotate للترتيب + checkboxes بـ `form=` خارجي + partial للخلية القابلة للتعديل) جاهزين يتعمموا عليها بسرعة نسبيًا لو حبيتوا، لكن ده مش جزء من الـ acceptance criteria الحالي.
- Column resizing وColumn visibility (إخفاء/إظهار أعمدة) مستبعدين صراحة في ROADMAP.md.

### ملفات اتلمست في مرحلة 3

```
staff/views/products/crud.py                    (+ ترتيب/فلترة/تجميع في product_list، + product_bulk_action، + product_quick_update_price)
staff/views/products/__init__.py                (+ exports)
staff/urls.py                                    (+ 2 routes جديدة)
staff/templatetags/staff_ui.py                   (+ تاج sortable_th)
staff/templates/staff/products/list.html         (إعادة بناء: checkboxes + bulk bar + sticky header + فلاتر + تجميع)
staff/templates/staff/products/partials/price_cell.html   (جديد بالكامل)
staff/tests_products_list.py                     (جديد بالكامل — 22 اختبار)
products/services/import_export/parsing.py       (إصلاح جانبي — KeyError على 'barcode')
static/css/tailwind.css                          (rebuild)
```

## مرحلة 4 — Lifecycle + Action Bar
**الحالة: ✅ مكتملة**

### ✅ اتعمل

**App جديد `tags/`** (نظام وسوم عام، ContentType-based — بنفس فكرة `activity.ActivityLog`):
- `tags/models.py` — `Tag` (اسم فريد + لون من 7 ألوان) و`TaggedItem` (ربط عام بأي كيان، UniqueConstraint يمنع تكرار نفس الوسم على نفس العنصر).
- `tags/services.py` — `add_tag()`, `remove_tag()`, `tags_for()`, `delete_tagged_items_for()` (الواجهة الوحيدة المفروض أي view يستخدمها).
- `tags/views.py` + `tags/urls.py` — endpoints عامة `tags:tag_add` / `tags:tag_remove` تشتغل على أي موديل (نفس نمط `activity:add_note`)، صلاحية: أي موظف (أدمن/مخزن) مش عميل.
- `tags/templatetags/tag_tags.py` + `tags/templates/tags/_panel.html` — تاج `{% tag_panel obj %}` (شارات + فورم إضافة/إزالة، مع datalist لاقتراح وسوم موجودة).
- الوسم بيتشارك بين كيانات مختلفة: وسم "عاجل" على طلب ومنتج بيشاوروا لنفس صف `Tag` (نفس اللون، وتعديله بيتغيّر في كل مكان).
- مطبّق فعليًا على صفحة تفاصيل الطلب (`staff/orders/detail.html`) — أي كيان تاني (منتج، عميل) يقدر ياخد نفس الميزة بسطرين بس (`{% load tag_tags %}{% tag_panel obj %}`) من غير أي migration جديدة.
- migration: `tags/migrations/0001_initial.py` (اتعملت وتطبقت محليًا بـ SQLite للفحص — **لسه محتاجة `migrate` فعلي على السيرفر الحقيقي زي أي app جديد**، نفس الخطوة اللي كانت باقية في مرحلة 2 وخلصت دلوقتي).
- مسجّلة في `INSTALLED_APPS` و`config/urls.py` (`path('tags/', include('tags.urls'))`).

**شريط حالة مرئي للطلبات** (`staff/templates/staff/orders/partials/status_bar.html`):
- Stepper من 3 خطوات مبني على `Order.Status` مباشرة (من غير أي حقل/موديل إضافي): "الطلب مُستلم" → "تم التأكيد" → "تم التسليم".
- `NEEDS_APPROVAL` بتترسم كجزء من خطوة "التأكيد" لسه معلّقة (لون برتقالي مميز) بدل ما تتحسب خطوة رابعة منفصلة.
- `REJECTED` حالة نهائية خارج المسار الطبيعي تمامًا — بتترسم كتنبيه أحمر منفصل بدل ما تتحشر جوه الـ stepper.
- مطبّق في `staff/orders/detail.html` فوق جدول الأصناف مباشرة.

**قائمة إجراءات موحدة** (`{% action_menu %}` في `staff_ui.py` + `staff/components/action_menu.html`):
- component عام قابل لإعادة الاستخدام لأي صفحة تفاصيل — بديل عن تكديس أزرار/روابط ثانوية متفرقة (طباعة، تصدير، أرشفة، تكرار...).
- كل عنصر إجراء لازم يكون رابط GET (لصفحة تأكيد لو الإجراء بيغيّر حالة — نفس نمط `product_delete`/`product_duplicate` الحاليين، مش تنفيذ مباشر من القائمة نفسها).
- مطبّقة في صفحة تفاصيل الطلب: جمّعت رابطي "طباعة الطلب للمراجعة اليدوية" و"عرض/طباعة الفاتورة" (كانوا متبعثرين في أماكن مختلفة من الصفحة) في قائمة "طباعة" واحدة.
- مطبّقة في صفحة تعديل المنتج: قائمة "إجراءات أخرى" فيها "تكرار المنتج" و"حذف المنتج" (بدل ما "حذف" يكون رابط مستقل زي ما كان).

**تكرار منتج (Duplicate)** (`staff/views/products/crud.py::product_duplicate`):
- بنفس فكرة "Duplicate Post" في WordPress — نسخ منتج موجود كنقطة بداية بدل ملء فورم من الصفر.
- GET بيعرض صفحة تأكيد (`staff/products/duplicate_confirm.html`، نفس نمط `product_delete`)، POST هو اللي بينفّذ فعليًا.
- النسخة الجديدة بتاخد: القسم، الاسم (بلاحقة "(نسخة)")، الاسم الإنجليزي، المصنّع، الوصف، وكل الوحدات (بالاسم والحجم والسعر وسعر التكلفة).
- بتاخد عمدًا: **الباركود** (unique، مينفعش يتكرر)، **الصورة** (تُرفع يدويًا)، **المخزون/الحركات** (تبدأ من غير أي رصيد).
- بتتحفظ `is_active=False` افتراضيًا — الموظف لازم يراجع البيانات (خصوصًا الاسم والباركود) ويفعّلها يدويًا.
- بتسجّل `ActivityLog.CREATED` بملاحظة توضح إنها نسخة من أي منتج (بالاسم والكود).
- بعد التنفيذ، بيتوجّه الموظف مباشرة لصفحة تعديل النسخة الجديدة عشان يراجعها فورًا.
- صلاحية: `products.add_product` (نفس صلاحية `product_add` العادي).

### 🧪 الفحص

- test suite الكامل للمشروع (148 اختبار بعد الإضافة، كانوا 134 قبلها) شغّال بالكامل ونجح (`OK`) — 14 اختبار جديد.
- `tags/tests.py` (9 اختبارات): إعادة استخدام نفس الوسم بين كيانات مختلفة (طلب + منتج)، عدم تكرار نفس الوسم على نفس العنصر، إزالة وسم من عنصر واحد بس من غير ما تأثر على عناصر تانية، رفض الاسم الفاضي، صلاحيات (موظف يقدر / عميل ميقدرش / زائر غير مسجل بيتحول للوجن).
- `staff/tests_products_duplicate.py` (5 اختبارات): GET بيعرض تأكيد من غير ما ينشئ حاجة، POST بينشئ نسخة معطّلة بنفس الوحدات، الباركود ميتنسخش، تسجيل نشاط بيشاور للمنتج الأصلي، رفض التنفيذ من غير صلاحية `products.add_product`.
- فحص يدوي إضافي (smoke test عبر Django test client): الصفحات الثلاث (تعديل منتج، تأكيد التكرار، تفاصيل الطلب) بترجع 200 وفيها فعليًا العناصر الجديدة (زرار التكرار، شريط الحالة، لوحة الوسوم، قائمة الإجراءات) — مش بس الاختبارات الوحدوية.

### خارج نطاق مرحلة 4 عن قصد (مش نواقص)
- الوسوم مطبّقة على **الطلبات بس** حاليًا (ده اللي معيار القبول ذكره بالاسم: "عاجل"، "يحتاج مراجعة") — الموديل جاهز يتعمم على منتج/عميل بسطرين بس لو احتجتوا ده بعدين.
- قائمة الإجراءات الموحدة اتطبقت على الطلب والمنتج بس (الصفحتين اللي فيهم فعلاً إجراءات ثانوية متبعثرة تستاهل التجميع) — أي صفحة تفاصيل تانية تقدر تستخدم نفس الـ component بسهولة.
- "تصدير" و"أرشفة" في وصف مرحلة 4 اتترجموا هنا لأقرب حاجة موجودة فعليًا في النظام (الفاتورة/الطباعة كـ"تصدير"، الحذف/التعطيل كـ"أرشفة") بدل ما نخترع مفاهيم مالهاش استخدام حالي.

### ملفات اتلمست/اتضافت في مرحلة 4

```
tags/                                   (جديد بالكامل)
├── models.py, services.py, views.py, urls.py, admin.py, apps.py, tests.py
├── migrations/0001_initial.py
└── templatetags/tag_tags.py
    templates/tags/_panel.html
config/settings.py                     (+ 'tags' في INSTALLED_APPS)
config/urls.py                         (+ include('tags.urls'))
staff/templatetags/staff_ui.py         (+ action_menu, color_classes, icon_svg + أيقونات dots/duplicate/archive)
staff/templates/staff/components/action_menu.html   (جديد بالكامل)
staff/templates/staff/orders/partials/status_bar.html   (جديد بالكامل)
staff/templates/staff/orders/detail.html   (+ شريط الحالة + لوحة الوسوم + قائمة إجراءات الطباعة)
staff/views/orders.py                  (+ order_actions في order_detail)
staff/views/products/crud.py           (+ product_duplicate + product_actions في product_edit)
staff/views/products/__init__.py       (+ export product_duplicate)
staff/urls.py                          (+ route التكرار)
staff/templates/staff/products/form.html            (قائمة إجراءات بدل رابط حذف مستقل)
staff/templates/staff/products/duplicate_confirm.html   (جديد بالكامل)
staff/tests_products_duplicate.py      (جديد بالكامل — 5 اختبارات)
```

---

### 🆕 إصلاحات وإضافات خارج المراحل (28 يوليو)

طلبات مباشرة من صاحب المشروع بعد مراجعة مرحلة 4 فعليًا على السيرفر — مش جزء من مرحلة معينة في ROADMAP.md، لكن مرتبطة بمكونات اتعملت فيها (`{% action_menu %}`, `{% tag_panel %}`, `TagAdmin`/`ActivityLogAdmin`).

**1. باج `@click.outside` في القوائم المنسدلة (Alpine.js):**
- السبب: `@click.outside="open = false"` كان متحط على الزرار نفسه بدل الـ `div` الأب اللي لافّ الزرار والقايمة مع بعض — أي كليك جوه القايمة (زي الكتابة في input) كان بيتحسب "بره"، فبيقفلها فورًا.
- نفس الباج بالظبط في مكانين: `tags/templates/tags/_panel.html` (فورم إضافة وسم) و`staff/templates/staff/components/action_menu.html` (قائمة الإجراءات الموحدة من مرحلة 4) — وده كان السبب الحقيقي وراء شكوى سابقة إن القايمة "بتفتح لتحت الصفحة" (مكنش CSS positioning زي ما كان مفترض الأول).
- الإصلاح: نقل `@click.outside` من `<button>` لـ الـ `<div x-data="...">` الأب في الملفين.

**2. رابط الفاتورة الرسمية ناقص من صفحة العميل بعد التسليم:**
- صفحة تفاصيل الطلب لصفحة الستاف (`staff:order_detail`) كانت أصلًا بتعرض رابط الفاتورة صح لما `order.status == DELIVERED` (مرحلة 4). المشكلة كانت في **صفحة العميل نفسه** (`orders:order_detail`) — مفيش أي بلوك أو إشارة لحالة `DELIVERED` خالص، فالعميل مكنش بيعرف إن طلبه اتسلّم ولا إن فيه فاتورة يقدر يفتحها، رغم إن صلاحية `invoices:print` كانت بتسمحله يشوفها أصلًا (backend جاهز، UI ناقص).
- الإصلاح: `orders/views/order.py::order_detail` بقى بيجيب الفاتورة المرتبطة (`select_related('invoice')`) ويمررها للقالب، و`orders/templates/orders/order_detail.html` بقى فيه بلوك جديد لحالة `DELIVERED` (بنفس نمط بلوكات `PENDING`/`NEEDS_APPROVAL` الموجودة) فيه رابط "عرض/طباعة الفاتورة" برقمها.

**3. نقل صفحتي "الوسوم" و"سجل الأنشطة" من Django admin لواجهة staff مبسطة:**
- كانت الصفحتين الوحيدتين المتبقيتين اللي بتتدار من `/admin/` (والوصول لـ `/admin/` أصلًا مقصور على role=ADMIN بس — `is_staff`/`is_superuser` بيتحددوا من الدور، مفيش موظف مخزن يقدر يوصله).
- **الوسوم** (`staff/views/tags.py` + `staff/templates/staff/tags/list.html`، routes جديدة تحت `/staff/tags/`): نسخة مبسطة من `TagAdmin` — بحث بالاسم، فلتر لون، ترتيب (اسم/عدد الاستخدامات/تاريخ الإنشاء) عبر `{% sortable_th %}`، Pagination (30/صفحة، بنفس نمط بقية قوائم الستاف)، إضافة/تعديل عبر قوائم منسدلة صغيرة (نفس مكوّن الـ dropdown المُصلح في البند 1)، وحذف بتأكيد (`data-confirm`) بيوضح عدد العناصر المتأثرة (الحذف بيمسح كل `TaggedItem` المرتبط تلقائيًا، بنفس سلوك admin الافتراضي).
- **سجل الأنشطة** (`staff/views/activity.py` + `staff/templates/staff/activity/list.html`، route جديد `/staff/activity/`): نسخة مبسطة من `ActivityLogAdmin` — **للقراءة فقط** (بحث في الملاحظة/ملخص التغييرات، فلتر نوع الحدث، فلتر نوع الكيان، Pagination)، بنفس القيد اللي كان في الـ admin نفسه (مفيش إضافة/تعديل/حذف يدوي، السجل بيتكتب من الكود بس).
- **صلاحيات دقيقة جديدة** في كتالوج `staff/permissions.py` (`PERMISSION_SECTIONS`) — قسمين جداد: `🏷️ الوسوم` (view/add/change/delete على `tags.tag`) و`🗒️ سجل الأنشطة` (view بس على `activity.activitylog`). بيظهروا تلقائيًا في شاشة صلاحيات الموظف (`employee_edit`) من غير أي تعديل فيها، لأنها بتلف على الكتالوج بشكل عام. القائمة الجانبية والأزرار جوه صفحة الوسوم (إضافة/تعديل/حذف) بقوا مربوطين بنفس الصلاحيات دي (`{% if perms.tags.view_tag %}` وهكذا) بدل ما يكونوا مقصورين على الأدمن.

⚠️ **لسه محتاج فحص فعلي:** التعديلات دي اتفحصت syntax-wise (`ast.parse`) بس — مفيش `python manage.py check` ولا test run فعلي حصل عليها لحد دلوقتي (محتاجة بيئة فيها قاعدة بيانات مُجهزة). يُنصح بتجربتها محليًا أو عمل اختبارات مشابهة لـ `staff/tests_products_duplicate.py` قبل الدمج النهائي، خصوصًا لصفحة الوسوم الجديدة (CRUD كامل).

### ملفات اتلمست في الإصلاحات والإضافات دي

```
tags/templates/tags/_panel.html                      (إصلاح @click.outside)
staff/templates/staff/components/action_menu.html    (إصلاح @click.outside)
orders/views/order.py                                (+ جلب الفاتورة في order_detail)
orders/templates/orders/order_detail.html             (+ بلوك حالة DELIVERED مع رابط الفاتورة)
staff/views/tags.py                                   (جديد بالكامل — tag_list/add/edit/delete)
staff/views/activity.py                               (جديد بالكامل — activity_list، read-only)
staff/urls.py                                          (+ 5 routes: tags + activity)
staff/templates/staff/tags/list.html                   (جديد بالكامل)
staff/templates/staff/activity/list.html               (جديد بالكامل)
staff/templates/staff/base.html                        (+ رابطين في القائمة الجانبية، مربوطين بـ perms)
staff/permissions.py                                   (+ قسمي tags وactivity في PERMISSION_SECTIONS)
```

---

## مرحلة 5 — تحسينات واجهة المتجر
**الحالة: ✅ مكتملة**

### ✅ اتعمل

**1. الموديل (`products/models.py`):**
- `Product.size` (CharField حر) — مقاس المنتج (لبس/هدوم)، منفصل تمامًا عن `ProductUnit.size` (حجم الوحدة قطعة/كرتونة).
- `Product.similar_products` و `Product.complementary_products` — self M2M غير متماثل (`symmetrical=False`)، حقلين منفصلين لأن المنطق مختلف.
- موديل جديد `ProductVariantGroup` (id + اسم داخلي اختياري) + `Product.variant_group` FK (`on_delete=SET_NULL`).
- خاصية `Product.variant_siblings` — باقي أعضاء نفس مجموعة المقاسات، بتستفيد من الـ prefetch لو موجود (بدون N+1).
- **Migration 0016**: إنشاء الحقول/الموديل — اتولّدت بسويتش SQLite المؤقت المعتاد.
- **Migration 0017 (data migration)**: بتنفّذ القرار الموثّق في ROADMAP.md — استخراج مقاسات لاتينية قياسية (S/M/L/XL/XXL/XXXL) من آخر `name_ar` بالـ regex، نقلها لـ `Product.size`، وتنضيف الاسم. محافظة عمدًا (مقاسات نصية قياسية بس، مش أي رقم/حرف لاتيني تاني) لتفادي تخريب أسماء منتجات حقيقية. قابلة للتراجع (`RunPython` بـ reverse function).

**2. منتجات مشابهة ومكمّلة (staff):**
- `staff/views/products/relations.py` (جديد) — بحث عام (`_search_products`)، إضافة/إزالة لكل من `similar`/`complementary`، وربط/فك ربط منفصل لمقاسات التنويع (`product_variant_link`/`unlink` — بينشئ/يعيد استخدام `ProductVariantGroup`، وبيمسح المجموعة تلقائيًا لو فضل عضو واحد بس).
- **Product Picker عام قابل لإعادة الاستخدام** (`staff/templates/staff/products/partials/product_picker.html`) — بحث htmx (`hx-get` + `hx-trigger keyup changed delay:300ms`) + شرائح (chips) قابلة للإزالة، بيتعاد استخدامه 3 مرات (مشابه/مكمّل/تنويع) بفرق الـ `relation` parameter بس.
- `staff/templates/staff/products/partials/related_products_section.html` (جديد) — القسم اللي بيلمّ التلاتة، بيتضم بـ `{% include %}` في تاب جديد "روابط" داخل `staff/templates/staff/products/form.html` (وضع التعديل بس، زي تابي الوسوم/النشاط بالظبط).
- حقل `size` اتضاف لفورم المنتج (`products/forms.py`) وتاب "بيانات المنتج"، واتضاف لـ `PRODUCT_TRACKED_FIELDS` عشان يتتبّع في سجل النشاط.

**3. عرض المتجر (`store/`):**
- `store/views.py::product_detail` — `prefetch_related` كامل (منتجات مشابهة/مكمّلة بوحداتها وخصوماتها ومخزونها، ومجموعة التنويع بمخزونها) عشان مفيش N+1 على الصفحة. القائمتين مقصورتين على 6 عناصر كحد أقصى (انظر التعديل تحت).
- `store/templates/store/product_detail.html` — شارة مقاس، صف شرائح "المقاسات المتاحة" (المقاس الحالي مميّز، المقاسات التانية روابط لو متاحة أو عناصر معطّلة visually لو مش متاحة — **مش مخفية**)، وقسمين "منتجات مشابهة"/"غالبًا ما يُطلب معه" في الآخر (تسميات فصحى نهائية، انظر التعديل تحت).
- `store/templates/store/partials/related_products_carousel.html` (جديد) — كاروسيل عام (بيتحرك يمين-لشمال طبيعيًا لأن الصفحة `dir="rtl"` أصلًا، من غير أي تخصيص إضافي)، بيعيد استخدام `product_card.html` نفسه. سكرول يدوي بس، بدون أي حركة تلقائية.
- `store/templates/store/partials/product_card.html` — شارة "تفاصيل أكتر" ظاهرة دايمًا على صورة كل كارت (مُعدّلة من hover-only، انظر تحت)، CSS/HTML بحت (بدون أي query أو شرط إضافي — ظاهرة بشكل موحّد على كل الكروت).

**4. إعادة الطلب (Reorder):**
- `orders/views/order.py::order_reorder` (جديد) — بينشئ **سلة جديدة مخصّصة** (مش بيضيف على السلة النشطة الحالية، تفاديًا لخلط أصناف الطلب القديم بطلبية شغّال عليها العميل فعلًا)، وبيستخدم `Cart.add()` الموجود بالظبط (نفس بوابات الأمان: الوحدة مسموحة لنوع حساب العميل الحالي + الصنف متوفر بالمخزون) — مفيش تكرار منطق. الأصناف اللي مش قابلة للإضافة (مخزون نفذ أو وحدة بقت غير مسموحة) بتتجاهل مع رسالة توضيحية بالاسم، مش بتوقف باقي العملية.
- زرار "إعادة الطلب لسلة جديدة" في `orders/templates/orders/order_detail.html` (صفحة العميل).
- **ملحوظة نطاق:** الزرار اتحط في صفحة تفاصيل الطلب بس، مش في كارت قائمة الطلبات (`order_list.html`) — الكارت هناك `<a>` واحد لافّ كل حاجة، فإضافة زرار منفصل جواه محتاجة إعادة هيكلة قالب مش سطر واحد. لو حابب الزرار في القائمة كمان، محتاج طلب صريح.

### 🔄 تعديلات بعد مراجعة تجربة العميل (نفس يوم التسليم)

مراجعة إضافية من منظور العميل النهائي (B2B، بساطة الشراء) — نتيجتها تعديلين على التنفيذ فوق:
1. **تسمية بفصحى بسيطة بدل عامية (صفحة المتجر فقط):** بعد مراجعة إضافية إن المواقع الكبيرة (Amazon/Noon) بتستخدم فصحى مش عامية، "منتجات مكمّلة" بقت **"غالبًا ما يُطلب معه"** (تأطير "Frequently bought together" بفصحى)، و"منتجات مشابهة" فضلت بنفس الاسم (ده أصلاً مصطلح المواقع الكبيرة). تسميات staff الداخلية فضلت زي ما هي. كل قسم بقى مقصور على 6 عناصر كحد أقصى (`[:6]` في `store/views.py`) بدون "عرض المزيد" — عدد ثابت صغير، مفيش تعقيد إضافي.
2. **إلغاء الاعتماد على hover في تلميح "تفاصيل أكتر":** hover مبيشتغلش على موبايل/تاتش خالص، وده قناة الطلب الشائعة لعميل B2B. الشارة بقت ظاهرة دايمًا (ركن صغير أسفل الصورة) بدل ما تظهر عند hover بس.

### 🧪 الفحص

- `python manage.py check` و`makemigrations --check --dry-run` نضاف بالكامل بعد كل تعديل (بما فيها التعديلين فوق).
- **test suite الكامل شغّال ونجح (`OK`)** — الفحص كان وقتها عبر Django test client يدوي بدل unit tests رسمية (لو حابب اختبارات رسمية زي `tags/tests.py` قولّي أضيفها). **تصحيح لاحق (مرحلة 6):** عدد اختبارات المشروع الفعلي هو **153** مش 148 كما كان مكتوب هنا وقتها — الفرق راجع لملفين اختبار (`staff/tests_products_list.py`، `staff/tests_products_duplicate.py`) فاتهم العدّ اليدوي القديم، مش اختبارات ضاعت أو اتعطّلت.
- فحص يدوي فعلي (smoke test): بحث/ربط/فك ربط منتجات مشابهة ومقاسات بديلة، صفحة تفاصيل منتج بمقاسات بديلة (متاح كرابط / غير متاح كعنصر معطّل) ومنتجات مشابهة، إعادة الطلب (سلة جديدة فعلية بنفس الكمية)، واستخراج المقاس بالـ regex على أسماء تجريبية متنوعة (نتائج مطابقة للمتوقع في كل الحالات، بما فيها أسماء من غير مقاس خالص اللي محدش لمسها).



### ملفات اتلمست/اتضافت في مرحلة 5

```
products/models.py                                              (+ size, variant_group, similar/complementary_products, variant_siblings, ProductVariantGroup)
products/forms.py                                                (+ حقل size)
products/migrations/0016_productvariantgroup_...py               (جديد)
products/migrations/0017_extract_size_from_name.py               (جديد — data migration)
staff/views/products/relations.py                                (جديد بالكامل)
staff/views/products/crud.py                                     (+ سياق تاب الروابط في product_edit، + size في PRODUCT_TRACKED_FIELDS)
staff/views/products/__init__.py                                 (+ export دوال relations)
staff/urls.py                                                    (+ 7 routes للعلاقات/التنويعات)
staff/templates/staff/products/form.html                        (+ تاب "روابط" + حقل المقاس)
staff/templates/staff/products/partials/product_picker.html      (جديد — component عام)
staff/templates/staff/products/partials/relation_picker_results.html   (جديد)
staff/templates/staff/products/partials/related_products_section.html (جديد)
store/views.py                                                   (+ prefetch كامل في product_detail)
store/templates/store/product_detail.html                        (+ شارة/شرائح مقاس + كاروسيلات)
store/templates/store/partials/related_products_carousel.html    (جديد)
store/templates/store/partials/product_card.html                 (+ hover hint)
orders/views/order.py                                            (+ order_reorder)
orders/views/__init__.py                                         (+ export order_reorder)
orders/urls.py                                                   (+ route reorder)
orders/templates/orders/order_detail.html                        (+ زرار إعادة الطلب)
```

## مرحلة 6 — الحد الأدنى للطلب لكل عميل
**الحالة: ✅ مكتملة**

### ✅ اتعمل

**1. الموديل (`accounts/models.py`):**
- `ClientProfile.min_order_amount` (`DecimalField`، `null=True, blank=True`). التمييز مقصود: `null` = "مفيش تخصيص لهذا العميل" (استخدم القيمة العامة)، بينما `0` = "الحد الأدنى لهذا العميل تحديدًا هو صفر" (قيمة فعلية مختلفة عن الفاضي).
- **Migration `accounts/0009`**: إضافة العمود بس (nullable) — مفيش data migration ولا لمس لأي بيانات موجودة، زي القاعدة العامة "توسّع من غير كسر" في `ROADMAP.md`.

**2. منطق القيمة الفعلية (`orders/models.py`):**
- دالة `get_effective_min_order_amount(client_profile)` — لو `client_profile.min_order_amount` محدّد (مش `None`) بترجعه، وإلا بترجع `SiteConfig.get_solo().min_order_amount` العام كـ fallback.

**3. الاستخدام (`orders/views/cart.py`, `orders/views/checkout.py`):**
- الاتنين بقوا بيستخدموا `get_effective_min_order_amount(request.user.client_profile)` بدل `SiteConfig.get_solo().min_order_amount` مباشرة. الشكل والرسائل زي ما هما، القيمة بس اللي بقت خاصة بالعميل الحالي.

**4. واجهة الإدارة (`staff/views/clients.py`, `staff/templates/staff/clients/detail.html`):**
- قسم جديد في تاب "بيانات العميل" بصفحة تفاصيل العميل — فورم فيه حقل واحد (فاضي = استخدام القيمة العامة)، مع عرض القيمة العامة والقيمة الفعلية المطبّقة حاليًا على هذا العميل تحديدًا.
- View جديد `client_update_min_order` (صلاحية `accounts.change_clientprofile`، نفس صلاحية `client_approve`/`client_reject`) — بيسجّل أي تعديل في `ActivityLog` (تعديل القيمة أو إلغاء التخصيص).
- Route جديد `staff:client_update_min_order` في `staff/urls.py`.

### 🧪 الفحص

- `python manage.py check` و`makemigrations --check --dry-run` نضاف — مفيش تغيير ناقص.
- **test suite الكامل شغّال فعليًا بعد `migrate` كامل لكل التطبيقات (بما فيها migrations مرحلة 5) — العدد الصحيح 153 اختبار (راجع التصحيح فوق في قسم مرحلة 5)، والنتيجة `OK`.**
- فحص منطقي للتمييز `None` مقابل `0`: الشرط `is not None` (مش truthy check) بيضمن إن عميل حدّد صفر كحد أدنى بيتفرق صح عن عميل من غير تخصيص خالص.

### ملفات اتلمست/اتضافت في مرحلة 6

```
accounts/models.py                                    (+ min_order_amount على ClientProfile)
accounts/migrations/0009_clientprofile_min_order_amount.py  (جديد)
orders/models.py                                       (+ get_effective_min_order_amount)
orders/views/cart.py                                    (استخدام القيمة الفعلية بدل SiteConfig مباشرة)
orders/views/checkout.py                                (نفس التعديل)
staff/views/clients.py                                  (+ client_update_min_order، + context في client_detail)
staff/urls.py                                            (+ route client_update_min_order)
staff/templates/staff/clients/detail.html               (+ فورم تعديل الحد الأدنى في تاب بيانات العميل)
```

## مرحلة 7 — أتمتة ومتابعة (Odoo-inspired)
**الحالة: ✅ مكتملة — آخر مرحلة في الخطة الأصلية**

### ✅ اتعمل

**1. متابعات مجدولة — App جديد `followups/`** (ContentType-based، بنفس فكرة `activity.ActivityLog`/`tags.Tag`):
- `followups/models.py` — موديل `FollowUp` واحد: `activity_type` (مكالمة/زيارة/متابعة سداد/أخرى)، `due_date`، `assigned_to` (الموظف المسؤول)، `done_at`/`done_by` (حالة الإنجاز)، `note` (تفاصيل مختصرة اختيارية)، `created_by`.
- **مقصود إنه موديل منفصل عن `activity.ActivityLog`:** `ActivityLog` سجل تاريخي لما *حصل بالفعل* (Audit + Chatter)، أما `FollowUp` مهمة *لسه هتحصل*، ليها تاريخ استحقاق وموظف مسؤول وحالة إنجاز — غرض مختلف تمامًا فمش اتلخبطوا في موديل واحد.
- `followups/services.py` — `create_followup()`, `mark_done()`, `followups_for()`, `open_followups_count_for()`, `delete_followups_for()` (الواجهة الوحيدة المفروض أي view يستخدمها).
- `followups/views.py` + `followups/urls.py` — endpoints عامة (`followups:followup_add`/`followup_done`/`followup_delete`) تشتغل على أي كيان (عميل حاليًا)، بنفس نمط `tags:tag_add`/`activity:add_note` بالظبط. صلاحية: أي موظف (أدمن/مخزن) مش عميل، والموظف المكلَّف بالمتابعة لازم يكون نشط (`status=ACTIVE`) — مينفعش تتكلّف عميل أو موظف لسه في الانتظار.
- `followups/templatetags/followup_tags.py` + `followups/templates/followups/_panel.html` — تاج `{% followup_panel obj %}` (فورم جدولة + قائمة مفتوحة أولًا فمنجزة، بشارات "متأخرة"/"مستحقة اليوم"/"قادمة"/"منجزة").
- **مطبّق فعليًا على تاب جديد "المتابعات" في صفحة تفاصيل العميل** (`staff/templates/staff/clients/detail.html`) — ده اللي بيحل مشكلة تتبّع `PENDING` والمتأخرين في السداد المذكورة في ROADMAP.md: بدل ما يفضل الموظف فاكر في دماغه "لازم أتصل بفلان الأسبوع الجاي"، بيجدول متابعة فعلية بتاريخ استحقاق وموظف مسؤول. أي كيان تاني يقدر ياخد نفس الميزة بسطرين بس (`{% load followup_tags %}{% followup_panel obj %}`) من غير أي migration جديدة.
- **صفحة "المتابعات" في لوحة الموظف** (`staff/views/followups.py::followup_list`, route `staff:followup_list`) — تجميع كل المتابعات (مش بس بتاعة عميل واحد) في مكان واحد، بفلاتر "متابعاتي/الكل" و"مفتوحة/متأخرة/منجزة/الكل"، مع رابط مباشر للعميل المرتبط بكل متابعة. الجدولة والإنجاز الفعليين بيحصلوا من فورم المتابعة على صفحة الكيان نفسه — الصفحة دي للمتابعة اليومية بس، مش فورم إدارة كامل.
- migration: `followups/migrations/0001_initial.py` (اتولّدت واتفحصت بـ `makemigrations --check` — **لسه محتاجة `migrate` فعلي على السيرفر الحقيقي**، نفس الخطوة المتكررة مع كل app جديد).
- مسجّلة في `INSTALLED_APPS` و`config/urls.py` (`path('followups/', include('followups.urls'))`).
- صلاحيات دقيقة جديدة في كتالوج `staff/permissions.py` (`PERMISSION_SECTIONS`): قسم `📅 المتابعات` (view/add/change/delete على `followups.followup`) — بتظهر تلقائيًا في شاشة صلاحيات الموظف. نقطة النهاية العامة (إضافة/إنجاز/إلغاء متابعة من صفحة العميل) بتتحقق من الدور (أدمن/مخزن) زي `tags`/`activity`، بينما صفحة القائمة (`followup_list`) وأزرار "تم الإنجاز"/"إلغاء" فيها بتتحقق من صلاحية Django الحقيقية (`followups.view_followup`/`change_followup`/`delete_followup`) — مستوى تحكم أدق من صفحات العميل الفردية.
- رابط جديد "المتابعات" في القائمة الجانبية (`staff/navigation.py`).

**2. مقترحات توريد تلقائي — صفحة موحّدة بدل تنبيه متفرق:**
- `inventory/models.py::Inventory.suggested_reorder_qty` (خاصية جديدة) — الفرق بين الحد الأدنى والمتاح فعليًا (`min_quantity - available`)، وصفر لو الصنف مش تحت الحد الأدنى أصلًا. حساب بسيط على حقول محمّلة بالفعل (بدون أي استعلام إضافي)، مش اقتراح ذكي أو تنبؤ بالطلب — البيانات كانت جاهزة أصلًا (`min_quantity`/`low_stock()` من مرحلة 3)، الشغل كله كان في العرض زي ما حدد ROADMAP.md بالظبط. مقابلها `suggested_reorder_display` لعرض نفس القيمة بالوحدة الكبرى.
- **صفحة جديدة `staff:reports_supply_suggestions`** (`staff/views/reports.py::supply_suggestions` + `staff/templates/staff/reports/supply_suggestions.html`) — تجميع كل الأصناف تحت `min_quantity` في صفحة واحدة، بفلتر قسم، وتصدير Excel (بنفس نمط `stagnant_products`/`build_simple_workbook` الموجود فعلًا)، وطباعة/PDF مباشرة من المتصفح.
- **استبدال الروابط المتفرقة القديمة** بدل التنبيه المبعثر: كارت "تنبيهات المخزون" في لوحة التحكم الرئيسية ورابط "عرض كل الأصناف المنخفضة" فيه، وكذلك بطاقة روابط صفحة التقارير، كلهم بقوا بيوجّهوا للصفحة الموحّدة الجديدة بدل فلتر `?low=1` القديم في صفحة المخزون العامة (الفلتر القديم نفسه فضل موجود كخيار عام داخل صفحة المخزون، مش اتشال — بس مبقاش هو المسار الأساسي للتنبيه).
- صلاحية الوصول: `inventory.view_inventory` (نفس صلاحية صفحة المخزون العادية، لأنها فعليًا بيانات مخزون).

### 🧪 الفحص

- `python manage.py check` و `makemigrations --check --dry-run` نضاف بالكامل — مفيش تغيير ناقص ولا تحذيرات جديدة.
- **test suite الكامل شغّال فعليًا** (مش نظريًا) على بيئة SQLite محلية (Postgres/Redis مش متاحين من بيئة التطوير الحالية، نفس القيد المتكرر من مرحلة 2) — العدد **174 اختبار** (كانوا 153 في مرحلة 6)، **21 اختبار جديد**، والنتيجة **`OK`** لكل الـ 174.
- `followups/tests.py` (11 اختبار): إنشاء متابعة وربطها بالكيان عن طريق ContentType، عزل متابعات كيان عن كيان تاني، تسجيل الإنجاز (`done_at`/`done_by`)، المتابعة المتأخرة (`is_overdue`) بترجع `False` بمجرد إنجازها حتى لو استحقاقها فات، العداد المفتوح بيتجاهل المنجزة، ورفض الـ view العام (فورم فاضي/تاريخ فاضي/تكليف عميل أو موظف غير نشط/عميل بيحاول يجدول متابعة لنفسه).
- `staff/tests_followups.py` (4 اختبارات): صفحة "المتابعات" — فلتر "متابعاتي" الافتراضي بيستبعد متابعات الموظفين التانيين، فلتر "الكل" بيرجّعهم كلهم، فلتر "منجزة" بيستبعد المفتوحة، ورفض دخول العميل للصفحة.
- `staff/tests_supply_suggestions.py` (6 اختبارات): حساب `suggested_reorder_qty` (صفر لو مش منخفض، الفرق الصحيح لو منخفض، الحساب بيعتمد على المتاح مش الرصيد الكلي لو فيه محجوز)، الصفحة بتعرض الأصناف المنخفضة بس، فلتر القسم بيشتغل صح، ورفض دخول العميل للصفحة.

### خارج نطاق مرحلة 7 عن قصد (مش نواقص)
- المتابعات المجدولة مطبّقة على **العميل بس** حاليًا (ده اللي معيار المشكلة في ROADMAP.md ركّز عليه: `PENDING` والمتأخرين في السداد) — الموديل عام (ContentType) وجاهز يتعمم على أي كيان تاني بسطرين بس لو احتجتوا ده بعدين (مثلًا: متابعة على طلب معلّق).
- مفيش أتمتة/تذكير تلقائي فعلي (إيميل/إشعار لحظي وقت استحقاق المتابعة) — الميزة المطلوبة في ROADMAP.md كانت "الجدولة والتتبّع" (بدل التتبع بالذاكرة)، مش إشعارات push. لو حبيتوا إشعار وقت استحقاق متابعة، ده امتداد طبيعي على `notifications/` الموجود، يستاهل طلب منفصل.
- مقترحات التوريد بتحسب "الفرق عن الحد الأدنى" بس (كمية بسيطة ترجّع الرصيد للحد الأدنى بالظبط) — مفيش توقّع طلب مستقبلي أو هامش أمان إضافي، لأن ROADMAP.md حدد صراحةً إن الشغل المطلوب كله في الـ view مش خوارزمية جديدة.
- فلتر `?low=1` القديم في صفحة المخزون العامة فضل موجود (مش اتشال) — مجرد مش هو المسار الرئيسي للتنبيه بقى.

### ملفات اتلمست/اتضافت في مرحلة 7

```
followups/                              (جديد بالكامل)
├── models.py, services.py, views.py, urls.py, admin.py, apps.py, tests.py
├── migrations/0001_initial.py
└── templatetags/followup_tags.py
    templates/followups/_panel.html
config/settings.py                      (+ 'followups' في INSTALLED_APPS)
config/urls.py                          (+ include('followups.urls'))
inventory/models.py                     (+ suggested_reorder_qty, suggested_reorder_display)
staff/views/followups.py                (جديد بالكامل — followup_list)
staff/views/reports.py                  (+ supply_suggestions, _export_supply_suggestions_excel)
staff/views/clients.py                  (+ open_followups_count في client_detail)
staff/urls.py                           (+ routes: followup_list, reports_supply_suggestions)
staff/navigation.py                     (+ رابط "المتابعات" في القائمة الجانبية)
staff/permissions.py                    (+ قسم "📅 المتابعات" في PERMISSION_SECTIONS)
staff/templates/staff/clients/detail.html          (+ تاب "المتابعات")
staff/templates/staff/dashboard.html               (روابط تنبيه المخزون → صفحة مقترحات التوريد)
staff/templates/staff/reports/dashboard.html       (+ رابط "مقترحات التوريد")
staff/templates/staff/followups/list.html          (جديد بالكامل)
staff/templates/staff/reports/supply_suggestions.html   (جديد بالكامل)
staff/tests_followups.py                (جديد بالكامل — 4 اختبارات)
staff/tests_supply_suggestions.py       (جديد بالكامل — 6 اختبارات)
```

---

## تعديلات بعد مرحلة 7 (خارج نطاق الخطة الأصلية)

### 30 يوليو 2026 — إزالة تعديل الكميات من صفحة الطلب + إزالة السعر من فاتورة المراجعة

طلب مباشر من العميل بعد اختبار مرحلة 6 و7 على السيستم:

1. **إزالة زرار "تعديل الكميات" من صفحة تفاصيل الطلب وهو "في الانتظار"** (`staff/templates/staff/orders/detail.html`):
   - اتشالت خانة إدخال الكمية (input) والزرار "حفظ تعديل الكميات وإرسال للعميل للموافقة" والنص التوضيحي المصاحب لها — الكمية بقت تتعرض دايمًا كنص للقراءة بس (`<span>`)، بغض النظر عن حالة الطلب.
   - الجدول بقى `<div>` عادي بدل `<form>` (مفيش حاجة تتبعت منه دلوقتي).
   - **الـ backend اتسيب زي ما هو عمدًا** (`staff/views/orders.py` — action `update_quantities`) — الطلب كان "شيل الزرار" مش "امسح الميزة بالكامل"، فالتعديل اتقصر على الواجهة بس، أقل تغيير ممكن وأكثر أمانًا (مفيش خطر يكسر أي حاجة تانية زي منطق `NEEDS_APPROVAL`/الموافقة من العميل اللي لسه مستخدم أماكن تانية). لو حبيتوا حذف منطق الـ backend نفسه كمان (View handling + جدول الـ amendment) في مرحلة تانية، ده قرار منفصل محتاج تأكيد إضافي لأنه بيأثر على حالة `NEEDS_APPROVAL` بالكامل.

2. **إزالة السعر/الإجمالي من "فاتورة المراجعة"** (`staff/templates/staff/orders/print.html` — نسخة المخزن المطبوعة للمراجعة اليدوية أثناء التحضير):
   - اتشال عمود "الإجمالي" من الجدول بالكامل.
   - اتشال سعر الوحدة القديمة من ملاحظة "كانت: ... × ... ج.م" عند الأصناف المعدَّلة — بقت "كانت: [الكمية القديمة]" بس.
   - اتشال بلوك "الإجمالي" في آخر صفحة طباعة بالكامل.
   - باقي بيانات الفاتورة (رقم الطلب، التاريخ، العميل، الكميات، مساحة توقيع المخزن) زي ما هي من غير أي تغيير.

**الفحص:** `python manage.py check` نظيف، وتشغيل test suite الكامل فعليًا بعد التعديلين — **174/174 نجحوا** (نفس العدد، مفيش اختبار اتكسر ولا احتاج تعديل، لأن التغيير كله واجهة/عرض بدون أي تغيير في منطق الـ backend أو الموديلات).

---

## ملاحظة لأي محادثة جديدة مع Claude

ابدأ بقراءة الجدول في أول الملف ده. **كل المراحل من 1 لـ 7 خلصت بالكامل** — دي كانت آخر مرحلة في `ROADMAP.md` الأصلي. لو فيه طلبات جديدة، هتكون خارج نطاق الخطة الأصلية (زي "باج @click.outside" و"رابط الفاتورة" بعد مرحلة 4) — وثّقها بنفس الأسلوب (قسم منفصل بتاريخه، مش مدمجة جوه مرحلة قديمة). الـ migrations الوحيدة اللي لسه محتاجة تتطبّق فعليًا على السيرفر الحقيقي (لو لسه ماتمّتش): `accounts/0009` (مرحلة 6) و`followups/0001_initial` (مرحلة 7) — راجع ملفات `APPLY_INSTRUCTIONS_phase*.md` المرفقة لخطوات كل واحدة.
