"""
Targeted coverage for the email sender display-name fix: the From header
must carry a "Rithavo" display name alongside the unchanged
hello@rithavo.com mailbox, never a bare address.
"""

from unittest.mock import MagicMock, patch

from app.email_sender import ZohoSMTPEmailSender


def test_zoho_sender_from_header_has_rithavo_display_name():
    sender = ZohoSMTPEmailSender(
        host="smtp.zoho.com", port=587, username="hello@rithavo.com", password="fake-test-password-not-real",
    )
    with patch("app.email_sender.smtplib.SMTP") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_server
        sender.send(to="customer@example.com", subject="Your Rithavo sign-in link", body="Click here")

        sent_msg = mock_server.send_message.call_args[0][0]
        assert sent_msg["From"] == "Rithavo <hello@rithavo.com>"
        assert sent_msg["To"] == "customer@example.com"
        assert sent_msg["Subject"] == "Your Rithavo sign-in link"
