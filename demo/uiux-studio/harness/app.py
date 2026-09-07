import json
import os
from datetime import datetime, timezone
import urllib.parse
import urllib.request

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent, tool
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

from harness.publish import get_draft_html, load_approved_patterns
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

OUTPUT_STYLES = {
    "design": "",
    "mockup": ("출력 유형: 목업(Mockup). 실서비스 수준 완성도 대신 빠른 검토용 목업 톤으로 — "
               "이미지 영역은 회색 플레이스홀더 박스, 데이터는 대표 샘플 1~2건만."),
    "wireframe": ("출력 유형: 와이어프레임(Wireframe). 로우파이 구조 스케치로 — 흑백+회색만 사용"
                  "(브랜드 컬러 금지), 박스/라인/원 플레이스홀더, 텍스트는 실제 레이블만, "
                  "이미지·아이콘은 X 표시된 회색 박스, 점선 테두리로 스케치 느낌."),
    "ux-flow": ("출력 유형: UX 플로우. 한 HTML 안에 가로 스크롤 컨테이너로 화면 프레임 3~5개를 "
                "순서대로 배치하고, 프레임 사이를 화살표(→)와 트리거 레이블(탭/입력/제출)로 연결해 "
                "하나의 사용자 흐름을 보여줘라. 각 프레임은 390px 폭 모바일 화면."),
}

REFINE_SYSTEM = """You are Hana Bank's UI/UX design refinement agent.
원본 HTML 전체가 주어진다. 사용자가 클릭으로 선택한 요소(selector와 요소 HTML)와
수정 지시에 따라 그 부분만 수정하고, 나머지 마크업·스타일·텍스트는 그대로 보존하라.
지시가 전체 톤 변경을 요구하면 필요한 최소 범위만 함께 조정한다.
완성된 전체 HTML을 publish_draft로 정확히 1회 발행하라 (title: 원본 제목 유지 + " (수정)",
axis: "수정"). 발행 후 무엇을 바꿨는지 한 문장 한국어 요약으로 마쳐라."""


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


def _recall_designer_memory(actor: str, brief: str) -> str:
    """Long-term designer preferences from AgentCore Memory (best effort)."""
    memory_id = os.environ.get("MEMORY_ID", "")
    if not memory_id or not actor:
        return ""
    try:
        client = boto3.client("bedrock-agentcore")
        records = client.retrieve_memory_records(
            memoryId=memory_id, namespace=f"/designers/{actor}",
            searchCriteria={"searchQuery": brief, "topK": 5})
        texts = [r.get("content", {}).get("text", "")
                 for r in records.get("memoryRecordSummaries", [])]
        texts = [t for t in texts if t]
        return "\n".join(f"- {t}" for t in texts)
    except Exception:
        return ""


def _record_generation(actor: str, brief: str, published: list):
    memory_id = os.environ.get("MEMORY_ID", "")
    if not memory_id or not actor:
        return
    try:
        titles = ", ".join(d["title"] for d in published)
        boto3.client("bedrock-agentcore").create_event(
            memoryId=memory_id, actorId=actor,
            sessionId=f"generate-{published[0]['id'] if published else 'none'}",
            eventTimestamp=datetime.now(timezone.utc),
            payload=[{"conversational": {
                "content": {"text": f"디자이너 {actor}가 브리프 '{brief}'로 시안을 "
                                    f"생성함: {titles}"},
                "role": "USER"}}])
    except Exception:
        pass


@app.entrypoint
def _run_flow(payload):
    """공유 엔진(design_loop): 상품명세서 → PRD → 프로세스 화면 → 리뷰·테스트 → 리포트. 스텝을 갤러리 드래프트로 발행."""
    import design_loop
    import design_deps as dd
    specs, sm, checklists = dd.load_seed()
    spec = next((x for x in specs if x.get("id") == payload.get("product_spec_id")), specs[0] if specs else None)
    if not spec or not sm:
        return {"error": "design_seed 자산이 없습니다 (product_specs/sm_model)"}
    model_id = payload.get("model_id") or os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-5")
    deps = dd.make_deps(model_id)
    res = design_loop.run(spec, sm, checklists, deps, output_type=payload.get("output_type", "design"))
    if res.get("error"):
        return {"error": res["error"], "code": res.get("code"), "missing": res.get("missing")}
    published = []
    for st in (res.get("flow") or {}).get("steps") or []:
        url = publish_draft_s3(f"{spec['productName']} · {st.get('title')}", "스텝", st.get("html", ""))
        published.append({"id": url.rsplit("/", 1)[1].removesuffix(".html"), "title": st.get("title"), "axis": "스텝", "url": url})
    rep = res.get("report") or {}
    return {"drafts": published, "report": {k: rep.get(k) for k in ("score", "openItems", "attempts", "regenerated")},
            "prd": {"steps": [s2.get("id") for s2 in (res.get("prd") or {}).get("steps") or []],
                    "branchSteps": (res.get("prd") or {}).get("branchSteps")},
            "ok": res.get("ok"), "usage": deps["usage"](), "model_id": model_id,
            "engine": "design_loop (platform 공유 엔진)"}


