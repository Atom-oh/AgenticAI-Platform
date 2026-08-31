---
title: Gateway 심화
description: AgentCore Gateway가 OpenAPI·Smithy·Lambda·MCP 서버를 MCP 툴로 변환하는 메커니즘과 semantic tool search, 인바운드 인가 구조를 실무 관점에서 정리한다.
outline: [2, 3]
---

# Gateway 심화

::: tip 이 장에서 얻는 것
- AgentCore Gateway가 OpenAPI/Smithy/Lambda/기존 MCP 서버를 **하나의 virtual MCP server**로 aggregation하는 정확한 메커니즘과, HTTP target·Inference target과의 차이
- 에이전트가 Gateway와 주고받는 MCP 오퍼레이션(`tools/list`, `tools/call`)과, semantic tool search가 노출하는 `x_amz_bedrock_agentcore_search` 툴의 정확한 호출 방식
- **왜 semantic tool search가 정확도 문제와 직결되는가** — Part 3 [tool-overload](../03-accuracy-eval/tool-overload)에서 다루는 "툴이 많을수록 선택 정확도가 떨어진다"는 문제를 Gateway 레벨에서 구조적으로 완화하는 지점
- 인바운드 인가 4가지 타입(JWT, IAM, `AUTHENTICATE_ONLY`, `NONE`)의 차이와, "오프로드형" 인가를 쓸 때 반드시 어딘가에 인가를 배치해야 한다는 운영 함정
- AgentCore Policy(Cedar)가 Gateway 툴 호출의 인가 지점으로 어떻게 결합되는지에 대한 최소한의 연결점(자세한 내용은 [Part 9](../09-authorization/cedar-verified-permissions)로 이동)
:::

## 왜 문제가 되는가

에이전트가 실제 업무 시스템(주문 조회 API, 환불 Lambda, 사내 Jira, 외부 MCP 서버 등)에 접근하려면 세 가지 문제를 매번 새로 풀어야 한다. 첫째, 각 시스템의 프로토콜(REST, gRPC, 사내 RPC)을 에이전트가 이해하는 MCP 형식으로 번역해야 한다. 둘째, 그 시스템에 접근할 자격 증명(API key, OAuth 토큰, IAM 역할)을 안전하게 보관하고 교체해야 한다. 셋째, 툴이 수십·수백 개로 늘어나면 에이전트가 매 턴마다 전체 툴 목록을 프롬프트에 넣어야 하므로 프롬프트 크기와 툴 선택 정확도가 동시에 나빠진다.

AgentCore Gateway는 이 세 문제를 하나의 관리형 서비스로 묶는다. "Translation"(프로토콜 번역), "Secure Credential Exchange"(자격 증명 교체), "Semantic Tool Selection"(툴 검색)이 Gateway의 핵심 역량으로 명시되어 있다.[[AWS 공식 문서 — Gateway 개요]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html) 이 장은 이 세 역량 중 "번역"과 "검색"을, 그리고 이를 감싸는 인바운드 인가 구조를 다룬다. 자격 증명 교체(egress/outbound 인가)와 툴 호출 자체의 세밀한 인가(Cedar)는 각각 [per-tool-obo](../09-authorization/per-tool-obo)와 [cedar-verified-permissions](../09-authorization/cedar-verified-permissions)에서 다룬다.

## 핵심 개념

### 3개 타겟 카테고리 — MCP / HTTP / Inference

Gateway는 하나의 엔드포인트 뒤에 세 카테고리의 타겟을 붙일 수 있다.[[AWS 공식 문서 — Core concepts]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-core-concepts.html)

- **MCP target** — aggregation 모드로 동작한다. Gateway가 붙은 모든 MCP target의 능력을 합쳐 **하나의 통합 virtual MCP server**로 노출한다. 클라이언트는 개별 target을 구분하지 않고 단일 `tools/list` 응답만 받는다. capability synchronization, semantic tool search, target 레벨 three-legged OAuth(3LO)를 지원한다.
- **HTTP target** — 프로토콜 번역이나 aggregation 없이 요청을 그대로 타겟(AgentCore Runtime 에이전트, 다른 에이전트, A2A 서비스, 임의의 HTTP 엔드포인트)으로 전달한다. path 기반으로 각 타겟에 개별 접근하며, capability synchronization과 semantic tool search를 지원하지 않는다.
- **Inference target** — LLM 추론 요청을 `model` 필드 기준으로 여러 모델 제공자(Bedrock, OpenAI, Anthropic 등)로 라우팅하는 통합 엔드포인트다.

