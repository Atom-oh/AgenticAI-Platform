---
title: OAuth 토큰 교환과 MCP
description: RFC 8693 OAuth 2.0 Token Exchange의 프로토콜 세부사항과 MCP 인가 스펙이 이를 어떻게 요구하는지 다룬다.
outline: [2, 3]
---

# OAuth 토큰 교환과 MCP

::: tip 이 장에서 얻는 것
- RFC 8693(`urn:ietf:params:oauth:grant-type:token-exchange`) 토큰 교환 grant type의 정확한 요청/응답 파라미터
- MCP 인가 스펙(2025-06-18)이 OAuth 2.1 위에서 강제하는 것 — `resource` 파라미터(RFC 8707), audience 검증, token passthrough 금지
- AgentCore Identity가 지원하는 아웃바운드 OAuth 4가지 패턴(2LO/3LO/token exchange(OBO)/passthrough)과 각각의 API 호출
- 여러 MCP 서버가 서로 다른 OAuth 자격증명을 요구할 때의 관리 모델과 사전 통합 목록
:::

## 왜 문제가 되는가

에이전트가 하나의 MCP 클라이언트로 여러 MCP 서버(Google Drive, Slack, 사내 Gateway 등)를 호출하는 구조에서는, 클라이언트가 받은 하나의 access token을 그대로 여러 서버에 전달하고 싶은 유혹이 생긴다. 하지만 MCP 인가 스펙은 이를 명시적으로 금지한다.

> "MCP servers **MUST NOT** accept or transit any other tokens." — [MCP Authorization spec, Access Token Usage](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)

문제는 두 층위에서 발생한다.

1. **Audience(`aud`) 불일치.** 토큰 A용으로 발급된 access token을 서버 B가 검증 없이 받아주면, A에서 탈취된 토큰이 B에서도 그대로 통한다. MCP 스펙은 이를 "Access Token Privilege Restriction" 취약점으로 규정한다.
2. **Confused deputy.** MCP 서버가 자신이 받은 인바운드 토큰을 검증 없이 다운스트림 API에 그대로 전달(pass-through)하면, 다운스트림은 그 토큰이 "MCP 서버가 검증한 것"이라고 오신뢰하게 된다. MCP 스펙의 Security Considerations는 이를 별도 섹션("Confused Deputy Problem")으로 다룬다 — 자세한 배경과 세션 태그 기반 방어 아키텍처는 [OBO가 필요한 이유](/09-authorization/per-tool-obo)와 [confused deputy](/09-authorization/confused-deputy)에서 다룬다.

이 장은 그 배경 지식을 전제로, **"토큰을 그대로 넘기지 않는다면 정확히 무엇으로 바꿔서 넘기는가"** — 즉 RFC 8693 Token Exchange의 프로토콜 세부사항과 MCP·AgentCore가 이를 구현에 반영하는 방식에 집중한다.

## 핵심 개념

### RFC 8693: OAuth 2.0 Token Exchange grant type

