from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0011_fcmdevice_custom_notifications"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notifications",
            name="notification_type",
            field=models.CharField(choices=[('system', 'System Update'), ('login', 'New Login Detected'), ('security', 'Security Alert'), ('friend_request', 'New Friend Request'), ('friend_accept', 'Friend Request Accepted'), ('post_like', 'New Like on Post'), ('story_like', 'New Like on Story'), ('post_comment', 'New Comment on Post'), ('story_comment', 'New Reply on Story'), ('post_mention', 'Mentioned in Post'), ('story_mention', 'Mentioned in Story'), ('otp', 'OTP'), ('password_reset', 'Password Reset'), ('message', 'New Message'), ('pet_reminder', 'Pet Care Reminder'), ('pet_match', 'Potential Pet Playmate Found'), ('vet_appointment', 'Vet Appointment Update'), ('meetup_invite', 'Meetup Invitation'), ('meetup_update', 'Meetup Details Changed'), ('event_reminder', 'Upcoming Pet Event'), ('order_status', 'Order Progress Update'), ('product_interest', 'Interest in Your Product'), ('service_booking', 'Service Booking Confirmed'), ('reward', 'Reward Available'), ('streak_bonus', 'Daily Streak Bonus'), ('referral_bonus', 'Referral Bonus Earned'), ('promotion', 'Special Promotion'), ('marketing', 'Marketing Message'), ('info', 'General Information'), ('membership_expiry', 'Membership Expiry Alert'), ('low_balance', 'Low Balance Alert'), ('new_order', 'New Order Notification'), ('weekly_report', 'Weekly Performance Report'), ('tip_received', 'Tip Received'), ('tip_sent', 'Tip Sent'), ('tip_available', 'Tips Ready to Withdraw'), ('tip_enable_request', 'Enable Tip Service')], default='info', max_length=20),
        ),
    ]
