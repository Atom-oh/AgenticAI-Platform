---
title: 애플리케이션이 아니라 플랫폼
description: 단일 에이전트 앱과 에이전트를 만드는 메타플랫폼의 차이를 정리한다.
outline: [2, 3]
---

# 애플리케이션이 아니라 플랫폼

::: tip 이 장에서 얻는 것
- "에이전트 하나 배포"와 "에이전트를 만드는 플랫폼 운영"이 왜 다른 종류의 엔지니어링 문제인지 구분할 수 있다.
- 이 책이 전제하는 4-layer 메타플랫폼 구조(빌더 에이전트 → 배포 런타임 → 카탈로그 → 거버넌스)를 설명할 수 있다.
- 언제 단일 에이전트 앱으로 충분하고, 언제 플랫폼 투자가 정당화되는지 판단 기준을 세울 수 있다.
- Platform Engineering(golden path, self-service, platform-as-a-product)의 개념을 AI 워크로드에 적용할 때 무엇이 그대로 옮겨지고 무엇이 새로 생기는지 안다.
:::

## 왜 문제가 되는가

전형적인 "에이전트를 만들어봅시다" 프로젝트는 단일 애플리케이션 관점에서 시작한다. 요구사항 하나, 시스템 프롬프트 하나, 툴 몇 개, Bedrock 모델 하나를 골라 배포하면 끝이다. 이 관점에서 문제는 유한하다 — 프롬프트를 얼마나 잘 쓰는가, 툴 호출이 얼마나 정확한가, 응답 지연이 얼마나 짧은가.

그런데 이 책이 다루는 대상은 그게 아니다. 사용자(주로 비개발자)가 빌더 에이전트와 대화하며 새 에이전트를 정의하고, 그 정의가 AWS Bedrock AgentCore 위에 실제로 배포되고, 배포된 에이전트가 공용 MCP 서버·RAG 코퍼스·Skill 카탈로그를 재사용하고, 플랫폼 엔지니어는 누가 어떤 툴·데이터·모델을 얼마의 비용 한도로 호출할 수 있는지 조직 전체 단위로 통제한다. 이것은 "에이전트 애플리케이션"이 아니라 **에이전트를 만드는 메타플랫폼**이다.

이 전환에서 최소 네 가지가 질적으로 달라진다.

1. **소유권의 방향이 바뀐다.** 단일 앱에서는 팀이 프롬프트와 코드를 소유한다. 플랫폼에서는 팀이 "에이전트를 만드는 도구"를 소유하고, 실제 에이전트 정의(프롬프트·툴 조합·정책)는 최종 사용자가 카탈로그에서 조립한다. 품질과 안전에 대한 책임 소재가 애플리케이션 팀에서 플랫폼 팀으로 이동한다.
2. **테넌시가 기본값이 된다.** 하나의 배포가 아니라, 서로 다른 팀·부서가 생성한 수백~수천 개의 에이전트 인스턴스가 같은 런타임·같은 IAM 경계·같은 모델 쿼터를 공유한다. 격리, 쿼터, 비용 배분이 선택이 아니라 1일차 요구사항이 된다.
3. **재사용 가능한 자산이 1급 시민이 된다.** MCP 서버, RAG 코퍼스, Skill, 프롬프트 템플릿은 한 에이전트의 부속물이 아니라 카탈로그에 등록되고 버전 관리되며 여러 에이전트가 동시에 참조하는 공유 자원이다. 하나를 고치면 그것을 참조하는 모든 에이전트의 동작이 바뀐다.
4. **거버넌스가 배포 시점이 아니라 실행 시점의 문제가 된다.** 단일 앱은 배포 전에 리뷰하면 된다. 플랫폼은 비개발자가 실시간으로 새 조합을 만들어내므로, 어떤 툴 호출이 허용되는지·어떤 데이터에 접근 가능한지·모델 비용이 얼마나 나가는지를 **실행 중에** 결정론적으로 판정하는 레이어가 필요하다.