def invoke(payload):
    if payload.get("mode") == "flow":
        return _run_flow(payload)
    brief = payload.get("brief", "")
    if not brief:
        return {"error": "payload must include 'brief'"}
    asset_ids = list(payload.get("asset_ids") or [])
    agent_cfg = payload.get("agent_cfg") or {}
    actor = payload.get("actor", "")
    model_id = (payload.get("model_id") or agent_cfg.get("model_id")
                or os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-5"))
    asset_ids += [a for a in agent_cfg.get("asset_ids", []) if a not in asset_ids]
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

    if payload.get("mode") == "refine":
        base_id = payload.get("base_draft_id", "")
        base_html = get_draft_html(base_id)
        user_msg = (f"수정 지시: {brief}\n"
                    f"선택 요소 selector: {payload.get('selector', '')}\n"
                    f"선택 요소 HTML:\n{payload.get('element_html', '')}\n\n"
                    f"### 원본 전체 HTML\n```html\n{base_html}\n```")

        @tool
        def publish_refined(title: str, html: str) -> str:
            """Publish the refined full HTML as a new draft version. Call exactly once.

            Args:
                title: 원본 제목 + " (수정)"
                html: 수정이 반영된 완전한 HTML 문서 전체
            """
            url = publish_draft_s3(title, "수정", html, parent_id=base_id)
            draft_id = url.rsplit("/", 1)[1].removesuffix(".html")
            published.append({"id": draft_id, "title": title, "axis": "수정", "url": url,
                              "parent_id": base_id})
            return url

        model = BedrockModel(model_id=model_id, max_tokens=32000)
        agent = Agent(model=model, system_prompt=REFINE_SYSTEM, tools=[publish_refined])
        result = agent(user_msg)
        usage = {}
        try:
            u = result.metrics.accumulated_usage
            usage = {"inputTokens": int(u.get("inputTokens", 0)),
                     "outputTokens": int(u.get("outputTokens", 0)),
                     "totalTokens": int(u.get("totalTokens", 0))}
        except Exception:
            pass
        _record_generation(actor, f"[수정] {brief}", published)
        return {"drafts": published, "summary": str(result), "usage": usage,
                "model_id": model_id}

    patterns = load_approved_patterns(limit=2)
    system = SYSTEM
    style = OUTPUT_STYLES.get(payload.get("output_type", "design"), "")
    if style:
        system += "\n\n" + style
    if agent_cfg.get("system"):
        system += ("\n\n### 공유 에이전트 프리셋: " + agent_cfg.get("name", "agent") +
                   "\n" + agent_cfg["system"])
    if agent_cfg.get("skills"):
        system += ("\n\n프리셋이 요구하는 추가 스킬을 get_skill로 로드해 따르라: " +
                   ", ".join(agent_cfg["skills"]))
    memories = _recall_designer_memory(actor, brief)
    if memories:
        system += ("\n\n### 이 디자이너에 대한 기억 (AgentCore Memory)\n" + memories +
                   "\n위 취향/피드백 이력을 시안에 반영하되, 브리프와 선택 자산이 우선한다.")
    if patterns:
        refs = "\n\n".join(
            f"### 승인 패턴: {p['title']} (axis: {p['axis']})\n```html\n{p['html']}\n```"
            for p in patterns)
        system += ("\n\nBelow are org-approved reference drafts. Follow their "
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
            model_id=model_id,
            max_tokens=32000)  # full HTML drafts overflow the default output cap
        agent = Agent(model=model,
                      system_prompt=system,
                      tools=gateway.list_tools_sync() + [publish_draft])
        result = agent(brief)

    usage = {}
    try:
        u = result.metrics.accumulated_usage
        usage = {"inputTokens": int(u.get("inputTokens", 0)),
                 "outputTokens": int(u.get("outputTokens", 0)),
                 "totalTokens": int(u.get("totalTokens", 0))}
    except Exception:
        pass
    _record_generation(actor, brief, published)
    return {"drafts": published, "summary": str(result), "usage": usage,
            "model_id": model_id}


if __name__ == "__main__":
    app.run()