RFC 8693는 토큰 엔드포인트에 새 grant type을 추가한다. 아래 파라미터명은 [RFC 8693 원문](https://datatracker.ietf.org/doc/html/rfc8693)에서 정의된 그대로다.

**요청 파라미터** (`grant_type=urn:ietf:params:oauth:grant-type:token-exchange`):

| 파라미터 | 의미 |
|---|---|
| `grant_type` | 고정값 `urn:ietf:params:oauth:grant-type:token-exchange` |
| `subject_token` | 교환하려는 원본 토큰(예: 사용자 인증으로 발급된 access token) |
| `subject_token_type` | `subject_token`의 타입 URN. 예: `urn:ietf:params:oauth:token-type:access_token`, `urn:ietf:params:oauth:token-type:jwt` |
| `actor_token` (선택) | 위임(delegation)을 수행하는 행위자(actor)의 토큰 — 예: 에이전트/서비스 자신의 M2M 토큰 |
| `actor_token_type` (선택) | `actor_token`의 타입 URN |
| `requested_token_type` (선택) | 발급받고자 하는 토큰의 타입 URN |
| `resource` / `audience` (선택) | 발급될 토큰이 사용될 대상 리소스/audience를 명시 |
| `scope` (선택) | 요청하는 스코프 |

**응답 파라미터**: `access_token`, `issued_token_type`(발급된 토큰의 타입 URN), `token_type`(보통 `Bearer`), `expires_in`, 그리고 선택적으로 `scope`.

핵심은 `actor_token`의 존재 여부로 **impersonation과 delegation을 구분**한다는 점이다. `actor_token`이 없으면 순수 impersonation(원래 주체를 대신해 완전히 그 자격으로 행동), `actor_token`이 있으면 delegation(행위자가 누구를 대신해 행동하는지를 `act` claim으로 토큰에 남기는 방식)이다. 이 구분은 감사 로그에서 "누가 실제로 호출했는가"를 재구성할 때 중요하다.

### MCP 인가 스펙이 OAuth에 요구하는 것

MCP 공식 스펙(2025-06-18)의 [Authorization 섹션](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)은 HTTP 기반 전송에 한해 인가를 다음처럼 규정한다(STDIO 전송은 이 스펙을 따르지 않고 환경변수에서 자격증명을 얻도록 함).

- MCP 서버는 **OAuth 2.1 resource server**, MCP 클라이언트는 **OAuth 2.1 client** 역할을 맡는다.
- MCP 서버는 **OAuth 2.0 Protected Resource Metadata(RFC 9728)**를 `MUST` 구현해 인가 서버 위치를 알려야 하고, 401 응답의 `WWW-Authenticate` 헤더로 이를 안내해야 한다.
- 인가 서버는 **OAuth 2.0 Authorization Server Metadata(RFC 8414)**를 `MUST` 제공해야 한다.
- MCP 클라이언트는 인가/토큰 요청 모두에 **Resource Indicators(RFC 8707)의 `resource` 파라미터**를 `MUST` 포함해야 하며, 이 값은 MCP 서버의 canonical URI(예: `https://mcp.example.com/mcp`, 쿼리·프래그먼트 없음)여야 한다.
- MCP 서버는 자신에게 발급된 토큰인지 audience를 `MUST` 검증해야 하며, 검증에 실패하면 401을 반환해야 한다.
- 가장 중요한 제약: **"MCP servers MUST NOT accept or transit any other tokens"** — 인바운드 토큰을 다운스트림 API에 그대로 전달(passthrough)하는 것은 금지된다. 다운스트림 호출이 필요하면 MCP 서버는 별도의 OAuth client로서 행동하고, 다운스트림용 토큰은 별개의 발급 절차(즉 이 장이 다루는 token exchange)로 얻어야 한다.

이 마지막 규정이 RFC 8693 Token Exchange가 MCP 아키텍처에서 필요해지는 지점이다: MCP 서버(또는 그 뒤의 에이전트)가 인바운드 토큰을 폐기하지 않으면서도, 다운스트림 API가 요구하는 audience로 바꿔 발급받아야 한다.

### AgentCore Identity의 아웃바운드 OAuth 4가지 패턴

AgentCore Identity는 외부 서비스(MCP 서버 포함) 접근을 위해 다음 네 가지 아웃바운드 OAuth 패턴을 지원한다([AWS Security Blog, Propagate user authorization context](https://aws.amazon.com/blogs/security/propagate-user-authorization-context-in-ai-agents-with-amazon-bedrock-agentcore/)).

- **2LO(client credentials, M2M)**: 에이전트가 서비스 계정으로 인증해 조직 범위의 광범위한 토큰을 받는다. 사용자별 스코핑은 에이전트 쪽 쿼리 필터링에 의존한다.
- **3LO(Authorization Code)**: 사용자가 브라우저 리다이렉트로 명시적으로 동의하고, 외부 서비스가 사용자별 접근을 직접 강제한다. 백그라운드 작업에는 부적합(사용자 개입 필요).
- **Token exchange(OBO)**: 이미 인증된 사용자 identity를 추가 동의 없이 다운스트림 서비스용 토큰으로 교환한다.
- **Passthrough**: 인바운드 토큰을 그대로 전달 — MCP 스펙이 금지하는 패턴이며 원칙적으로 피해야 한다.

3LO를 사용할 때, 호스팅된 에이전트가 사용자를 대신해 OAuth 토큰을 얻어야 한다면 요청에 `X-Amzn-Bedrock-AgentCore-Runtime-User-Id` 헤더로 사용자 식별자를 지정할 수 있다. 이 헤더는 내부적으로 `GetWorkloadAccessTokenForUserId` 경로를 사용하며, 이 헤더를 포함해 `InvokeAgentRuntime`을 호출하려면 `bedrock-agentcore:InvokeAgentRuntime` 외에 `bedrock-agentcore:InvokeAgentRuntimeForUser` IAM 액션이 추가로 필요하다. 다만 AWS는 이 헤더가 "userId를 인증된 최종 사용자 identity에 대해 검증하지 않으며, 호출하는 워크로드가 올바른 값을 전달할 책임을 진다"고 명시한다 — 프로덕션에서는 발급자·서명·만료를 암호학적으로 검증하는 JWT bearer 경로(`GetWorkloadAccessTokenForJWT`)를 권장한다([AWS 문서, Authenticate and authorize with Inbound Auth and Outbound Auth](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html)).

OBO 토큰 교환의 실제 API 흐름은 다음과 같다([AWS 문서, On-behalf-of token exchange with AgentCore Identity](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/on-behalf-of-token-exchange.html)).

```bash
# 1) 인바운드 JWT로 workload access token 획득
aws bedrock-agentcore get-workload-access-token-for-jwt \
  --workload-name sample-workload \
  --user-token "inbound-jwt-token"
# → { "workloadAccessToken": "workload-access-token" }

# 2) OBO 토큰 교환 수행
aws bedrock-agentcore get-resource-oauth2-token \
  --resource-credential-provider-name sample-obo-provider \
  --oauth2-flow ON_BEHALF_OF_TOKEN_EXCHANGE \
  --scopes "sample-scope" \
  --workload-identity-token "workload-access-token"
# → { "accessToken": "on-behalf-of-token" }
```

credential provider 설정에서 `onBehalfOfTokenExchangeConfig.grantType`을 두 가지 모드 중 하나로 지정한다.

- `TOKEN_EXCHANGE`: RFC 8693를 그대로 따른다. `grant_type`은 `urn:ietf:params:oauth:grant-type:token-exchange`로 매핑되고, 인바운드 JWT가 `subject_token`(타입은 `urn:ietf:params:oauth:token-type:jwt`)이 된다. `actor_token_content`를 `M2M`(client credentials로 별도 획득한 access token을 `actor_token`으로 사용), `AWS_IAM_ID_TOKEN_JWT`, `NONE` 중 하나로 지정한다.
- `JWT_AUTHORIZATION_GRANT`: RFC 7523 §2.1(JWT Bearer Grant)을 따른다. `grant_type`은 `urn:ietf:params:oauth:grant-type:jwt-bearer`로 매핑되고, 인바운드 JWT가 `assertion` 파라미터로 전달된다. Microsoft의 On-Behalf-Of 플로우처럼 RFC 8693이 아니라 RFC 7523 기반으로 OBO를 구현하는 IdP를 위한 모드다(`requested_token_use=on_behalf_of`가 자동 추가됨).

이 두 모드 중 어느 것을 쓰느냐는 다운스트림 IdP가 어떤 표준을 구현했는지에 따라 결정되며, AgentCore Identity는 두 프로토콜의 차이를 흡수해 에이전트 개발자가 원시 토큰이나 client secret을 직접 다루지 않게 한다.

### 서버마다 다른 OAuth 자격증명 관리

Google Drive MCP는 Google OAuth, Slack MCP는 Slack OAuth, Salesforce는 Salesforce OAuth를 요구하는 상황에서, AgentCore Identity는 `credentialProviderVendor`로 사전 구성된 provider 세트를 제공한다. `UpdateOauth2CredentialProvider` API가 명시하는 지원 vendor 목록에는 `GoogleOauth2`, `GithubOauth2`, `SlackOauth2`, `SalesforceOauth2`, `MicrosoftOauth2`, `AtlassianOauth2`, `LinkedinOauth2`, `XOauth2`, `OktaOauth2`, `OneLoginOauth2`, `PingOneOauth2`, `FacebookOauth2`, `YandexOauth2`, `RedditOauth2`, `ZoomOauth2`, `TwitchOauth2`, `SpotifyOauth2`, `DropboxOauth2`, `NotionOauth2`, `HubspotOauth2`, `CyberArkOauth2`, `FusionAuthOauth2`, `Auth0Oauth2`, `CognitoOauth2`, 그리고 커스텀 IdP용 `CustomOauth2`가 포함된다([AWS API Reference, UpdateOauth2CredentialProvider](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_UpdateOauth2CredentialProvider.html)).

각 provider는 자신만의 client ID/secret과 콜백 URL을 가지며, 실제 토큰 저장·순환·per-tool 스코핑 설계(토큰 볼트 아키텍처)는 이 장의 범위가 아니다 — [OBO가 필요한 이유](/09-authorization/per-tool-obo)에서 STS 세션 태그와 토큰 볼트의 전체 그림을 다룬다. 이 장에서 기억할 것은 하나다: provider가 몇 개든, 각 provider로의 발급은 위에서 설명한 동일한 RFC 8693 / RFC 7523 그랜트 위에서 이루어진다는 점이다.

### Audience 검증이 confused deputy 방지의 전제조건인 이유

토큰 교환으로 audience를 바꿔도, 다운스트림 서버가 자신에게 발급된 토큰인지 검증하지 않으면 방어는 무의미하다. MCP 스펙은 이를 정확히 지목한다.

> "MCP servers **MUST** only accept tokens specifically intended for themselves and **MUST** reject tokens that do not include them in the audience claim." — [MCP Authorization spec, Access Token Privilege Restriction](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)

즉 `aud` 검증은 "토큰 교환을 했으니 안전하다"가 아니라 "교환된 토큰의 `aud`를 다운스트림이 실제로 검사한다"는 두 조건이 함께 성립해야 confused deputy가 막힌다는 뜻이다. `aud` 검증 자체의 실패 패턴과 방어 체크리스트는 [confused deputy](/09-authorization/confused-deputy)에서 별도로 다룬다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 에이전트가 조직 전체 데이터(사용자 무관)에 접근 | 2LO(client credentials) | 사용자별 위임이 필요 없고 백그라운드에서 동작해야 함 | 에이전트가 쿼리 필터링을 스스로 책임져야 하며, 과다 권한 토큰이 유출되면 조직 전체가 노출됨 |
| 사용자가 최초 1회 명시적으로 서비스 연동에 동의해야 함(예: 첫 GitHub 연동) | 3LO(Authorization Code) | 외부 서비스의 동의 화면을 반드시 거쳐야 하는 규제/정책 요구 | 브라우저 리다이렉트가 필요해 완전 자동화된 백그라운드 호출에는 쓸 수 없음 |
| 이미 인증된 사용자를 대신해 여러 다운스트림 API를 추가 동의 없이 호출 | Token exchange(OBO, RFC 8693) | 사용자 identity가 end-to-end로 전파되며 토큰 저장이 사용자별로 필요 없음 | 다운스트림 IdP가 RFC 8693 또는 RFC 7523 OBO를 지원해야 함(모든 IdP가 지원하지 않음) |
| MCP 서버가 받은 인바운드 토큰을 다운스트림에 그대로 넘기고 싶은 유혹 | 금지(passthrough 사용 안 함) | MCP 스펙이 명시적으로 금지하며 confused deputy로 직결 | 대안(토큰 교환)을 구현하는 엔지니어링 비용이 듦 |
| 다운스트림 IdP가 Microsoft Entra처럼 RFC 7523 기반 OBO만 지원 | `JWT_AUTHORIZATION_GRANT` 모드 | 해당 IdP의 네이티브 OBO 프로토콜과 일치 | RFC 8693의 `actor_token` 기반 delegation 감사 흔적은 얻지 못함 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 다운스트림 API가 401/403을 반환하며 "invalid audience" | 토큰 교환 없이 인바운드 토큰을 그대로 전달(passthrough), 또는 `resource`/`aud` 파라미터 누락 | 다운스트림에 전달된 토큰의 `aud` claim을 디코드해 원래 MCP 서버 URI가 남아있는지 확인 | RFC 8693 token exchange를 구현해 다운스트림 audience로 재발급받고, MCP 클라이언트는 `resource` 파라미터(RFC 8707)를 authorization/token 요청 모두에 포함 |
| MCP 서버가 401을 반환하는데 클라이언트가 재시도 루프에 빠짐 | 클라이언트가 `WWW-Authenticate` 헤더에서 `resource_metadata`를 파싱하지 못해 인가 서버를 재탐색하지 못함 | 401 응답 헤더에 `WWW-Authenticate`가 있는지, RFC 9728 Protected Resource Metadata 문서가 유효한지 확인 | MCP 클라이언트의 discovery 로직을 스펙에 맞춰 구현(PRM → AS metadata → 토큰 요청) |
| 특정 IdP에서만 OBO 교환이 `invalid_grant`로 실패 | 해당 IdP가 RFC 8693이 아니라 RFC 7523(JWT bearer) 기반 OBO만 지원하는데 `TOKEN_EXCHANGE` 모드로 설정됨 | credential provider의 `onBehalfOfTokenExchangeConfig.grantType` 확인, IdP 문서에서 지원 grant type 확인 | `JWT_AUTHORIZATION_GRANT` 모드로 전환(Microsoft 계열 IdP는 대개 이 모드) |
| 사용자가 3LO 동의를 이미 완료했는데 에이전트가 재차 브라우저 리다이렉트를 요구 | 세션 바인딩 실패 — 인가를 시작한 사용자와 완료한 사용자가 서버 관점에서 다르게 식별됨, 또는 authorization URL/session URI 10분 TTL 만료 | AgentCore Gateway의 URL 세션 바인딩 로그와 `CompleteResourceTokenAuth` 호출 타이밍 확인 | 세션 바인딩 URL·session URI를 TTL 내에 처리하도록 UX 조정, 사용자 identity를 세션 시작·완료 양쪽에서 동일하게 전달 |
| `X-Amzn-Bedrock-AgentCore-Runtime-User-Id` 헤더로 지정한 사용자가 실제 요청자와 다름 | 이 헤더는 AgentCore가 값을 검증하지 않고 호출 워크로드를 신뢰하는 구조이며, 잘못된 값이 그대로 자격증명 바인딩에 사용됨 | 헤더 설정 로직이 최종 사용자 인증 결과(예: IdP JWT)와 독립적으로 값을 넣는지 코드 리뷰 | 프로덕션에서는 JWT bearer 경로(`GetWorkloadAccessTokenForJWT`)로 전환해 발급자·서명·만료를 암호학적으로 검증 |

## 안티패턴

- ❌ 인바운드 access token을 MCP 서버가 검증 없이 다운스트림 API 호출에 그대로 재사용(passthrough) → ✅ RFC 8693 token exchange로 다운스트림 audience용 토큰을 별도 발급
- ❌ 모든 MCP 서버 호출에 동일한 2LO 서비스 계정 토큰 사용, 사용자별 스코핑을 애플리케이션 로직에서만 구현 → ✅ 사용자 identity 전파가 필요한 경로는 OBO token exchange, 조직 범위 데이터만 2LO
- ❌ `X-Amzn-Bedrock-AgentCore-Runtime-User-Id` 헤더를 프로덕션 사용자 인증의 유일한 근거로 사용 → ✅ JWT bearer(`GetWorkloadAccessTokenForJWT`) 경로로 발급자·서명·만료를 검증하고, 헤더는 개발/퀵스타트 단계로 한정
- ❌ MCP 클라이언트가 `resource` 파라미터 없이 토큰을 요청하고 인가 서버가 이를 무시해도 그냥 진행 → ✅ `resource` 파라미터를 항상 포함하고, 발급된 토큰의 `aud`가 실제로 의도한 MCP 서버 canonical URI인지 클라이언트/서버 양쪽에서 확인

## 계측 (SLI)

이 장에서 다루는 프로토콜 계층은 다음 신호로 계측한다. 구체적인 대시보드·알람 임계치는 조직의 IdP·게이트웨이 구성에 따라 달라지므로 임의의 수치를 제시하지 않는다.

- **토큰 교환 성공률**: `grant_type=token-exchange` 또는 `jwt-bearer` 요청 중 `invalid_grant`/`invalid_target` 오류 비율. 급증하면 IdP 설정 변경이나 audience 불일치를 의심한다.
- **Audience 거부율**: 다운스트림 리소스 서버가 `aud` 불일치로 401을 반환한 비율. 0이 아니면 클라이언트가 여전히 잘못된 audience로 토큰을 요청하고 있다는 신호다.
- **Passthrough 탐지**: MCP 서버 로그에서 인바운드 `Authorization` 헤더의 토큰과 다운스트림 호출에 사용된 토큰이 동일 JWT인지 비교 — 동일하면 스펙 위반이다.
- **`actor_token` 유무 비율**: delegation(actor_token 있음) vs impersonation(없음) 비율을 추적해, 감사 요구사항이 있는 워크로드가 실제로 delegation 경로를 타는지 확인한다.

## 체크리스트

- [ ] MCP 서버가 RFC 9728 Protected Resource Metadata를 제공하고 401 응답에 `WWW-Authenticate`를 포함하는가
- [ ] MCP 클라이언트가 authorization/token 요청 모두에 `resource` 파라미터(RFC 8707, canonical URI, 소문자 scheme/host, 트레일링 슬래시 없음)를 포함하는가
- [ ] MCP 서버가 자신에게 발급된 토큰인지 `aud` claim을 검증하고, 실패 시 401을 반환하는가
- [ ] 다운스트림 API 호출에 인바운드 토큰을 그대로 전달(passthrough)하는 코드 경로가 없는가
- [ ] 사용자 위임이 필요한 경로는 2LO가 아니라 OBO token exchange(RFC 8693) 또는 JWT bearer(RFC 7523)를 사용하는가
- [ ] 다운스트림 IdP가 지원하는 OBO 표준(`TOKEN_EXCHANGE` vs `JWT_AUTHORIZATION_GRANT`)을 확인하고 credential provider 설정을 그에 맞췄는가
- [ ] `X-Amzn-Bedrock-AgentCore-Runtime-User-Id` 헤더를 프로덕션에서 사용 중이라면, JWT bearer 경로로 전환할 계획이 있는가
- [ ] MCP 서버별로 서로 다른 OAuth credential provider가 분리되어 있고, 한 provider의 토큰이 다른 provider의 리소스에 사용될 수 없는가
- [ ] delegation이 필요한 워크로드에서 `actor_token`을 채워 `act` claim이 감사 로그에 남는가

## 참고

- [RFC 8693, OAuth 2.0 Token Exchange](https://datatracker.ietf.org/doc/html/rfc8693)
- [RFC 8707, Resource Indicators for OAuth 2.0](https://www.rfc-editor.org/rfc/rfc8707.html)
- [RFC 9728, OAuth 2.0 Protected Resource Metadata](https://datatracker.ietf.org/doc/html/rfc9728)
- [RFC 7523 §2.1, JWT Profile for OAuth 2.0 Authorization Grants](https://datatracker.ietf.org/doc/html/rfc7523)
- [MCP Authorization Specification (2025-06-18)](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)
- [AWS 문서, On-behalf-of token exchange with AgentCore Identity](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/on-behalf-of-token-exchange.html)
- [AWS 문서, Authenticate and authorize with Inbound Auth and Outbound Auth](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html)
- [AWS Security Blog, Propagate user authorization context in AI agents with Amazon Bedrock AgentCore](https://aws.amazon.com/blogs/security/propagate-user-authorization-context-in-ai-agents-with-amazon-bedrock-agentcore/)
- [AWS Machine Learning Blog, Implement on-behalf-of token exchange for multi-tenant agents with Amazon Bedrock AgentCore Gateway](https://aws.amazon.com/blogs/machine-learning/implement-on-behalf-of-token-exchange-for-multi-tenant-agents-with-amazon-bedrock-agentcore-gateway/)
- [AWS API Reference, UpdateOauth2CredentialProvider](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_UpdateOauth2CredentialProvider.html)
- [OBO가 필요한 이유](/09-authorization/per-tool-obo)
- [confused deputy](/09-authorization/confused-deputy)
