import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from workspace import browser_handler as handler


@pytest.mark.parametrize("crash", [False, True])
@pytest.mark.parametrize("subreaper", [True, False])
def test_detached_descendants_are_cleaned_after_timeout_or_crash(monkeypatch, tmp_path, crash, subreaper):
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
    if not subreaper:
        def unavailable(*args):
            raise RuntimeError("Restricted runtime")
        monkeypatch.setattr(handler, "_subreaper", unavailable)
    started = time.monotonic()
    result = handler.handler({"html": "<html></html>", "contract": {}}, None)
    assert result["engineError"] and result["passed"] is False
    assert result["functionalStatus"] == "incomplete"
    assert time.monotonic() - started < 4
    assert pid_file.exists()
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        current = handler._processes().get(pid)
        if current is None or current[2] == "Z":
            break
        time.sleep(0.02)
    current = handler._processes().get(pid)
    assert current is None or current[2] == "Z", "detached descendant must not remain running"


def test_driver_diagnostics_include_only_fixed_signatures(tmp_path):
    path = tmp_path / "stderr.log"
    path.write_text("UNRELATED_VALUE=must-not-be-returned\n"
                    "Error: Assertion error\nat _CRSession._onMessage\n"
                    "chrome_crashpad_handler: --database is required\n"
                    "Read-only file system\n")
    diagnostic = handler._driver_diagnostics(path)
    assert diagnostic == ["protocol-assertion", "crashpad-database", "read-only-filesystem"]
    assert "UNRELATED_VALUE" not in str(diagnostic)
    assert "must-not-be-returned" not in str(diagnostic)


def test_fatal_driver_assertion_does_not_wait_for_the_full_deadline(monkeypatch):
    real_popen = subprocess.Popen
    code = ("import sys,time;"
            "sys.stderr.write('Error: Assertion error\\nat _CRSession._onMessage\\n');"
            "sys.stderr.flush();time.sleep(15)")

    def launch(command, **kwargs):
        return real_popen([sys.executable, "-c", code], **kwargs)

    monkeypatch.setattr(handler.subprocess, "Popen", launch)
    monkeypatch.setattr(handler, "BROWSER_TIMEOUT_SECONDS", 10)
    started = time.monotonic()
    result = handler.handler({"html": "<html></html>", "contract": {}}, None)
    assert result["engineError"] and result["passed"] is False
    assert "protocol-assertion" in result["blockingFindings"][0]
    assert time.monotonic() - started < 4


def test_timeout_removes_only_its_invocation_profile_files(monkeypatch, tmp_path):
    real_popen = subprocess.Popen
    temporary_directories = []
    retained = tmp_path / "unrelated.txt"
    retained.write_text("retain")
    code = ("import os,pathlib,time;"
            "profile=pathlib.Path(os.environ['TMPDIR'])/'synthetic-profile';"
            "profile.mkdir();(profile/'cache').write_text('temporary');time.sleep(15)")

    def launch(command, **kwargs):
        temporary_directories.append(Path(kwargs["env"]["TMPDIR"]))
        assert temporary_directories[-1] == Path(command[-2]).parent
        return real_popen([sys.executable, "-c", code], **kwargs)

    monkeypatch.setattr(handler.subprocess, "Popen", launch)
    monkeypatch.setattr(handler, "BROWSER_TIMEOUT_SECONDS", 0.4)
    result = handler.handler({"html": "<html></html>", "contract": {}}, None)
    assert result["engineError"] and not result["passed"]
    assert temporary_directories[0].name.startswith("studio-browser-")
    assert not temporary_directories[0].exists()
    assert retained.read_text() == "retain"


@pytest.mark.parametrize("stubborn", [False, True])
def test_cleanup_discovers_late_marked_orphan_without_touching_unrelated_processes(monkeypatch, stubborn):
    root_pid, orphan_pid, baseline_pid, unrelated_pid = 81001, 81002, 81003, 81004
    marker = "test-invocation-marker"
    now, scans, killed = [0.0], [0], []
    known = {}
    baseline = {(baseline_pid, "baseline-start")}

    def processes():
        scans[0] += 1
        result = {baseline_pid: (1, "baseline-start", "S"),
                  unrelated_pid: (1, "unrelated-start", "S")}
        if scans[0] >= 2 and (orphan_pid not in killed or stubborn):
            result[orphan_pid] = (1, "orphan-start", "S")
        return result

    def environment(path):
        pid = int(path.parent.name)
        assert pid != baseline_pid, "Pre-existing process environments must not be inspected"
        return ("STUDIO_BROWSER_RUN=" + (marker if pid == orphan_pid else "another-run")).encode()

    def kill(pid, signal):
        assert pid == orphan_pid, "Unrelated processes must never be terminated"
        killed.append(pid)

    class Process:
        pid = root_pid

        def wait(self, timeout):
            assert timeout <= 1
            return 0

    monkeypatch.setattr(handler, "_processes", processes)
    monkeypatch.setattr(Path, "read_bytes", environment)
    monkeypatch.setattr(handler.os, "kill", kill)
    monkeypatch.setattr(handler.os, "waitpid", lambda *args: (0, 0))
    monkeypatch.setattr(handler.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(handler.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    clean = handler._cleanup(Process(), baseline, known, marker)
    assert killed and known[orphan_pid] == "orphan-start"
    assert baseline_pid not in known and unrelated_pid not in known
    assert clean is (not stubborn)
    assert now[0] <= 2.05
