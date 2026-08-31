---
title: AI를 위한 플랫폼 엔지니어링
description: IDP와 골든 패스 사고를 Agentic AI 플랫폼에 적용한다.
outline: [2, 3]
---

# AI를 위한 플랫폼 엔지니어링

::: tip 이 장에서 얻는 것
- IDP(Internal Developer Platform)·golden path·platform-as-a-product의 표준 정의와 출처
- 전통적 IDP의 golden path(배포 파이프라인)와 Agentic AI 플랫폼의 golden path(승인된 블루프린트를 통한 셀프서비스)가 왜 다른지
- 무엇을 셀프서비스로 열고 무엇을 플랫폼 엔지니어 리뷰로 게이트할지 정하는 결정 표
- 비개발자(도메인 전문가, 다른 팀)를 내부 고객으로 다루는 "플랫폼을 상품처럼" 운영 모델
- 4단계 실행 로드맵 개요와 상세 로드맵 문서로의 연결
:::

## 왜 문제가 되는가

플랫폼 엔지니어링 조직은 이미 "배포와 인프라 프로비저닝을 셀프서비스로 만든다"는 golden path 사고를 갖고 있다. 문제는 Agentic AI 플랫폼을 만들 때 이 사고를 기계적으로 재사용하면 두 가지 방향에서 틀린다는 점이다.

첫째, Agentic AI 플랫폼의 "배포 단위"는 컨테이너 이미지나 인프라 리소스가 아니라 에이전트가 호출할 수 있는 **능력의 조합** — Skill, MCP 서버, 프롬프트/블루프린트, 모델 접근권, 데이터 소스 — 이다. 이 조합은 전통적인 IaC 리소스보다 실행 시점 행동의 자유도가 훨씬 크다. 동일한 MCP 서버라도 어떤 프롬프트·어떤 권한 범위와 조합되느냐에 따라 완전히 다른 위험 프로파일을 갖는다.

