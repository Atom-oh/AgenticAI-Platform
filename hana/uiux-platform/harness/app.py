import json
import os
import urllib.parse
import urllib.request

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent, tool
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

from harness.publish import load_approved_patterns
from harness.publish import publish_draft as publish_draft_s3

app = BedrockAgentCoreApp()

SYSTEM = """You are Hana Bank's UI/UX design-draft agent.
Workflow, strictly in order:
1. Use get_skill to load hana-design-system, design-draft-html, and
   a11y-finance — follow them exactly.
2. Use list_design_tokens and get_brand_guideline — tokens are law.
3. Generate exactly 3 self-contained HTML draft variants for the brief.
   Each variant moves ONE axis (밀도, 강조, 흐름) per the design-draft-html skill.
4. Use publish_draft once per variant.
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
    asset_ids = payload.get("asset_ids") or []
    published = []

    @tool
    def publish_draft(title: str, axis: str, html: str) -> str:
        """Publish one HTML draft variant to the gallery. Returns its public URL.

        Args:
            title: short Korean title including the variant flavor
            axis: one of 밀도, 강조, 흐름
            html: complete self-contained HTML document
        """
        url = publish_draft_s3(title, axis, html)
        draft_id = url.rsplit("/", 1)[1].removesuffix(".html")
        published.append({"id": draft_id, "title": title, "axis": axis, "url": url})
        return url

    patterns = load_approved_patterns(limit=2)
    system = SYSTEM
    if patterns:
        refs = "\n\n".join(
            f"### 승인 패턴: {p['title']} (axis: {p['axis']})\n```html\n{p['html']}\n```"
            for p in patterns)
        system = SYSTEM + ("\n\nBelow are org-approved reference drafts. Follow their "
                           "structure and quality bar; do not copy their brief-specific "
                           "content.\n\n" + refs)
    if asset_ids:
        system += ("\n\n사용자가 선택한 자산: " + ", ".join(asset_ids) +
                   ". 생성 전에 각 id에 대해 get_asset을 호출해 내용을 확인하고, "
                   "이 자산들을 최우선 규범으로 사용하라 (선택 자산과 충돌하는 기본 토큰 "
                   "값은 선택 자산이 이긴다). type이 workflow인 자산은 화면 흐름 제약으로, "
                   "style-guide는 스타일 규범으로, skill은 추가 지침으로 따르라.")

    token = _m2m_token()
    gateway = MCPClient(lambda: streamablehttp_client(
        os.environ["GATEWAY_URL"], headers={"Authorization": f"Bearer {token}"}))
    with gateway:
        model = BedrockModel(
            model_id=os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-5"),
            max_tokens=32000)  # full HTML drafts overflow the default output cap
        agent = Agent(model=model,
                      system_prompt=system,
                      tools=gateway.list_tools_sync() + [publish_draft])
        result = agent(brief)
    return {"drafts": published, "summary": str(result)}


if __name__ == "__main__":
    app.run()
