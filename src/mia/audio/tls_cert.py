import subprocess
from pathlib import Path


def ensure_self_signed_cert(cert_dir: Path, common_name: str = "host.docker.internal") -> tuple[Path, Path]:
    """Returns (cert_path, key_path) for a self-signed TLS cert AttendeeAudioBridge
    can serve. Attendee's API rejects any websocket_settings.audio.url that
    doesn't start with wss://, so the bridge must terminate TLS -- generates a
    new 10-year cert/key pair via openssl on first use, reusing it on later
    calls rather than regenerating every run."""
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_path = cert_dir / "cert.pem"
    key_path = cert_dir / "key.pem"
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key_path), "-out", str(cert_path),
            "-days", "3650", "-nodes",
            "-subj", f"/CN={common_name}",
        ],
        check=True,
        capture_output=True,
    )
    return cert_path, key_path
