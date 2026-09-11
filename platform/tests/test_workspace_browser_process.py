import os
import subprocess
import sys
import time

import pytest

from workspace import browser_handler as handler


@pytest.mark.parametrize("crash", [False, True])
def test_detached_descendants_are_cleaned_after_timeout_or_crash(monkeypatch, tmp_path, crash):
    real_popen = subprocess.Popen
    pid_file = tmp_path / "descendant.pid"
    code = (
        "import subprocess,sys,time,pathlib;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(15)'],start_new_session=True);"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid));"
        + ("sys.exit(3)" if crash else "time.sleep(15)")
    )
    def launch(command, **kwargs):
        assert kwargs["start_new_session"] is True
        assert "AWS_ACCESS_KEY_ID" not in kwargs["env"]
        assert "AWS_SECRET_ACCESS_KEY" not in kwargs["env"]
        assert "AWS_LAMBDA_RUNTIME_API" not in kwargs["env"]
        assert kwargs["stdout"] != subprocess.PIPE and kwargs["stderr"] != subprocess.PIPE
        return real_popen([sys.executable, "-c", code], **kwargs)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-not-a-real-credential")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-not-a-real-secret")
    monkeypatch.setenv("AWS_LAMBDA_RUNTIME_API", "localhost:9001")
    monkeypatch.setattr(handler.subprocess, "Popen", launch)
    monkeypatch.setattr(handler, "BROWSER_TIMEOUT_SECONDS", 0.4)
    started = time.monotonic()
    result = handler.handler({"html": "<html></html>", "contract": {}}, None)
    assert result["engineError"] and result["passed"] is False
    assert result["functionalStatus"] == "incomplete"
    assert time.monotonic() - started < 4
    assert pid_file.exists()
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
