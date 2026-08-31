---
title: 6대 통증점
description: 이 책이 다루는 6가지 통증점과 해당 챕터, 그리고 4단계 실행 로드맵을 매핑한다.
outline: [2, 3]
---

# 6대 통증점

::: tip 이 장에서 얻는 것
- 이 책 전체의 목차를 "증상 → 근본 원인 → 해당 챕터"로 역방향 탐색하는 방법
- Agentic AI 플랫폼을 처음 구축할 때의 4단계 실행 로드맵
- 각 단계에서 다음 단계로 넘어가야 하는 정량적 전환 임계치
:::

## 왜 문제가 되는가

플랫폼 엔지니어가 이 책을 펼치는 시점은 대개 "개념을 배우고 싶어서"가 아니라 "뭔가 터져서"다. 응답이 느려졌거나, 에이전트가 헛소리를 하거나, Bedrock 비용이 예상보다 3배 나왔거나, 긴 대화 후반부에서 에이전트가 앞에서 한 말을 잊거나, 트래픽이 늘자 쿼터에 걸리거나, 특정 사용자에게 노출되면 안 되는 툴이 노출됐다는 보고를 받는 식이다.

이 장은 그런 증상을 이 책의 목차에 매핑하는 진입점이다. 각 통증점은 특정 Part가 전담한다 — 개념을 순서대로 읽지 않고, 지금 터진 문제부터 찾아 들어갈 수 있게 구성했다.

## 핵심 개념

### 6대 통증점 매핑 표

| # | 통증점 | 대표 증상 | 근본 원인(요약) | 담당 Part |
|---|---|---|---|---|
| 1 | 느려짐(지연) | 사용자가 응답을 기다리다 이탈, 툴 호출마다 수백 ms~수 초 누적 | 순차적 툴 라운드트립, 모델 크기와 작업 난이도 불일치, 스트리밍 미적용 | [Part 2 — 성능과 지연](/02-performance/) |
| 2 | 정확도 떨어짐 | 툴을 잘못 고름, 인자를 환각함, 멀티스텝 작업이 중간에 어긋남 | 툴 과부하(선택지 과다), 검색 실패, 단계 수 증가에 따른 오류 복합(error compounding) | [Part 3 — 정확도와 평가](/03-accuracy-eval/) |
| 3 | 캐시 히트가 안 됨(비용 폭증) | 같은 세션인데도 매 요청이 full price로 과금됨 | 프롬프트 앞부분에 동적 요소(타임스탬프, per-user 데이터) 삽입, 툴 목록 재정렬, JSON 키 순서 비결정성 | [Part 4 — 프롬프트 캐싱과 KV 캐시](/04-caching/) |
| 4 | 컨텍스트가 유지 안 됨 | 긴 대화 후반부에서 앞의 지시를 잊음, 긴 문서를 넣었더니 오히려 답이 나빠짐 | context rot, 컨텍스트 윈도우의 광고값과 실효 길이의 격차, compaction 부재 | [Part 5 — 컨텍스트 엔지니어링](/05-context/) |
| 5 | 스케일링·비용 통제 불가 | TPM 스로틀링, 콜드스타트 지연, 세션 유휴 비용 누적 | CPU/GPU% 기반 오토스케일이 LLM 워크로드에 안 맞음, 온디맨드 쿼터 한계, 세션=microVM 재사용 미설계 | [Part 8 — 스케일링과 비용](/08-scaling-cost/) |
| 6 | 권한을 세밀하게 못 나눔 | 모든 사용자가 모든 툴을 호출 가능, confused deputy, 환각된 인자로 고액 결제가 실행됨 | 서비스 신원으로 실행(감사 추적 붕괴), 툴 레벨 인가 부재, RAG 문서 엔타이틀먼트 미적용 | [Part 9 — 세밀 권한 제어](/09-authorization/) |

각 통증점은 서로 독립적이지 않다. 예를 들어 캐시 미스(3번)는 지연(1번)과 비용(5번)을 동시에 악화시키고, 컨텍스트 관리 실패(4번)는 정확도(2번) 저하로 이어진다. 하지만 계측 포인트와 해결 레버가 다르므로 별도 Part로 분리했다.

