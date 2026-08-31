---
title: Cedar와 Verified Permissions
description: AgentCore Policy(Cedar)의 4개 집행점과 Amazon Verified Permissions의 역할 분담을 실무 관점에서 정리한다.
outline: [2, 3]
---

# Cedar와 Verified Permissions

::: tip 이 장에서 얻는 것
- AgentCore Policy가 Gateway 툴 호출에 Cedar를 어떻게 적용하는지 — **4개 집행점**(정책 작성, 정책셋 분석, 툴 호출 집행, 툴 필터링)의 역할 구분
- `list_tools` 단계의 partial evaluation 필터링이 정확도와 프롬프트 인젝션 방어에 동시에 기여하는 이유
- JWT 클레임(위조 불가) vs LLM이 만든 툴 인자(정책으로 제약 가능)라는 구분이 왜 이 아키텍처의 핵심 논지인지
- Cedar 정책 문법과 액션 네이밍 규칙(`TargetName___tool_name`), temporal 정책(Dogwood)의 위치
- AgentCore Policy와 Amazon Verified Permissions(AVP)를 언제 병용해야 하는지에 대한 결정 기준
- LOG_ONLY → ENFORCE 전환 운영 절차와 IAM 권한 체크포인트
:::

## 왜 문제가 되는가

에이전트가 MCP 툴을 호출할 때, 가장 흔한 인가 실패는 "허용 목록에 없는 툴은 막았지만, 허용된 툴에 어떤 인자를 넘기는지는 아무도 검증하지 않는다"는 형태로 나타난다. 예를 들어 `process_refund` 툴 호출 자체는 화이트리스트에 있지만, 환불 금액이 $50,000이어도, 호출 주체가 실제로는 다른 고객이어도 막을 방법이 없다면 툴 레벨 인가는 무의미하다.

LLM이 만들어내는 것은 두 가지다. 하나는 "어떤 툴을 호출할지"이고, 다른 하나는 "그 툴에 어떤 인자를 넘길지"이다. 둘 다 환각(hallucination)의 대상이 될 수 있다. 반면 OAuth 토큰의 JWT 클레임(고객 tier, 역할, username 등)은 LLM이 임의로 조작할 수 없는 신뢰할 수 있는 신호다. AgentCore Policy의 핵심 설계 논지는 이 구분에서 출발한다 — **위조 불가능한 신원 신호(JWT 클레임)를 principal 조건으로, LLM이 생성한 가변 입력(툴 인자)을 context 조건으로 분리해 Cedar 정책으로 결정론적으로 제약**하면, 환각된 인자가 있어도 정책이 거부한다.

이 장은 Part 9의 "3중 방어"(Cedar + On-Behalf-Of + Knowledge Base 메타데이터 필터) 구조 중 첫 번째 축을 다룬다. 나머지 두 축은 [per-tool-obo](./per-tool-obo)와 [rag-entitlement-scoping](./rag-entitlement-scoping)에서 다룬다.

## 핵심 개념

### AgentCore Policy: Gateway 내장 툴콜 인가층

Amazon Bedrock AgentCore Gateway는 MCP 서버, Lambda, OpenAPI/Smithy 스펙을 하나의 툴 액세스 포인트로 묶는다. Policy in AgentCore(AgentCore Policy)는 이 Gateway에 붙는 Cedar 기반 결정론적 인가 계층이다.[[AWS 공식 문서]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-core-concepts.html) Policy Engine을 Gateway에 연결하면 **기본 전면 차단(default deny)**이 적용된다 — 어떤 요청과도 매칭되는 permit 정책이 없으면 무조건 DENY이며, 이는 명시적으로 별도 설정하는 것이 아니라 Cedar 평가 알고리즘 자체의 성질이다.[[Understanding Cedar policies]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-understanding-cedar.html)