이 장이 다루는 "OpenAPI/Smithy/Lambda/기존 MCP 서버 → MCP 툴 변환"은 **MCP target** 카테고리에 속한다. HTTP target으로 붙인 것은 애초에 aggregation·semantic search 대상이 아니라는 점이 실무에서 자주 혼동되는 지점이다.

### MCP target이 지원하는 5가지 툴 타입

MCP target 카테고리 안에서 Gateway가 실제로 변환하는 소스는 다음과 같다.[[AWS 공식 문서 — Core concepts]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-core-concepts.html)

| 소스 | 변환 방식 |
|---|---|
| OpenAPI 스펙 | REST API를 MCP 호환 툴로 변환. Gateway가 MCP ↔ REST 형식 번역을 자동 처리 |
| Smithy 모델 | AWS 서비스·커스텀 API를 기술하는 Smithy 정의로부터 MCP 호환 툴을 생성 |
| Lambda 함수 | Lambda를 툴로 연결. Gateway가 Lambda를 호출하고 응답을 MCP 형식으로 번역 |
| 기존 MCP 서버 | 원격 MCP 서버의 tools/prompts/resources capability를 그대로 흡수. tools는 필수, prompts·resources는 선택. 동기화 시점에 서버가 광고하는 모든 capability를 discover |
| Integrations / Connectors | Salesforce, Slack, Jira, Asana, Zendesk 등에 대한 사전 구성 템플릿·빌트인 커넥터로 1-click 연동 |

자격 증명 처리 방식은 소스별로 다르다. Smithy·Lambda 타겟은 Gateway에 붙은 **실행 역할(execution role)**을 그대로 사용해 호출한다. OpenAPI·MCP 서버 타겟은 API Key/OAuth 자격 증명을 저장하는 AgentCore Credential Provider를 붙이거나, SigV4 서명 기반 IAM 인가를 쓰거나, 공개 엔드포인트라면 무인가로 둘 수 있다.[[AWS 공식 문서 — Core concepts]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-core-concepts.html) 이 egress 자격 증명 교체의 세부 패턴(caller IAM credentials, token passthrough, on-behalf-of 토큰 교환)은 [per-tool-obo](../09-authorization/per-tool-obo)에서 별도로 다룬다.

### 에이전트가 실제로 호출하는 MCP 오퍼레이션

Gateway는 MCP 프로토콜 버전 `2026-07-28`, `2025-11-25`, `2025-06-18`, `2025-03-26`을 지원한다.[[AWS 공식 문서 — Use an AgentCore gateway]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using.md) 에이전트가 실제로 부르는 오퍼레이션은 아래와 같다.

| 오퍼레이션 | 설명 |
|---|---|
| `tools/list` | Gateway가 제공하는 전체 툴 목록 조회 (이 책과 실무에서 흔히 "list_tools"라 부르는 동작) |
| `tools/call` | 주어진 인자로 특정 툴 호출 ("call_tool") |
| `prompts/list` / `prompts/get` | 프롬프트 템플릿 목록·조회 |
| `resources/list` / `resources/read` / `resources/templates/list` | 리소스 목록·읽기·템플릿 |
| `elicitation/create` / `sampling/createMessage` | 서버가 클라이언트에게 추가 입력·LLM completion을 요청(서버 개시) |
| `notifications/progress` / `notifications/message` | 장시간 작업의 진행 상황·로그를 서버가 스트리밍 |

`2026-07-28`은 **stateless** 프로토콜 개정판이다 — 클라이언트가 `initialize` 핸드셰이크를 하지 않고, 각 요청에 `MCP-Protocol-Version` 헤더와 `_meta` 필드로 프로토콜 버전을 실어 보내며, capability는 `server/discover`로 알아낸다. `2025-11-25` 이하 버전은 여전히 `initialize` 핸드셰이크를 쓴다.[[AWS 공식 문서 — Use an AgentCore gateway]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using.md) 이 차이를 모르고 구버전 클라이언트 SDK로 `2026-07-28` 지원 Gateway에 붙이면 핸드셰이크 관련 오류가 나므로, Gateway를 만들 때 `protocolConfiguration.mcp.supportedVersions`와 클라이언트가 실제로 보내는 버전이 일치하는지 확인해야 한다.

