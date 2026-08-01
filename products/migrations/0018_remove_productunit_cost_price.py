from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0017_extract_size_from_name'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='productunit',
            name='cost_price',
        ),
    ]
