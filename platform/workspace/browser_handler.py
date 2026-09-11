"""Browser-only Lambda: no application services, AWS clients or network access."""
import base64
import ctypes
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

BROWSER_TIMEOUT_SECONDS = 100
_lock = threading.Lock()


def _driver_diagnostics(path):
    """Return fixed failure labels only, never captured logs or environment."""
    try:
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 65_536))
            text = stream.read(65_536).decode("utf-8", "replace")
    except OSError:
        return []
    signatures = (
        ("protocol-assertion", "_CRSession._onMessage"),
        ("crashpad-database", "--database is required"),
        ("read-only-filesystem", "Read-only file system"),
        ("operation-not-permitted", "Operation not permitted"),
        ("permission-denied", "Permission denied"),
        ("native-sigtrap", "SIGTRAP"),
        ("native-sigsegv", "SIGSEGV"),
        ("driver-disconnected", "Connection closed while reading from the driver"),
    )
    return [label for label, signature in signatures if signature in text]


def _processes():
    snapshot = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            value = (entry / "stat").read_text()
            fields = value[value.rfind(")") + 2:].split()
            snapshot[int(entry.name)] = (int(fields[1]), fields[19], fields[0])
        except (OSError, ValueError, IndexError):
            continue
    return snapshot


def _subreaper(enabled=None):
    libc = ctypes.CDLL(None, use_errno=True)
    old = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(old), 0, 0, 0) != 0:  # PR_GET_CHILD_SUBREAPER
        raise RuntimeError("Process supervision unavailable")
    if enabled is not None and libc.prctl(36, int(enabled), 0, 0, 0) != 0:
        raise RuntimeError("Process supervision unavailable")
    return bool(old.value)


def _track(root_pid, baseline, known, marker):
    snapshot = _processes()
    parent = os.getpid()
    root = snapshot.get(root_pid)
    if root and root[0] == parent and root_pid not in known:
        known[root_pid] = root[1]
    owned = {pid for pid, start in known.items() if pid in snapshot and snapshot[pid][1] == start}
    changed = True
    while changed:
        changed = False
        for pid, (ppid, start, _) in snapshot.items():
            if pid not in owned and ppid in owned:
                owned.add(pid)
                known[pid] = start
                changed = True
    # Some Lambda sandboxes do not permit prctl subreapers. An invocation-only
    # marker follows Node, Chromium and detached renderers through reparenting.
    # Read only new processes, and never decode or log their environment.
    needle = ("STUDIO_BROWSER_RUN=" + marker).encode()
    for pid, (_, start, state) in snapshot.items():
        if pid in known or (pid, start) in baseline or state == "Z":
            continue
        try:
            entries = (Path("/proc") / str(pid) / "environ").read_bytes().split(b"\0")
            if needle in entries:
                known[pid] = start
        except OSError:
            pass
    return snapshot


def _cleanup(process, baseline, known, marker):
    deadline = time.monotonic() + 2
    while True:
        snapshot = _track(process.pid, baseline, known, marker)
        for pid, start in list(known.items()):
            current = snapshot.get(pid)
            if not current or current[1] != start:
                continue
            if current[2] != "Z":
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
        root_stopped = True
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            root_stopped = False
        # Waiting/reparenting can reveal a detached child absent from the
        # previous scan. Discover ownership again before declaring success.
        final = _track(process.pid, baseline, known, marker)
        remaining = any(pid in final and final[pid][1] == start and final[pid][2] != "Z"
                        for pid, start in known.items())
        if root_stopped and not remaining:
            return True
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        time.sleep(min(0.02, left))


