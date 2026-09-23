import io
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "api")]
from handlers import core


@pytest.mark.parametrize("http_error", [False, True])
def test_control_room_proxy_never_returns_or_logs_upstream_bodies(monkeypatch, capsys, http_error):
    def fail(*args, **kwargs):
        if http_error:
            raise urllib.error.HTTPError("https://synthetic.invalid", 502, "PRIVATE_UPSTREAM",
                                         {}, io.BytesIO(b"PRIVATE_UPSTREAM Traceback"))
        raise RuntimeError("PRIVATE_UPSTREAM Traceback")
    monkeypatch.setattr(core.urllib.request, "urlopen", fail)
    result = core._control_room("GET", "/api/agents", "synthetic-token")
    captured = capsys.readouterr()
    assert result["error"] == "Control-room request failed"
    assert "PRIVATE_UPSTREAM" not in str(result) + captured.out + captured.err


@pytest.mark.parametrize("message", ["person@example.invalid", "x" * 2001])
def test_control_room_chat_rejects_local_identifiers_and_overflow_before_proxy(monkeypatch, message):
    from types import SimpleNamespace
    events = []
    monkeypatch.setattr(core, "_control_room", lambda *args, **kwargs: pytest.fail("unsafe chat reached proxy"))
    core.chat(SimpleNamespace(post=events.append), {"message": message, "idToken": "synthetic-token"})
    assert events[-1]["code"] == (422 if "@" in message else 400)
    assert message not in str(events)


@pytest.mark.parametrize('path', ['/api/agents', '/api/chat'])
@pytest.mark.parametrize('body', [
    {'error': {'message': 'PRIVATE_UPSTREAM Traceback'}, 'reply': 'PRIVATE_REPLY'},
    {'statusCode': 500, 'body': 'PRIVATE_UPSTREAM', 'reply': 'PRIVATE_REPLY'},
    {'errorMessage': 'PRIVATE_UPSTREAM', 'reply': 'PRIVATE_REPLY'},
])
def test_successful_http_envelope_does_not_expose_application_errors(monkeypatch, path, body):
    import json
    monkeypatch.setattr(core.urllib.request, 'urlopen', lambda *args, **kwargs: io.BytesIO(json.dumps(body).encode()))
    result = core._control_room('GET' if path.endswith('agents') else 'POST', path, 'synthetic-token')
    assert result == {'error': 'Control-room request failed', 'code': 502}
    assert 'PRIVATE_' not in str(result)
