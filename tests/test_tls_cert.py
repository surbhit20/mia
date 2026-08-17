from mia.audio.tls_cert import ensure_self_signed_cert


def test_ensure_self_signed_cert_creates_cert_and_key(tmp_path):
    cert_path, key_path = ensure_self_signed_cert(tmp_path)

    assert cert_path == tmp_path / "cert.pem"
    assert key_path == tmp_path / "key.pem"
    assert cert_path.exists()
    assert key_path.exists()
    assert cert_path.read_text().startswith("-----BEGIN CERTIFICATE-----")
    assert key_path.read_text().startswith("-----BEGIN PRIVATE KEY-----")


def test_ensure_self_signed_cert_reuses_existing_files(tmp_path):
    first_cert, first_key = ensure_self_signed_cert(tmp_path)
    first_cert_content = first_cert.read_text()
    first_key_content = first_key.read_text()

    second_cert, second_key = ensure_self_signed_cert(tmp_path)

    assert second_cert == first_cert
    assert second_key == first_key
    assert second_cert.read_text() == first_cert_content
    assert second_key.read_text() == first_key_content
