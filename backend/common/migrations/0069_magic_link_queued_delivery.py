# Generated for encrypted OTP delivery via QStash.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0068_profile_is_app"),
    ]

    operations = [
        migrations.AddField(
            model_name="magiclinktoken",
            name="encrypted_code",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
        migrations.AddField(
            model_name="magiclinktoken",
            name="email_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
