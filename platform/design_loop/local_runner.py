"""Offline runner for trusted repository Node tools. Fixed file names only; no caller path reaches the filesystem."""
from __future__ import annotations
import json, os, subprocess, tempfile  # noqa: E401
from pathlib import Path


class RunnerError(RuntimeError):
    pass


def run_node(script, request, *, timeout=30):
    script = Path(script).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp, "in.json"), Path(tmp, "out.json")
        src.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        try:
            proc = subprocess.run(["node", str(script), str(src), str(out)], cwd=script.parent, timeout=timeout,
                                  capture_output=True, text=True, env={"PATH": os.environ.get("PATH", "")})
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RunnerError(f"{script.name} failed") from error
        if proc.returncode != 0 or not out.exists():
            raise RunnerError(f"{script.name} failed")
        try:
            return json.loads(out.read_text(encoding="utf-8"))
        except ValueError as error:
            raise RunnerError(f"{script.name} returned invalid output") from error
