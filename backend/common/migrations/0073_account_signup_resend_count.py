from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("common", "0072_account_signup_challenge")]

    operations = [
        migrations.AddField(
            model_name="accountsignupchallenge",
            name="resend_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
