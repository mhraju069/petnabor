from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def record_stripe_account(apps, schema_editor):
    """Existing withdrawals: store their Connect account's id."""
    TipWithdrawal = apps.get_model("tip", "TipWithdrawal")
    for withdrawal in TipWithdrawal.objects.filter(stripe_account_id="").select_related("connect_account"):
        if withdrawal.connect_account_id:
            withdrawal.stripe_account_id = withdrawal.connect_account.stripe_account_id
            withdrawal.save(update_fields=["stripe_account_id"])


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tip", "0005_account_switch"),
    ]

    operations = [
        migrations.AddField(
            model_name="tipwithdrawal",
            name="stripe_account_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="Stripe Connect account the payout was made from (acct_xxx), as it was at the time.",
                max_length=255,
            ),
        ),
        migrations.RunPython(record_stripe_account, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="tipwithdrawal",
            name="user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="tip_withdrawals",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="tipwithdrawal",
            name="connect_account",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="withdrawals",
                to="tip.stripeconnectaccount",
            ),
        ),
    ]
