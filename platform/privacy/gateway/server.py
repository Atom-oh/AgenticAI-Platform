"""Private, stateless EKS HTTP endpoint; request bodies are never logged."""
from __future__ import annotations

import json
import ipaddress
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .engine import deidentify
from . import jsonio
from .errors import PrivacyFailure
from .models import choose_model, model_catalog

SLOTS = threading.BoundedSemaphore(4)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def trusted_peer(self):
        """Use the TCP peer, never a caller-controlled forwarding header.

        VPC CNI standard mode has a startup allow window. This check remains
        active from the first request, independently of policy programming.
        NLB target groups must disable client-IP preservation.
        """
        try:
            addresses = [
                ipaddress.IPv4Address(value.strip())
                for value in os.environ.get("PRIVACY_ALLOWED_CLIENT_IPS", "").split(",")
            ]
            private = ipaddress.IPv4Network("10.0.0.0/8")
            return (2 <= len(addresses) <= 4
                    and len(set(addresses)) == len(addresses)
                    and all(address in private for address in addresses)
                    and ipaddress.IPv4Address(self.client_address[0]) in addresses)
        except ValueError:
            return False

    def reject_peer(self):
        self.close_connection = True
        return self.reply(403, {"ok": False, "error": {
            "code": "untrusted-peer", "message": "허용되지 않은 연결입니다.",
        }})

    def reply(self, status: int, payload: dict):
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"ok": True, "processor": "eks-sllm"})
        if not self.trusted_peer():
            return self.reject_peer()
        if self.path != "/models":
            return self.reply(404, {"ok": False, "error": {"code": "not-found", "message": "Unknown operation"}})
        try:
            self.reply(200, model_catalog())
        except PrivacyFailure as error:
            self.reply(503, {"ok": False, "error": {"code": error.code, "message": "모델 설정을 확인할 수 없습니다."}})

    def do_POST(self):
        if not self.trusted_peer():
            return self.reject_peer()
        if self.path != "/deidentify":
            return self.reply(404, {"ok": False, "error": {"code": "not-found", "message": "Unknown operation"}})
        if not SLOTS.acquire(blocking=False):
            return self.reply(503, {"ok": False, "error": {"code": "busy", "message": "개인정보 처리기가 사용 중입니다."}})
        try:
            self.connection.settimeout(10)
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536 or self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding"):
                raise PrivacyFailure("invalid-input")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise PrivacyFailure("invalid-input")
            body = jsonio.loads(raw)
            if not isinstance(body, dict) or set(body) - {"text", "model", "purpose", "requestId"}:
                raise PrivacyFailure("invalid-input")
            if body.get("purpose") not in ("query", "payload", "evaluation"):
                raise PrivacyFailure("invalid-input")
            model = choose_model(body.get("model", "qwen"))
            self.reply(200, deidentify(body.get("text"), model, allow_tokens=body["purpose"] == "payload"))
        except PrivacyFailure as error:
            self.reply(422, {"ok": False, "error": {"code": error.code, "message": "개인정보 처리 검증을 완료하지 못해 요청을 차단했습니다."}})
        except (ValueError, OSError, TypeError):
            self.reply(400, {"ok": False, "error": {"code": "invalid-input", "message": "처리할 입력 형식을 확인하세요."}})
        except Exception:
            self.reply(500, {"ok": False, "error": {"code": "processor-error", "message": "개인정보 처리기를 확인하세요."}})
        finally:
            SLOTS.release()


def main():
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
