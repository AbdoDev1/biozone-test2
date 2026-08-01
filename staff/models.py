from django.db import models


class ReportsAccess(models.Model):
    """
    موديل بدون جدول بيانات فعلي (default_permissions فاضية ومفيش أي Field
    غير id) — الهدف الوحيد منه إنه يوفر صلاحية Django حقيقية 'staff.view_reports'
    نربطها بقسم التقارير (staff/views/reports.py)، بنفس أسلوب باقي الأقسام في
    permissions.py (PERMISSION_SECTIONS) اللي كلها مبنية على صلاحيات موديلات حقيقية.
    """
    class Meta:
        managed = True
        default_permissions = ()
        permissions = [
            ('view_reports', 'عرض قسم التقارير والتحليلات'),
        ]


class SystemBackupAccess(models.Model):
    """
    موديل بدون جدول بيانات فعلي (نفس أسلوب ReportsAccess فوق) — الهدف
    الوحيد منه إنه يوفر صلاحية Django حقيقية 'staff.manage_backup' نربطها
    بصفحة النسخ الاحتياطي اليدوي (staff/views/backup.py). مقصودة عمدًا
    كصلاحية منفصلة يمنحها الأدمن لمن يشاء (مش admin_required صريح) عشان
    تفضل مرنة زي باقي أقسام PERMISSION_SECTIONS، رغم إنها حساسة.
    """
    class Meta:
        managed = True
        default_permissions = ()
        permissions = [
            ('manage_backup', 'تشغيل نسخة احتياطية يدويًا وعرض تفاصيل الأخطاء'),
        ]
