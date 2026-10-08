"""Run the existing exact-bundle assertion/axe verifier in AgentCore Browser."""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from workspace.browser import evaluate_bundle
from workspace.rules import validate_contract
from workspace import browser as verifier, ontology_schema as schema


class Browser:
    def __init__(self, session, identifier, region):
        self.session, self.identifier, self.region = session, identifier, region
        self.client = session.client("bedrock-agentcore", region_name=region)

    def evaluate(self, build, contract, reference=None, visual_tolerance=0.15):
        if build.get("ok") is not True:
            raise ValueError("A successful trusted build is required")
        contract = validate_contract(contract)
        files = {name: base64.b64decode(value, validate=True) for name, value in build["files"].items()}
        started = self.client.start_browser_session(browserIdentifier=self.identifier,
            name="project-ontology-verification", sessionTimeoutSeconds=180, viewPort=contract["viewport"])
        session_id = started["sessionId"]
        try:
            host = "bedrock-agentcore." + self.region + ".amazonaws.com"
            path = f"/browser-streams/{self.identifier}/sessions/{session_id}/automation"
            request = AWSRequest(method="GET", url="https://" + host + path, headers={"host": host})
            SigV4Auth(self.session.get_credentials().get_frozen_credentials(),
                      "bedrock-agentcore", self.region).add_auth(request)
            remote = None

            def context(playwright, viewport):
                nonlocal remote
                if remote is None:
                    remote = playwright.chromium.connect_over_cdp(
                        "wss://" + host + path, headers=dict(request.headers), timeout=45000)
                return remote.new_context(viewport=viewport, device_scale_factor=1,
                    service_workers="block", accept_downloads=False, offline=True)

            result = evaluate_bundle(files, contract, reference, visual_tolerance,
                expected_hash=build["bundleHash"], _context_factory=context)
            axe = Path(os.environ.get("AXE_PATH", str(Path(verifier.__file__).parent / "axe.min.js")))
            if not axe.is_file():
                axe = Path(verifier.__file__).parents[1] / "gates/node_modules/axe-core/axe.min.js"
            profile = {"browserVersion": remote.version if remote is not None else None,
                "viewport": contract["viewport"], "visualTolerance": str(visual_tolerance),
                "verifierHash": hashlib.sha256(Path(verifier.__file__).read_bytes()).hexdigest(),
                "adapterHash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "axeHash": hashlib.sha256(axe.read_bytes()).hexdigest() if axe.is_file() else None}
            if not profile["browserVersion"] or not profile["axeHash"]:
                raise ValueError("The actual browser and verifier profile is unavailable")
            result["browserExecution"] = {"backend": "agentcore-browser", "browserId": self.identifier,
                "sessionId": session_id, "region": self.region, "bundleHash": result["bundleHash"],
                "profile": profile, "profileHash": schema.digest(profile)}
            return result
        finally:
            self.client.stop_browser_session(browserIdentifier=self.identifier, sessionId=session_id)