둘째, Agentic AI 플랫폼의 내부 고객은 소프트웨어 엔지니어만이 아니다. 도메인 전문가, 비개발자 현업 팀이 "이 Skill을 조합해서 내 업무를 자동화하고 싶다"고 요청하는 상황이 정상 케이스가 된다. 전통적 IDP는 "개발자의 인지 부하를 낮춘다"를 목표로 설계되지만([platformengineering.org의 IDP 정의](https://platformengineering.org/blog/what-are-golden-paths-a-guide-to-streamlining-developer-workflows)), Agentic AI 플랫폼은 "개발자가 아닌 사용자도 안전하게 셀프서비스할 수 있게 한다"는 더 넓은 목표를 추가로 떠안는다.

이 간극을 무시하면 두 가지 실패로 귀결된다. 셀프서비스 범위를 너무 넓게 열면(신규 MCP 서버를 누구나 등록·연결할 수 있게 하면) 공급망·권한 확장 리스크가 통제 불가능해진다(Part 12 [MCP 공급망](/12-security-korea/mcp-supply-chain) 참고). 반대로 모든 것을 리뷰 게이트에 태우면 플랫폼이 병목이 되어 그림자 IT — 팀들이 플랫폼 밖에서 자체적으로 LLM API를 호출하는 스크립트를 만드는 것 — 를 유발한다. 이 장은 그 사이에서 무엇을 셀프서비스로 열고 무엇을 게이트할지 정하는 기준을 다룬다.

## 핵심 개념

### IDP, golden path의 표준 정의

- **Internal Developer Platform(IDP)**: 개발자가 코드를 만들고 배포하는 데 필요한 셀프서비스 도구·기술의 표준화된 집합. 인프라와 도구를 하나의 플랫폼으로 통합해 개발자의 인지 부하를 낮추면서도 필요한 컨텍스트는 유지한다 — [platformengineering.org](https://platformengineering.org/blog/what-are-golden-paths-a-guide-to-streamlining-developer-workflows).
- **Golden path**: IDP를 통해 제공되는, 사전에 구성된 end-to-end 워크플로. Spotify Engineering이 대중화한 표현으로 "무언가를 만들기 위한 의견이 반영되고 지원되는 길(the opinionated and supported path to build something)"이며, 강제된 경로가 아니라 플랫폼 팀이 전적으로 지원·유지보수하는 **추천 경로**다 — [같은 출처](https://platformengineering.org/blog/what-are-golden-paths-a-guide-to-streamlining-developer-workflows).
- **Platform as a product**: CNCF TAG App Delivery Platforms Working Group의 백서는 클라우드 네이티브 플랫폼을 "플랫폼 사용자의 필요에 따라 정의되고 제시된 역량의 통합 집합(an integrated collection of capabilities defined and presented according to the needs of the platform's users)"으로 정의하고, "플랫폼은 사용자의 요구를 충족하기 위해 존재하며, 다른 소프트웨어 제품과 마찬가지로 그 요구에 기반해 설계·발전해야 한다"고 명시한다 — [CNCF Platforms White Paper](https://tag-app-delivery.cncf.io/whitepapers/platforms/).
- **Thinnest Viable Platform(TVP)**: Team Topologies(Skelton & Pais)가 제안한 개념으로, 플랫폼 팀이 다른 팀의 딜리버리를 가속하기 위해 필요한 최소한의 API·문서·도구 집합. MVP(minimum viable product)에 대응하는 개념이며, 위키 한 페이지만으로도 TVP가 될 수 있다고 설명한다 — [Team Topologies: What is a Thinnest Viable Platform](https://teamtopologies.com/key-concepts-content/what-is-a-thinnest-viable-platform-tvp).

이 네 정의는 서로 다른 출처지만 하나의 공통 축으로 모인다: 플랫폼은 "사용자가 필요로 하는 역량을 표준화된, 지원되는 방식으로 제공"하고, golden path는 그 표준화의 실행 형태이며, TVP는 그 표준화의 최소 규모를 규정한다.

### 전통적 golden path vs. Agentic AI golden path

전통적 IDP의 golden path는 대개 "코드 → 빌드 → 배포 → 관측"이라는 CI/CD 파이프라인 중심의 결정론적 워크플로다. 입력(코드)과 출력(배포된 서비스)이 명확하고, 파이프라인 각 단계는 정적으로 검증 가능하다.

Agentic AI 플랫폼의 golden path는 구조가 다르다. 에이전트의 런타임 행동은 비결정적이고, "무엇을 만들었는가"보다 "무엇에 접근할 수 있고 무엇을 할 수 있는가"가 리스크의 핵심이다. 따라서 이 책에서 다루는 Agentic AI golden path는 다음으로 재정의된다.

> **golden path = 승인된 블루프린트/템플릿을 통한 셀프서비스, 그 밖의 모든 것은 리뷰를 경유한다.**

구체적으로:

- **승인된 블루프린트 내부**: 사전 승인된 Skill 조합, 검증된 MCP 서버 목록, 표준 프롬프트 템플릿, 권한이 미리 스코프된 데이터 접근 범위 안에서는 비개발자도 셀프서비스로 에이전트를 조립·배포할 수 있다.
- **블루프린트 경계 바깥**: 신규 MCP 서버 등록, 새로운 모델/데이터 소스 접근, 권한 범위 확장은 플랫폼 엔지니어링 리뷰를 거친다.

이 구분은 추상적 원칙이 아니라 이미 실제 제품에 구현된 패턴이다. Claude Code의 관리형 MCP 접근 제어는 정확히 이 두 축을 제공한다 — `managed-mcp.json`으로 고정된 서버 집합만 허용하는 "Fixed deployment", 또는 `allowedMcpServers` + `allowManagedMcpServersOnly: true`로 "사용자가 승인된 카탈로그 중에서 골라 쓰되 카탈로그 밖은 차단"하는 "Approved catalog" 패턴이다 — [Control MCP server access for your organization](https://code.claude.com/docs/en/managed-mcp). 이 문서는 "Claude Code에는 사용자가 브라우징해서 설치할 수 있는 내장 MCP 레지스트리가 없다"는 점도 명시하므로, 승인 카탈로그를 사내 위키나 관리형 플러그인 마켓플레이스로 별도 배포해야 한다는 실무 시사점이 있다.

같은 두 축의 구도는 MCP 생태계 차원에서도 나타난다. Anthropic의 공식 MCP Registry는 네임스페이스를 검증하고 메타데이터를 표준화하는 "루트 권위(root authority)" 역할을 하지만, 어떤 서버를 조직이 실제로 써도 되는지를 규율하지는 않는다. 조직은 이 공개 피드를 가져와 자체 allow/deny 목록을 적용하고 내부 카탈로그를 운영해야 한다 — [Digital Thought Disruption, MCP Registry in 2026](https://digitalthoughtdisruption.com/2026/07/20/mcp-registry-discover-verify-safely-connect-servers/).

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요

### 플랫폼을 상품처럼(as-a-product) 다룬다는 것

CNCF 정의를 Agentic AI 맥락에 적용하면, "플랫폼의 사용자"에 비개발자·현업 도메인 전문가가 포함된다는 것이 가장 큰 차이다. 이는 다음을 요구한다.

- **내부 고객 세그먼트별 요구사항 수집**: 개발자는 "새 MCP 서버를 얼마나 빨리 등록할 수 있는가"를 신경 쓰지만, 현업 사용자는 "승인된 블루프린트 안에서 내가 원하는 조합을 찾을 수 있는가"를 신경 쓴다. CNCF TAG App Delivery는 이런 세그먼트를 "퍼소나(persona)"로 명시적으로 다루도록 권고한다 — [CNCF, Platform as a Product: Understanding the Personas](https://tag-app-delivery.cncf.io/blog/paap-personas/).
- **로드맵과 SLA를 상품처럼 공개**: 어떤 Skill/MCP 서버가 카탈로그에 있고, 새 항목이 리뷰를 통과하는 데 걸리는 시간(리드타임)을 공개적으로 관리한다. 이는 Part 11 [카탈로그와 레지스트리](/11-builder-agent/catalog-registry)에서 다루는 카탈로그 설계, [에이전트 CI/CD](/11-builder-agent/agent-cicd)에서 다루는 검증·배포 파이프라인과 직결된다.
- **TVP 원칙 적용**: 처음부터 모든 셀프서비스 기능을 구현하려 하지 않는다. Team Topologies의 TVP 개념대로, "측정 가능하게 딜리버리를 가속하는 최소 집합"부터 시작한다 — [Team Topologies TVP](https://teamtopologies.com/key-concepts-content/what-is-a-thinnest-viable-platform-tvp).

::: warning 미정착 영역
"AI 에이전트 플랫폼의 golden path"라는 용어 자체는 업계에 아직 표준 정의가 없다. 이 장의 정의("승인된 블루프린트 셀프서비스 + 나머지는 리뷰")는 전통적 golden path 정의를 저자가 Agentic AI 워크로드에 맞춰 재해석한 것이며, CNCF Platforms WG나 platformengineering.org가 이 형태로 명문화한 바는 아직 확인되지 않았다. 조직마다 이 경계를 다르게 그릴 수 있다.
:::

## 결정 표

무엇을 비개발자에게 셀프서비스로 열어줄 것인가, 무엇을 플랫폼 엔지니어 리뷰로 게이트할 것인가.

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 이미 승인된 MCP 서버 목록 중에서 조합 선택 | 셀프서비스 허용 | 서버 자체는 이미 리뷰를 통과했으므로 조합 리스크만 남음; Claude Code의 `allowedMcpServers` "Approved catalog" 패턴과 동일 구조 — [출처](https://code.claude.com/docs/en/managed-mcp) | 조합 폭발(combinatorial risk)은 여전히 남으므로 권한 최소화(Part 9 [per-tool OBO](/09-authorization/per-tool-obo))가 별도로 필요 |
| 표준 프롬프트/블루프린트 템플릿을 파라미터만 바꿔 재사용 | 셀프서비스 허용 | 템플릿 설계 시점에 이미 보안·품질 리뷰가 끝났음 | 파라미터 자체가 인젝션 벡터가 될 수 있음(Part 12 [prompt-injection](/12-security-korea/prompt-injection)) |
| 신규 MCP 서버를 카탈로그에 등록 | 리뷰 필수 | 공급망 신뢰 검증(코드 서명, 유지보수 상태, 권한 요구사항)이 필요 — 공식 MCP Registry는 네임스페이스만 검증하고 보안 감사는 하지 않음 — [출처](https://code.claude.com/docs/en/managed-mcp) | 리드타임이 늘어나 그림자 IT 유인이 커짐; 리드타임 SLA를 공개해 상쇄 |
| 신규 모델(예: 더 큰 컨텍스트, 다른 벤더) 접근 요청 | 리뷰 필수 | 비용·데이터 거주지·안전성 프로파일이 모델마다 다름(Part 10 AgentCore 관련 챕터 참고) | 실험 속도 저하; 샌드박스 테넌트에서는 완화 가능 |
| 기존 에이전트의 권한 범위 확장(새 리소스 접근, 쓰기 권한 추가) | 리뷰 필수 | 권한 확장은 confused deputy·과다 권한 위험을 직접 키움(Part 9 [confused-deputy](/09-authorization/confused-deputy)) | — |
| Skill을 조합해 새 워크플로 구성(신규 외부 접근 없음) | 셀프서비스 허용, 사후 감사 | 실행 시점 행동은 여전히 감사 로그로 확인 가능(Part 9 [hitl-audit](/09-authorization/hitl-audit)) | 감사 지연으로 인한 사후 발견 리스크 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 현업 팀이 플랫폼 밖에서 자체 LLM API 키로 스크립트를 운영 | golden path가 없거나 리드타임이 과도해 그림자 IT 유인이 큼 | 조직 내 LLM API 키 발급 현황과 플랫폼 카탈로그 사용률 비교 | 승인된 블루프린트 범위를 넓히고 카탈로그 등록 리드타임 SLA를 공개 |
| 신규 MCP 서버가 리뷰 없이 프로덕션에 연결됨 | `allowedMcpServers`/`allowManagedMcpServersOnly` 같은 조직 차원 강제 설정이 없고 사용자 설정이 병합되도록 방치됨 | `allowManagedMcpServersOnly`가 관리형 설정 소스에서 설정돼 있는지 확인 — [출처](https://code.claude.com/docs/en/managed-mcp) | 관리형 설정 소스에서만 allowlist를 강제하고, denylist는 항상 모든 소스에서 병합되도록 유지 |
| 승인된 서버 이름(`serverName`)만으로 허용 목록을 운영했더니 동일 이름의 다른 서버가 통과됨 | `serverName`은 사용자가 자유롭게 붙이는 라벨일 뿐 서버 자체를 식별하지 않음 | allowlist 엔트리가 `serverUrl`/`serverCommand` 기반인지 점검 | `serverName` 대신 `serverUrl` 또는 `serverCommand`로 강제 |
| 플랫폼 팀이 모든 조합을 사전 승인하려다 카탈로그 등록 자체가 병목이 됨 | TVP 원칙 없이 처음부터 광범위한 셀프서비스를 설계 | 카탈로그 등록 대기열 길이·평균 리드타임 추적 | Thinnest Viable Platform부터 시작해 사용률 기반으로 확장 — [출처](https://teamtopologies.com/key-concepts-content/what-is-a-thinnest-viable-platform-tvp) |

## 안티패턴

- ❌ "개발자용 IDP golden path를 그대로 복사해서 비개발자에게 노출" → ✅ 비개발자 퍼소나의 요구(승인된 블루프린트 탐색, 결과 이해)를 별도로 수집해 golden path를 다시 설계
- ❌ "모든 MCP 서버 등록을 리뷰 큐에 태워 병목을 만듦" → ✅ 승인된 카탈로그 안에서는 셀프서비스를 허용하고, 신규 등록·권한 확장만 리뷰로 게이트
- ❌ "`serverName` 매칭만으로 allowlist를 구성" → ✅ `serverUrl`/`serverCommand` 기반 매칭을 강제하고 `serverName`은 denylist 보조용으로만 사용
- ❌ "플랫폼을 한 번 만들고 끝나는 프로젝트로 취급" → ✅ CNCF platform-as-a-product 정의대로 사용자 요구에 따라 지속적으로 진화시키는 상품으로 운영

## 계측 (SLI)

Agentic AI 플랫폼의 golden path 건강도를 측정하려면 최소한 다음을 추적해야 한다.

- **카탈로그 커버리지**: 실제 프로덕션 에이전트 중 승인된 블루프린트/카탈로그 항목만으로 구성된 비율
- **등록 리드타임**: 신규 MCP 서버/Skill이 요청부터 카탈로그 등록까지 걸리는 시간(중앙값, p90)
- **그림자 사용 비율**: 플랫폼 카탈로그를 경유하지 않은 LLM API 직접 호출 비율(가능한 범위에서 API 게이트웨이 로그로 추정)
- **정책 이탈 탐지 지연**: allowlist/denylist 위반 시도가 발생한 시점부터 감지까지 걸리는 시간

> ⚠️ 위 지표들에 대한 업계 표준 목표치(구체적 숫자)는 확인하지 못했다. 조직별 베이스라인을 먼저 측정하고 상대적 개선을 추적하는 것을 권장한다.

## 체크리스트

- [ ] IDP/golden path/platform-as-a-product의 정의를 조직 문서에 명시하고 출처를 남겼는가
- [ ] "승인된 블루프린트 셀프서비스" 범위와 "리뷰 게이트" 범위를 결정 표 형태로 문서화했는가
- [ ] MCP 서버 allowlist가 `serverUrl`/`serverCommand` 기반으로 구성되어 있고 관리형 설정 소스에서만 강제되는가
- [ ] 비개발자 퍼소나의 요구사항을 별도로 수집했는가
- [ ] 카탈로그 등록 리드타임을 측정하고 공개하는가
- [ ] Thinnest Viable Platform 원칙에 따라 초기 범위를 최소화했는가
- [ ] 4단계 로드맵([6대 통증점](/00-intro/six-pain-points))과 이 장의 결정 표가 서로 연결돼 있는가

## 참고

이 장에서 소개한 4단계 실행 로드맵(1단계 0~4주 뼈대 구축, 2단계 4~10주 통증점 선제 차단, 3단계 10주+ 게이트화, 4단계 한국 금융 하이브리드 아키텍처)의 전체 표와 세부 내용은 [6대 통증점](/00-intro/six-pain-points)에서 다룬다. 셀프서비스 카탈로그의 구체적 설계는 [카탈로그와 레지스트리](/11-builder-agent/catalog-registry), 검증·배포 파이프라인은 [에이전트 CI/CD](/11-builder-agent/agent-cicd), MCP 공급망 리스크는 [MCP 공급망](/12-security-korea/mcp-supply-chain), 권한 스코핑은 Part 9([confused-deputy](/09-authorization/confused-deputy), [per-tool-obo](/09-authorization/per-tool-obo), [hitl-audit](/09-authorization/hitl-audit))을 참고한다.

- [platformengineering.org — What are golden paths?](https://platformengineering.org/blog/what-are-golden-paths-a-guide-to-streamlining-developer-workflows)
- [CNCF TAG App Delivery — Platforms White Paper](https://tag-app-delivery.cncf.io/whitepapers/platforms/)
- [CNCF TAG App Delivery — Platform as a Product: Understanding the Personas](https://tag-app-delivery.cncf.io/blog/paap-personas/)
- [Team Topologies — What is a Thinnest Viable Platform (TVP)?](https://teamtopologies.com/key-concepts-content/what-is-a-thinnest-viable-platform-tvp)
- [Claude Code Docs — Control MCP server access for your organization](https://code.claude.com/docs/en/managed-mcp)
- [Digital Thought Disruption — MCP Registry in 2026](https://digitalthoughtdisruption.com/2026/07/20/mcp-registry-discover-verify-safely-connect-servers/) (⚠️ 비공식 출처)
