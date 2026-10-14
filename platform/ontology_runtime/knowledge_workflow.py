"""One measured guideline extraction followed by independent proposal validation."""
from __future__ import annotations

import json

from design_loop import guide_rules
from ontology_runtime.design_model import DesignModel
from ontology_runtime.execution_session import ExecutionSession
from workspace.knowledge_execution import OPERATIONS, verify_proposal, condition_ids


class KnowledgeWorkflow:
    def __init__(self, gateway, signer, configuration, *, model_factory=DesignModel):
        self.gateway, self.signer, self.configuration, self.model_factory = gateway, signer, configuration, model_factory

    def run(self, event, session_id):
        execution = ExecutionSession(self.gateway, self.signer, event, session_id)
        try:
            raw, source = execution.read()
            manifest = json.loads(raw)
            if (manifest["operation"] not in OPERATIONS or execution.job["operation"] != manifest["operation"]
                    or manifest["tool"] != self.configuration["interpreter"]
                    or manifest["admissions"] != execution.job["admissions"] or manifest["model"] != execution.job["model"]):
                raise ValueError("knowledge-manifest-mismatch")
            context = execution.upload("context", "context.json", {"input": source["sha256"],
                                      "snapshot": manifest["snapshot"]["hash"], "approvalEffect": False})
            execution.stage("context", [source], [context], {})
            model = self.model_factory(execution, manifest)
            if manifest["operation"] == "design.derive":
                from workspace.derive_proposals import SYSTEM, prompt
                response = model.generate(SYSTEM, prompt(manifest))
            else:
                response = model.generate(guide_rules._SYSTEM,
                    guide_rules._prompt(manifest["pages"], manifest["nodeIds"], condition_ids(manifest["snapshot"])))
            proposal = execution.upload("generate", "proposal.json", {"response": response})
            boundary = execution.upload("generate", "model-boundary.json", model.boundaries)
            execution.stage("generate", [source, context], [proposal, boundary],
                            {"proposal": proposal, "boundary": boundary}, model.service())
            checked = verify_proposal(manifest, response)
            result = execution.upload("verify", "knowledge.json", checked)
            final = execution.upload("verify", "result.json", {"objects": [result]})
            code = "derivation-proposal-rejected" if manifest["operation"] == "design.derive" else "guideline-proposal-rejected"
            issues = [{"code": code, "count": len(checked["rejected"])}] if checked["rejected"] else []
            execution.stage("verify", [proposal], [result, final], {"knowledgeResult": result, "issues": issues})
            return execution.finish(final, "needs_changes" if issues else "succeeded")
        except Exception:
            try:
                execution.call("fail", {"code": "knowledge-execution-failed"})
            except Exception:
                pass
            raise
        finally:
            execution.close()
