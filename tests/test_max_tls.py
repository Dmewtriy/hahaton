import ssl
from pathlib import Path

import httpx

from korsik.max_client import MAXClient


def test_custom_max_root_preserves_default_ca_store_and_tls_validation(runtime):
    certificate = Path(__file__).resolve().parents[1] / "certs" / "russian_trusted_root_ca.pem"
    settings = runtime.settings.model_copy(update={"max_ca_file": str(certificate)})
    default_roots = set(ssl.create_default_context().get_ca_certs(binary_form=True))
    client = MAXClient(
        settings, client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    )
    try:
        roots = set(client.verify.get_ca_certs(binary_form=True))
        assert default_roots <= roots
        assert ssl.PEM_cert_to_DER_cert(certificate.read_text(encoding="ascii")) in roots
        assert client.verify.check_hostname is True
        assert client.verify.verify_mode == ssl.CERT_REQUIRED
    finally:
        client.close()