### 이 책의 나머지 구조

6대 통증점을 직접 다루지 않는 Part도 있다 — Part 1(에이전트 설계 기초), Part 6(벡터 검색), Part 7(메모리), Part 10(AgentCore 심화), Part 11(빌더 에이전트), Part 12(보안·한국 규제)는 통증점의 **원인이 되는 설계 결정**이나, 통증점을 다루기 위한 **인프라 심화 지식**을 제공한다. 예를 들어 Part 6의 청킹 전략은 Part 3(정확도)의 검색 실패율에 직결되고, Part 10의 AgentCore Runtime 계약은 Part 8(스케일링)의 콜드스타트 문제와 직결된다.

## 결정 표

| 지금 겪고 있는 상황 | 먼저 읽을 곳 |
|---|---|
| 데모에서는 괜찮았는데 프로덕션에서 느리다 | [Part 2 지연의 해부](/02-performance/latency-anatomy) |
| 에이전트가 존재하지 않는 함수를 호출하려 한다 | [Part 3 검색과 환각된 인자](/03-accuracy-eval/retrieval-and-hallucinated-args) |
| Bedrock 청구서가 예상보다 훨씬 크다 | [Part 4 캐시 지표와 경제성](/04-caching/cache-metrics-economics) |
| 긴 세션에서 에이전트가 이상해진다 | [Part 5 Context rot](/05-context/context-rot) |
| ThrottlingException이 자주 뜬다 | [Part 8 동시성 쿼터와 스로틀링](/08-scaling-cost/concurrency-quotas-throttling) |
| 누가 무엇을 호출할 수 있는지 통제가 안 된다 | [Part 9 Cedar와 Verified Permissions](/09-authorization/cedar-verified-permissions) |
| 지금 막 플랫폼을 처음 구축한다 | 아래 4단계 로드맵부터 |

## 실행 로드맵

Agentic AI 플랫폼을 처음부터 구축한다면, 아래 4단계를 순서대로 밟는다. 각 단계는 이전 단계의 산출물을 전제로 한다.

### 1단계 (0~4주) — 뼈대

AgentCore Runtime 계약(포트·헬스체크·`/invocations` 프로토콜) 위에 얇은 프레임워크-중립 하니스를 올린다. 프롬프트는 처음부터 **static → dynamic** 순서로 설계하고, 캐시 브레이크포인트를 정적 프리픽스 끝에 둔다([Part 4](/04-caching/cache-miss-root-causes)). 관측(ADOT `gen_ai.*` 스팬 + CloudWatch Transaction Search)은 day 1에 켠다 — 나중에 켜면 초기 구간의 지연·비용 데이터가 영구히 사라진다.

### 2단계 (4~10주) — 통증점 선제 차단

툴이 **50개를 넘기기 전에** AgentCore Gateway의 semantic tool search와 code-execution 패턴을 도입한다([Part 3 툴 과부하](/03-accuracy-eval/tool-overload)). 권한은 **Cedar(툴 레벨) + OBO(신원 전파) + Knowledge Base 메타데이터 필터(데이터 레벨)의 3중 방어를 MVP부터** 설계하고, 처음에는 `LOG_ONLY`로 시작해 `ENFORCE`로 전환한다([Part 9](/09-authorization/cedar-verified-permissions)). 메모리는 AgentCore 빌트인 전략 + 네임스페이스 기반 테넌트 격리를 적용한다([Part 7](/07-memory/agentcore-memory-alternatives)).

### 3단계 (10주+) — 게이트화

OTel 스팬에 더해 AgentCore Evaluations(trajectory/goal 평가)와 Bedrock RAG evaluation을 **CI 배포 게이트**로 편입한다. dev → staging → prod 프로모션에 evals-as-gate를 적용한다([Part 11 에이전트 CI/CD](/11-builder-agent/agent-cicd)). 멀티에이전트 패턴은 **비용 서킷 브레이커 + per-run 예산 캡**과 함께만 도입한다([Part 1 단일 vs 멀티 에이전트](/01-agent-design/single-vs-multi-agent)).

