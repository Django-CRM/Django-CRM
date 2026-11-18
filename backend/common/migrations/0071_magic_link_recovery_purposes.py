from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("common", "0070_magic_link_token_purpose")]
    operations = [
        migrations.AlterField(
            model_name="magiclinktoken",
            name="purpose",
            field=models.CharField(
                choices=[
                    ("passwordless", "Passwordless sign-in"),
                    ("password_login", "Password login verification"),
                    ("password_reset", "Password reset verification"),
                    ("recovery_login", "Recovery email sign-in"),
                ],
                default="passwordless",
                max_length=20,
            ),
        ),
    ]