이 네 가지는 Platform Engineering 커뮤니티가 일반 소프트웨어 딜리버리에서 이미 다뤄온 문제와 형태가 같다 — 팀 인지 부하를 낮추고, self-service golden path를 제공하고, 플랫폼을 제품처럼 운영하는 것([Platform as a Product, Team Topologies](https://teamtopologies.com/videos-slides/what-is-platform-as-a-product-clues-from-team-topologies)). 다만 AI 에이전트 워크로드는 여기에 두 가지를 더 얹는다: (a) 만들어지는 산출물 자체가 비결정적 소프트웨어(LLM 기반 에이전트)이고, (b) 실행 시점의 툴 호출 하나하나가 잠재적으로 실제 세계에 부작용을 낸다(외부 API 호출, 파일 시스템 조작, 브라우저 자동화). 그래서 "플랫폼 엔지니어링을 AI에 적용한다"는 것은 기존 IDP(Internal Developer Platform) 개념을 그대로 재탕하는 것이 아니라, 런타임 거버넌스 레이어를 추가로 설계하는 일이다.

## 핵심 개념

### 메타플랫폼의 4개 레이어

이 책이 전제하는 구조는 대략 다음 네 레이어로 나뉜다. 각 레이어의 세부 설계는 이후 장(`meta-platform-overview`, `platform-engineering-for-ai`, `10-agentcore`, `11-builder-agent`)에서 다룬다. 여기서는 왜 이 네 개가 분리되어야 하는지만 짚는다.

- **빌더 에이전트 레이어**: 사용자와 대화하며 요구사항을 새 에이전트의 시스템 프롬프트·툴 목록·메모리 정책으로 변환한다. 이 자체가 하나의 에이전트다 — "에이전트를 만드는 에이전트".
- **배포/런타임 레이어**: 빌더가 만든 정의를 실제로 실행 가능한 워크로드로 바꾼다. AWS Bedrock AgentCore가 이 레이어를 제공한다.
- **카탈로그 레이어**: Skill, MCP 서버, 프롬프트, RAG 코퍼스를 등록·검색·재사용 가능하게 만든다.
- **거버넌스 레이어**: 누가 무엇을 얼마나 쓸 수 있는지 IAM·정책·쿼터로 통제하고, 실행을 관측한다.

이 네 레이어가 분리되어야 하는 이유는 단순하다: 각각의 변경 빈도와 실패 모드가 다르다. 빌더 에이전트의 프롬프트는 매주 바뀔 수 있지만, 거버넌스 정책은 규정 준수와 직결되어 변경 승인 절차가 훨씬 무겁다. 하나의 코드베이스·하나의 배포 파이프라인에 욱여넣으면 변경 속도가 가장 느린 레이어(거버넌스)에 전체가 맞춰지거나, 반대로 감사 추적이 느슨해진다.

### AWS Bedrock AgentCore: 배포 레이어의 실제 구성

이 책은 배포 레이어를 AWS Bedrock AgentCore로 구체화한다. AgentCore는 "모듈형 서비스들의 묶음"으로, 각 서비스를 독립적으로 또는 조합해서 쓸 수 있다는 것이 핵심 설계 원칙이다.

| 서비스 | 역할 |
|---|---|
| Runtime | 세션 격리가 보장된 서버리스 실행 환경. 콜드 스타트 최적화, 비동기 에이전트를 위한 확장 실행 시간, 멀티모달·멀티에이전트 지원 |
| Gateway | 기존 API·Lambda·MCP 서버를 에이전트가 호출 가능한 도구로 통합 노출 |
| Identity | 기존 IdP(Cognito, Okta, Entra ID, Auth0 등)와 연동되는 에이전트 전용 자격·인증 관리 |
| Memory | 세션 내 단기 메모리와 세션을 넘어 지속되는 장기 메모리, 에이전트 간 메모리 공유 |
| Code Interpreter | 에이전트가 생성한 코드를 실행하는 격리 샌드박스 |
| Browser | 웹 자동화를 위한 관리형 클라우드 브라우저 런타임 |
| Observability | OpenTelemetry 호환 형식으로 에이전트 실행 경로·중간 산출물을 추적 |
| Policy | 자연어 또는 [Cedar](https://www.cedarpolicy.com/en) 정책 언어로 정의한 규칙을 Gateway가 모든 툴 호출 전에 강제하는 결정론적 통제 계층 |
| Evaluations | 프로덕션 트래픽과 회귀 테스트에 대해 응답 품질·안전성·작업 완수·툴 사용을 built-in evaluator로 자동 채점 |
| Registry | 조직 내 에이전트·MCP 서버·툴·Skill을 게시·검토·승인하는 거버넌스 워크플로가 포함된 중앙 카탈로그 |

(출처: [AWS 공식 개요 — Amazon Bedrock AgentCore란](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html))

이 목록에서 이 책의 논지에 특히 중요한 두 서비스가 Policy와 Registry다. Policy는 "모델의 추론 루프 바깥에서" 강제되는 결정론적 한계라는 점이 핵심이다 — 즉 프롬프트로 아무리 잘 지시해도, 실제 차단은 모델이 아니라 Gateway가 한다. Policy와 Evaluations는 2025년 12월 프리뷰로 처음 공개되었고, Policy는 2026년 3월 3일, Evaluations는 2026년 3월 31일 GA로 전환되었다(출처: [AWS What's New — Policy/Evaluations 프리뷰](https://aws.amazon.com/about-aws/whats-new/2025/12/amazon-bedrock-agentcore-policy-evaluations-preview), [AWS Evaluations GA 발표](https://aws.amazon.com/about-aws/whats-new/2026/03/agentcore-evaluations-generally-available)). GA 시점이 최근이라는 것은, 이 레이어가 아직 조직마다 성숙도가 크게 다르고 운영 관행이 계속 바뀌고 있다는 뜻이기도 하다.

Registry는 "내부 개발자 또는 고객에게 승인된 툴·공유 메모리 스토어·거버넌스된 서비스 접근 권한으로 에이전트를 빌드·배포하는 paved path를 제공한다"고 AWS가 공식 문서에서 명시한다(출처: 위 개요 문서의 "Agent Platforms" 사용 사례 설명). 이는 이 책이 세우는 논지 — 메타플랫폼이 곧 golden path 제공 장치라는 것 — 를 AWS가 자신들의 공식 유즈케이스로 인정한 것이라고 볼 수 있다.

### Platform Engineering 개념의 이식: golden path, self-service, platform-as-a-product

일반 소프트웨어 딜리버리에서 이미 정립된 개념 세 가지를 그대로 가져온다.

- **Golden path**: Spotify Engineering이 대중화한 표현으로, "무언가를 만드는 데 있어 의견이 반영되고 지원되는 경로(the opinionated and supported path to build something)"를 뜻한다. 강제된 경로가 아니라 권장되는 경로라는 점이 중요하다(출처: [platformengineering.org — What are golden paths?](https://platformengineering.org/blog/what-are-golden-paths-a-guide-to-streamlining-developer-workflows)). 이 책의 맥락에서 golden path는 "빌더 에이전트가 기본으로 제안하는 조합(모델+툴+메모리 정책)"이다. 사용자가 원하면 벗어날 수 있지만, 벗어나면 거버넌스 검토가 더 무거워지는 식으로 설계해야 한다.
- **Self-service**: Team Topologies는 플랫폼을 "그것을 사용하는 팀의 인지 부하를 낮추고 자율성을 높이면서 최종 고객에게 가치가 흐르는 속도를 가속하는 수단"으로 정의하며, 예외적인 플랫폼일수록 self-service를 갖춰 변경이 온디맨드로 가능하고 요청자가 구현에 실질적 발언권을 가진다고 설명한다(출처: [Team Topologies — What is Platform as a Product?](https://teamtopologies.com/videos-slides/what-is-platform-as-a-product-clues-from-team-topologies)). 이 책에서 self-service는 비개발자가 코드를 한 줄도 쓰지 않고 빌더 에이전트와의 대화만으로 에이전트를 카탈로그에 등록하는 흐름을 뜻한다.
- **Platform as a product**: 같은 자료는 플랫폼을 다른 디지털 제품과 같은 마음가짐으로 개발해야 한다고 강조한다 — 무엇을 만들 수 있는가가 아니라 무엇을 만들어야 하는가로 초점을 옮기는 것. Backstage 같은 개발자 포털도 이 사상 위에서 만들어졌다 — 소프트웨어 카탈로그, 표준화된 템플릿, 문서를 한곳에 모아 인지 부하를 줄이는 것이 목표다(출처: [Backstage.io](https://backstage.io/)).

::: warning 미정착 영역
Backstage류 IDP 포털을 "에이전트/Skill/MCP 카탈로그"에 그대로 재사용할 수 있는가는 아직 업계에서 정착된 답이 없다.

- **재사용 가능하다는 입장**: 카탈로그·메타데이터·검색이라는 핵심 문제는 동일하므로, Backstage의 소프트웨어 카탈로그 플러그인 모델을 확장해 MCP 서버·Skill을 엔티티로 등록하면 된다는 주장이 있다. AgentCore Registry 자체도 "게시-리뷰-승인" 워크플로를 갖춘 카탈로그라는 점에서 유사한 설계 사상을 보인다.
- **별도 설계가 필요하다는 입장**: 에이전트/Skill 카탈로그는 버전 간 동작 차이(같은 프롬프트라도 모델 버전이 바뀌면 출력이 달라짐), 런타임 정책 바인딩(Policy·쿼터), 평가 결과(Evaluations 점수)까지 카탈로그 항목의 메타데이터로 포함해야 하므로, 정적 소프트웨어 카탈로그와는 데이터 모델이 근본적으로 다르다는 반론이 있다.

이 책은 두 입장 중 하나를 확정하지 않고, `11-builder-agent`와 카탈로그 관련 장에서 설계 옵션으로 병기한다.
:::

### 단일 앱과 플랫폼의 경계선

플랫폼 전환이 항상 정당화되지는 않는다. 판단 기준은 대략 다음과 같다.

- 에이전트 정의를 만드는 사람이 **한 팀**으로 고정되어 있고 변경 빈도가 낮다면, 단일 앱 + 표준 CI/CD로 충분하다.
- 에이전트 정의를 만드는 사람이 **여러 팀·비개발자**로 확장되고, 그 수가 계속 늘어난다면 카탈로그·거버넌스 레이어가 필요해진다.
- 툴·데이터 접근 권한이 팀마다 달라야 한다면(멀티테넌시 요구), 단일 앱의 IAM 롤 하나로는 감당이 안 되고 Identity/Policy 레이어가 필요하다.
- 비용을 팀·부서 단위로 배분하고 한도를 걸어야 한다면, 단일 앱의 계정 단위 예산 알람으로는 불충분하고 세밀한 사용량 계측이 필요하다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 에이전트 정의를 만드는 팀이 1개, 변경 빈도가 낮음 | 단일 에이전트 애플리케이션 + 표준 CI/CD | 플랫폼 레이어(카탈로그·Policy·Registry) 구축 비용을 아낄 수 있음 | 조직이 커지면 재구축 비용 발생 |
| 여러 팀/비개발자가 에이전트를 계속 새로 만듦 | 빌더 에이전트 + AgentCore 배포 + 카탈로그 | self-service로 요청 대기열을 없애고 재사용률을 높임 | 초기 golden path 설계·거버넌스 정책 수립에 투자 필요 |
| 툴 호출 안전성을 프롬프트 지시만으로 보장하려 함 | AgentCore Policy(Cedar 기반)로 모델 추론 루프 바깥에서 강제 | 프롬프트 인젝션·모델 오작동과 무관하게 결정론적으로 차단 가능 | 정책 작성·유지보수라는 별도 운영 부담 발생 |
| 공용 MCP 서버·RAG 코퍼스를 여러 에이전트가 재사용 | Registry에 등록하고 버전 관리 | 중복 구현 방지, 한 곳을 고치면 전체에 반영 | 한 서버 장애/변경이 다수 에이전트에 동시 영향 |
| 비용을 팀 단위로 통제해야 함 | 모델·툴 호출 단위 사용량 계측 + 쿼터 | 예산 초과를 사전에 차단 가능 | 계측 파이프라인 구축·운영 비용 발생 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 비개발자가 만든 에이전트가 프로덕션 데이터에 무제한 접근 | 거버넌스를 프롬프트 지시로만 구현하고 실행 시점 강제 계층이 없음 | Gateway 앞단에 Policy가 붙어 있는지, 어떤 정책도 없이 툴이 직접 노출되는지 확인 | AgentCore Policy로 툴 호출 전 결정론적 검사를 추가 |
| 같은 목적의 MCP 서버가 팀마다 중복 구현됨 | 카탈로그/Registry가 없어 재사용 경로가 발견되지 않음 | 조직 내 MCP 서버 수와 기능 중복도를 조사 | Registry에 등록·검색 가능하게 하고 신규 생성 전 검색을 golden path에 포함 |
| 특정 팀의 에이전트가 전체 Bedrock 모델 쿼터를 소진 | 팀 단위 쿼터·비용 배분이 없고 계정 단위로만 관리 | CloudWatch에서 팀별 InvokeModel 호출량 분포 확인 | Identity 기반으로 팀별 쿼터·predictable 비용 한도 설정 |
| 배포된 에이전트의 실제 동작이 빌더 에이전트가 보여준 설명과 다름 | 빌더가 생성한 정의와 런타임 배포본 사이에 버전 드리프트 | Registry의 정의 버전과 Runtime에 실제 배포된 버전을 비교 | 정의-배포 파이프라인에 버전 고정과 변경 감지 추가 |
| 에이전트 품질이 조용히 저하되는데 아무도 모름 | 배포 후 지속적 평가(Evaluations) 없이 배포 시점 리뷰에만 의존 | Observability에서 최근 트레이스에 대한 Evaluations 점수 추이 확인 | 프로덕션 트래픽에 대한 상시 Evaluations 파이프라인 구성 |

## 안티패턴

- ❌ "에이전트 하나 잘 만들면 나머지는 복붙" → ✅ 두 번째 에이전트를 만드는 순간부터 카탈로그·거버넌스 설계를 시작한다. 복붙은 곧 버전 드리프트와 중복 취약점으로 돌아온다.
- ❌ 프롬프트에 "이 데이터는 절대 접근하지 마" 라고 적어두고 그것을 보안 경계로 취급 → ✅ 실행 시점 강제(Policy/IAM)로 옮기고, 프롬프트 지시는 사용자 경험 보조 수단으로만 취급한다.
- ❌ 플랫폼 팀이 모든 에이전트 정의를 직접 리뷰·승인하는 중앙 게이트를 만듦 → ✅ Registry에 자동화된 승인 기준(정책 검사, 평가 점수 임계값)을 넣어 self-service를 유지하면서 안전선을 확보한다.
- ❌ 배포 레이어(AgentCore)만 갖추고 카탈로그·Registry 없이 "필요하면 다시 물어보라"는 식으로 운영 → ✅ 재사용 가능한 자산을 처음부터 등록·검색 가능하게 만들어 self-service golden path를 완성한다.

## 계측 (SLI)

플랫폼 전환이 실제로 효과가 있는지 확인하려면 애플리케이션 지표(응답 지연, 정확도)만으로는 부족하다. 플랫폼 자체의 건강도를 재는 지표가 필요하다.

- **golden path 채택률**: 신규 에이전트 중 권장 조합(모델+승인된 툴셋)을 그대로 사용한 비율. 낮으면 golden path가 실제 요구를 못 따라가고 있다는 신호다.
- **재사용률**: 신규 에이전트가 기존 카탈로그 자산(MCP 서버, Skill, RAG 코퍼스)을 참조한 비율 대비 처음부터 새로 만든 비율.
- **거버넌스 우회 시도 탐지율**: Policy가 차단한 툴 호출 수와, 그중 실제 악의적/오작동으로 확인된 비율.
- **평가 커버리지**: 프로덕션에 배포된 에이전트 중 Evaluations 파이프라인이 상시 붙어 있는 비율.
- **팀당 비용 예측 가능성**: 팀별 모델·툴 호출 비용이 사전 설정한 예산 한도 내에서 편차 없이 유지되는지.

이 지표들의 구체적 수집 방법과 대시보드 설계는 `08-scaling-cost`, `10-agentcore` 장에서 다룬다.

## 체크리스트

- [ ] 우리가 만드는 것이 "고정된 한 팀이 소유하는 에이전트 앱"인지, "여러 팀/비개발자가 계속 새 에이전트를 만드는 플랫폼"인지 명시적으로 정의했는가.
- [ ] 툴 호출 안전성이 프롬프트 지시가 아니라 실행 시점 강제 계층(Policy/Gateway)에 의존하는가.
- [ ] 공용 자산(MCP 서버, Skill, RAG 코퍼스)이 카탈로그/Registry에 등록되어 검색·재사용 가능한가.
- [ ] 팀·부서 단위로 모델/툴 호출 비용을 계측하고 쿼터를 걸 수 있는가.
- [ ] 배포 후 지속적 평가(Evaluations)가 붙어 있어 품질 저하를 조용히 방치하지 않는가.
- [ ] golden path에서 벗어난 조합을 만들 때 거버넌스 검토가 자동으로 더 무거워지는가.

## 참고

- [Amazon Bedrock AgentCore란 무엇인가 — AWS 공식 개요](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html)
- [Amazon Bedrock AgentCore Policy, Evaluations 등 프리뷰 발표 — AWS What's New (2025-12)](https://aws.amazon.com/about-aws/whats-new/2025/12/amazon-bedrock-agentcore-policy-evaluations-preview)
- [Amazon Bedrock AgentCore Evaluations GA 발표 — AWS What's New (2026-03)](https://aws.amazon.com/about-aws/whats-new/2026/03/agentcore-evaluations-generally-available)
- [Cedar 정책 언어](https://www.cedarpolicy.com/en)
- [What are golden paths? — platformengineering.org](https://platformengineering.org/blog/what-are-golden-paths-a-guide-to-streamlining-developer-workflows)
- [What is Platform as a Product? Clues from Team Topologies](https://teamtopologies.com/videos-slides/what-is-platform-as-a-product-clues-from-team-topologies)
- [Backstage.io — Software Catalog and Developer Platform](https://backstage.io/)
