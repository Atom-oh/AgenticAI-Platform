"""Local-only synthetic UI rehearsal using the actual application API.

This test utility never authenticates a real user or calls AWS. Do not deploy it.
Build platform/web first, then run this file and use capture-workbench.cjs.
Use --port to isolate a capture run; the bind address stays loopback-only.
"""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import json
import sys
import threading
import time
import argparse

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "api"), str(ROOT / "tests")]
from test_workspace_storage import FakeTable, FakeS3
from workspace.storage import Storage
from workspace.http import WorkspaceAPI
from workspace.worker import Worker

TOKEN = "synthetic-workbench-rehearsal"
ACTOR = "synthetic-operator"
storage = Storage(table=FakeTable(), s3=FakeS3(), bucket="synthetic-local")
lock = threading.RLock()


def unavailable(*args, **kwargs):
    raise ValueError("합성 로컬 시연에서는 외부 모델을 호출하지 않습니다.")


worker = Worker(storage=storage, model_call=unavailable)


class LocalWorker:
    def invoke(self, **request):
        payload = json.loads(request["Payload"])
        worker.handle(payload)
        return {"StatusCode": 202}


api = WorkspaceAPI(storage=storage, lambda_client=LocalWorker(), worker_fn="synthetic-worker")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "web" / "dist"), **kwargs)

    def log_message(self, *_args):
        return

    def do_GET(self):
        if self.path.startswith("/studio-api/"):
            return self.api_request()
        if self.path == "/config.json":
            body = json.dumps({"region": "ap-northeast-2", "cognitoClientId": "synthetic-client",
                               "wssUrl": f"ws://127.0.0.1:{self.server.server_port}/unavailable",
                               "graphBackend": "local", "planeDeployed": False}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def do_POST(self):
        self.api_request()

    def do_PUT(self):
        self.api_request()

    def do_DELETE(self):
        self.api_request()

    def api_request(self):
        if self.headers.get("Authorization") != "Bearer " + TOKEN or not self.path.startswith("/studio-api/"):
            self.send_error(401)
            return
        size = int(self.headers.get("Content-Length", "0"))
        if size > 262144:
            self.send_error(413)
            return
        parsed = urlsplit(self.path)
        claims = {"sub": ACTOR, "token_use": "access", "exp": int(time.time()) + 3600,
                  "cognito:groups": ["platform-operators"]}
        event = {"rawPath": parsed.path, "headers": dict(self.headers),
                 "queryStringParameters": {k: values[0] for k, values in parse_qs(parsed.query).items()},
                 "requestContext": {"http": {"method": self.command},
                                    "authorizer": {"jwt": {"claims": claims}}},
                 "body": self.rfile.read(size).decode() if size else ""}
        with lock:
            result = api.handle(event)
        body = result["body"].encode()
        self.send_response(result["statusCode"])
        for name, value in result["headers"].items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Loopback-only synthetic Workbench rehearsal server.")
    parser.add_argument("--port", type=int, default=8766, help="Loopback port (default: 8766)")
    options = parser.parse_args()
    if not 1 <= options.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    with ThreadingHTTPServer(("127.0.0.1", options.port), Handler) as server:
        print(f"Synthetic rehearsal listening at http://127.0.0.1:{server.server_port}", flush=True)
        server.serve_forever()
