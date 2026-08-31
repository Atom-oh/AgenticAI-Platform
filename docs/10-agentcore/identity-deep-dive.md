---
title: Identity 심화
description: AgentCore Identity가 제공하는 인바운드·아웃바운드 인증 방식의 전체 지형과, Gateway/Runtime과의 책임 분담을 다룬다.
outline: [2, 3]
---

# Identity 심화

::: tip 이 장에서 얻는 것
- AgentCore Runtime의 인바운드 인증 두 가지(IAM SigV4 / JWT Bearer)가 **런타임 한 버전당 상호 배타적**이라는 제약과, JWT 인가자(`customJWTAuthorizer`)의 정확한 설정 필드(`discoveryUrl`/`allowedAudience`/`allowedClients`/`allowedScopes`/`customClaims`/`allowedWorkloadConfiguration`)
- AgentCore Gateway는 Runtime보다 인바운드 옵션이 더 넓다는 것 — IAM, JWT 외에 "offloaded"(authenticate-only, no authorization) 유형까지 지원
- 아웃바운드 인증 4가지 패턴(2LO/3LO/token exchange(OBO)/passthrough)의 전체 지형과 각각을 언제 쓰는지 — 프로토콜 세부사항은 [OAuth 토큰 교환과 MCP](/09-authorization/oauth-token-exchange-mcp), [툴별 On-Behalf-Of](/09-authorization/per-tool-obo)로 위임
- AgentCore Identity·Gateway·Runtime 세 컴포넌트가 인증 책임을 어떻게 나누는지, 그리고 Gateway 뒤에 Runtime을 둘 때 "우회 방지"를 어떻게 강제하는지
:::

## 왜 문제가 되는가

AgentCore로 에이전트를 배포하는 순간 최소 두 개의 서로 다른 인증 경계가 동시에 생긴다. 하나는 **누가 이 에이전트를 호출할 수 있는가**(인바운드), 다른 하나는 **이 에이전트가 어떤 신원으로 외부 서비스를 호출하는가**(아웃바운드)다. 두 경계는 서로 독립적인 설계 결정이지만, 실무에서는 종종 뒤섞여 다뤄진다 — "OAuth를 쓴다"는 말이 인바운드 JWT 검증을 가리킬 수도, 아웃바운드 3LO 동의 플로우를 가리킬 수도 있어 설계 문서에서 혼선이 생긴다.

문제는 AgentCore 컴포넌트가 여러 개(Runtime, Gateway, Identity)이고, 각각이 인바운드/아웃바운드 인증을 다르게, 그리고 서로 다른 폭으로 지원한다는 데 있다. Runtime은 인바운드로 IAM SigV4 또는 JWT 중 **하나만** 고를 수 있고 둘을 동시에 켤 수 없다. Gateway는 그보다 선택지가 넓어서 IAM, JWT 외에 인가 판단 자체를 다른 컴포넌트로 떠넘기는 "offloaded" 유형까지 있다. 이 차이를 모르고 설계하면 "Gateway 뒤의 Runtime은 SigV4로 막아뒀으니 안전하다"고 오판하거나, 반대로 Runtime을 JWT로 열어뒀는데 Gateway를 우회해 직접 호출당하는 구멍을 만든다.

아웃바운드 쪽은 이미 Part 9에서 프로토콜 수준(RFC 8693 token exchange, `act`/`sub` 클레임, STS 세션태그)까지 깊게 다뤘다. 이 장은 그 상세 메커니즘을 반복하지 않는다. 대신 이 장의 역할은 **지도(map)**다 — AgentCore Identity가 제공하는 인바운드/아웃바운드 인증 방식이 몇 가지이고, 각각이 언제 맞는 선택인지, 그리고 그 선택이 Gateway·Runtime이라는 인프라 계층에서 정확히 어느 API 필드로 표현되는지를 정리한다.

## 핵심 개념

### 인바운드: Runtime의 IAM SigV4 vs JWT — 상호 배타적 선택

AgentCore Runtime은 호스팅된 에이전트로의 인바운드 인증을 두 메커니즘 중 하나로 지원한다.

