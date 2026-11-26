import pytest

from app.email_sender import (
    ConsoleEmailSender,
    EmailNotConfigured,
    UnconfiguredEmailSender,
    ZohoSMTPEmailSender,
    get_email_sender,
)

_SMTP = ("RITHAVO_WEB_ZOHO_SMTP_HOST", "RITHAVO_WEB_ZOHO_SMTP_USERNAME", "RITHAVO_WEB_ZOHO_SMTP_PASSWORD")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in _SMTP + ("VERCEL", "RITHAVO_REQUIRE_EMAIL"):
        monkeypatch.delenv(key, raising=False)


def test_local_and_tests_keep_the_console_sender():
    assert isinstance(get_email_sender(), ConsoleEmailSender)


def test_deployed_without_smtp_fails_instead_of_pretending(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    sender = get_email_sender()
    assert isinstance(sender, UnconfiguredEmailSender)
    with pytest.raises(EmailNotConfigured):
        sender.send("a@example.com", "s", "b")


def test_require_email_flag_has_the_same_effect(monkeypatch):
    monkeypatch.setenv("RITHAVO_REQUIRE_EMAIL", "1")
    assert isinstance(get_email_sender(), UnconfiguredEmailSender)


def test_smtp_settings_win_everywhere(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv(_SMTP[0], "smtp.example.com")
    monkeypatch.setenv(_SMTP[1], "u@example.com")
    monkeypatch.setenv(_SMTP[2], "x")
    assert isinstance(get_email_sender(), ZohoSMTPEmailSender)


def test_auth_start_reports_not_configured(app_and_client, monkeypatch):
    app, client = app_and_client

    class _Down:
        def send(self, to, subject, body):
            raise EmailNotConfigured("none")

    monkeypatch.setattr(app.state, "email_sender", _Down())
    resp = client.post("/auth/start", data={"email": "someone@example.com"})
    assert resp.status_code == 503
    assert "not set up" in resp.json()["detail"]
