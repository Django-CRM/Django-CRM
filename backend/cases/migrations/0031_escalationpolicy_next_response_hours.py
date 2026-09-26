import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cases", "0030_escalationpolicy_first_response_hours_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="escalationpolicy",
            name="next_response_hours",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Target hours for each reply after the first. Blank uses the built-in default.",
                null=True,
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(8760),
                ],
            ),
        ),
    ]
