"""Deliver the meeting summary to the user's own inbox.

Sends only to the authenticated account -- never to other attendees, who did
not agree to have their words forwarded from someone else's mailbox.
"""

import base64
from email.message import EmailMessage


def _encode(message: EmailMessage) -> str:
    # Gmail requires base64url, not plain base64. Standard base64 is accepted
    # by the client library and then silently rejected or mangled by the API,
    # so this is an easy thing to get wrong and not notice.
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def email_summary(
    gmail_service,
    subject: str,
    attachment_bytes: bytes,
    attachment_name: str,
    attachment_mimetype: str,
    body_text: str = "",
) -> str:
    """Mail the summary to the authenticated user, as an attachment.

    Returns the sent message's id. The recipient is looked up rather than
    configured, so there is no address to keep in sync and no way for a
    misconfiguration to send someone else's meeting to a stranger.
    """
    address = gmail_service.users().getProfile(userId="me").execute()["emailAddress"]

    message = EmailMessage()
    message["To"] = address
    message["From"] = address
    message["Subject"] = subject
    message.set_content(body_text or "Summary attached.")

    maintype, _, subtype = attachment_mimetype.partition("/")
    message.add_attachment(
        attachment_bytes, maintype=maintype, subtype=subtype, filename=attachment_name
    )

    sent = (
        gmail_service.users()
        .messages()
        .send(userId="me", body={"raw": _encode(message)})
        .execute()
    )
    return sent["id"]
