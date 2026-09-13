"""The HTTP boundary rejects non-NLB peers before reading any personal data."""
import http.client
import json
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import pytest

from privacy.gateway.server import Handler


@contextmanager
def gateway(monkeypatch, allowed, peer="10.0.2.10"):
    if allowed is None:
        monkeypatch.delenv("PRIVACY_ALLOWED_CLIENT_IPS", raising=False)
    else:
        monkeypatch.setenv("PRIVACY_ALLOWED_CLIENT_IPS", allowed)

    class PeerServer(ThreadingHTTPServer):
        def get_request(self):
            sock, address = super().get_request()
            return sock, (peer, address[1])

    server = PeerServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def request(port, path, method="GET", body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.request(method, path, body, headers or {})
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


@pytest.mark.parametrize("allowed", [None, "", "0.0.0.0/0", "10.0.2.10/32",
                                     "8.8.8.8", "10.0.2.10,bad", "127.0.0.1"])
def test_missing_or_invalid_allowlist_blocks_processing(monkeypatch, allowed):
    with gateway(monkeypatch, allowed) as port:
        with patch("privacy.gateway.server.deidentify") as infer:
            status, _ = request(port, "/deidentify", "POST", "{}",
                                {"Content-Type": "application/json"})
        assert status == 403
        infer.assert_not_called()


def test_direct_peer_cannot_spoof_forwarding_header_or_upload_body(monkeypatch):
    with gateway(monkeypatch, "10.0.2.10,10.0.3.10", peer="10.0.2.99") as port:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.putrequest("POST", "/deidentify")
        conn.putheader("Content-Length", "100")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("X-Forwarded-For", "10.0.2.10")
        conn.endheaders()  # Do not send the body: rejection must precede reading it.
        assert conn.getresponse().status == 403
        conn.close()
        assert request(port, "/models")[0] == 403
        assert request(port, "/health")[0] == 200


@pytest.mark.parametrize("peer", ["10.0.2.10", "10.0.3.10"])
def test_each_nlb_peer_can_process_and_read_catalog(monkeypatch, peer):
    with gateway(monkeypatch, "10.0.2.10,10.0.3.10", peer=peer) as port:
        with patch("privacy.gateway.server.model_catalog", return_value={"models": []}):
            assert request(port, "/models") == (200, {"models": []})
        with patch("privacy.gateway.server.choose_model", return_value=object()), \
             patch("privacy.gateway.server.deidentify", return_value={"ok": True}) as infer:
            assert request(port, "/deidentify", "POST",
                           json.dumps({"text": "합성 질문", "purpose": "query", "model": "qwen"}),
                           {"Content-Type": "application/json"}) == (200, {"ok": True})
            infer.assert_called_once()