> ⚠️ 리서치 시점(2026-08) 기준으로 AWS 공식 What's New의 GA 공지 URL은 확인하지 못했다. 다만 devguide가 프리뷰 표시 없이 게시되어 있고, 2026-03 전후로 다수의 AWS 공식 블로그(보안 블로그, ML 블로그)가 실사용 절차를 상세히 다루고 있어 리서치 시점 기준 GA 상태로 판단한다. 정확한 GA 일자가 필요하다면 AWS What's New를 직접 재확인할 것.

### 4개 집행점

AgentCore Policy는 하나의 게이트가 아니라 파이프라인 전체에 걸친 4개의 집행점으로 구성된다.[[Why Policy in AgentCore chose Cedar]](https://aws.amazon.com/blogs/security/why-policy-in-amazon-bedrock-agentcore-chose-cedar-for-securing-agentic-workflows/)

1. **정책 작성(Policy authoring)** — neuro-symbolic 검증. LLM이 자연어 요구사항을 Cedar 정책으로 번역하고, 생성된 정책은 Cedar schema validation과 automated reasoning analysis를 거쳐 논리적 모순·과도한 허용/차단을 걸러낸다.[[Core concepts]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-core-concepts.html) LLM의 유연성과 형식적 검증(symbolic verification)을 결합했다는 점에서 "neuro-symbolic"이라 불린다.
2. **컨트롤플레인 전체 정책셋 분석(Cedar analysis)** — 개별 정책이 아니라 정책 집합 전체를 automated reasoning으로 검사해 "항상 허용"되거나 "항상 거부"되는 정책, 상호 충돌·중복을 탐지한다.[[Core concepts]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-core-concepts.html)
3. **툴 호출 집행(tool call enforcement)** — 모든 Gateway 툴 요청이 실제 실행 전에 Cedar 정책 전체에 대해 평가된다. Policy Engine은 요청에 적용 가능한 모든 정책을 평가해 ALLOW/DENY를 결정하며, default-deny와 forbid-overrides-permit을 자동으로 강제한다.[[Core concepts]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-core-concepts.html)
4. **툴 필터링(tool filtering)** — Cedar의 **partial evaluation**을 이용해 어떤 조건에서도 항상 거부되는 액션을 식별하고, 이를 `list_tools` 응답 자체에서 제외한다. 결과적으로 에이전트(그리고 LLM)는 애초에 호출할 수 없는 툴의 존재를 알 수 없다.[[Why Policy in AgentCore chose Cedar]](https://aws.amazon.com/blogs/security/why-policy-in-amazon-bedrock-agentcore-chose-cedar-for-securing-agentic-workflows/)

④가 중요한 이유는 단순한 UX 개선이 아니다. Part 3 [tool-overload](../03-accuracy-eval/tool-overload)에서 다루듯, LLM에 노출되는 툴 개수가 늘어날수록 선택 정확도가 떨어지고 잘못된 툴을 고르는 빈도가 늘어난다 — 애초에 호출 불가능한 툴을 목록에서 제거하면 이 문제가 구조적으로 줄어든다. 동시에 Part 12 [prompt-injection](../12-security-korea/prompt-injection)에서 다루는 공격 표면과도 직결된다. 프롬프트 인젝션의 상당수는 "이 도구를 이런 인자로 호출해봐"라는 형태로 LLM을 유도하는데, 애초에 그 툴이 `list_tools`에 없다면 LLM은 그 존재 자체를 모델 컨텍스트에 담을 수 없어 유도가 성립하지 않는다. 즉 접근 제어와 프롬프트 인젝션 방어선이 같은 메커니즘(partial evaluation)에서 파생된다.

### 위조 불가능한 신원 vs LLM이 만든 인자

principal 타입은 두 가지다.

- `AgentCore::OAuthUser` — OAuth 인증된 사용자. JWT의 `sub` 클레임에서 생성되며, `username`, `scope`, `role` 등 JWT 클레임을 태그로 노출한다.
- `AgentCore::IamEntity` — IAM 인증 caller. IAM ARN을 `id` 속성으로 가진다.[[Core concepts]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-core-concepts.html)

정책 조건은 `principal.getTag(...)`로 이 위조 불가능한 클레임을 읽고, `context.input.*`으로 LLM이 만든 툴 인자를 읽는다. 아래는 공식 문서의 예시 정책이다.[[Understanding Cedar policies]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-understanding-cedar.html)

```cedar
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"RefundTool___process_refund",
  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:<region>:<account>:gateway/refund-gateway"
)
when {
  principal.hasTag("username") &&
  principal.getTag("username") == "John" &&
  context.input.amount < 500
};
```

이 정책은 "John이라는 사용자만, $500 미만의 환불만" 허용한다. LLM이 환불 금액을 $50,000으로 환각해 호출을 시도해도 `context.input.amount < 500` 조건에서 결정론적으로 거부된다 — 이것이 "LLM 환각을 막는다"는 말의 정확한 의미다. LLM의 출력을 신뢰하지 않고, LLM이 만들 수 없는 신호(JWT 클레임)와 정책 조건의 교차 검증으로 안전을 확보한다.

액션 이름은 `TargetName___tool_name` 형식(밑줄 3개)으로 자동 생성된다. `RefundTool___process_refund`는 Gateway target 이름 `RefundTool`과 툴 이름 `process_refund`를 결합한 것이다. Policy Engine은 Gateway의 툴 정의로부터 Cedar schema를 자동 생성하므로, 정책 작성 시점에 액션·컨텍스트 필드가 스키마와 어긋나면 검증에서 걸러진다.[[Core concepts]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-core-concepts.html)

### Cedar의 결정론적 성질

Cedar는 loop나 상태를 갖지 않는 정책 언어로, 개별 정책은 서로 독립적으로 평가된다(policy independence). 평가 알고리즘은 다음과 같다.[[Understanding Cedar policies]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-understanding-cedar.html)

1. 매칭되는 `forbid` 정책이 하나라도 있으면 DENY.
2. `forbid`가 없고 매칭되는 `permit`이 하나라도 있으면 ALLOW.
3. 둘 다 없으면 DENY(default deny).

이를 **forbid-overrides-permit**이라 부른다. `forbid`의 `unless` 절은 그 forbid가 적용되지 않는 조건을 정의할 뿐, 별도의 permit을 발생시키지 않는다 — forbid는 절대 ALLOW를 만들지 않는다.[[Understanding Cedar policies]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-understanding-cedar.html) 정책이 상태를 갖지 않고 루프가 없기 때문에 일반적인 경우 평가는 O(n) 시간에 종료된다는 것이 Cedar 채택 근거 중 하나로 제시된다.[[Why Policy in AgentCore chose Cedar]](https://aws.amazon.com/blogs/security/why-policy-in-amazon-bedrock-agentcore-chose-cedar-for-securing-agentic-workflows/)

### Temporal 정책 — Dogwood

기본 Cedar 정책은 상태가 없어(stateless) 요청 하나만 보고 평가한다. 세션에 걸친 조건 — "누적 노출 총액이 얼마를 넘지 않아야 한다", "특정 워크플로우 순서를 지켜야 한다", "고액 작업은 사전 승인이 필요하다" — 은 **Dogwood**로 작성한다. Dogwood는 Cedar와 호환되는 오픈소스 정책 언어로, 유효한 모든 Cedar 정책은 그대로 유효한 Dogwood 정책이다. 즉 기존 Cedar 정책을 마이그레이션할 필요가 없다.[[Core concepts]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-core-concepts.html)

temporal 정책은 `x-amzn-bedrock-agentcore-policy-session-id` 헤더로 지정한 policy session에 대해 평가된다. 세션의 시작·끝 경계는 애플리케이션이 정의한다(단일 대화, 멀티스텝 작업, 장기 워크플로우 등).[[Core concepts]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-core-concepts.html) 세션 헤더 없이 temporal 정책을 걸면 평가에 필요한 이전 이벤트 컨텍스트가 없어 정책이 의도대로 동작하지 않으므로, temporal 정책을 쓰는 순간 세션 ID 전파는 선택이 아니라 필수 요건이다.

temporal 정책을 활성화하면 Gateway는 세션 신원을 전파하기 위해 Workload Access Token(WAT)을 발급받아야 하며, 이를 위해 실행 역할에 `bedrock-agentcore:GetWorkloadAccessToken`을 workload-identity-directory 리소스로 스코프하여 추가로 부여해야 한다. 이 권한이 없으면 temporal 정책 활성화 시점부터 토큰 발급 단계에서 `AccessDenied`로 모든 툴 호출이 실패한다.[[AgentCore Gateway and Policy IAM Permissions]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-permissions.html)

### 필요 IAM 권한

Gateway 실행 역할에는 다음 세 권한이 필수다. 누락 시 Policy Engine을 기존 Gateway에 붙이는 순간 `InternalServerException`이 발생하거나, permit 정책이 있어도 모든 툴 호출이 기본적으로 거부된다.[[AgentCore Gateway and Policy IAM Permissions]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-permissions.html)

- `bedrock-agentcore:AuthorizeAction` — 개별 요청에 대해 Cedar 정책을 평가해 인가 결정을 내린다.
- `bedrock-agentcore:PartiallyAuthorizeActions` — partial evaluation으로 caller가 호출 가능한 툴 목록을 산출한다(④ 툴 필터링의 실제 구현체).
- `bedrock-agentcore:GetPolicyEngine` — Policy Engine 설정을 조회한다. 이 권한이 빠지면 LOG_ONLY 모드에서는 에러 없이 조용히 실패하고, ENFORCE로 전환하는 순간에야 문제가 드러난다.

정책 생성·수정을 담당하는 리소스 관리 역할에는 별도로 `bedrock-agentcore:InvokeGateway`가 필요하다 — `CreatePolicy`/`UpdatePolicy`는 Cedar 문의 액션을 Gateway의 실제 스키마와 대조 검증하기 위해 내부적으로 Gateway를 호출하기 때문이다. 이 권한이 없으면 정책은 `CREATE_FAILED` 상태로 남고 "Insufficient permissions to call gateway"라는, 원인 위치(정책 생성 역할)와 에러 메시지(Gateway ARN)가 어긋나는 오류가 발생한다.[[AgentCore Gateway and Policy IAM Permissions]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-permissions.html)

### 운영 권고 — LOG_ONLY → ENFORCE

Policy Engine은 LOG_ONLY 모드로 먼저 검증한 뒤 ENFORCE로 전환하는 것이 권장 절차다. LOG_ONLY 상태에서는 정책이 실제로 차단하지 않으므로 신규 정책셋이 프로덕션 트래픽에서 의도한 대로 매칭되는지 로그로 관찰할 수 있고, 위 IAM 권한 누락 같은 문제도 ENFORCE 전환 전에 걸러낼 여지가 생긴다.[[AgentCore Gateway and Policy IAM Permissions]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-permissions.html)

### AgentCore Policy vs Amazon Verified Permissions

둘 다 Cedar를 쓰지만 위치가 다르다.

| | AgentCore Policy | Amazon Verified Permissions (AVP) |
|---|---|---|
| GA 시점 | 2026-03경(확인 필요, 위 경고 참조) | 2023-06-13 GA[[AWS What's New]](https://aws.amazon.com/about-aws/whats-new/2023/06/amazon-verified-permissions-generally-available) |
| 집행 위치 | Gateway 내장, 툴콜 경계 | 범용 외부화 PDP(Policy Decision Point), 애플리케이션이 API로 호출 |
| 평가 대상 | 에이전트 → 툴 호출 (액션 = `Target___tool`) | 임의의 애플리케이션 리소스·액션 |
| 주 사용처 | 에이전트/Gateway 내부 인가 | 에이전트 밖 애플리케이션 레이어 인가(예: 백엔드 API, 웹 앱) |

AgentCore Policy는 "에이전트가 어떤 툴을 어떤 인자로 부를 수 있는가"에 특화된 게이트웨이 내장 계층이고, AVP는 에이전트 존재와 무관하게 범용 애플리케이션 인가에 쓰는 외부화 PDP다. 에이전트가 백엔드 API나 웹 애플리케이션과 상호작용하는 지점이 있다면(예: 에이전트가 호출한 백엔드가 최종 사용자 권한으로 리소스에 접근해야 하는 경우), AgentCore Policy만으로는 그 경계를 덮지 못하므로 AVP를 병용해야 한다. 즉 둘은 경쟁 관계가 아니라 "에이전트 내부 툴콜 경계"와 "에이전트 밖 애플리케이션 경계"를 나눠 맡는 상호 보완 관계다.

Cedar가 Rego(OPA) 대비 42.8배~80.8배, OpenFGA 대비 28.7배~35.2배 빠르다는 벤치마크가 있다.

> ⚠️ 이 수치는 Cedar 개발자(AWS 소속 저자 포함)가 발표한 논문의 자체 벤치마크(USENIX Security 2024)로, 벤더/개발자 자체 주장에 해당한다.[[Cedar 논문(arXiv)]](https://arxiv.org/pdf/2403.04651) 독립적인 제3자 재현 결과와 비교해 판단할 것.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| Gateway 툴 호출 자체를 제약해야 함(고객 tier·역할별 툴 접근) | AgentCore Policy | Gateway 내장, JWT 클레임을 principal 태그로 직접 사용 | Gateway 범위 밖(백엔드 API 등) 인가는 커버 못함 |
| 에이전트 밖 애플리케이션(웹앱/백엔드 API)의 리소스 인가 | Amazon Verified Permissions | 범용 외부화 PDP, 임의 리소스·액션 모델링 가능 | 별도 스키마·정책 관리 필요, Gateway 툴콜과 자동 연동 안 됨 |
| 세션에 걸친 워크플로우 순서·누적 한도·고액 승인 강제 | Dogwood(temporal 정책) + AgentCore Policy | 기존 Cedar 정책 그대로 유지, 세션 상태만 추가 | 세션 ID 전파 설계·WAT 발급 IAM 권한 추가 필요 |
| 신규 정책셋을 프로덕션에 처음 적용 | LOG_ONLY로 시작 후 ENFORCE 전환 | 잘못된 차단으로 인한 장애를 사전에 로그로 검증 | 검증 기간 동안 실제 차단 효과 없음(관찰만) |
| LLM이 만든 툴 인자를 제약해야 함(금액, 대상 리소스 ID 등) | `context.input.*` 조건 | 환각된 인자도 정책 조건으로 결정론적 거부 | 툴 스키마 변경 시 정책 재검증 필요 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| Policy Engine을 붙였는데 모든 호출이 `InternalServerException` | 실행 역할에 `AuthorizeAction`/`PartiallyAuthorizeActions`/`GetPolicyEngine` 중 하나 이상 누락 | CloudWatch Logs·X-Ray에서 권한 오류 확인 | 세 권한을 정확한 policy-engine/gateway ARN에 부여 |
| LOG_ONLY에서는 잘 되던 것이 ENFORCE 전환 후 전부 실패 | `GetPolicyEngine` 누락으로 LOG_ONLY에서 조용히 실패 중이었음 | ENFORCE 전환 전 세 권한 모두 존재하는지 재확인 | LOG_ONLY 단계에서부터 권한 점검을 절차화 |
| `CreatePolicy`가 `policyId`는 반환하지만 `CREATE_FAILED`로 전환 | 정책 생성 역할(Resource Management Role)에 `InvokeGateway` 누락 — 에러가 가리키는 대상(Gateway)과 실제 원인 위치(정책 생성 역할)가 다름 | 에러 메시지의 "Insufficient permissions to call gateway with ID" 확인 후 어떤 역할이 CreatePolicy를 호출했는지 추적 | Resource Management Role에 대상 Gateway ARN 스코프로 `InvokeGateway` 부여 |
| temporal 정책 활성화 후 모든 툴 호출이 `AccessDenied` | WAT 발급용 `GetWorkloadAccessToken` 누락 | 토큰 민트 단계에서 `AccessDenied` 로그 확인 | workload-identity-directory 리소스로 스코프해 권한 추가 |
| 정책을 걸었는데 LLM이 여전히 금지된 툴 이름을 언급/시도 | permit 정책 없이 forbid만 있어 default-deny로 걸리는데도, partial evaluation이 "조건부 거부"를 "항상 거부"로 오인하지 못해 `list_tools`에 남아있음 | 해당 액션이 무조건(unconditional) forbid인지, 조건부 forbid인지 정책 로직 재검토 | 항상 거부되어야 하는 액션은 조건 없는 forbid로 명확히 표현해 partial evaluation이 필터링하도록 함 |
| 정책이 예상과 다르게 허용/차단됨(정책 여러 개가 동시에 매칭) | forbid-overrides-permit을 오해 — `unless` 절이 permit을 만든다고 가정 | Cedar analysis 리포트에서 충돌·중복 정책 확인 | forbid의 `unless`는 forbid 미적용 조건일 뿐 permit을 발생시키지 않음을 재확인, 정책셋 분석 정기 실행 |
| 세션 헤더를 안 보냈는데 temporal 정책이 조용히 무시됨 | `x-amzn-bedrock-agentcore-policy-session-id` 헤더 미전파 | 요청 트레이스에서 헤더 존재 여부 확인 | 첫 요청부터 세션 ID를 생성·전파하도록 클라이언트 수정 |

## 안티패턴

- ❌ 툴 이름만 화이트리스트로 막고 인자(금액, 리소스 ID)는 검증하지 않음 → ✅ `context.input.*` 조건으로 인자 자체를 정책에 넣어 환각된 값도 결정론적으로 거부
- ❌ 정책 하나하나를 개별적으로만 검증하고 정책셋 전체 충돌을 점검하지 않음 → ✅ Cedar analysis로 컨트롤플레인 레벨에서 항상 허용/항상 거부·중복 정책을 주기적으로 스캔
- ❌ 새 정책셋을 곧바로 ENFORCE로 배포 → ✅ LOG_ONLY로 먼저 관찰 기간을 두고 실제 트래픽에서 매칭 여부를 확인한 뒤 전환
- ❌ AgentCore Policy만으로 에이전트 밖 백엔드 API/웹앱 인가까지 처리하려 함 → ✅ 에이전트 밖 경계는 Amazon Verified Permissions로 별도 모델링해 병용
- ❌ 세션에 걸친 누적 한도·워크플로우 순서를 애플리케이션 코드에서 수동으로 추적 → ✅ Dogwood temporal 정책 + policy session 헤더로 정책 레벨에서 강제
- ❌ Cedar/Rego 성능 비교 수치를 검증 없이 "공식 성능"으로 인용 → ✅ 벤더(Cedar 개발자) 자체 벤치마크임을 명시하고 필요 시 자체 환경에서 재현

## 계측 (SLI)

AgentCore Policy 자체의 관측 지표를 설계할 때는 최소한 다음을 CloudWatch Logs/X-Ray에서 추적한다.

- **정책 평가 결정 분포**: ALLOW/DENY 비율, 그리고 DENY 중 default-deny(정책 미매칭)로 떨어진 비율 — 정책 커버리지가 좁아서 발생하는 오탐인지 구분하는 데 쓴다.
- **LOG_ONLY 관찰 기간 중 "실제로는 차단됐을" 요청 비율**: ENFORCE 전환 전 위험도를 정량화한다.
- **`list_tools` 필터링 전/후 노출 툴 개수**: partial evaluation으로 실제 몇 개가 걸러지는지 — 급격한 변화는 정책 회귀(regression) 신호다.
- **temporal 정책 세션당 누적 지표**(예: 세션당 총 노출 금액, 워크플로우 단계 위반 횟수): 세션 경계 설계가 의도대로 동작하는지 확인.
- **`GetWorkloadAccessToken`/`AuthorizeAction`/`PartiallyAuthorizeActions` 호출의 `AccessDenied` 비율**: IAM 권한 드리프트를 조기에 잡는다.

## 체크리스트

- [ ] Gateway 실행 역할에 `AuthorizeAction`, `PartiallyAuthorizeActions`, `GetPolicyEngine` 세 권한이 정확한 ARN 스코프로 부여되어 있는가
- [ ] 정책 생성/관리 역할에 대상 Gateway ARN 스코프의 `InvokeGateway`가 부여되어 있는가
- [ ] temporal 정책을 쓰는가? 그렇다면 `GetWorkloadAccessToken`이 workload-identity-directory 리소스로 부여되어 있고, 클라이언트가 첫 요청부터 `x-amzn-bedrock-agentcore-policy-session-id`를 전파하는가
- [ ] 위조 불가능한 신원 신호(JWT 클레임 → principal 태그)와 LLM이 만든 인자(`context.input.*`)를 정책 조건에서 명확히 분리했는가
- [ ] 항상 거부되어야 하는 액션이 조건 없는 `forbid`로 표현되어 partial evaluation이 `list_tools`에서 실제로 걸러내는가(Part 3 tool-overload, Part 12 prompt-injection 관점에서 재확인)
- [ ] 신규/변경 정책셋을 LOG_ONLY로 먼저 배포하고 관찰 기간을 거쳐 ENFORCE로 전환하는 절차가 있는가
- [ ] Cedar analysis를 정기적으로 돌려 정책셋 전체의 충돌·중복·"항상 허용/항상 거부" 정책을 탐지하는가
- [ ] 에이전트 밖 애플리케이션 경계(백엔드 API, 웹앱)의 인가가 필요한 경우 Amazon Verified Permissions를 별도로 구성했는가
- [ ] Cedar/Rego 등 성능 비교 수치를 인용할 때 출처가 벤더 자체 벤치마크임을 명시했는가

## 참고

- [Core concepts - Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-core-concepts.html)
- [Understanding Cedar policies - Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-understanding-cedar.html)
- [AgentCore Gateway and Policy in AgentCore IAM Permissions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-permissions.html)
- [Why Policy in Amazon Bedrock AgentCore chose Cedar for securing agentic workflows](https://aws.amazon.com/blogs/security/why-policy-in-amazon-bedrock-agentcore-chose-cedar-for-securing-agentic-workflows/)
- [Secure AI agents with Policy and Lambda interceptors in Amazon Bedrock AgentCore gateway](https://aws.amazon.com/blogs/machine-learning/secure-ai-agents-with-policy-and-lambda-interceptors-in-amazon-bedrock-agentcore-gateway/)
- [Securing AI agents with temporal policies in Amazon Bedrock AgentCore](https://aws.amazon.com/blogs/machine-learning/securing-ai-agents-with-temporal-policies-in-amazon-bedrock-agentcore/)
- [Control agent behaviors and cost beyond a single action: new capabilities in Amazon Bedrock AgentCore](https://aws.amazon.com/blogs/machine-learning/control-agent-behaviors-and-cost-beyond-a-single-action-new-capabilities-in-amazon-bedrock-agentcore/)
- [Amazon Verified Permissions is now generally available (AWS What's New, 2023-06-13)](https://aws.amazon.com/about-aws/whats-new/2023/06/amazon-verified-permissions-generally-available)
- [Cedar: A New Language for Expressive, Fast, Safe, and Extensible Access Control (arXiv, Cedar 개발자 논문)](https://arxiv.org/pdf/2403.04651)

::: warning 미정착 영역
AgentCore Policy의 정확한 GA 발표일 및 발표 채널(AWS What's New 게시물)은 이 리서치에서 1차 출처로 확인하지 못했다. devguide 게시 상태와 다수 2차 출처(AWS 공식 블로그 포함)를 근거로 GA로 판단했으나, 정확한 일자가 필요한 문서(예: 계약·SLA 관련)에는 AWS What's New를 직접 재확인해 인용할 것.
:::
