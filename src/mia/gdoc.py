"""Create the meeting summary as a native Google Doc."""

from googleapiclient.http import MediaInMemoryUpload

# Asking Drive to store HTML under this mimeType makes it convert the upload
# into a real Doc. The alternative -- the Docs API's create-then-batchUpdate
# -- would mean translating the summary into styled text runs by hand.
_GOOGLE_DOC_MIMETYPE = "application/vnd.google-apps.document"


def create_doc(drive_service, title: str, html_body: str) -> str:
    """Create a Doc from an HTML body and return its shareable URL.

    Created private to the user: the drive.file scope grants access only to
    files mia creates, and nothing here shares the result.
    """
    media = MediaInMemoryUpload(
        html_body.encode("utf-8"), mimetype="text/html", resumable=False
    )
    created = drive_service.files().create(
        body={"name": title, "mimeType": _GOOGLE_DOC_MIMETYPE},
        media_body=media,
        fields="id,webViewLink",
    ).execute()

    # Derive the URL from the id when Drive omits webViewLink. Reading the key
    # directly would raise on a request that actually SUCCEEDED, and the caller
    # treats any exception here as "Drive failed" -- so a real document would
    # be reported as an error and duplicated into the local fallback. Doc URLs
    # are deterministic from the id, and id is always returned.
    if created.get("webViewLink"):
        return created["webViewLink"]
    return f"https://docs.google.com/document/d/{created['id']}/edit"