### semantic tool search — `x_amz_bedrock_agentcore_search`

Gateway 생성 시 semantic search를 활성화하면, `tools/list` 응답에 일반 툴과 함께 `x_amz_bedrock_agentcore_search`라는 내장 툴이 노출된다. 에이전트는 이 툴을 `tools/call`로 호출하며 인자는 자연어 `query` 하나뿐이다.[[AWS 공식 문서 — Search for tools with natural language query]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using-mcp-semantic-search.md)

```json
{
  "jsonrpc": "2.0",
  "id": "search-tools-request",
  "method": "tools/call",
  "params": {
    "name": "x_amz_bedrock_agentcore_search",
    "arguments": { "query": "find order information" }
  }
}
```

응답은 쿼리와 관련된 툴 목록이다. 즉 에이전트는 매 턴마다 전체 툴 목록을 프롬프트에 넣는 대신, 태스크 맥락에 맞는 부분집합만 요청할 수 있다. Gateway 개요 문서는 이를 "Semantic Tool Selection — 에이전트가 수천 개의 툴을 쓰면서도 프롬프트 크기를 줄이고 지연을 낮추도록 한다"고 설명한다.[[AWS 공식 문서 — Gateway 개요]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)

**이 기능이 중요한 이유는 정확도 문제와 직결되기 때문이다.** Part 3 [tool-overload](../03-accuracy-eval/tool-overload)에서 다루듯, LLM에 노출되는 툴 개수가 늘어날수록 올바른 툴을 고를 확률이 떨어진다 — 선택지가 늘어날수록 프롬프트 안에서 각 툴 설명이 상대적으로 희석되고, 유사한 이름·용도의 툴 사이에서 혼동이 늘어난다. semantic tool search는 이 문제를 "애초에 프롬프트에 넣는 툴 개수를 쿼리 시점에 줄인다"는 방식으로 구조적으로 완화한다. 벡터 인덱스는 Gateway aggregation 계층에 내장되어 있으므로(MCP target에만 지원되는 것이 이 때문이다), 별도의 외부 검색 서비스를 호출하는 추가 네트워크 홉이 생기지 않는다 — 검색도 결국 `tools/call` 한 번의 MCP 라운드트립으로 끝난다.

::: warning 미정착 영역
"툴 300개를 semantic search로 쿼리당 10~15개로 줄인다"는 식의 구체적인 축소 비율은 이번 리서치에서 AWS 공식 문서나 검증 가능한 출처를 찾지 못했다. 커뮤니티 발표·블로그에서 이런 수치가 언급되는 경우가 있으나, 이 책의 수치 인용 원칙(출처 없는 수치는 쓰지 않는다)에 따라 여기서는 구체적인 비율을 단정하지 않는다. 자체 벤치마크로 "쿼리당 반환 툴 개수"와 "전체 툴 대비 비율"을 측정해 이 자리를 채우는 것을 권장한다.
:::

운영 임계치는 이 책 전체에서 일관되게 **툴이 50개를 넘기기 전에** semantic tool search를 도입하라는 것이다([00-intro/six-pain-points](../00-intro/six-pain-points), [03-accuracy-eval/tool-overload](../03-accuracy-eval/tool-overload)). 50개는 "이 시점부터 정확도가 급락한다"는 정밀한 관측값이 아니라, 컨텍스트 사용량과 캐시 안정성(툴 목록이 프롬프트 앞부분에 있고 캐시 브레이크포인트에 영향을 준다는 점, [Part 4](../04-caching/cache-miss-root-causes))을 함께 고려한 이 책의 운영 권고이므로, 자신의 워크로드에서 재현되는지 직접 측정하는 것이 안전하다.

### 인바운드 인가 — 누가 Gateway를 호출할 수 있는가

Gateway는 4가지 인바운드 인가 타입을 지원한다.[[AWS 공식 문서 — Set up inbound authorization]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-inbound-auth.html)

