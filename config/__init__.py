# استيراد تطبيق Celery هنا يضمن إنه يتحمّل تلقائيًا لما Django يبدأ —
# راجع config/celery.py للتفاصيل.
from .celery import app as celery_app

__all__ = ('celery_app',)
