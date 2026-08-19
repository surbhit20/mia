from unittest.mock import MagicMock, patch

from mia.gdoc import create_doc


def _service(link="https://docs.google.com/document/d/abc123/edit"):
    service = MagicMock()
    service.files.return_value.create.return_value.execute.return_value = {
        "id": "abc123",
        "webViewLink": link,
    }
    return service


@patch("mia.gdoc.MediaInMemoryUpload")
def test_returns_the_doc_url(mock_upload):
    service = _service()

    url = create_doc(service, "Budget sync", "<h1>Budget sync</h1>")

    assert url == "https://docs.google.com/document/d/abc123/edit"


@patch("mia.gdoc.MediaInMemoryUpload")
def test_requests_conversion_to_a_native_google_doc(mock_upload):
    # Without this mimeType Drive stores a raw .html file instead of a Doc.
    service = _service()

    create_doc(service, "Budget sync", "<h1>Budget sync</h1>")

    kwargs = service.files.return_value.create.call_args.kwargs
    assert kwargs["body"] == {
        "name": "Budget sync",
        "mimeType": "application/vnd.google-apps.document",
    }
    assert kwargs["fields"] == "id,webViewLink"


@patch("mia.gdoc.MediaInMemoryUpload")
def test_uploads_the_html_body_as_html(mock_upload):
    service = _service()

    create_doc(service, "Budget sync", "<h1>Budget sync</h1>")

    mock_upload.assert_called_once_with(b"<h1>Budget sync</h1>", mimetype="text/html", resumable=False)


@patch("mia.gdoc.MediaInMemoryUpload")
def test_builds_the_url_from_the_id_when_drive_omits_the_link(mock_upload):
    # The document was created successfully; only the link field is missing.
    # Raising here would make the caller log a Drive failure and write a
    # duplicate local copy of a summary that already exists in Drive.
    service = MagicMock()
    service.files.return_value.create.return_value.execute.return_value = {"id": "abc123"}

    url = create_doc(service, "Budget sync", "<h1>Budget sync</h1>")

    assert url == "https://docs.google.com/document/d/abc123/edit"