def _execute(event):
    """Bound the whole driver process tree; a crashed protocol cannot hang Lambda."""
    with tempfile.TemporaryDirectory(prefix="studio-browser-") as directory:
        request, output = Path(directory) / "request.json", Path(directory) / "result.json"
        trace = Path(directory) / "phase.txt"
        request.write_text(json.dumps(event, ensure_ascii=False))
        environment = {key: os.environ[key] for key in
                       ("PATH", "HOME", "LANG", "LD_LIBRARY_PATH", "PLAYWRIGHT_BROWSERS_PATH", "AXE_PATH", "WORKSPACE_CHROMIUM_PATH")
                       if key in os.environ}
        environment.update(PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1", TMPDIR=directory,
                           STUDIO_TRACE_PATH=str(trace), DEBUG="pw:browser")
        marker = uuid.uuid4().hex
        environment["STUDIO_BROWSER_RUN"] = marker
        # Lambda runs one invocation at a time. Serialize local calls too, so
        # subreaper adoption is scoped to this invocation's new child tree.
        with _lock:
            try:
                old_reaper = _subreaper(True)
            except RuntimeError:
                old_reaper = None
            baseline = {(pid, row[1]) for pid, row in _processes().items()}
            known, timed_out, fatal_driver = {}, False, False
            process = None
            clean = True
            try:
                # Files cannot leave communicate() waiting on inherited pipes.
                stderr_path = Path(directory) / "stderr.log"
                with (Path(directory) / "stdout.log").open("wb") as stdout, stderr_path.open("wb") as stderr:
                    process = subprocess.Popen([sys.executable, "-m", "workspace.browser_task", str(request), str(output)],
                                               stdout=stdout, stderr=stderr, env=environment, start_new_session=True)
                    deadline = time.monotonic() + BROWSER_TIMEOUT_SECONDS
                    while process.poll() is None:
                        _track(process.pid, baseline, known, marker)
                        if "protocol-assertion" in _driver_diagnostics(stderr_path):
                            fatal_driver = True
                            break
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            timed_out = True
                            break
                        try:
                            process.wait(timeout=min(0.2, remaining))
                        except subprocess.TimeoutExpired:
                            pass
                    code = process.returncode
                    clean = _cleanup(process, baseline, known, marker)
            finally:
                if process is not None and process.poll() is None:
                    clean = _cleanup(process, baseline, known, marker)
                if old_reaper is not None:
                    _subreaper(old_reaper)
        diagnostics = ",".join(_driver_diagnostics(stderr_path)) or "no-known-signature"
        if timed_out or fatal_driver:
            phase = trace.read_text()[:80] if trace.is_file() else "startup"
            known = {"cdp-session", "frame-tree", "isolated-world", "isolated-evaluate", "launch", "load", "interaction", "done"}
            reason = "프로토콜 오류로 중단했습니다" if fatal_driver else "제한 시간을 초과했습니다"
            raise ValueError(f"브라우저 드라이버가 {reason}. 단계: {phase if phase in known else 'startup'}; 진단: {diagnostics}")
        if not clean:
            raise ValueError("브라우저 프로세스 종료를 확인하지 못했습니다.")
        if code != 0 or not output.is_file():
            raise ValueError(f"브라우저 드라이버가 정상 종료되지 않았습니다. 진단: {diagnostics}")
        if output.stat().st_size > 8_000_000:
            raise ValueError("브라우저 응답 크기가 너무 큽니다.")
        return json.loads(output.read_text())


def handler(event, context):
    try:
        reference = event.get("referenceBase64")
        if reference and len(reference) > 4_000_000:
            raise ValueError("기준 이미지가 너무 큽니다.")
        if reference:
            base64.b64decode(reference, validate=True)
        result = _execute(event)
        if len(json.dumps(result, ensure_ascii=False).encode()) > 5_000_000:
            result.pop("screenshotBase64", None)
            result.pop("diffBase64", None)
            result.update(passed=False)
            result["blockingFindings"].append("화면 증거가 응답 한도를 초과했습니다.")
            if len(json.dumps(result, ensure_ascii=False).encode()) > 5_000_000:
                raise ValueError("검증 근거가 응답 한도를 초과했습니다.")
        return result
    except Exception as error:
        return {"passed": False, "engineError": True, "functionalStatus": "incomplete", "checks": [],
                "accessibility": {"status": "incomplete", "violations": []},
                "visual": {"status": "not-run"}, "networkRequests": [], "consoleErrors": [],
                "blockingFindings": [str(error) if isinstance(error, ValueError) else f"검증 실행 오류: {type(error).__name__}"]}
