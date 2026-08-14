from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('studio', '0003_rename_studio_stud_folder__2b6a19_idx_studio_stud_folder__f5144d_idx'),
    ]

    operations = [
        migrations.CreateModel(
            name='LandingPageSettings',
            fields=[
                ('id', models.PositiveSmallIntegerField(primary_key=True, serialize=False, default=1, editable=False)),
                ('banner_1_link', models.CharField(blank=True, max_length=500, verbose_name='رابط البانر الأول')),
                ('banner_2_link', models.CharField(blank=True, max_length=500, verbose_name='رابط البانر الثاني')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='آخر تحديث')),
                ('hero_image', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='landing_hero_settings', to='studio.studioimage', verbose_name='صورة الـ Hero')),
                ('banner_1', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='landing_banner_1_settings', to='studio.studioimage', verbose_name='البانر الأول')),
                ('banner_2', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='landing_banner_2_settings', to='studio.studioimage', verbose_name='البانر الثاني')),
            ],
            options={
                'verbose_name': 'إعدادات الصفحة الرئيسية',
                'verbose_name_plural': 'إعدادات الصفحة الرئيسية',
            },
        ),
    ]
