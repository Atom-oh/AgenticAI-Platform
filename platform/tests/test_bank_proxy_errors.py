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
