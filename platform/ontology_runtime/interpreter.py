"""Fixed trusted tool operations in AgentCore; no runtime package installation."""
from __future__ import annotations

import hashlib
import json
import re
import shlex
import time
from contextlib import contextmanager

from workspace import ontology_schema as schema
from workspace.ontology_analysis import validate_analysis, validate_execution


class InterpreterUnavailable(RuntimeError):
    pass


class Interpreter:
    backend = "agentcore-code-interpreter"

    def __init__(self, client, configuration, *, clock=time.monotonic, sleep=time.sleep):
        self.client, self.config = client, dict(configuration)
        self.clock, self.sleep = clock, sleep
        schema._fields(self.config, {"identifier", "region", "bucket", "key", "version",
            "archiveHash", "architecture", "nodeMajor", "analyzerCodeHash", "dependencyLockHash"})
        for key in ("archiveHash", "analyzerCodeHash", "dependencyLockHash"):
            schema._hash(self.config[key])
        if (self.config["architecture"] not in {"arm64", "x64"}
                or not re.fullmatch(r"[a-z0-9-]{3,63}", self.config["bucket"])
                or self.config["key"] != "tools/" + self.config["archiveHash"] + ".tar.gz"):
            raise ValueError("Invalid immutable tool archive configuration")
        for key in ("identifier", "region", "version"):
            schema._text(self.config[key], 256)

    def _invoke(self, session, operation, arguments):
        try:
            response = self.client.invoke_code_interpreter(codeInterpreterIdentifier=self.config["identifier"],
                sessionId=session, name=operation, arguments=arguments)
            results = []
            for event in response["stream"]:
                if set(event) != {"result"} or event["result"].get("isError"):
                    raise InterpreterUnavailable("AgentCore tool operation failed")
                results.append(event["result"])
            if not results:
                raise InterpreterUnavailable("AgentCore returned no tool evidence")
            return results
        except InterpreterUnavailable:
            raise
        except Exception:
            # SDK errors can contain command arguments, source data or headers.
            raise InterpreterUnavailable("AgentCore tool transport is unavailable") from None

    def _command(self, session, command, deadline):
        started = self._invoke(session, "startCommandExecution", {"command": command})
        task = started[-1].get("structuredContent", {}).get("taskId")
        if not isinstance(task, str) or not task:
            raise InterpreterUnavailable("AgentCore command task is missing")
        while self.clock() < deadline:
            self.sleep(0.2)
            result = self._invoke(session, "getTask", {"taskId": task})[-1].get("structuredContent", {})
            if result.get("taskStatus") == "completed":
                if result.get("exitCode") != 0:
                    raise InterpreterUnavailable("Trusted tool command did not complete")
                return result
            if result.get("taskStatus") in {"failed", "stopped", "cancelled"}:
                raise InterpreterUnavailable("Trusted tool command failed")
        try:
            self._invoke(session, "stopTask", {"taskId": task})
        finally:
            raise InterpreterUnavailable("Trusted tool command exceeded its deadline")

    @contextmanager
    def _session(self):
        identifier = self.config["identifier"]
        try:
            started = self.client.start_code_interpreter_session(codeInterpreterIdentifier=identifier,
                name="project-ontology", sessionTimeoutSeconds=300)
            session = started["sessionId"]
        except Exception:
            raise InterpreterUnavailable("AgentCore interpreter session could not start") from None
        try:
            yield session
        finally:
            try:
                self.client.stop_code_interpreter_session(codeInterpreterIdentifier=identifier, sessionId=session)
            except Exception:
                raise InterpreterUnavailable("AgentCore interpreter cleanup was not acknowledged") from None

    def _prepare(self, session, deadline):
        cfg = self.config
        arguments = ["aws", "--no-cli-pager", "--region", cfg["region"],
            "--cli-connect-timeout", "10", "--cli-read-timeout", "30", "s3api", "get-object",
            "--bucket", cfg["bucket"], "--key", cfg["key"], "--version-id", cfg["version"],
            "/tmp/ontology-tools.tar.gz"]
        self._command(session, shlex.join(arguments), deadline)
        # Only deployment-controlled values enter this script. Application input
        # is transferred by writeFiles and never interpolated into commands.
        extraction = """import hashlib,json,pathlib,tarfile
p=pathlib.Path('/tmp/ontology-tools.tar.gz')
assert hashlib.sha256(p.read_bytes()).hexdigest()==ARCHIVE
root=pathlib.Path('/tmp/ontology-tools');root.mkdir()
with tarfile.open(p,'r:gz') as archive:
 members=archive.getmembers()
 assert len(members)<=10000 and sum(m.size for m in members)<=268435456
 for m in members:
  assert m.isfile() and not m.name.startswith('/') and all(x not in ('','.','..') for x in pathlib.PurePosixPath(m.name).parts)
 archive.extractall(root,members=members,filter='data')
assert hashlib.sha256((root/'source-analyzer/analyze.cjs').read_bytes()).hexdigest()==ANALYZER
assert hashlib.sha256((root/'source-analyzer/package-lock.json').read_bytes()).hexdigest()==LOCK
print(json.dumps({'directory':str(pathlib.Path.cwd())}))
"""
        extraction = extraction.replace("ARCHIVE", repr(cfg["archiveHash"])).replace(
            "ANALYZER", repr(cfg["analyzerCodeHash"])).replace("LOCK", repr(cfg["dependencyLockHash"]))
        directory = json.loads(self._command(session, "python3 - <<'PY'\n" + extraction + "\nPY", deadline)["stdout"])["directory"]
        profile = json.loads(self._command(session,
            "node -e 'console.log(JSON.stringify({nodeVersion:process.version,architecture:process.arch}))'", deadline)["stdout"])
        if (profile["architecture"] != cfg["architecture"]
                or not re.fullmatch(r"v\d+\.\d+\.\d+", profile["nodeVersion"])
                or int(profile["nodeVersion"].split(".")[0][1:]) != cfg["nodeMajor"]
                or not directory.startswith("/") or len(directory) > 500):
            raise InterpreterUnavailable("Managed runtime does not match the trusted archive")
        return directory, profile

    def execute(self, operation, payload):
        if operation not in {"analyze", "compile"}:
            raise ValueError("Only fixed source-analysis and React compiler operations are available")
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(raw.encode()) > 4_500_000:
            raise ValueError("Tool input exceeds the bounded transport")
        started = self.clock()
        with self._session() as session:
            directory, profile = self._prepare(session, started + 120)
            self._invoke(session, "writeFiles", {"content": [{"path": "request.json", "text": raw}]})
            root, command = ("source-analyzer", "analyze.cjs") if operation == "analyze" else ("react-kit", "compile.cjs")
            execution = "cd /tmp/ontology-tools/" + root + " && " + shlex.join([
                "node", "--max-old-space-size=512", command, directory + "/request.json", directory + "/result.json"])
            self._command(session, execution, min(started + 240, self.clock() + 60))
            results = self._invoke(session, "readFiles", {"paths": ["result.json"]})
            texts = [item["resource"]["text"] for result in results for item in result.get("content", [])
                     if isinstance(item.get("resource"), dict) and isinstance(item["resource"].get("text"), str)]
            if len(texts) != 1 or len(texts[0].encode()) > 32_000_000:
                raise InterpreterUnavailable("Trusted tool result is missing or oversized")
            try:
                value = json.loads(texts[0])
            except ValueError:
                raise InterpreterUnavailable("Trusted tool output is not valid JSON") from None
            receipt = {"backend": self.backend, "sourceExecuted": False, "interpreterId": self.config["identifier"],
                "inputHash": schema.digest(payload),
                "sessionId": session, "region": self.config["region"], **profile,
                "toolArchiveHash": self.config["archiveHash"], "analyzerCodeHash": self.config["analyzerCodeHash"],
                "dependencyLockHash": self.config["dependencyLockHash"], "elapsedMs": int((self.clock() - started) * 1000)}
            validate_execution(receipt)
            if operation == "analyze":
                validate_analysis(payload, value)
            elif not isinstance(value, dict) or type(value.get("ok")) is not bool:
                raise InterpreterUnavailable("React compiler evidence is invalid")
            return {"analysis" if operation == "analyze" else "build": value, "execution": receipt}

    def __call__(self, payload):
        return self.execute("analyze", payload)
