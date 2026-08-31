---
title: MCP 서버 설계
description: MCP 서버를 플랫폼 컴포넌트로 설계하는 원칙 — 스키마 안정성, 툴 개수 절제, 응답 상한, 그리고 code execution 패턴을 다룬다.
outline: [2, 3]
---

# MCP 서버 설계

::: tip 이 장에서 얻는 것
- MCP 서버를 "툴 모음"이 아니라 **플랫폼 컴포넌트**로 설계하는 4가지 원칙: 스키마 안정성, 툴 개수 절제, 응답 상한, 컨텍스트 통과 경로 통제
- 직접 tool call 방식의 두 가지 구조적 비용(툴 정의 선적재 + 중간 결과 컨텍스트 통과)과, Anthropic이 제안한 **code execution with MCP** 패턴이 이를 어떻게 해소하는지
- 직접 tool call vs code execution vs AgentCore Gateway 경유를 고르는 결정 표
- MCP 서버 운영에서 반복되는 실패 모드 진단 표와 배포 전 체크리스트
:::

## 왜 문제가 되는가

MCP(Model Context Protocol)는 에이전트와 외부 시스템을 잇는 표준 프로토콜로 자리 잡았고, 오픈소스 공개 이후 커뮤니티가 수천 개의 MCP 서버를 만들었다([Anthropic, Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)). 문제는 대부분의 MCP 서버가 "기존 API에 얇은 래퍼를 씌운 것"으로 만들어지고, 그 순간부터 플랫폼의 세 가지 핵심 지표를 동시에 갉아먹는다는 점이다.