- **JWT(OAuth)** — `CustomJWTAuthorizerConfiguration`으로 discovery URL, client ID, allowed audience/scope 등을 지정. Cognito를 포함한 임의의 OIDC identity provider를 쓸 수 있다. 토큰이 없거나 유효하지 않으면 `401`, scope가 부족하면 `403`을 반환하며 `WWW-Authenticate` 헤더에 RFC 6750 형식으로 필요한 scope와 `resource_metadata`(`/.well-known/oauth-protected-resource`)를 광고한다 — MCP 호환 클라이언트가 자동으로 필요 scope를 찾아낼 수 있게 하는 설계다.
- **IAM(SigV4)** — 호출자의 IAM 자격 증명으로 인가. `bedrock-agentcore:InvokeGateway`를 특정 Gateway ARN으로 스코프해 부여한다.
- **`AUTHENTICATE_ONLY`** — SigV4 서명은 검증하지만 인가 결정은 내리지 않는다. 서명된 요청이면 누구든 타겟까지 전달된다.
- **`NONE`** — 인증도 인가도 하지 않는다.

뒤 두 타입은 "오프로드형(offloaded)" 인가다 — Gateway 자신은 인가 결정을 내리지 않고, 다운스트림 타겟, Gateway에 붙은 정책 엔진(AgentCore Policy/Cedar), 또는 interceptor Lambda 중 하나에 인가를 위임한다.[[AWS 공식 문서 — Set up inbound authorization]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-inbound-auth.html) 문서는 이 조합을 "기존 런타임의 인증·인가를 바꾸지 않고 Gateway를 앞단에 두는" 점진적 온보딩 패턴으로 명시한다 — 예를 들어 IAM 런타임은 `AUTHENTICATE_ONLY` + `CALLER_IAM_CREDENTIALS`(호출자 신원 그대로 서명 전달)로, OAuth 런타임은 `NONE` + `JWT_PASSTHROUGH`(인바운드 토큰을 그대로 전달)로 묶어 기존 인가를 그대로 유지한 채 Gateway 기능을 점진 도입할 수 있다. 다만 문서는 `JWT_PASSTHROUGH`를 프로덕션 권장 패턴이 아니라고 명시하며, 장기적으로는 캐치된 토큰을 그대로 재사용하는 대신 대상별로 새로 발급하는 **on-behalf-of(OBO) 토큰 교환**으로 옮기라고 권고한다.[[AWS 공식 문서 — Set up inbound authorization]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-inbound-auth.html)

### AgentCore Policy(Cedar)와의 결합점

