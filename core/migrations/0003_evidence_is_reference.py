# Generated manually
from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_evidence_tags'),
    ]

    operations = [
        migrations.AddField(
            model_name='evidence',
            name='is_reference',
            field=models.BooleanField(default=False, verbose_name='Еталон'),
        ),
    ]
