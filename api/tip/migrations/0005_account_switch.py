from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tip", "0004_last_available_balance"),
    ]

    operations = [
        migrations.AddField(
            model_name="stripeconnectaccount",
            name="pending_stripe_account_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="New Stripe account being set up to replace the current one.",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="stripeconnectaccount",
            name="switch_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="stripeconnectaccount",
            name="replaced_account_ids",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Stripe accounts this user used before (newest last), for support.",
            ),
        ),
    ]
