import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("common", "0071_magic_link_recovery_purposes")]

    operations = [
        migrations.CreateModel(
            name="AccountSignupChallenge",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("email", models.EmailField(db_index=True, max_length=254)),
                ("first_name", models.CharField(max_length=100)),
                ("last_name", models.CharField(max_length=100)),
                ("phone", models.CharField(max_length=25)),
                ("country", models.CharField(max_length=2)),
                ("organization_name", models.CharField(max_length=100)),
                ("password_hash", models.CharField(max_length=256)),
                ("code_hash", models.CharField(max_length=256)),
                ("terms_version", models.CharField(max_length=32)),
                ("privacy_version", models.CharField(max_length=32)),
                ("terms_accepted_at", models.DateTimeField()),
                ("privacy_accepted_at", models.DateTimeField()),
                ("marketing_consent", models.BooleanField(default=False)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                ("is_used", models.BooleanField(default=False)),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
            ],
            options={
                "db_table": "account_signup_challenge",
                "ordering": ("-created_at",),
                "indexes": [models.Index(fields=["email", "created_at"], name="signup_email_created_idx")],
            },
        ),
    ]
