import json
import os
import urllib.parse
import urllib.request

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent, tool
from strands.tools.mcp import MCPClient

from harness.publish import publish_draft

app = BedrockAgentCoreApp()

SYSTEM = """You are Hana Bank's UI/UX design-draft agent.
Workflow, strictly in order:
1. get_skill('hana-design-system'), get_skill('design-draft-html'),
   get_skill('a11y-finance') — follow them exactly.
2. list_design_tokens and get_brand_guideline — tokens are law.
3. Generate exactly 3 self-contained HTML draft variants for the brief.
   Each variant moves ONE axis (밀도, 강조, 흐름) per the design-draft-html skill.
4. Call publish_draft(title, axis, html) once per variant.
Finish with a short Korean summary of the three variants and their axes."""


def _m2m_token() -> str:
    secret = boto3.client("secretsmanager").get_secret_value(
        SecretId=os.environ["M2M_SECRET_ARN"])["SecretString"]
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials", "scope": "hana-mcp/invoke",
        "client_id": os.environ["M2M_CLIENT_ID"], "client_secret": secret}).encode()
    req = urllib.request.Request(
        f"https://{os.environ['USER_POOL_DOMAIN']}/oauth2/token", data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())["access_token"]


@app.entrypoint
def invoke(payload):
    brief = payload.get("brief", "")
    if not brief:
        return {"error": "payload must include 'brief'"}
    published = []

    @tool
    def publish_draft_tool(title: str, axis: str, html: str) -> str:
        """Publish one HTML draft variant to the gallery. Returns its public URL.

        Args:
            title: short Korean title including the variant flavor
            axis: one of 밀도, 강조, 흐름
            html: complete self-contained HTML document
        """
        url = publish_draft(title, axis, html)
        draft_id = url.rsplit("/", 1)[1].removesuffix(".html")
        published.append({"id": draft_id, "title": title, "axis": axis, "url": url})
        return url

    token = _m2m_token()
    gateway = MCPClient(lambda: streamablehttp_client(
        os.environ["GATEWAY_URL"], headers={"Authorization": f"Bearer {token}"}))
    with gateway:
        agent = Agent(model=os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-5"),
                      system_prompt=SYSTEM,
                      tools=gateway.list_tools_sync() + [publish_draft_tool])
        result = agent(brief)
    return {"drafts": published, "summary": str(result)}


if __name__ == "__main__":
    app.run()
