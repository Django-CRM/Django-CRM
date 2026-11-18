from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("common", "0073_account_signup_resend_count")]

    operations = [
        migrations.AddField("org", "industry", models.CharField(blank=True, default="", max_length=100)),
        migrations.AddField("org", "employee_count", models.CharField(blank=True, default="", max_length=20)),
        migrations.AddField("org", "description", models.TextField(blank=True, default="")),
        migrations.AddField("profile", "job_title", models.CharField(blank=True, default="", max_length=100)),
        migrations.AddField("accountsignupchallenge", "job_title", models.CharField(blank=True, default="", max_length=100)),
        migrations.AddField("accountsignupchallenge", "industry", models.CharField(blank=True, default="", max_length=100)),
        migrations.AddField("accountsignupchallenge", "employee_count", models.CharField(blank=True, default="", max_length=20)),
        migrations.AddField("accountsignupchallenge", "website", models.URLField(blank=True, default="")),
        migrations.AddField("accountsignupchallenge", "business_description", models.TextField(blank=True, default="")),
    ]
