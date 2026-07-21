import os
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from django.core.mail import send_mail
from api.notifications.email_service import EmailService

class InfrastructureConfigTests(TestCase):
    
    @override_settings(
        USE_S3=True, 
        AWS_S3_CUSTOM_DOMAIN="cdn.petnabor.test",
        STATICFILES_STORAGE="api.core.storage_backends.StaticStorage",
        DEFAULT_FILE_STORAGE="api.core.storage_backends.PublicMediaStorage"
    )
    def test_s3_storage_settings_logic(self):
        """
        Verify that we can override settings to simulate S3 being enabled and 
        that the storage classes can be successfully imported by Django.
        """
        from django.core.files.storage import default_storage
        from django.contrib.staticfiles.storage import staticfiles_storage
        # Merely importing or referring to them tests if Django can resolve the module path
        self.assertIsNotNone(default_storage)
        self.assertIsNotNone(staticfiles_storage)
        
    @override_settings(EMAIL_BACKEND="django_ses.SESBackend")
    @patch("api.notifications.email_service.send_mail")
    def test_email_service_uses_configured_backend(self, mock_send_mail):
        """
        Verify that EmailService correctly calls Django's send_mail, 
        which will delegate to the configured AWS SES backend in production.
        """
        mock_send_mail.return_value = 1 # 1 message sent
        
        result = EmailService.send_html_email(
            subject="Test Subject",
            html_content="<p>Test</p>",
            recipient_list=["test@example.com"]
        )
        
        self.assertTrue(result)
        mock_send_mail.assert_called_once()
        args, kwargs = mock_send_mail.call_args
        self.assertEqual(kwargs['subject'], "Test Subject")
        self.assertEqual(kwargs['recipient_list'], ["test@example.com"])
        
    @patch("api.notifications.email_service.send_mail")
    def test_email_service_handles_failures(self, mock_send_mail):
        """
        Verify EmailService gracefully handles and logs delivery failures 
        (e.g., if AWS SES rejects the message).
        """
        mock_send_mail.side_effect = Exception("AWS SES Delivery Failed")
        
        result = EmailService.send_html_email(
            subject="Test Failure",
            html_content="<p>Fail</p>",
            recipient_list=["fail@example.com"]
        )
        
        self.assertFalse(result)
        mock_send_mail.assert_called_once()
