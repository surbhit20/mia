import base64
from email import message_from_bytes
from unittest.mock import MagicMock

from mia.mailer import email_summary


def _service(address="me@example.com", sent_id="msg_1"):
    service = MagicMock()
    service.users.return_value.getProfile.return_value.execute.return_value = {
        "emailAddress": address
    }
    service.users.return_value.messages.return_value.send.return_value.execute.return_value = {
        "id": sent_id
    }
    return service


def _sent_message(service):
    raw = service.users.return_value.messages.return_value.send.call_args.kwargs["body"]["raw"]
    return message_from_bytes(base64.urlsafe_b64decode(raw))


def test_sends_to_the_authenticated_user_only():
    # The recipient is looked up, never configured, so a bad setting cannot
    # mail someone else's meeting to a stranger.
    service = _service(address="surbhit@example.com")

    email_summary(service, "Summary", b"pdf-bytes", "summary.pdf", "application/pdf")

    message = _sent_message(service)
    assert message["To"] == "surbhit@example.com"
    assert message["From"] == "surbhit@example.com"


def test_uses_the_given_subject():
    service = _service()

    email_summary(service, "Summary from today's 9am meeting", b"x", "s.pdf", "application/pdf")

    assert _sent_message(service)["Subject"] == "Summary from today's 9am meeting"


def test_attaches_the_document():
    service = _service()

    email_summary(service, "Summary", b"%PDF-1.4 fake", "Weekly sync.pdf", "application/pdf")

    parts = list(_sent_message(service).walk())
    attachments = [p for p in parts if p.get_filename()]
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "Weekly sync.pdf"
    assert attachments[0].get_content_type() == "application/pdf"
    assert attachments[0].get_payload(decode=True) == b"%PDF-1.4 fake"


def test_encodes_as_base64url_not_plain_base64():
    # Gmail rejects standard base64. The failure is silent enough that only an
    # explicit check catches it.
    service = _service()

    email_summary(service, "Summary", b"\xfb\xff" * 40, "s.pdf", "application/pdf")

    raw = service.users.return_value.messages.return_value.send.call_args.kwargs["body"]["raw"]
    assert "+" not in raw and "/" not in raw
    base64.urlsafe_b64decode(raw)  # must not raise


def test_returns_the_sent_message_id():
    service = _service(sent_id="msg_42")

    assert email_summary(service, "S", b"x", "s.pdf", "application/pdf") == "msg_42"


def test_html_attachments_are_supported_for_the_drive_failure_path():
    service = _service()

    email_summary(service, "S", b"<h1>hi</h1>", "summary.html", "text/html")

    attachments = [p for p in _sent_message(service).walk() if p.get_filename()]
    assert attachments[0].get_content_type() == "text/html"
