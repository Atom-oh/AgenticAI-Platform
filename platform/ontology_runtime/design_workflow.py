"""Actual design extraction, composition, compilation and independent Browser verification."""
from __future__ import annotations

import base64
import copy
import json

from workspace import design_stages as delivery
from design_loop import prd_extract, verify_graph
from design_loop.convention import Registry
from ontology_runtime.design_model import DesignModel
from ontology_runtime.execution_session import ExecutionSession
from workspace.react_quality import react_report_passes
from workspace.rules import contract_hash


class DesignWorkflow:
    def __init__(self, gateway, interpreter, browser, signer, memory=None, *, model_factory=DesignModel):
        self.gateway, self.interpreter, self.browser, self.signer, self.memory = gateway, interpreter, browser, signer, memory
        self.model_factory = model_factory

    def run(self, event, session_id):
        execution = ExecutionSession(self.gateway, self.signer, event, session_id)
        try:
            raw, source = execution.read()
            manifest = json.loads(raw)
            if (manifest["operation"] != execution.job["operation"] or manifest["tool"] != self.interpreter.config
                    or manifest["admissions"] != execution.job["admissions"] or manifest["model"] != execution.job["model"]):
                raise ValueError("design-manifest-mismatch")
            model = self.model_factory(execution, manifest)
            execution.memory_scope = {"actor": execution.job["actor"], "projectId": execution.job["projectId"],
                "executionId": execution.job["executionId"], "attemptId": execution.job["attemptId"],
                "resourcesHash": execution.job["memoryContextHash"], "iat": execution.clock() // 1000}
            continuity = {"events": [], "authority": False}
            if self.memory:
                sessions = execution.call("memory_sessions", {})["sessions"]
                continuity = self.memory.read_sessions(execution.memory_scope, sessions)
                if execution.call("memory_sessions", {})["sessions"] != sessions:
                    raise ValueError("memory-authority-changed")
            context = execution.upload("context", "context.json", {"input": source["sha256"], "continuity": continuity,
                "designId": manifest["designId"], "designRevision": manifest["designRevision"]})
            execution.stage("context", [source], [context], {})
            if manifest["operation"] == "design.extract":
                return self._extract(execution, manifest, source, context, model)
            if manifest["operation"] in {"design.generate", "design.edit"}:
                return self._generate(execution, manifest, source, context, model)
            if manifest["operation"] == "design.release":
                return self._release(execution, manifest, source, context)
            raise ValueError("design-operation-unavailable")
        except Exception:
            # The ledger preserves outstanding call uncertainty and accounting. A terminal
            # or recovered attempt rejects this call, so a completed result is never undone.
            try:
                execution.call("fail", {"code": "design-execution-failed"})
            except Exception:
                pass
            raise
        finally:
            execution.close()

    def _extract(self, execution, manifest, source, context, model):
        result = prd_extract.extract_prd(manifest["pages"], model.dependencies(), model=manifest["model"])
        if result.get("blocked"):
            raise ValueError(result["blocked"])
        generated = execution.upload("generate", "prd-proposal.json", result)
        boundary = execution.upload("generate", "model-boundary.json", model.boundaries)
        execution.stage("generate", [source, context], [generated, boundary], {"boundary": boundary}, model.service())
        verified = prd_extract.validate_prd(manifest["pages"], result["prd"])
        output = execution.upload("verify", "design.json", verified)
        final = execution.upload("verify", "result.json", {"objects": [output]})
        memory = self.memory.append(execution.memory_scope, "generated", output["sha256"]) if self.memory else None
        execution.stage("verify", [generated], [output, final], {"issues": verified["issues"], "designResult": output, **({"memory": memory} if memory else {})})
        return execution.finish(final, "succeeded" if not verified["issues"] else "needs_changes")

    @staticmethod
    def _bundle(manifest, generated):
        k = delivery.knowledge(manifest["snapshot"])
        prepared = manifest["prepared"]
        return {"prd": manifest["prd"], "flow": prepared["flow"], "expectation": prepared["expectation"],
            "screens": delivery.unpacked(generated["screens"]), "binding_values": prd_extract.bindings(manifest["prd"]),
            "contract": prepared["contract"], "registry": Registry(k, state=generated["registry"])}

    def _generate(self, execution, manifest, source, context, model):
        k = delivery.knowledge(manifest["snapshot"])
        if manifest["operation"] == "design.edit":
            generated = self._edit(manifest, model)
        else:
            generated = delivery.generate(manifest["snapshot"], manifest["prepared"], manifest["prd"],
                                         model.dependencies(), variant=manifest["variant"], max_rounds=manifest["maxRounds"])
        bundle = self._bundle(manifest, generated)
        judgments = {}
        # Judge composition before compilation. Results are reused only with the exact same
        # composition/context/profile keys; tester and coverage still run after Browser.
        deps = {**model.dependencies(), "react_report_passes": react_report_passes, "contract_hash": contract_hash}
        verify_graph._reviewer(bundle, k, deps, generated["mode"], judgments)
        if not model.calls:
            # A fully deterministic checklist still needs no invented model observation.
            raise ValueError("design-deterministic-operation-required")
        source_files = execution.upload("generate", "sources.json", {"files": generated["files"]}, role="source")
        candidate = execution.upload("generate", "candidate.json", generated)
        boundary = execution.upload("generate", "model-boundary.json", model.boundaries)
        execution.stage("generate", [source, context], [source_files, candidate, boundary], {"boundary": boundary}, model.service())
        return self._compile_and_verify(execution, manifest, source_files, candidate, generated, bundle, deps, judgments)

    def _edit(self, manifest, model):
        from design_loop import edit, react_project
        generated = copy.deepcopy(manifest["baseResult"]["generated"])
        screens = delivery.unpacked(generated["screens"])
        target = manifest["edit"]
        key = target["screenId"], target["state"]
        if key not in screens:
            raise ValueError("design-edit-target")
        k = delivery.knowledge(manifest["snapshot"])
        changed = edit.edit(screens[key], target["targetId"], target["instruction"], k, model.dependencies(),
            flow=manifest["prepared"]["flow"], binding_paths=prd_extract.bindings(manifest["prd"]), mode=generated["mode"])
        if changed.get("blocked") or not changed.get("composition"):
            raise ValueError("design-edit-failed")
        stable = edit.stability(screens[key], changed["composition"], target["targetId"])
        if not stable["stable"]:
            raise ValueError("design-edit-scope")
        screens[key] = changed["composition"]
        registry = Registry(k, state=generated["registry"])
        generated.update(screens=delivery.packed(screens), files=delivery.source_files(manifest["snapshot"], manifest["prepared"], manifest["prd"], screens, registry.to_json()),
            edit={"screenId": key[0], "state": key[1], "targetId": target["targetId"], "stability": stable})
        return generated

    def _compile_and_verify(self, execution, manifest, source_files, candidate, generated, bundle, deps, judgments):
        from design_loop.react_project import compile_request
        contract = manifest["prepared"]["contract"]
        call_id = execution.intent("compile", "interpreter", 300000)
        result = self.interpreter.execute("compile", compile_request(generated["files"], catalog_hash=contract["catalogHash"], contract=contract))
        build, observed = result["build"], result["execution"]
        execution.outcome(call_id, session_id=observed["sessionId"], status="completed" if build.get("ok") else "failed")
        if not build.get("ok"):
            execution.call("fail", {"code": "design-compile-failed"})
            return {"status": "failed"}
        source_zip = execution.upload("compile", "source.zip", base64.b64decode(build["projectZipBase64"], validate=True), binary=True)
        dist_zip = execution.upload("compile", "bundle.zip", base64.b64decode(build["distZipBase64"], validate=True), binary=True, role="bundle")
        build_copy = {key: value for key, value in build.items() if key not in {"projectZipBase64", "distZipBase64"}}
        build_ref = execution.upload("compile", "build.json", {"build": build_copy, "execution": observed})
        execution.stage("compile", [source_files, candidate], [source_zip, dist_zip, build_ref],
            {"sourceHash": source_zip["sha256"], "bundleHash": dist_zip["sha256"],
             "reactSourceHash": build["sourceHash"], "reactBundleHash": build["bundleHash"]},
            {"kind": "interpreter", "sessionId": observed["sessionId"], "taskId": call_id,
             "exitCode": 0, "profile": execution.job["toolProfiles"]["interpreter"]})
        browser_call = execution.intent("browser", "browser", 180000)
        reference = None
        baseline = None
        if manifest["operation"] == "design.release":
            import hashlib
            reference = base64.b64decode(manifest["approvedScreenshot"], validate=True)
            baseline = {**manifest["approved"]["screenshot"], "size": len(reference)}
            if (hashlib.sha256(reference).hexdigest() != baseline["sha256"]
                    or source_zip["sha256"] != manifest["approved"]["sourceHash"]
                    or dist_zip["sha256"] != manifest["approved"]["bundleHash"]):
                execution.call("fail", {"code": "design-release-source-changed"})
                return {"status": "failed"}
        report = self.browser.evaluate(build, contract, reference=reference,
                                       visual_tolerance=0.02 if baseline else 0.15)
        execution.outcome(browser_call, session_id=report["browserExecution"]["sessionId"])
        from design_loop.evidence import assemble
        report = assemble(build_copy, report, contract=contract, contract_hash_fn=contract_hash)
        large_call = execution.intent("browser", "browser", 120000)
        large = self.browser.evaluate(build, contract, text_scale=2.0)
        large = assemble(build_copy, large, contract=contract, contract_hash_fn=contract_hash)
        execution.outcome(large_call, session_id=large["browserExecution"]["sessionId"])
        report_ref = execution.upload("browser", "browser.json", report)
        large_ref = execution.upload("browser", "large-text.json", large)
        shot = execution.upload("browser", "screenshot.png", base64.b64decode(report["screenshotBase64"], validate=True), binary=True)
        browser_result = {"passed": report["passed"], "functionalStatus": report["functionalStatus"],
                          "accessibility": report["accessibility"], "report": report_ref}
        if baseline:
            browser_result.update(comparison=manifest["approved"]["screenshot"], visualDiff=report["visual"].get("changedRatio"))
        execution.stage("browser", [dist_zip, *([baseline] if baseline else [])], [report_ref, large_ref, shot], browser_result,
            {"kind": "browser", "sessionId": report["browserExecution"]["sessionId"], "taskId": browser_call,
             "exitCode": 0, "profile": execution.job["toolProfiles"]["browser"]})
        bundle.update(build=build, browser_report=report, large_text_report=large)
        # Judgment cache verification repeats the exact source admission through the Gateway before this stage.
        judgments = {key: verify_graph.stored_judgment(key, value["verdicts"], execution.job["admissions"])
                     for key, value in judgments.items()}
        def current(stored):
            if stored["admissions"] != execution.job["admissions"]:
                return False
            execution.call("heartbeat", {})  # live source/admission/attempt fence before reuse
            return True
        def missing_judgment(*args):
            raise ValueError("current-judgment-required")
        deps = {**deps, "verify_lineage": current, "generate": missing_judgment}
        if manifest["operation"] == "design.release":
            from design_loop import coverage
            checked, missing = verify_graph._tester(bundle, {"react_report_passes": react_report_passes, "contract_hash": contract_hash})
            covered = coverage.check(manifest["prd"], bundle["flow"], bundle["expectation"], bundle["screens"],
                                     delivery.knowledge(manifest["snapshot"]), bundle["binding_values"])
            issues = [*checked, *covered["findings"]]
            passed = not missing and not covered["blocked"] and not any(f["severity"] == "critical" for f in issues)
            verified = {"verdict": "pass" if passed else "fail", "passed": passed, "approvable": passed,
                        "issues": issues, "findings": issues, "reviewer": "approved-source-rebuild"}
        else:
            verified = verify_graph.verify(bundle, delivery.knowledge(manifest["snapshot"]), deps,
                                           mode=generated["mode"], judgments=judgments)
        value = {"generated": generated, "verification": verified, "build": build_ref,
                 "source": source_zip, "bundle": dist_zip, "browser": report_ref, "largeText": large_ref,
                 "screenshot": shot, "variant": manifest["variant"], "contract": contract,
                 "sourceHash": build["sourceHash"], "bundleHash": build["bundleHash"]}
        output = execution.upload("verify", "design.json", value)
        final = execution.upload("verify", "result.json", {"objects": [*([source_files] if manifest["operation"] != "design.release" else []), source_zip, dist_zip, output]})
        memory = self.memory.append(execution.memory_scope, "verified", output["sha256"]) if self.memory else None
        execution.stage("verify", [dist_zip, report_ref, large_ref], [output, final], {**verified, "designResult": output, **({"memory": memory} if memory else {})})
        if manifest["operation"] == "design.release" and not verified["passed"]:
            execution.call("fail", {"code": "design-release-verification-failed"})
            return {"status": "failed"}
        return execution.finish(final, "succeeded" if verified["verdict"] == "pass" and verified["approvable"] else "needs_changes")

    def _release(self, execution, manifest, source, context):
        generated = manifest["baseResult"]["generated"]
        bundle = self._bundle(manifest, generated)
        return self._compile_and_verify(execution, manifest, context, source, generated, bundle, {}, {})
