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
from pathlib import Path

BROWSER_TIMEOUT_SECONDS = 100
_lock = threading.Lock()


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


def _track(root_pid, baseline, known):
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
            # The subreaper adopts detached grandchildren even when their
            # original parent exits before the next polling interval.
            if pid not in owned and (ppid in owned or (ppid == parent and (pid, start) not in baseline)):
                owned.add(pid)
                known[pid] = start
                changed = True
    return snapshot


def _cleanup(process, baseline, known):
    deadline = time.monotonic() + 2
    remaining = True
    while remaining and time.monotonic() < deadline:
        snapshot = _track(process.pid, baseline, known)
        remaining = False
        for pid, start in list(known.items()):
            current = snapshot.get(pid)
            if not current or current[1] != start:
                continue
            if current[2] != "Z":
                remaining = True
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
        if remaining:
            time.sleep(0.02)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        return False
    final = _processes()
    return not any(pid in final and final[pid][1] == start and final[pid][2] != "Z" for pid, start in known.items())


def _execute(event):
    """Bound the whole driver process tree; a crashed protocol cannot hang Lambda."""
    with tempfile.TemporaryDirectory(prefix="studio-browser-") as directory:
        request, output = Path(directory) / "request.json", Path(directory) / "result.json"
        trace = Path(directory) / "phase.txt"
        request.write_text(json.dumps(event, ensure_ascii=False))
        environment = {key: os.environ[key] for key in
                       ("PATH", "HOME", "LANG", "LD_LIBRARY_PATH", "PLAYWRIGHT_BROWSERS_PATH", "AXE_PATH", "WORKSPACE_CHROMIUM_PATH")
                       if key in os.environ}
        environment.update(PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1", TMPDIR="/tmp", STUDIO_TRACE_PATH=str(trace))
        # Lambda runs one invocation at a time. Serialize local calls too, so
        # subreaper adoption is scoped to this invocation's new child tree.
        with _lock:
            old_reaper = _subreaper(True)
            baseline = {(pid, row[1]) for pid, row in _processes().items()}
            known, timed_out = {}, False
            process = None
            clean = True
            try:
                # Files cannot leave communicate() waiting on inherited pipes.
                with (Path(directory) / "stdout.log").open("wb") as stdout, (Path(directory) / "stderr.log").open("wb") as stderr:
                    process = subprocess.Popen([sys.executable, "-m", "workspace.browser_task", str(request), str(output)],
                                               stdout=stdout, stderr=stderr, env=environment, start_new_session=True)
                    deadline = time.monotonic() + BROWSER_TIMEOUT_SECONDS
                    while process.poll() is None:
                        _track(process.pid, baseline, known)
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            timed_out = True
                            break
                        try:
                            process.wait(timeout=min(0.2, remaining))
                        except subprocess.TimeoutExpired:
                            pass
                    code = process.returncode
                    clean = _cleanup(process, baseline, known)
            finally:
                if process is not None and process.poll() is None:
                    clean = _cleanup(process, baseline, known)
                _subreaper(old_reaper)
        if timed_out:
            phase = trace.read_text()[:80] if trace.is_file() else "startup"
            known = {"cdp-session", "frame-tree", "isolated-world", "isolated-evaluate", "launch", "load", "interaction", "done"}
            raise ValueError(f"브라우저 드라이버 제한 시간을 초과했습니다. 단계: {phase if phase in known else 'startup'}")
        if not clean:
            raise ValueError("브라우저 프로세스 종료를 확인하지 못했습니다.")
        if code != 0 or not output.is_file():
            raise ValueError("브라우저 드라이버가 정상 종료되지 않았습니다.")
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
