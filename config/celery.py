"""
تهيئة Celery — تنفيذ العمليات الثقيلة (حاليًا: استيراد الإكسل، ولاحقًا
تصدير/نسخ احتياطي لو احتجنا) في worker منفصل تمامًا عن Gunicorn، عشان
طلب HTTP يرجع فورًا من غير ما ياخد worker كامل لمدة طويلة — ده اللي كان
سبب 504 من nginx مع ملف استيراد 3000 صف (راجع تقرير اختبار المرحلة 0).

نفس Redis المستخدم أصلاً كـ CACHES/CHANNEL_LAYERS (راجع settings.py)
بيتستخدم هنا كـ broker + result backend على قاعدة بيانات منفصلة، من غير
حاجة لخدمة إضافية غير Redis نفسه.
"""
import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('biozone')
# namespace='CELERY' يعني أي إعداد في settings.py اسمه بادئ بـ CELERY_
# (زي CELERY_BROKER_URL) بيتقرا تلقائيًا هنا من غير تكرار.
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