### 4단계 — 한국 금융 하이브리드

PII를 토큰화한 뒤 Bedrock으로 라우팅하고, 연구개발망 예외·SaaS 이용 관련 시행세칙을 활용하며, 온프렘이 필요한 워크로드는 EKS Hybrid Nodes + PrivateLink로, 크로스리전 추론은 지리(geo) 프로파일로 고정한다([Part 12](/12-security-korea/korea-fsc-regulation)). 이 영역은 **법률자문이 아니며** 규제가 분기 단위로 바뀌므로 정기적으로 재확인해야 한다.

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 캐시 히트율이 낮은데 원인을 못 찾음 | 통증점 3번(캐싱)과 4번(컨텍스트)을 혼동 — 캐시 미스와 context rot은 다른 문제 | 캐시 read/write 토큰 비율과 입력 토큰 절대량을 각각 확인 | [Part 4](/04-caching/cache-miss-root-causes)와 [Part 5](/05-context/context-rot)를 구분해서 진단 |
| 권한 문제인데 정확도 문제로 오인 | 툴이 노출되면 안 되는데 노출되어 모델이 "정상적으로" 잘못된 툴을 씀 | 해당 툴이 `list_tools` 응답에 노출되는지 확인 | [Part 9 Cedar 툴 필터링](/09-authorization/cedar-verified-permissions)으로 애초에 노출 자체를 막는다 |
| 로드맵을 1단계부터 순서대로 다 끝내려다 몇 달째 프로덕션에 못 나감 | 1단계를 완벽하게 끝내려는 완벽주의 — 로드맵은 단계별로 "충분히 좋음"을 목표로 함 | 1단계 체크리스트가 관측·캐시 구조·기본 계약에만 집중되어 있는지 확인 | 2단계 항목(권한·툴 과부하)을 1단계에 앞서 당기지 않는다 |

## 안티패턴

- ❌ 통증점 6개를 동시에 해결하려 든다 → ✅ 지금 가장 아픈 통증점 하나를 먼저 계측하고 해당 Part로 진입한다.
- ❌ 로드맵의 단계를 건너뛰고 3단계(게이트화)부터 시작한다 → ✅ 1단계의 관측 기반이 없으면 게이트가 무엇을 근거로 통과/실패를 판정할지 알 수 없다.

## 계측 (SLI)

이 장 자체는 계측 대상이 없다 — 각 통증점에 대응하는 Part의 "계측 (SLI)" 섹션을 참고한다. 다만 아래 전환 임계치 표는 여러 Part에 흩어진 신호를 한곳에 모은 정본이다.

### 전환 임계치 표

| 신호 | 임계치 | 조치 |
|---|---|---|
| 캐시 히트율 | < 70% | 프롬프트 구조(동적 요소 앞배치·툴 재정렬·JSON 비결정성) 점검, 1시간 TTL 검토 |
| 검색 recall@20 실패율 | > 3% | contextual retrieval → Contextual BM25 하이브리드 → 리랭커 순차 적용 |
| 컨텍스트 사용량 | 광고값의 1/4 초과 | 툴 수 감축 · semantic search · compaction |
| 파이프라인 단계 수 | 10단계 이상 | p^n 성공률 재계산, 단계 축소·체크포인트·durable execution |
| TPM 스로틀 | 빈발 | CRIS(글로벌 프로파일, 약 10% 저렴) → 프로비저닝/Reserved 검토 |

## 체크리스트

- [ ] 지금 겪는 증상을 위 결정 표에서 찾아 해당 Part로 진입했다
- [ ] 4단계 로드맵 중 현재 단계를 명확히 알고 있다
- [ ] 전환 임계치 표의 5개 신호를 계측하고 있다(계측이 없으면 임계치를 넘었는지 알 수 없다)
- [ ] 한국 금융 규제가 적용되는 환경이라면 4단계를 별도로 검토했다

## 참고

- [Platform Engineering — What is a Golden Path](https://platformengineering.org/blog/what-is-a-golden-path) — 비공식/커뮤니티 출처
- 이 책의 각 Part `index.md` — 통증점별 세부 결정 요약
