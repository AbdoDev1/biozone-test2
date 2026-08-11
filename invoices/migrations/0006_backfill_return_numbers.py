from collections import defaultdict

from django.db import migrations


def backfill_return_numbers(apps, schema_editor):
    """
    الإشعارات (InvoiceReversal) اللي اتنشأت قبل هذه الـ migration (المسار
    القديم PRE_DELIVERY بس، قبل ما return_number يتضاف) مالهاش رقم إشعار —
    هنا بنولّده لها بنفس منطق InvoiceReversal.save() بالظبط (تسلسل داخل
    نفس الفاتورة بترتيب created_at)، عشان تتعرض صح فورًا في القوائم
    (display_reference) من غير أي حركة يدوية بعد الـ deploy.
    """
    InvoiceReversal = apps.get_model('invoices', 'InvoiceReversal')

    per_invoice = defaultdict(list)
    for reversal in InvoiceReversal.objects.filter(return_number='').order_by('created_at', 'pk'):
        per_invoice[reversal.invoice_id].append(reversal)

    for invoice_id, reversals in per_invoice.items():
        invoice = reversals[0].invoice
        suffix = invoice.invoice_number.split('-', 1)[1]
        # عدد الإشعارات اللي كان عندها رقم بالفعل لنفس الفاتورة (متوقع صفر
        # عمليًا وقت أول تشغيل لهذه الـ migration، بس محسوبة للأمان لو
        # اتنفذت مرتين أو فيه بيانات جزئية).
        already_numbered = InvoiceReversal.objects.filter(invoice_id=invoice_id).exclude(return_number='').count()
        for offset, reversal in enumerate(reversals, start=1):
            reversal.return_number = f'RTN-{suffix}-{already_numbered + offset:02d}'
            reversal.save(update_fields=['return_number'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('invoices', '0005_alter_invoicereversal_options_invoiceitem_order_item_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_return_numbers, noop_reverse),
    ]