**첫째, 토큰 비용.** 직접 tool call 방식에서 툴 정의는 모델이 호출하기 전에 전부 컨텍스트 윈도에 선적재된다. Anthropic은 이 문제를 정량화했다: 대부분의 MCP 클라이언트에서 툴 정의는 컨텍스트 앞부분에 로드되고, 각 정의는 이름·설명·파라미터 스키마를 포함해 상당한 토큰을 차지한다 — 수천 개 툴이 연결된 에이전트는 요청을 읽기도 전에 수십만 토큰을 소모할 수 있다([Anthropic, Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)). 여기에 더해 **모든 중간 결과가 모델 컨텍스트를 통과**한다. Google Drive에서 회의록을 읽어 Salesforce 레코드에 붙이는 단순 작업조차, 회의록 전문이 (1) tool result로 한 번, (2) 다음 tool call의 파라미터로 또 한 번, 총 두 번 컨텍스트를 지나간다. Anthropic의 예시에서는 이 패턴만으로 추가 5만 토큰이 소모됐다([같은 글](https://www.anthropic.com/engineering/code-execution-with-mcp)).

**둘째, 정확도.** 툴이 많아질수록 모델의 툴 선택 정확도는 떨어진다. 이 현상 자체는 [툴 과부하](/03-accuracy-eval/tool-overload)에서 정량적으로 다루므로 여기서는 설계 함의만 가져온다: MCP 서버 설계자는 "API 엔드포인트 수 = 툴 수"라는 등식을 깨야 한다.

**셋째, 캐시 경제성.** 툴 정의는 프롬프트 캐시 프리픽스의 최상위 계층(`tools`)에 위치하므로, MCP 서버가 스키마를 불안정하게 내보내면 — 재시작마다 설명 문구가 달라지거나 필드 순서가 흔들리면 — 그 아래 모든 캐시가 무효화된다. 이 메커니즘은 [캐시 미스 근본 원인](/04-caching/cache-miss-root-causes)에서 진단했다. 이 장은 그 원인을 만들지 않는 **서버 쪽 설계**를 다룬다.

역할 분담을 명확히 하면: [툴 설계](/01-agent-design/tool-design)는 **개별 툴 하나의 인터페이스**(이름, 파라미터, 응답 포맷)를 다루고, 이 장은 **MCP 서버 단위의 설계 결정** — 툴을 몇 개로 묶을지, 스키마를 어떻게 안정화할지, 직접 호출과 code execution 중 무엇을 택할지 — 을 다룬다.

## 핵심 개념

### 원칙 1: 스키마는 배포 단위로 고정한다

MCP 서버가 내보내는 툴 스키마(이름, description, `inputSchema`)는 프롬프트 캐시 관점에서 **캐시 키의 일부**다. Anthropic 캐시는 정확 프리픽스 매칭 기반이고 `tools → system → messages` 계층에서 `tools`가 최상위이므로([Anthropic, Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)), 스키마가 1바이트만 달라져도 전체 캐시가 깨진다.

서버 설계 규칙으로 번역하면:

- 스키마를 **정적 상수**로 선언한다. 런타임에 문자열을 조립해 description을 만들지 않는다(타임스탬프, 버전 문자열, 환경 이름 삽입 금지).
- JSON 직렬화 시 키 순서를 고정한다. 언어 런타임의 dict/map 순회 순서에 의존하지 않는다.
- 스키마 변경은 서버 버전 릴리스와 함께만 일어나게 하고, 변경 시 클라이언트 캐시 무효화 비용을 릴리스 노트에 명시한다.
- 동적으로 리소스가 늘어나는 도메인(예: 테이블이 계속 추가되는 DB)은 "리소스마다 툴 하나"가 아니라 "조회 툴 하나 + 리소스 식별자 파라미터"로 모델링해 스키마 자체는 불변으로 유지한다.

MCP 명세는 서버가 `notifications/tools/list_changed`로 툴 목록 변경을 알릴 수 있게 허용하지만([MCP Specification, Tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)), 캐시 관점에서 이 기능은 "쓸 수 있다"와 "자주 써도 된다"가 다르다. 목록 변경 알림이 세션 중에 발생하면 그 세션의 캐시 프리픽스는 사실상 리셋이다.

### 원칙 2: 툴 개수는 서버가 절제한다

기존 API의 엔드포인트를 1:1로 툴화하면 CRUD 4개 × 리소스 N개로 툴이 폭발한다. 서버 설계 단계에서 줄이는 전형적 기법:

- **워크플로 단위 통합**: 모델이 항상 연달아 호출하는 3개 API(조회 → 변환 → 저장)를 하나의 툴로 묶는다. 개별 툴의 인터페이스 품질 기준은 [툴 설계](/01-agent-design/tool-design) 참고.
- **enum 파라미터로 변형 흡수**: `list_users`, `list_admins`, `list_guests` 대신 `list_accounts(role: enum)`.
- **읽기 전용 서브셋 분리**: 에이전트 용도별로 필요한 툴만 담은 별도 서버(또는 서버 프로파일)를 노출한다. 전사 공용 "만능 MCP 서버" 하나보다, 용도별 서버 여러 개가 컨텍스트 비용과 권한 통제 양쪽에서 낫다.

몇 개가 임계인지에 대한 정량 근거와 실측은 [툴 과부하](/03-accuracy-eval/tool-overload)에서 다룬다.

### 원칙 3: 응답에 상한을 둔다

MCP 서버는 응답 크기를 스스로 제한해야 한다. "전체 결과를 돌려주고 모델이 알아서 고르게 한다"는 설계는 중간 결과가 컨텍스트를 통과하는 직접 호출 모드에서 컨텍스트 폭발로 직결된다. 서버 레벨 규칙:

- 목록형 응답은 기본 페이지 크기를 강제하고(`limit` 기본값 + 서버 측 최대치), truncation이 일어났음을 응답에 명시한다.
- 대용량 바이너리/문서는 본문 대신 **참조(리소스 URI, 사전서명 URL, 파일 경로)** 를 반환하고, 필요한 부분만 읽는 별도 툴을 제공한다.
- 응답 직렬화 포맷에서 모델이 쓰지 않는 필드(내부 ID, 감사 메타데이터)를 기본 제거하고, 필요 시 `verbose` 플래그로만 노출한다.

개별 툴의 응답 포맷 설계(무엇을 어떤 구조로 돌려줄지)는 [툴 설계](/01-agent-design/tool-design)의 영역이고, 여기서의 규칙은 "서버가 상한을 **강제**해야 한다"는 플랫폼 정책이다 — 프롬프트로 "짧게 답해달라"고 부탁하는 것은 통제가 아니다.

### 원칙 4: 컨텍스트 통과 경로를 설계 대상으로 삼는다 — code execution 패턴

Anthropic은 2025년 11월 엔지니어링 블로그에서 직접 tool call의 대안으로 **code execution with MCP** 패턴을 제시했다([Anthropic, Code execution with MCP: Building more efficient agents](https://www.anthropic.com/engineering/code-execution-with-mcp)). 핵심 아이디어: MCP 서버의 툴들을 모델에게 "호출 가능한 tool 목록"으로 주는 대신, **파일시스템 위의 TypeScript 함수**로 제시한다.

```
servers/
├── google-drive/
│   ├── getDocument.ts   // 타입 있는 함수 인터페이스
│   └── index.ts
├── salesforce/
│   ├── updateRecord.ts
│   └── index.ts
└── ...
```

에이전트는 `./servers/` 디렉터리를 탐색해 필요한 서버·함수만 발견하고(progressive disclosure), 실행 코드에서 그 함수들을 import해 호출한다:

```typescript
// 중간 데이터가 모델 컨텍스트를 거치지 않는다
import * as gdrive from "./servers/google-drive";
import * as salesforce from "./servers/salesforce";

const transcript = (await gdrive.getDocument({ documentId: "abc123" })).content;
await salesforce.updateRecord({
  objectType: "SalesMeeting",
  recordId: "00Q5f000001abcXYZ",
  data: { Notes: transcript },
});
```

이 구조가 바꾸는 것:

1. **툴 정의 선적재 제거.** 모델은 전체 툴 카탈로그 대신 파일 목록만 보고, 실제로 쓸 함수의 정의만 읽는다. 필요하면 `search_tools` 같은 검색 툴로 관련 정의만 로드한다. Anthropic의 예시 시나리오에서는 15만 토큰이 2천 토큰으로 줄었다 — 98.7% 절감([같은 글](https://www.anthropic.com/engineering/code-execution-with-mcp)).
2. **중간 결과 오프로딩.** 위 코드에서 `transcript`는 실행 환경 안에서만 흐르고, 모델은 최종 확인 메시지만 본다. 1만 행 스프레드시트에서 5행만 필터링해 돌려주는 식의 가공도 코드 안에서 끝난다.
3. **PII·프라이버시 이점.** 중간 데이터가 모델 컨텍스트를 거치지 않으므로, 고객 이메일·전화번호 같은 민감 데이터가 모델 제공자에게 전송되지 않게 만들 수 있다. Anthropic은 한 단계 더 나아가 실행 하니스가 PII를 자동 토크나이즈하는 패턴 — 모델은 `[EMAIL_1]`, `[PHONE_1]` 같은 플레이스홀더만 보고, 실제 값은 `gdrive.getDocument` → `salesforce.updateRecord` 경로로 실행 환경 안에서만 이동 — 을 제시한다([같은 글](https://www.anthropic.com/engineering/code-execution-with-mcp)). "민감 데이터가 어느 경계까지 나갈 수 있는가"가 규제 요건인 한국 금융 맥락에서는 이 속성이 아키텍처 선택지를 바꾼다 — [하이브리드 아키텍처](/12-security-korea/hybrid-architecture)에서 다룬다.
4. **상태 유지와 skill 축적.** 중간 결과를 파일로 저장해 작업을 재개하거나, 검증된 코드 조각을 재사용 가능한 함수(skill)로 축적할 수 있다.

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요
>
> 위 98.7%는 Anthropic 블로그의 단일 예시 시나리오 수치다. 이후 커뮤니티 구현들(오픈소스 code-mode 하니스, 개인 벤치마크)에서 워크로드에 따라 **50~99% 토큰 절감**이 보고되고 있으나, 이는 표준화된 벤치마크가 아니라 개별 구현의 자체 측정치다. 자신의 워크로드에서 툴 카탈로그 크기·중간 결과 크기를 직접 계측한 뒤 판단해야 한다.

**공짜가 아니다.** 이 패턴은 에이전트가 임의 코드를 실행할 수 있는 환경을 전제한다. Anthropic도 명시한다: 안전한 실행 환경(샌드박싱, 리소스 제한, 모니터링)이 필요하고, 이는 직접 tool call에는 없는 운영 부담과 보안 고려사항을 추가한다([같은 글](https://www.anthropic.com/engineering/code-execution-with-mcp)). 직접 호출 모드는 하니스가 파라미터 검증·권한 검사를 호출 지점에서 통제할 수 있지만, code execution 모드에서는 그 통제가 샌드박스 경계로 이동한다.

::: warning 미정착 영역
Code execution with MCP는 2025년 11월 제안 이후 빠르게 확산 중이지만, 표준화된 하니스 계약(파일시스템 레이아웃, 함수 시그니처 생성 규칙, PII 토크나이즈 인터페이스)은 아직 없다. MCP 명세 자체의 일부가 아니며, 구현체마다 방식이 다르다. 지금 도입한다면 하니스 교체 비용을 감수할 수 있는 경계(내부 플랫폼 레이어)에 두고, 툴 함수 인터페이스는 MCP 스키마에서 기계적으로 생성해 원본 스키마를 single source of truth로 유지하라.
:::

### 배포 형태: 직접 구축 vs Gateway 경유

MCP 서버를 직접 작성하는 것만이 선택지가 아니다. 이미 REST API·Lambda 함수·OpenAPI 스펙이 있는 조직이라면, Amazon Bedrock AgentCore Gateway가 기존 API와 Lambda 함수를 코드 변경 없이 MCP 툴로 노출해 준다 — Gateway가 프로토콜 변환·인증(inbound/outbound)·툴 검색을 대신 처리한다([AWS, Amazon Bedrock AgentCore Gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)). 스키마 안정성 관점에서도 Gateway는 툴 정의를 OpenAPI/Smithy 스펙에서 파생시키므로 "런타임 조립 스키마" 문제를 구조적으로 피한다. 상세 아키텍처와 시맨틱 툴 검색은 [AgentCore Gateway 딥다이브](/10-agentcore/gateway-deep-dive)에서 다룬다.

### 공급망 보안은 별도 장에서

서드파티 MCP 서버 도입 시의 tool poisoning(툴 description에 숨긴 악성 지시), rug pull(설치 후 스키마 변조), 서버 사칭 등 공급망 위협은 이 장의 범위가 아니다 — 정본은 [MCP 공급망 보안](/12-security-korea/mcp-supply-chain)이다. 이 장의 설계 원칙 중 "스키마를 배포 단위로 고정하고 변경을 해시로 감지한다"(원칙 1)는 캐시 경제성뿐 아니라 rug pull 탐지의 기초이기도 하다는 점만 짚어 둔다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 툴 수 적음(한 자릿수), 중간 결과 작음, 왕복 2~3회 이내 | 직접 tool call | 하니스가 가장 단순하고, 호출 지점 권한 통제가 쉬움 | 툴·데이터가 늘면 컨텍스트 비용이 선형 이상으로 증가 |
| 툴 카탈로그가 크거나(수십~수천), 대용량 중간 결과를 툴 간에 전달 | code execution with MCP | 필요한 정의만 로드 + 중간 결과가 컨텍스트를 우회([Anthropic](https://www.anthropic.com/engineering/code-execution-with-mcp)) | 샌드박스 구축·운영 부담, 하니스 계약 미표준화 |
| 민감 데이터(PII)가 툴 간 이동해야 하는데 모델 경유가 규제·정책상 곤란 | code execution + PII 토크나이즈 하니스 | 실데이터는 실행 환경 안에서만 흐르고 모델은 플레이스홀더만 봄 | 토크나이즈 레이어 구현·검증 필요 — [하이브리드 아키텍처](/12-security-korea/hybrid-architecture) 참고 |
| 기존 REST API/Lambda/OpenAPI 자산을 MCP로 노출하고 싶음 | AgentCore Gateway | 코드 변경 없이 MCP화, 인증·툴 검색 내장([AWS docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)) | AWS 종속, Gateway 계층의 지연·과금 추가 — [Gateway 딥다이브](/10-agentcore/gateway-deep-dive) |
| 리소스가 동적으로 늘어나는 도메인(테이블, 프로젝트, 채널…) | 툴은 고정하고 리소스는 파라미터로 | 스키마 불변 → 캐시 안정([캐시 미스 근본 원인](/04-caching/cache-miss-root-causes)) | 파라미터 검증 로직이 서버 쪽으로 이동 |
| 하나의 서버를 여러 에이전트 용도가 공유 | 용도별 툴 서브셋 프로파일 분리 | 툴 과부하 방지 + 최소 권한([툴 과부하](/03-accuracy-eval/tool-overload)) | 프로파일 관리 오버헤드, 서버 인스턴스 증가 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| MCP 서버 연결 후 캐시 히트율 급락 | 서버가 재시작·요청마다 스키마를 미세하게 다르게 직렬화 | 두 세션의 `tools` 배열을 덤프해 diff; 스키마 해시를 로깅 | 스키마 정적 상수화, 키 정렬 고정 — [캐시 미스 근본 원인](/04-caching/cache-miss-root-causes) |
| 요청을 시작하기도 전에 컨텍스트 수만~수십만 토큰 소모 | 전체 툴 카탈로그 선적재(직접 호출 모드의 구조적 비용) | 첫 요청의 input token 수에서 사용자 프롬프트 토큰을 뺀 값 계측 | 툴 서브셋 프로파일 분리, 또는 code execution 패턴으로 전환 |
| 단순 파이프라인 작업의 토큰 비용이 데이터 크기에 비례해 폭증 | 중간 결과가 tool result + 다음 tool call 파라미터로 컨텍스트를 2회 통과 | 트레이스에서 동일 페이로드가 두 turn에 반복 등장하는지 확인 | code execution으로 데이터 경로를 실행 환경 내부로 이동 |
| 모델이 엉뚱한 툴을 고르거나 존재하지 않는 툴을 호출 | 툴 개수 과다 + 유사 이름/설명 중복 | 툴 선택 정확도 eval — [툴 과부하](/03-accuracy-eval/tool-overload) | 워크플로 단위 통합, enum 파라미터화, 서브셋 분리 |
| 특정 툴 호출 후 컨텍스트가 잘리거나 이후 turn 품질 저하 | 응답 상한 없는 목록/문서 반환 | 툴별 응답 토큰 분포(p95, max) 계측 | 서버 측 페이지네이션 강제, 대용량은 참조 반환 |
| PII가 모델 제공자 로그·컨텍스트에 노출 | 민감 데이터가 tool result로 모델을 경유 | 트레이스에서 PII 패턴 스캔 | code execution + 토크나이즈, 또는 마스킹 프록시 — [하이브리드 아키텍처](/12-security-korea/hybrid-architecture) |
| 잘 돌던 에이전트가 서드파티 서버 업데이트 후 이상 행동 | 툴 description 변조(rug pull) 또는 tool poisoning | 스키마 해시 변경 감지 + description diff 리뷰 | 스키마 핀 고정, 승인 게이트 — 정본은 [MCP 공급망 보안](/12-security-korea/mcp-supply-chain) |

## 안티패턴

- ❌ REST 엔드포인트를 1:1로 툴화해 CRUD × 리소스만큼 툴 생성 → ✅ 워크플로 단위로 통합하고 변형은 enum 파라미터로 흡수
- ❌ description에 버전·환경·시각 등 동적 문자열 삽입 → ✅ 스키마는 배포 아티팩트로 고정, 변경은 릴리스로만
- ❌ "결과 전체를 주면 모델이 알아서 필터링" → ✅ 서버가 페이지 크기·응답 상한을 강제하고 truncation을 명시
- ❌ 대용량 문서를 tool result 본문으로 반환 → ✅ 참조(URI/경로) 반환 + 부분 읽기 툴 제공
- ❌ 전사 공용 만능 MCP 서버 하나에 모든 팀의 툴을 등록 → ✅ 에이전트 용도별 서브셋 프로파일, 최소 권한
- ❌ code execution을 "토큰 절감 트릭"으로만 보고 샌드박스 없이 도입 → ✅ 샌드박스·리소스 제한·모니터링을 전제 조건으로 취급([Anthropic](https://www.anthropic.com/engineering/code-execution-with-mcp))
- ❌ 커뮤니티의 "99% 절감" 수치를 그대로 기획서에 인용 → ✅ 자기 워크로드의 툴 카탈로그·중간 결과 크기를 계측한 자체 수치 사용

## 계측 (SLI)

MCP 서버를 플랫폼 컴포넌트로 운영한다면 최소 다음을 계측한다.

- **툴 정의 오버헤드**: 세션 첫 요청의 input tokens 중 툴 정의가 차지하는 비율. 이 값이 크면 서브셋 분리 또는 code execution 검토 신호.
- **스키마 안정성**: 서버가 내보내는 툴 스키마의 해시를 배포 버전별로 기록하고, 배포 없이 해시가 바뀌면 알림. 캐시 히트율(`cache_read_input_tokens / input_tokens`)과 함께 대시보드에 놓으면 스키마 churn → 캐시 미스 인과가 바로 보인다.
- **툴별 응답 크기 분포**: p50/p95/max 응답 토큰. p95가 상한 정책을 초과하는 툴은 설계 리뷰 대상.
- **툴 호출 성공률·지연**: MCP 레벨 오류(프로토콜 오류, 타임아웃)와 도메인 오류(권한 거부, not found)를 분리 집계. 도메인 오류는 모델이 복구할 수 있도록 응답에 지침을 담아야 하는데, 그 포맷은 [툴 설계](/01-agent-design/tool-design) 참고.
- **툴 선택 정확도**: 대표 태스크 셋에서 의도한 툴이 호출됐는지 — 측정 하니스는 [툴 과부하](/03-accuracy-eval/tool-overload)와 [평가 하니스](/03-accuracy-eval/eval-harness) 참고.
- (code execution 도입 시) **컨텍스트 우회율**: 실행 환경 안에서만 흐른 데이터량 vs 모델 컨텍스트를 통과한 데이터량. PII 정책 준수의 정량 근거가 된다.

## 체크리스트

배포 전:

- [ ] 툴 스키마가 정적 상수인가? 런타임 조립 문자열(시각, 환경명, 랜덤 값)이 description·스키마에 없는가?
- [ ] JSON 직렬화 키 순서가 고정돼 있는가? 재시작 두 번 후 스키마 해시가 동일한가?
- [ ] 툴 수가 에이전트 용도 대비 절제돼 있는가? 항상 연달아 호출되는 툴을 통합했는가?
- [ ] 모든 목록형 툴에 서버 측 기본/최대 페이지 크기가 있는가? truncation이 응답에 표시되는가?
- [ ] 대용량 결과는 본문 대신 참조로 반환하는가?
- [ ] 오류 응답이 프로토콜 오류와 도메인 오류를 구분하고, 도메인 오류에 복구 지침이 있는가?
- [ ] 민감 데이터가 tool result로 모델을 경유하는 경로를 식별했는가? 경유가 불가한 데이터면 code execution/마스킹 경로를 설계했는가?

운영 중:

- [ ] 스키마 해시 변경이 배포 이벤트와만 상관하는지 모니터링하는가?
- [ ] 툴 정의 오버헤드·캐시 히트율·툴별 응답 크기 분포가 대시보드에 있는가?
- [ ] 서드파티 MCP 서버는 버전을 핀 고정하고 스키마 diff 리뷰를 거치는가? ([MCP 공급망 보안](/12-security-korea/mcp-supply-chain))
- [ ] (code execution 사용 시) 샌드박스 리소스 제한·네트워크 egress 정책·실행 로그가 있는가?

## 참고

- Anthropic, [Code execution with MCP: Building more efficient agents](https://www.anthropic.com/engineering/code-execution-with-mcp) (2025-11) — 툴 정의 선적재·중간 결과 통과 문제와 code execution 패턴, 150,000 → 2,000 토큰(98.7%) 예시, PII 토크나이즈
- Anthropic, [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) — `tools → system → messages` 캐시 계층과 정확 프리픽스 매칭
- Model Context Protocol, [Specification — Tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) — 툴 정의 구조와 `list_changed` 알림
- AWS, [Amazon Bedrock AgentCore Gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html) — 기존 API/Lambda의 MCP화
- 책 내 교차 참조: [툴 설계](/01-agent-design/tool-design) · [툴 과부하](/03-accuracy-eval/tool-overload) · [캐시 미스 근본 원인](/04-caching/cache-miss-root-causes) · [AgentCore Gateway 딥다이브](/10-agentcore/gateway-deep-dive) · [MCP 공급망 보안](/12-security-korea/mcp-supply-chain) · [하이브리드 아키텍처](/12-security-korea/hybrid-architecture)