- **IAM SigV4(기본값)** — 추가 설정 없이 자동으로 동작하며, 다른 AWS API 호출과 동일한 방식이다. 호출자는 AWS 자격증명으로 요청에 서명한다.
- **JWT Bearer Token** — 에이전트 생성 시 `authorizerConfiguration.customJWTAuthorizer`로 지정한다. Cognito, Auth0, Okta 등 OpenID Connect 호환 IdP가 발급한 access token을 `Authorization: Bearer` 헤더로 전달한다.

공식 문서는 이 둘의 관계를 명시적으로 못박는다.

> "An AgentCore Runtime can support either IAM SigV4 or JWT Bearer Token based inbound auth, but not both simultaneously. You can always create different versions of your AgentCore Runtime and configure them for different inbound authorization types." ([AWS 공식 문서, Authenticate and authorize with Inbound Auth and Outbound Auth](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html))

즉 하나의 런타임 리소스는 배포 시점에 인바운드 방식을 확정해야 하고, 두 방식을 동시에 열어 "SigV4로도, JWT로도 들어올 수 있게" 만들 수 없다. 웹/모바일 최종 사용자에게 노출되는 엔드포인트와 백엔드 서비스 간 호출을 같은 에이전트에서 처리해야 한다면, 서로 다른 인가 방식을 가진 **런타임 버전을 따로** 만들어야 한다.

JWT 인가자(`customJWTAuthorizer`)는 다음 필드로 구성된다([AWS 공식 문서, 위와 동일](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html)).

| 필드 | 검증 대상 클레임 | 설명 |
|---|---|---|
| `discoveryUrl` | `iss` | OpenID Connect discovery URL. 패턴은 `^.+/\.well-known/openid-configuration$`을 만족해야 하며, 이 URL이 가리키는 issuer가 토큰의 `iss` 클레임과 일치해야 한다 |
| `allowedClients` | `client_id` | 토큰의 `client_id` 클레임이 목록 중 하나와 일치해야 함 |
| `allowedAudience` | `aud` | 토큰의 `aud` 클레임 중 하나가 목록과 일치해야 함(`client_id`와 `aud`를 둘 다 지정하면 둘 다 검증) |
| `allowedScopes` | `scope` | 허용 스코프 목록 |
| `customClaims` | 임의 클레임 | 클레임 이름·값·비교 연산자(`CONTAINS_ANY` 등)로 정의하는 커스텀 검증 — 예를 들어 department 클레임이 `["Sales","Finance"]` 중 하나를 포함해야 통과 |
| `allowedWorkloadConfiguration` | 요청의 identity chain | 요청이 특정 AgentCore Gateway를 거쳐 왔는지를 `hostingEnvironments`(Gateway ARN) 또는 `workloadIdentities`(워크로드 identity 이름)로 강제. Runtime 대상에 한해서만 지원되며, 허용되는 hosting environment는 현재 AgentCore Gateway뿐 |