`AUTHENTICATE_ONLY`나 `NONE`으로 인바운드 인가를 오프로드했을 때 실무에서 가장 흔히 선택하는 "정책 엔진" 경로가 AgentCore Policy(Cedar)다. Cedar 정책은 `tools/call` 요청 시점에 개별 툴 호출을 ALLOW/DENY로 결정하고, partial evaluation으로 항상 거부될 툴을 `tools/list` 응답 자체에서 걸러낸다 — 이 필터링이 semantic tool search와 함께 작동하면, 애초에 호출 불가능한 툴은 벡터 인덱스 검색 결과에도 노출되지 않아야 한다는 점이 정합성 확인 포인트다. AgentCore Policy의 4개 집행점, principal/context 조건 문법, LOG_ONLY → ENFORCE 전환 절차는 이 장에서 반복하지 않는다 — [Part 9 Cedar와 Verified Permissions](../09-authorization/cedar-verified-permissions)를 참고한다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| REST API를 툴화해야 함 | OpenAPI target | 기존 스펙 재사용, MCP↔REST 번역을 Gateway가 처리 | 스펙 품질(operation description)이 곧 툴 설명 품질 — 부실한 스펙은 부실한 툴 설명으로 이어짐 |
| AWS 서비스·커스텀 API를 스펙 기반으로 노출 | Smithy target | Smithy 정의 재사용, 실행 역할로 바로 호출 | Smithy 생태계에 대한 이해 필요, OpenAPI보다 생태계가 좁음 |
| 커스텀 비즈니스 로직을 임의 언어로 구현 | Lambda target | 언어 자유도, 실행 역할 기반 단순 인증 | Lambda 콜드스타트·타임아웃이 툴 호출 지연에 그대로 반영 |
| 이미 MCP 서버가 있는 팀 리소스를 통합 | MCP server target | tools/prompts/resources를 그대로 흡수, 재구현 불필요 | 대상 MCP 서버의 가용성·버전 변경이 Gateway aggregation에 즉시 영향 |
| 다른 에이전트/A2A 서비스에 라우팅 | HTTP target | 프로토콜 변환 없이 그대로 전달 | aggregation·semantic search 미지원 — 별도 주소로 접근해야 함 |
| 툴이 50개 미만이고 당장 늘어날 계획이 없음 | semantic search 비활성 유지 | 불필요한 복잡도 회피 | 툴이 늘어난 뒤 뒤늦게 켜면 마이그레이션·프롬프트 재설계 비용 발생 |
| 툴이 50개에 근접하거나 이미 초과 | semantic search 활성화 + `x_amz_bedrock_agentcore_search` 노출 | `tools/list` 프롬프트 부담과 오선택률 완화 | 벡터 인덱스 최신화 지연, 검색 recall 튜닝 필요 |
| 신규 런타임을 기존 인증 그대로 온보딩 | `AUTHENTICATE_ONLY`/`NONE` + 매칭 outbound(caller IAM/JWT passthrough) | 런타임 인증 로직 변경 없이 점진 도입 | 인가가 Gateway에는 없다는 점을 명확히 인지해야 함 — 다운스트림이 반드시 인가를 해야 함 |
| 장기 프로덕션에서 세밀한 툴콜 인가 필요 | AgentCore Policy(Cedar) 부착 | JWT 클레임 기반 결정론적 인가, `tools/list` 필터링까지 자동 | 정책 작성·Cedar analysis 운영 부담 ([Part 9](../09-authorization/cedar-verified-permissions) 참고) |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| HTTP target으로 붙인 타겟이 semantic search 결과에 안 나옴 | HTTP target은 aggregation·semantic search 미지원 — 설계상 정상 동작 | 해당 타겟이 MCP target인지 HTTP target인지 Gateway 설정 확인 | MCP target으로 재구성하거나, HTTP target은 별도 경로로 직접 호출하도록 에이전트 로직 분리 |
| `2026-07-28` 클라이언트 SDK로 붙였는데 `initialize` 관련 오류 | 이 버전은 stateless라 `initialize` 핸드셰이크 자체가 없음 — 구버전 클라이언트 코드가 여전히 핸드셰이크를 기대 | 클라이언트가 보내는 `MCP-Protocol-Version`/`_meta` 필드와 Gateway의 `supportedVersions` 비교 | 클라이언트를 stateless 흐름(`server/discover`)에 맞게 갱신하거나, Gateway `supportedVersions`를 구버전 포함으로 조정 |
| semantic search를 켰는데 필요한 툴이 검색 결과에서 빠짐 | 벡터 인덱스가 툴 설명(OpenAPI operation description 등) 기반이라, 설명이 부실하거나 모호하면 recall이 떨어짐 | 해당 툴의 원본 스펙 description 품질 확인 | operation description을 구체적인 사용 시나리오 문구로 보강 — Gateway가 이 설명으로 MCP 툴 설명을 생성하므로 스펙 품질이 곧 검색 품질 |
| `AUTHENTICATE_ONLY`/`NONE`으로 두고 "인증됐으니 인가도 됐다"고 오인 | 두 타입 모두 Gateway 자체의 인가 결정이 없다는 것을 문서가 명시적으로 경고 | 다운스트림 타겟이나 정책 엔진이 실제로 인가 로직을 갖고 있는지 별도 확인 | 정책 엔진(AgentCore Policy) 부착 또는 interceptor Lambda로 인가를 명시적으로 배치 |
| `JWT_PASSTHROUGH`를 프로덕션에 그대로 방치 | 온보딩 편의를 위한 임시 패턴을 영구화 — 캡처된 토큰이 Gateway와 타겟 양쪽에서 모두 유효해 audience 스코프가 넓으면 위험 | 해당 target의 outbound authorization 타입이 여전히 `JWT_PASSTHROUGH`인지 정기 점검 | on-behalf-of(OBO) 토큰 교환으로 전환 — 대상별로 audience가 좁은 새 토큰을 발급받도록 변경 |
| OpenAPI target 생성 후 MCP 툴 설명이 전부 의미 없는 문자열로 나옴 | 스펙에 operation description을 채우지 않고 업로드 | 생성된 `tools/list` 응답의 description 필드가 스펙 원문과 일치하는지 확인 | 스펙에 명확한 operation description을 채운 뒤 target 재생성/업데이트 |

## 안티패턴

