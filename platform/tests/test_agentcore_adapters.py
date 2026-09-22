import hashlib
import io
import json
import subprocess
import sys
import tarfile
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from botocore.credentials import Credentials

from ontology_runtime.interpreter import Interpreter, InterpreterUnavailable
from ontology_runtime.browser import Browser
from ontology_runtime.prepare_context import prepare
from workspace.ontology_analysis import local_analyze
from workspace import ontology_schema as schema


def configuration():
    return {"identifier": "synthetic-interpreter", "region": "ap-northeast-2", "bucket": "synthetic-tools",
            "key": "tools/" + "a" * 64 + ".tar.gz", "version": "v1", "archiveHash": "a" * 64,
            "architecture": "arm64", "nodeMajor": 24, "analyzerCodeHash": "b" * 64, "dependencyLockHash": "c" * 64}


@pytest.mark.parametrize("tamper", [False, True])
def test_actual_archive_validation_script_rejects_modified_tool_bytes(tmp_path, tamper):
    buffer = io.BytesIO()
    analyzer, lock = b"// synthetic trusted parser", b"{}"
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, raw in [("source-analyzer/analyze.cjs", analyzer), ("source-analyzer/package-lock.json", lock)]:
            item = tarfile.TarInfo(name)
            item.size = len(raw)
            archive.addfile(item, io.BytesIO(raw))
    raw_archive = buffer.getvalue()
    cfg = configuration()
    cfg.update(archiveHash=hashlib.sha256(raw_archive).hexdigest(),
               analyzerCodeHash=hashlib.sha256(analyzer).hexdigest(), dependencyLockHash=hashlib.sha256(lock).hexdigest())
    cfg["key"] = "tools/" + cfg["archiveHash"] + ".tar.gz"
    adapter = Interpreter(None, cfg)

    def physical_command(session, command, deadline):
        if command.startswith("aws "):
            (tmp_path / "ontology-tools.tar.gz").write_bytes(raw_archive + b"changed" if tamper else raw_archive)
            return {"stdout": ""}
        if command.startswith("python3 "):
            script = command.split("\n", 1)[1].rsplit("\nPY", 1)[0].replace("/tmp/ontology-tools", str(tmp_path / "ontology-tools"))
            result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True)
            if result.returncode:
                raise InterpreterUnavailable("Trusted tool command did not complete")
            return {"stdout": result.stdout}
        return {"stdout": '{"nodeVersion":"v24.0.0","architecture":"arm64"}'}

    adapter._command = physical_command
    if tamper:
        with pytest.raises(InterpreterUnavailable):
            adapter._prepare("session", 1000)
        assert not (tmp_path / "ontology-tools").exists()
    else:
        assert adapter._prepare("session", 1000)[1]["nodeVersion"] == "v24.0.0"
        assert (tmp_path / "ontology-tools/source-analyzer/analyze.cjs").read_bytes() == analyzer


def test_source_is_transferred_as_data_and_never_interpolated_into_commands():
    code = 'const text = "$(touch /tmp/not-executed)"; export {text};'
    payload = {"schemaVersion": 1, "files": [{"path": "App.ts", "kind": "code", "text": code,
               "sha256": hashlib.sha256(code.encode()).hexdigest()}], "resolver": {"aliases": {}, "packages": {}, "jsonAssetFields": []}}
    result = local_analyze(payload)["analysis"]
    adapter = Interpreter(None, configuration())
    adapter._session = lambda: nullcontext("session")
    adapter._prepare = lambda *args: ("/workspace", {"nodeVersion": "v24.0.0", "architecture": "arm64"})
    commands, writes = [], []
    adapter._command = lambda session, command, deadline: commands.append(command)

    def transfer(session, operation, arguments):
        if operation == "writeFiles":
            writes.append(json.loads(arguments["content"][0]["text"]))
            return [{}]
        assert operation == "readFiles"
        return [{"content": [{"resource": {"text": json.dumps(result)}}]}]

    adapter._invoke = transfer
    receipt = adapter.execute("analyze", payload)
    assert writes == [payload]
    assert commands and all(code not in command and "not-executed" not in command for command in commands)
    assert receipt["execution"]["inputHash"] == schema.digest(payload)
    assert receipt["execution"]["sourceExecuted"] is False


def test_browser_adapter_records_the_observed_profile_and_keeps_credentials_out_of_the_page(monkeypatch, tmp_path):
    from ontology_runtime import browser as module
    axe = tmp_path / "axe.min.js"
    axe.write_text("/* synthetic verifier fixture */")
    monkeypatch.setenv("AXE_PATH", str(axe))
    calls, contexts = [], []
    client = SimpleNamespace(
        start_browser_session=lambda **kwargs: {"sessionId": "synthetic-session"},
        stop_browser_session=lambda **kwargs: calls.append(kwargs))
    session = SimpleNamespace(client=lambda *args, **kwargs: client,
                              get_credentials=lambda: Credentials("synthetic-access", "synthetic-secret"))
    remote = SimpleNamespace(version="151.0.synthetic", new_context=lambda **kwargs: contexts.append(kwargs))
    playwright = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=lambda *args, **kwargs: remote))
    monkeypatch.setattr(module, "validate_contract", lambda value: value)

    def verify(files, contract, reference, tolerance, **kwargs):
        kwargs["_context_factory"](playwright, contract["viewport"])
        return {"passed": True, "bundleHash": kwargs["expected_hash"]}

    monkeypatch.setattr(module, "evaluate_bundle", verify)
    result = Browser(session, "synthetic-browser", "ap-northeast-2").evaluate(
        {"ok": True, "files": {}, "bundleHash": "a" * 64}, {"viewport": {"width": 390, "height": 844}})
    assert result["browserExecution"]["profile"]["browserVersion"] == remote.version
    assert contexts == [{"viewport": {"width": 390, "height": 844}, "device_scale_factor": 1,
                        "service_workers": "block", "accept_downloads": False, "offline": True}]
    assert calls == [{"browserIdentifier": "synthetic-browser", "sessionId": "synthetic-session"}]


def test_context_manifest_includes_verifier_and_build_inputs_and_rejects_symlink_parents(tmp_path):
    platform = Path(__file__).resolve().parents[1]
    axe = tmp_path / "axe"
    axe.mkdir()
    (axe / "axe.min.js").write_text("/* synthetic */")
    (axe / "LICENSE").write_text("synthetic license")
    result = prepare(platform, tmp_path / "context", axe)
    rows = {row["path"]: row["sha256"] for row in json.loads((result / "source-manifest.json").read_text())}
    assert {"workspace/axe.min.js", "workspace/AXE_LICENSE", "@build/Dockerfile", "@build/requirements.lock"} <= rows.keys()
    assert rows["workspace/axe.min.js"] == hashlib.sha256((axe / "axe.min.js").read_bytes()).hexdigest()
    link = tmp_path / "linked-axe"
    link.symlink_to(axe, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        prepare(platform, tmp_path / "unsafe-context", link)
