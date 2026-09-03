"""Phase 0: console-only. No SMTP integration exists yet in this service —
deliberately, so there is no possibility of a test run sending a real
email. A real sender is a later-phase decision, made explicitly, not a
byproduct of Phase 0 plumbing."""


class ConsoleEmailSender:
    def __init__(self):
        self.sent = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append({"to": to, "subject": subject, "body": body})
        print(f"\n--- Rithavo Web: email would be sent (no SMTP configured) ---\nTo: {to}\nSubject: {subject}\n{body}\n---\n")
