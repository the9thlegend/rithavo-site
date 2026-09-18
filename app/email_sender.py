"""
Phase 2B-1.6 — outbound email backend for the rithavo.com magic-link flow.

Same two-backend shape as db.py's SQLite/Postgres split and
photo_storage.py's Local/Supabase split in the sibling project: a
zero-config default used in every test and local dev run
(ConsoleEmailSender — never touches the network, just records what would
have been sent), and a real backend used only when its own credentials
are configured (ZohoSMTPEmailSender).

This deliberately mirrors rithavo-career-profile/app/email_sender.py's
already-proven ZohoSMTPEmailSender/get_email_sender() pattern rather than
importing it — the two services never depend on each other at runtime
(see config.py's own rationale for the same rule on DATABASE_URL/
SESSION_SECRET). It also deliberately reuses the *same* Zoho relay and
sender mailbox (hello@rithavo.com) rather than introducing a new
third-party transactional-email vendor: rithavo.com's Zoho MX/SPF are
already live and already proven to deliver this exact kind of email in
production for the sibling app, so this is the smallest-safe-change
option — it requires zero new DNS records and no new vendor relationship.
Its own env var names are still distinct from the sibling's
(RITHAVO_WEB_ZOHO_SMTP_* vs ZOHO_SMTP_*) so the two services' configs can
never be confused with each other even if both are ever set in the same
shell — same rule config.py already applies to every other credential
this service reads.

Selected once at app startup (app.state.email_sender), the same lifecycle
as app.state.db/app.state.payment_gateway — not re-selected per request.
"""

import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr


class ConsoleEmailSender:
    """Default backend. Records every send in self.sent (tests assert
    against this directly) and also prints it, so a developer running the
    app locally without Zoho configured can still complete the sign-in
    flow by reading the link straight off the server's own console."""

    def __init__(self):
        self.sent = []  # [{"to": str, "subject": str, "body": str}, ...]

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append({"to": to, "subject": subject, "body": body})
        print(f"\n--- Rithavo Web: email would be sent (no SMTP configured) ---\nTo: {to}\nSubject: {subject}\n{body}\n---\n")


class ZohoSMTPEmailSender:
    """Real delivery via Zoho Mail SMTP, using hello@rithavo.com — the
    same authenticated mailbox and relay already used in production by
    the sibling app, reused here rather than duplicated as a second Zoho
    mailbox (which would need to be created by hand in Zoho's own admin
    console, outside what this phase touches)."""

    def __init__(self, host: str, port: int, username: str, password: str):
        self.host = host
        self.port = port
        self.username = username
        self.password = password

    def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        # Display name only -- the underlying mailbox/address (self.username)
        # is unchanged. Recipients previously saw the bare address (which
        # rendered as "hello" in some clients) because no display name was
        # ever set.
        msg["From"] = formataddr(("Rithavo", self.username))
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=15) as server:
            server.starttls()
            server.login(self.username, self.password)
            server.send_message(msg)


def get_email_sender():
    host = os.environ.get("RITHAVO_WEB_ZOHO_SMTP_HOST")
    username = os.environ.get("RITHAVO_WEB_ZOHO_SMTP_USERNAME")
    password = os.environ.get("RITHAVO_WEB_ZOHO_SMTP_PASSWORD")
    port = int(os.environ.get("RITHAVO_WEB_ZOHO_SMTP_PORT", "587"))
    if host and username and password:
        return ZohoSMTPEmailSender(host, port, username, password)
    return ConsoleEmailSender()