Runtime을 생성하면 AgentCore Identity가 자동으로 **워크로드 identity**를 만든다. 이 워크로드 identity는 에이전트가 IAM 역할로 AWS 리소스에 접근하든, OAuth 토큰으로 외부 서비스를 호출하든, API 키로 서드파티 툴을 쓰든 일관된 디지털 신원을 유지하게 해준다([AWS Security Blog, Propagate user authorization context](https://aws.amazon.com/blogs/security/propagate-user-authorization-context-in-ai-agents-with-amazon-bedrock-agentcore/)).

### 인바운드: Gateway는 Runtime보다 선택지가 넓다

Gateway의 인바운드 인가는 Runtime과 다른 지형을 가진다. 공식 문서는 세 범주를 명시한다([AWS 공식 문서, Set up inbound authorization for your gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-inbound-auth.html)).

- **JWT** — Runtime과 동일한 개념(`CustomJWTAuthorizerConfiguration`)이며, discovery URL·client ID·allowed audience 등을 지정한다.
- **IAM identity** — 호출자의 IAM 자격증명으로 인가. 필요한 액션은 `bedrock-agentcore:InvokeGateway` 하나이며, 프로덕션에서는 `Resource`를 생성한 Gateway ARN으로 반드시 좁혀야 한다.
- **Offloaded authorization** — Gateway 자신은 인가 결정을 내리지 않고 다른 컴포넌트(다운스트림 타깃, Gateway에 붙은 정책 엔진, 인터셉터 Lambda)에 위임한다. 여기에는 **Authenticate-only**(인증만 하고 인가는 위임)와 **No Authorization**(인가 자체를 하지 않음) 두 서브타입이 있다.

Runtime이 "SigV4 아니면 JWT, 둘 중 하나"라는 이진 선택인 것과 달리, Gateway는 정책 엔진이나 인터셉터 Lambda로 인가 로직을 완전히 외부화할 수 있는 세 번째 축이 있다는 점이 인프라 설계에서 중요하다. 예를 들어 [Cedar 기반 세분화 인가](/09-authorization/cedar-verified-permissions)를 쓰는 아키텍처는 Gateway의 offloaded 유형을 전제로 한다 — Gateway가 자체 인가 결정을 내리지 않고 정책 엔진에 위임하기 때문이다.

### Gateway 뒤에 Runtime을 둘 때: 우회 방지

Runtime을 Gateway 뒤에 두는 목적은 정책 기반 인가, Guardrails, 요청/응답 인터셉터, 통합 관측성을 Gateway 계층에서 적용하는 것이다. 하지만 이 이점은 **호출자가 Gateway를 건너뛰고 Runtime을 직접 호출할 수 없을 때만** 유효하다. Runtime의 인바운드 방식에 따라 강제 메커니즘이 다르다.

- **Runtime이 IAM SigV4일 때**: Runtime의 리소스 기반 정책에서 Gateway의 실행 역할만 `Allow`하고 나머지 모든 principal을 명시적으로 `Deny`한다(`aws:PrincipalArn` 조건). 명시적 `Deny`는 동일 계정 내 어떤 identity 기반 정책의 `Allow`보다 항상 우선한다([AWS 공식 문서, runtime-oauth.html](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html)). 이때 Gateway 실행 역할 자체를 다른 principal이 assume할 수 없도록 그 역할의 신뢰 정책에 `aws:SourceArn`/`aws:SourceAccount` 조건을 걸어야 한다 — 그렇지 않으면 그 역할을 assume할 수 있는 어떤 주체든 Gateway를 사칭해 Runtime을 호출할 수 있다.
- **Runtime이 JWT일 때**: `customJWTAuthorizer.allowedWorkloadConfiguration`으로 요청의 identity chain에 특정 Gateway가 포함되어야만 통과하도록 강제한다. `hostingEnvironments`(Gateway ARN 지정)와 `workloadIdentities`(Gateway의 워크로드 identity 이름 지정) 중 하나 이상을 채우면 되며, 둘 다 지정하면 어느 한쪽만 일치해도 허용된다.

두 메커니즘 모두 목적은 같다 — "Gateway가 제공하는 정책·Guardrails·관측성이 실제로 모든 트래픽에 적용되는가"를 인프라 수준에서 보장하는 것. 이 우회 방지는 [confused deputy 문제](/09-authorization/confused-deputy)에서 다루는 신뢰 경계 설계와 같은 계열의 방어다.

### 아웃바운드: 4가지 패턴의 지형

AgentCore Identity가 지원하는 아웃바운드 인증 패턴은 네 가지로 정리된다. 프로토콜 세부사항(RFC 8693/RFC 7523 그랜트 타입, `act`/`sub` 클레임, STS 세션태그)은 이미 [OAuth 토큰 교환과 MCP](/09-authorization/oauth-token-exchange-mcp)와 [툴별 On-Behalf-Of](/09-authorization/per-tool-obo)에서 다뤘으므로 이 장에서는 반복하지 않고 "언제 무엇을 쓰는가"라는 지도 역할에 집중한다.

| 패턴 | 통칭 | 언제 쓰는가 | 상세는 |
|---|---|---|---|
| Client credentials grant | 2LO(M2M) | 사용자와 무관한 배치/시스템 작업(지식베이스 색인, 스케줄 작업). 사용자 상호작용 불필요 | [툴별 On-Behalf-Of](/09-authorization/per-tool-obo) |
| Authorization code grant | 3LO(user-delegated) | 사용자의 개인 데이터(캘린더, Google Drive 등)에 최초로 접근하며 명시적 동의가 필요할 때. 브라우저 리다이렉트가 필요해 완전 무인 작업에는 부적합 | [OAuth 토큰 교환과 MCP](/09-authorization/oauth-token-exchange-mcp) |
| Token exchange(OBO) | On-behalf-of | 이미 인증된 사용자를 대신해 추가 동의 없이 다운스트림 API를 호출. `TOKEN_EXCHANGE`(RFC 8693) 또는 `JWT_AUTHORIZATION_GRANT`(RFC 7523) 그랜트 타입 중 다운스트림 IdP가 지원하는 쪽을 선택 | [툴별 On-Behalf-Of](/09-authorization/per-tool-obo) |
| Passthrough | — | 인바운드 토큰을 그대로 다운스트림에 전달. MCP 인가 스펙이 명시적으로 금지하며, 원칙적으로 피해야 함 | [OAuth 토큰 교환과 MCP](/09-authorization/oauth-token-exchange-mcp) |

AWS 공식 문서는 세 패턴(2LO/3LO/OBO)이 상호 배타적이지 않다고 명시한다 — 하나의 에이전트가 내부 지식베이스 접근에는 client credentials를, 특정 사용자의 데이터 접근에는 OBO를, 최초 SaaS 연결에는 authorization code grant를 동시에 쓸 수 있다([AWS 공식 문서, common-use-cases](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/common-use-cases.html)). 실무에서는 다운스트림 호출마다 "이 호출이 사용자를 대신하는가, 시스템 자체 작업인가"를 먼저 분류하고 그에 맞는 패턴을 고르는 것이 설계의 시작점이다.

### 3LO와 `X-Amzn-Bedrock-AgentCore-Runtime-User-Id` 헤더

3LO(Authorization Code Grant)를 쓸 때, 호스팅된 에이전트가 사용자를 대신해 OAuth 토큰을 얻어야 하는 경로가 두 가지 있다.

1. **JWT 인바운드 경로(프로덕션 권장)** — 요청에 실린 인바운드 JWT가 `GetWorkloadAccessTokenForJWT`를 통해 워크로드 액세스 토큰으로 교환되고, 이 토큰이 이후 `GetResourceOauth2Token` 호출에서 사용자 식별의 근거가 된다. 발급자·서명·만료가 암호학적으로 검증된다.
2. **`X-Amzn-Bedrock-AgentCore-Runtime-User-Id` 헤더 경로** — IdP 토큰이 아직 없는 개발/퀵스타트 단계나, 자체 사용자 식별자를 관리하는 엔터프라이즈 고객을 위한 경로. 내부적으로 `GetWorkloadAccessTokenForUserId`를 사용하며, 이 헤더를 포함해 `InvokeAgentRuntime`을 호출하려면 기존 `bedrock-agentcore:InvokeAgentRuntime` 외에 `bedrock-agentcore:InvokeAgentRuntimeForUser` IAM 액션이 추가로 필요하다.

AWS 공식 문서는 두 경로의 신뢰 모델 차이를 정확히 지목한다.

> "The `X-Amzn-Bedrock-AgentCore-Runtime-User-Id` header path does not verify the userId against an authenticated end-user identity — it relies on the calling workload to pass the correct value and on your IAM policies to restrict who can supply it." ([AWS 공식 문서, runtime-oauth.html](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html))

즉 이 헤더는 "누가 실제 요청자인가"를 AgentCore가 대신 증명해주지 않는다. AWS는 이를 위해 다음 보안 관례를 명시한다([AWS 공식 문서, 동일 URL](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html)).

- `bedrock-agentcore:InvokeAgentRuntimeForUser` 권한을 신뢰된 principal에만, 리소스 조건으로 특정 런타임에 스코프해 부여한다.
- 헤더 값은 클라이언트가 임의로 채운 값이 아니라, IAM 호출자 identity나 토큰 클레임 같은 인증된 컨텍스트에서 파생시킨다.
- 인증된 IAM principal(SigV4 컨텍스트)과 전달된 `user-id` 값의 관계를 감사 로그로 남기고, CloudTrail에서 `runtimeUserId` 파라미터가 포함된 `InvokeAgentRuntime` 호출을 모니터링한다.
- user-id 위임이 필요 없는 런타임에서는 `bedrock-agentcore:InvokeAgentRuntimeForUser`를 명시적으로 `Deny`해 헤더 자체를 받아들이지 못하게 한다.

이 헤더는 개발 단계의 빠른 경로이지 프로덕션 사용자 인증의 대체물이 아니다 — 이 관점은 [툴별 On-Behalf-Of](/09-authorization/per-tool-obo)의 안티패턴 항목과 동일하다.

### Identity·Gateway·Runtime의 책임 분담

AgentCore Identity는 독립된 서비스가 아니라 Runtime과 Gateway 양쪽에 "네이티브하게 통합"된다.

> "The service integrates natively with Amazon Bedrock AgentCore to provide identity and credential management for agent applications, including [...] Amazon Bedrock AgentCore Runtime [...] and Amazon Bedrock AgentCore Gateway." ([AWS 공식 문서, identity.html](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity.html))

세 컴포넌트의 역할을 인프라 관점에서 나누면 다음과 같다.

- **Runtime/Gateway**는 **인바운드 인가 결정**을 내린다 — 요청에 실린 SigV4 서명이나 JWT가 유효한지, 어떤 클레임/스코프를 요구하는지를 각자의 authorizer 설정(`customJWTAuthorizer`, IAM 리소스 정책, offloaded 위임)으로 판단한다.
- **AgentCore Identity**는 **워크로드 identity 관리와 아웃바운드 자격증명 발급**을 담당한다 — Runtime/Gateway가 인가한 요청에 대해 워크로드 액세스 토큰을 발급(`GetWorkloadAccessTokenForJWT`/`GetWorkloadAccessTokenForUserId`)하고, 그 토큰을 근거로 OAuth credential provider를 통해 다운스트림용 토큰을 발급(`GetResourceOauth2Token`)하며, 그 자격증명을 Token Vault에 보관한다.
- **에이전트 코드(Harness 포함)**는 이 둘 사이에서 워크로드 액세스 토큰을 받아 Identity SDK(`requires_access_token` 등)로 다운스트림 호출용 토큰을 요청하는 소비자다. Harness 관리형 루프 자체는 인증 방식을 새로 정의하지 않고, Runtime/Identity가 제공하는 토큰을 그대로 받아 쓴다.

즉 "이 요청이 여기까지 도달해도 되는가"는 Runtime/Gateway의 authorizer 설정이 결정하고, "도달한 뒤 이 에이전트가 무슇 신원으로 어디까지 나갈 수 있는가"는 Identity가 결정한다. 두 결정은 독립적으로 설계·검증해야 한다 — 인바운드가 뚫려도 아웃바운드 스코프가 좁으면 피해가 제한되고, 아웃바운드가 넓어도 인바운드가 막히면 애초에 도달하지 못한다(defense in depth).

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| Runtime을 백엔드 서비스·내부 워크로드에서만 호출 | 인바운드 IAM SigV4 | 추가 설정 없이 표준 AWS 자격증명으로 동작, IAM 정책으로 세밀한 제어 가능 | 웹/모바일 최종 사용자에게 직접 노출할 수 없음(AWS SDK/서명 필요) |
| Runtime을 웹/모바일 앱의 최종 사용자에게 직접 노출 | 인바운드 JWT(`customJWTAuthorizer`) | Cognito/Auth0/Okta 등 기존 OIDC IdP의 access token을 그대로 사용, 클레임 기반 세분화 인가(`customClaims`) 가능 | IdP 설정과 discovery URL 관리 부담, AWS SDK가 bearer token 호출을 지원하지 않아 HTTPS 클라이언트 직접 구현 필요 |
| 동일 에이전트를 내부 호출과 최종 사용자 호출 모두에 노출해야 함 | 인바운드 방식이 다른 런타임 버전을 별도로 생성 | 한 런타임은 SigV4와 JWT를 동시에 지원할 수 없음 | 버전 관리·배포 파이프라인이 두 배로 늘어남 |
| Gateway 뒤에서 세분화 인가 정책(Cedar 등)을 별도 엔진으로 강제하고 싶음 | Gateway의 offloaded(authenticate-only) 인바운드 유형 | Gateway가 인증만 하고 인가 결정은 정책 엔진/인터셉터로 위임, 정책 로직을 Gateway 배포와 분리 | Gateway 자체는 세밀한 인가를 하지 않으므로 위임받은 컴포넌트가 fail-closed로 동작해야 함 |
| Runtime을 Gateway로만 접근하게 강제(우회 방지) | Runtime이 SigV4면 리소스 정책 `Deny`+`aws:PrincipalArn`, JWT면 `allowedWorkloadConfiguration` | Gateway가 제공하는 정책·Guardrails·관측성이 실제로 모든 트래픽에 적용됨을 보장 | Gateway 실행 역할의 신뢰 정책도 함께 잠가야 함(그렇지 않으면 역할 자체가 사칭 경로가 됨) |
| 개발/퀵스타트 단계에서 아직 IdP 토큰이 없는데 사용자별 자격증명 바인딩을 테스트해야 함 | `X-Amzn-Bedrock-AgentCore-Runtime-User-Id` 헤더 | IdP 통합 없이도 사용자 스코프 자격증명 흐름을 빠르게 검증 가능 | userId가 검증되지 않으므로 프로덕션에서는 JWT 경로로 전환 필요 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| Runtime을 JWT로 재배포했는데 기존 SigV4 호출 클라이언트가 갑자기 403을 받음 | 한 런타임은 SigV4와 JWT를 동시에 지원하지 않는다는 제약을 모르고 기존 런타임의 인가 타입을 변경 | `authorizerConfiguration`이 설정된 런타임 버전인지, 기존 SigV4 호출자가 새 버전의 discovery URL/allowedClients를 만족하는 JWT를 갖고 있는지 확인 | 인바운드 방식이 다른 별도 런타임 버전을 만들고, 호출자별로 올바른 버전을 라우팅 |
| Gateway 뒤의 Runtime이 Gateway를 거치지 않고 직접 호출당함(정책·Guardrails 우회) | Runtime의 리소스 정책에 `Deny`가 없거나, JWT 런타임에 `allowedWorkloadConfiguration`이 설정되지 않음 | Runtime 리소스 정책에서 `AllowOnlyGatewayRole` 유사 Statement와 명시적 `Deny`가 있는지, JWT 런타임이면 `authorizerConfiguration.customJWTAuthorizer.allowedWorkloadConfiguration`이 채워져 있는지 확인 | SigV4 Runtime은 `aws:PrincipalArn` 조건의 명시적 `Deny` 추가, JWT Runtime은 `hostingEnvironments`/`workloadIdentities`로 Gateway만 허용 |
| Gateway 실행 역할을 다른 서비스가 assume해서 Runtime을 "Gateway인 척" 호출 | Gateway 실행 역할의 신뢰 정책에 `aws:SourceArn`/`aws:SourceAccount` 조건이 없어 누구나 그 역할을 assume 가능 | 신뢰 정책의 `Principal`/`Condition` 검토, CloudTrail에서 해당 역할의 `AssumeRole` 호출자 확인 | 신뢰 정책에 `aws:SourceArn`을 Gateway ARN으로 스코프하는 조건 추가 |
| `customClaims` 검증을 걸어뒀는데도 예상과 다른 사용자가 통과 | `claimMatchOperator`(예: `CONTAINS_ANY`)의 의미를 오해해 의도보다 넓게 매칭 | 실제 통과한 토큰의 해당 클레임 값을 디코드해 `authorizingClaimMatchValue`와 연산자 의미를 재확인 | 연산자를 요구사항에 맞게 재설정(예: 정확히 일치가 필요하면 `CONTAINS_ANY` 대신 다른 연산자 검토) |
| `X-Amzn-Bedrock-AgentCore-Runtime-User-Id`로 지정한 사용자가 실제 요청자와 다름 | 이 헤더는 AgentCore가 값을 검증하지 않고 호출 워크로드를 신뢰하는 구조이며, 잘못된 값이 그대로 자격증명 바인딩에 쓰임 | 헤더 설정 로직이 인증된 principal 컨텍스트와 독립적으로 값을 채우는지 코드 리뷰, CloudTrail에서 `runtimeUserId`와 호출 principal의 관계 감사 | 프로덕션에서는 JWT 경로(`GetWorkloadAccessTokenForJWT`)로 전환하고, 헤더가 불필요한 런타임에서는 `InvokeAgentRuntimeForUser`를 명시적으로 `Deny` |
| 2LO(client credentials)만으로 사용자별 데이터 접근을 처리하다 감사 요구사항을 충족 못 함 | 사용자 위임이 필요한 호출을 시스템 신원(2LO)으로만 수행, OBO/3LO 미적용 | 다운스트림 액세스 로그의 principal 필드에 사용자 식별자가 남는지 확인 | 사용자 대신 수행하는 호출은 OBO(token exchange) 또는 3LO로 전환 — 상세는 [툴별 On-Behalf-Of](/09-authorization/per-tool-obo) |

## 안티패턴

- ❌ 하나의 Runtime에 SigV4와 JWT를 동시에 설정하려 시도(지원되지 않음) → ✅ 인바운드 방식별로 별도 Runtime 버전을 배포하고 클라이언트를 올바른 버전으로 라우팅한다.
- ❌ Gateway 뒤에 Runtime을 두고도 Runtime의 리소스 정책·`allowedWorkloadConfiguration`을 설정하지 않아 직접 호출을 허용 → ✅ SigV4는 명시적 `Deny`+`aws:PrincipalArn`, JWT는 `allowedWorkloadConfiguration`으로 Gateway 경유를 강제한다.
- ❌ `X-Amzn-Bedrock-AgentCore-Runtime-User-Id` 헤더를 프로덕션 사용자 인증의 유일한 근거로 사용 → ✅ JWT 경로(`GetWorkloadAccessTokenForJWT`)로 전환하고, 헤더는 개발/퀵스타트 단계로 한정한다.
- ❌ "OAuth를 쓴다"는 결정을 인바운드(JWT 검증)와 아웃바운드(2LO/3LO/OBO) 구분 없이 뭉뚱그려 설계 문서에 기록 → ✅ 인바운드 인가자 설정과 아웃바운드 credential provider 설정을 별도 섹션으로 명시한다.
- ❌ Gateway 실행 역할의 신뢰 정책을 잠그지 않고 Runtime 쪽 `Allow`만 설정 → ✅ Gateway 실행 역할 신뢰 정책에도 `aws:SourceArn`/`aws:SourceAccount` 조건을 걸어 역할 사칭 경로를 막는다.

## 계측 (SLI)

이 장이 다루는 것은 인가 방식의 지형이므로, 구체적 알람 임계치는 조직의 IdP·트래픽 패턴에 따라 달라진다. 다음 신호를 계측 대상으로 삼는다.

- **인바운드 인가 실패율(방식별 분리)**: SigV4 서명 오류와 JWT 검증 실패(만료/audience 불일치/커스텀 클레임 불일치)를 별도로 추적한다. 급증은 IdP 설정 변경이나 클라이언트 버그를 시사한다.
- **Runtime 직접 호출 탐지**: Gateway를 우회해 Runtime에 도달한 요청 수(리소스 정책 `Deny`에 걸려 거부된 시도 포함). 0이 아니면 우회 시도 또는 설정 오류가 있다는 신호다.
- **`InvokeAgentRuntimeForUser` 사용 비율**: `X-Amzn-Bedrock-AgentCore-Runtime-User-Id` 헤더 경로가 얼마나 사용되는지, 그리고 프로덕션 트래픽에서 이 비율이 0에 가까워지는지(JWT 경로로 이전 진행도 지표).
- **워크로드 액세스 토큰 발급/아웃바운드 credential provider 호출 지연시간**: `GetWorkloadAccessTokenForJWT`→`GetResourceOauth2Token` 체인의 p50/p95. 이 장에서 다룬 인바운드 인가 뒤에 이어지는 아웃바운드 발급 경로의 상세 계측은 [OAuth 토큰 교환과 MCP](/09-authorization/oauth-token-exchange-mcp)의 계측 절 참조.

## 체크리스트

- [ ] 각 Runtime 버전의 인바운드 방식(SigV4 vs JWT)이 호출자 유형(내부 서비스 vs 최종 사용자)에 맞게 명시적으로 결정되어 있는가.
- [ ] 동일 에이전트를 두 유형의 호출자에게 노출해야 한다면, 인바운드 방식이 다른 별도 Runtime 버전으로 분리했는가.
- [ ] JWT 인가자의 `discoveryUrl`/`allowedClients`/`allowedAudience`/`allowedScopes`/`customClaims`가 실제 IdP·클라이언트 설정과 일치하는지 확인했는가.
- [ ] Gateway 뒤에 Runtime을 둔다면, SigV4는 리소스 정책 `Deny`+`aws:PrincipalArn`, JWT는 `allowedWorkloadConfiguration`으로 Gateway 경유를 강제했는가.
- [ ] Gateway 실행 역할의 신뢰 정책에 `aws:SourceArn`/`aws:SourceAccount` 조건이 걸려 있어 다른 principal이 그 역할을 assume할 수 없는가.
- [ ] Gateway의 인바운드 유형(IAM/JWT/offloaded)이 아키텍처가 요구하는 인가 위임 구조(예: Cedar 정책 엔진)와 맞는가.
- [ ] `X-Amzn-Bedrock-AgentCore-Runtime-User-Id` 헤더를 쓰고 있다면, `InvokeAgentRuntimeForUser` 권한이 신뢰된 principal·특정 리소스로 스코프되어 있고 프로덕션 전환 계획이 있는가.
- [ ] 아웃바운드 인증 패턴(2LO/3LO/OBO/passthrough) 선택이 각 다운스트림 호출별로 "사용자를 대신하는가"라는 기준으로 문서화되어 있는가.
- [ ] 인바운드 인가자 설정과 아웃바운드 credential provider 설정이 설계 문서에서 별도 섹션으로 구분되어 있는가(둘을 뭉뚱그려 "OAuth 쓴다"로 기록하지 않았는가).

## 참고

- [AWS 공식 문서, Authenticate and authorize with Inbound Auth and Outbound Auth](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html)
- [AWS 공식 문서, Set up inbound authorization for your gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-inbound-auth.html)
- [AWS 공식 문서, Provide identity and credential management for agent applications with Amazon Bedrock AgentCore Identity](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity.html)
- [AWS 공식 문서, Get started with AgentCore Identity](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-getting-started.html)
- [AWS 공식 문서, Common use cases (User-delegated / M2M / OBO)](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/common-use-cases.html)
- [AWS Security Blog, Propagate user authorization context in AI agents with Amazon Bedrock AgentCore](https://aws.amazon.com/blogs/security/propagate-user-authorization-context-in-ai-agents-with-amazon-bedrock-agentcore/)
- [AWS CloudFormation Reference, AWS::BedrockAgentCore::Gateway CustomJWTAuthorizerConfiguration](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-bedrockagentcore-gateway-customjwtauthorizerconfiguration.html)
- [OAuth 토큰 교환과 MCP](/09-authorization/oauth-token-exchange-mcp)
- [툴별 On-Behalf-Of](/09-authorization/per-tool-obo)
- [confused deputy 문제](/09-authorization/confused-deputy)