- ❌ 툴이 100개를 넘도록 방치하고 나서야 semantic search 도입을 검토한다 → ✅ 50개 임계치를 도구 개수 계측(SLI)에 걸어두고 미리 활성화한다.
- ❌ HTTP target을 MCP target처럼 취급하며 semantic search가 안 되는 이유를 못 찾는다 → ✅ 타겟 카테고리(MCP/HTTP/Inference)별 지원 기능 차이를 설계 시점에 문서화한다.
- ❌ `AUTHENTICATE_ONLY`/`NONE`을 "테스트니까 괜찮다"는 이유로 프로덕션까지 끌고 간다 → ✅ 오프로드형 인가를 쓰는 순간 정책 엔진이나 interceptor Lambda로 인가 배치 지점을 명시적으로 결정한다.
- ❌ `JWT_PASSTHROUGH`를 "일단 되니까" 영구 패턴으로 쓴다 → ✅ 온보딩 초기 단계로만 쓰고 OBO 토큰 교환으로 이행 계획을 세운다.
- ❌ OpenAPI/Smithy 스펙의 operation description을 비워둔 채 target을 만든다 → ✅ 스펙 description을 곧 MCP 툴 설명으로 취급해 처음부터 구체적으로 작성한다.

## 계측 (SLI)

- **Gateway에 등록된 MCP 툴 총 개수**: 50개 임계치 전환을 판단하는 1차 신호. 타겟 추가마다 누적 개수를 CI/배포 파이프라인에서 추적.
- **`tools/call` 성공률과 지연(타겟 카테고리별)**: OpenAPI/Smithy/Lambda/MCP server 타겟별로 분리해 어느 변환 경로가 실패·지연을 유발하는지 구분.
- **`x_amz_bedrock_agentcore_search` 호출당 반환 툴 개수와 recall**: 실제로 필요한 툴이 결과에 포함되는지 정기 샘플링으로 검증 — 자체 벤치마크 대상.
- **인바운드 인가 오류율(`401`/`403`) 및 원인 분포**: scope 부족(`insufficient_scope`)과 토큰 부재를 구분해 어느 쪽이 늘고 있는지 관찰.
- **오프로드형 인가(`AUTHENTICATE_ONLY`/`NONE`) 타겟 비율**: 이 값이 0이 아니라면 해당 타겟마다 정책 엔진·interceptor Lambda·다운스트림 인가 중 무엇이 실제로 인가를 담당하는지 매핑 표를 유지해야 한다.

## 체크리스트

- [ ] 각 타겟이 MCP/HTTP/Inference 중 어느 카테고리인지, 그리고 semantic search·aggregation 지원 여부를 알고 있다
- [ ] OpenAPI/Smithy 스펙의 operation description을 실제 사용 시나리오 기준으로 채웠다
- [ ] 클라이언트가 보내는 MCP 프로토콜 버전이 Gateway의 `supportedVersions`와 일치한다 (특히 `2026-07-28` stateless 흐름 여부)
- [ ] 등록된 MCP 툴 총 개수를 계측하고 있고, 50개 임계치 전에 semantic tool search 활성화 계획이 있다
- [ ] `x_amz_bedrock_agentcore_search`의 recall을 자체 벤치마크로 정기 검증한다
- [ ] 인바운드 인가 타입(JWT/IAM/`AUTHENTICATE_ONLY`/`NONE`)을 명시적으로 선택했고, 오프로드형이라면 인가 책임 소재(정책 엔진/interceptor Lambda/다운스트림)를 문서화했다
- [ ] `JWT_PASSTHROUGH`를 쓰고 있다면 이것이 임시 온보딩 패턴이며 OBO로 이행할 계획이 있음을 인지하고 있다
- [ ] Cedar 기반 툴콜 인가가 필요한 경우 [Part 9](../09-authorization/cedar-verified-permissions)의 4개 집행점과 LOG_ONLY→ENFORCE 절차를 별도로 검토했다

## 참고

- [Amazon Bedrock AgentCore Gateway — 개요](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [Core concepts for Amazon Bedrock AgentCore Gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-core-concepts.html)
- [Supported targets for Amazon Bedrock AgentCore gateways](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-supported-targets.html)
- [Amazon Bedrock AgentCore Gateway features](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-features.html)
- [Use an AgentCore gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using.md)
- [Search for tools in your AgentCore gateway with a natural language query](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using-mcp-semantic-search.md)
- [Set up inbound authorization for your gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-inbound-auth.html)
- [Part 3 — 툴 과부하](../03-accuracy-eval/tool-overload)
- [Part 9 — Cedar와 Verified Permissions](../09-authorization/cedar-verified-permissions)
