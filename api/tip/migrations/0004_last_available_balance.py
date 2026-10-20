from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tip", "0003_payments_safety"),
    ]

    operations = [
        migrations.AddField(
            model_name="stripeconnectaccount",
            name="last_available_balance",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text=(
                    "USD available on Stripe as we last saw it. A balance.available "
                    "event above this means pending tips cleared: we notify the user."
                ),
                max_digits=10,
            ),
        ),
    ]
