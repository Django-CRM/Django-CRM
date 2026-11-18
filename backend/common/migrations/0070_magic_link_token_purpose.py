from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0069_magic_link_queued_delivery"),
    ]

    operations = [
        migrations.AddField(
            model_name="magiclinktoken",
            name="purpose",
            field=models.CharField(
                choices=[
                    ("passwordless", "Passwordless sign-in"),
                    ("password_login", "Password login verification"),
                ],
                default="passwordless",
                max_length=20,
            ),
        ),
    ]
