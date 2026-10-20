from decimal import Decimal

from django.db import migrations, models


def raise_old_default_minimum(apps, schema_editor):
    # The old default ($1) loses money on Stripe fees; only change it if the
    # admin never customised it.
    TipSettings = apps.get_model("tip", "TipSettings")
    TipSettings.objects.filter(minimum_tip_amount=Decimal("1.00")).update(minimum_tip_amount=Decimal("3.00"))


class Migration(migrations.Migration):

    dependencies = [
        ("tip", "0002_add_held_status_and_transfer_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="stripeconnectaccount",
            name="needs_reconnect",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Stripe says this account no longer exists or isn't accessible. Kept "
                    "(never deleted) so the link to the user's money isn't lost; the user "
                    "re-onboards to replace it."
                ),
            ),
        ),
        migrations.AddField(
            model_name="tip",
            name="stripe_dispute_id",
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="tip",
            name="status_before_dispute",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="tip",
            name="funds_reversed",
            field=models.BooleanField(
                default=False,
                help_text="The recipient's share was pulled back from their account during a dispute.",
            ),
        ),
        migrations.AlterField(
            model_name="tip",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("held", "Held — Awaiting Recipient Account"),
                    ("succeeded", "Succeeded"),
                    ("failed", "Failed"),
                    ("refunded", "Refunded"),
                    ("cancelled", "Cancelled"),
                    ("disputed", "Disputed (chargeback)"),
                ],
                db_index=True,
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="tipsettings",
            name="minimum_tip_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("3.00"),
                help_text=(
                    "Minimum tip amount in USD. Stripe's card fee (2.9% + $0.30) is paid "
                    "by the platform, so with a 20% commission tips under ~$1.76 lose money."
                ),
                max_digits=8,
            ),
        ),
        migrations.RunPython(raise_old_default_minimum, migrations.RunPython.noop),
    ]
