---
title: AgentCore Memory와 대안
description: AgentCore Memory의 2계층 구조(raw 이벤트·전략 기반 장기 기억)를 해부하고 Mem0·Zep(Graphiti)·Letta·LangGraph를 특성 축으로 비교해 선택 기준을 세운다.
outline: [2, 3]
---

# AgentCore Memory와 대안

::: tip 이 장에서 얻는 것
- AgentCore Memory의 2계층(단기 raw 이벤트 / 장기 전략 기반) 구조와 정확한 API 표면(`CreateEvent`, `ListSessions`, `ListEvents`, `GetEvent`, `RetrieveMemoryRecords` 등)
- 빌트인 장기 전략 4종(`semanticMemoryStrategy`, `summaryMemoryStrategy`, `userPreferenceMemoryStrategy`, `episodicMemoryStrategy`)과 override/self-managed 확장 지점의 정확한 이름과 네임스페이스 패턴
- Mem0 / Zep(Graphiti) / Letta / LangGraph checkpointer·store의 아키텍처 특성 비교 — 순위가 아니라 특성 축
- "개인화인가, 감사(audit)인가, AWS 밖인가"라는 세 질문으로 좁혀지는 결정 프레임워크
- 비동기 추출 파이프라인이 만드는 실패 모드와 그것을 잡는 SLI
:::

## 왜 문제가 되는가

에이전트 메모리 시스템 선택은 벡터 DB 선택처럼 나중에 교체하면 되는 문제가 아니다. 메모리 계층은 **데이터가 쌓이는 곳**이고, 쌓인 데이터의 스키마(사실 목록인가, 요약인가, 시간 그래프인가)가 곧 에이전트의 행동 특성을 결정한다. 6개월치 사용자 선호가 Mem0의 fact 스키마로 쌓인 뒤 AgentCore의 `UserPreferenceMemoryStrategy`로 옮기는 것은 단순 ETL이 아니라 추출 파이프라인 전체의 재실행이며, 원본 대화(raw 이벤트)를 보존하지 않았다면 재실행 자체가 불가능하다.

두 번째 이유는 **관리 책임의 경계**다. AgentCore Memory는 추출·통합(extraction/consolidation) LLM 호출까지 AWS가 관리하는 완전관리형이고, Mem0 OSS나 Graphiti는 추출용 LLM 비용·레이트리밋·프롬프트 품질을 플랫폼 팀이 직접 진다. 이 경계를 의식하지 않고 "기능표"만 보고 고르면, 운영 단계에서 추출 파이프라인 장애가 고스란히 플랫폼 팀 온콜로 돌아온다.

세 번째, 이 책의 demo(`demo/builder-harness`)가 실제로 겪은 문제이기도 하다. Harness는 기본값으로 managed memory를 자동 프로비저닝하는데(뒤에서 다룬다), 실행 역할에 `bedrock-agentcore:ListEvents`/`CreateEvent` 권한이 없으면 `InvokeHarness`가 스트림 도중 `runtimeClientError`(AccessDeniedException)로 실패한다. `bedrock:InvokeModel`만으로는 부족하다 — 메모리가 "자동으로 붙는다"는 것은 권한 표면도 자동으로 넓어진다는 뜻이다.

## 핵심 개념

### AgentCore Memory의 2계층 구조

AgentCore Memory는 단기(short-term)와 장기(long-term)의 2계층으로 구성된다.[^types]

**단기 = raw 이벤트.** 상호작용 하나하나(메시지, 툴 호출)를 `CreateEvent`로 이벤트로 저장하고, `sessionId` + `actorId` 스코프로 묶는다. 세션 복원 경로는 세 개의 read API다:

- `ListSessions` — actor의 과거 세션 목록 조회
- `ListEvents` — 특정 세션의 이벤트 목록(대화 이력) 재구성. `memoryId`/`actorId`/`sessionId` 필수, `includePayloads`(기본 true), branch 필터와 event metadata 필터(`EQUALS_TO`/`EXISTS`/`NOT_EXISTS`) 지원[^listevents]
- `GetEvent` — 특정 이벤트 단건 조회

핵심 용도는 **서비스 재시작·프로세스 교체 후 컨텍스트 복원**이다. 이벤트가 원본 그대로 남아 있으므로, 에이전트 런타임이 죽어도 같은 `sessionId`로 대화를 그대로 이어갈 수 있다.[^types] `CreateEvent` 시 key-value metadata를 붙여 `ListEvents` 필터로 활용할 수 있지만, event metadata는 customer managed key로 암호화되지 않으므로 민감 정보를 넣으면 안 된다.[^types]

**장기 = 전략 기반 추출.** `CreateEvent`(또는 `IngestData`)로 들어온 원본에서 비동기 백그라운드 파이프라인이 **추출(extraction) → 통합(consolidation)** 을 수행해 memory record를 만든다.[^types] 조회는 `GetMemoryRecord` / `ListMemoryRecords` / `RetrieveMemoryRecords` 세 API이며, `RetrieveMemoryRecords`가 시맨틱 검색을 수행한다.[^types]

### 빌트인 장기 전략

`CreateMemory`의 `memoryStrategies`에 지정하는 빌트인 전략은 4종이다(공식 문서의 설정 키 기준).[^strategies]

| 전략 (설정 키) | 추출 대상 | 네임스페이스 예시 (공식 문서) |
|---|---|---|
| `semanticMemoryStrategy` | 사실·엔티티·맥락 지식. 시맨틱 검색으로 조회 | `/support_cases/{sessionId}/facts/` |
| `summaryMemoryStrategy` | 세션 단위 러닝 요약 (actor+session 스코프) | `/summaries/{actorId}/{sessionId}/` |
| `userPreferenceMemoryStrategy` | 사용자 선호·선택·스타일 | `/users/{actorId}/preferences/` |
| `episodicMemoryStrategy` | 상호작용을 시나리오·의도·행동·결과·산출물의 구조화된 episode로 기록. episode 간 reflection으로 패턴 학습 | `/strategy/{memoryStrategyId}/actors/{actorId}/sessions/{sessionId}/` (reflection은 별도 네임스페이스) |

Episodic은 실존이 확인된 정식 전략이다 — Harness 문서와 built-in strategy 설정 문서 모두에 등재되어 있고, `reflection.namespaceTemplates`라는 고유 설정 블록을 가진다.[^strategies][^harness]

**커스터마이징 계층이 두 겹 있다.** 첫째, "built-in with overrides"(작업 지시서의 CustomMemoryStrategy에 해당) — 빌트인 전략의 시스템 프롬프트에 `appendToPrompt`로 지시를 추가하고, 추출·통합에 쓸 Bedrock 모델을 바꿀 수 있다. 단 출력 스키마는 변경 불가이고, consolidation 연산 이름(`AddMemory`/`UpdateMemory`)을 바꾸면 파이프라인이 실패한다. 이때는 `memoryExecutionRoleArn`이 필수이며 추출·통합 LLM 사용량이 **내 계정에 별도 과금**된다.[^custom] 둘째, self-managed strategy — 출력 스키마까지 포함한 엔드투엔드 제어가 필요할 때 쓴다.[^custom] 전략별로 override 가능한 단계가 다르다: Semantic/User preference는 Extraction+Consolidation, Summary는 Consolidation만, Episodic은 Extraction+Consolidation+Reflection.[^custom]

테넌트·사용자별 네임스페이스 격리 설계(namespace를 IAM 조건과 결합하는 패턴 등)는 이 장의 범위가 아니다 — [메모리 조회 스코핑](/07-memory/memory-retrieval-scoping)이 정본이다.

### Harness의 memory 설정과 이 책의 demo

AgentCore Harness는 memory 설정을 3-way 유니온으로 받는다: `managedMemoryConfiguration`(Harness가 자동 프로비저닝) / `agentCoreMemoryConfiguration`(기존 Memory 리소스 BYO) / `disabled`.[^harness] Managed memory의 기본값은 **semantic + summarization 전략, 이벤트 30일 만료**이고, `strategies` 배열(`SEMANTIC`/`SUMMARIZATION`/`USER_PREFERENCE`/`EPISODIC`)과 `eventExpiryDuration`으로 조정한다. 장기 기억 retrieval은 활성 전략에서 자동 유도되며 기본 파라미터는 전략 네임스페이스별 `topK=10`, `relevanceScore=0.2`다.[^harness]

이 책의 demo(`demo/builder-harness`)는 `create-harness.json`에 memory 필드를 두지 않아 이 기본 경로, 즉 **Harness managed memory를 실제로 사용한다**(`AgenticBookBuilderDemo-*` 리소스 자동 생성). 운영에서 확인된 함정 둘: (1) `DeleteHarness`는 managed memory를 기본 cascade 삭제하는데 삭제가 비동기라, 같은 이름으로 즉시 재생성하면 memory가 아직 `DELETING` 상태여서 `Memory with name ... already exists`로 실패한다 — 완전 소멸까지 폴링해야 한다. (2) 실행 역할에 memory 액션(`CreateEvent`, `ListEvents`, `RetrieveMemoryRecords` 등)을 명시해야 하며, demo의 `execution-policy.json`은 이를 `memory/AgenticBookBuilderDemo-*` 리소스로 스코프해 두었다.

### 대안 라이브러리의 아키텍처

**Mem0** — LLM 추출 파이프라인 기반. `add` 시 LLM이 메시지에서 사실·결정·선호를 뽑아 additive하게 저장하고(`infer=True` 기본, `infer=False`면 원문 저장), `search`로 관련도 순 조회한다. 스코프는 `user_id`(주 스코프)와 `agent_id`/`app_id`/`run_id`. 관리형 Mem0 Platform과 self-hosted OSS 양쪽을 제공하며 페이로드는 동일하다.[^mem0]

**Zep / Graphiti** — 시간 지식 그래프(temporal knowledge graph). Graphiti는 사실에 유효 기간(언제 참이 되었고 언제 대체되었는지)을 부여하는 bi-temporal 모델을 쓰고, 낡은 사실은 삭제가 아니라 **invalidation**되어 이력이 보존된다. 검색은 시맨틱 임베딩 + BM25 + 그래프 순회의 하이브리드이고, 백엔드로 Neo4j·FalkorDB·Amazon Neptune 등을 지원한다. Zep은 Graphiti를 코어로 쓰는 관리형 서비스, Graphiti 자체는 오픈소스 self-hosted(그래프 DB 별도 필요)다.[^graphiti]

**Letta (MemGPT 계보)** — "에이전트가 자기 메모리를 편집한다"는 접근. OS 가상 메모리 유비로, in-context의 core memory 블록(예: `human`, `persona` 라벨)과 out-of-context의 archival/recall memory를 계층화하고, 에이전트가 자기 블록을 직접 읽고 수정한다. 에이전트는 서버 측 영속 엔티티다. 오픈소스 self-hosted와 Letta Cloud(관리형)를 모두 제공한다.[^letta]

**LangGraph checkpointer / store** — 프레임워크 내장 영속화. checkpointer는 thread 스코프 단기 기억으로, 그래프 상태를 체크포인트로 저장해 대화 연속성·human-in-the-loop·time travel·장애 복구를 지원한다(`thread_id` 필수). Store(`BaseStore`)는 thread를 가로지르는 장기 key-value 저장소다. 백엔드는 `PostgresSaver`/`SqliteSaver`/`InMemorySaver` 등이며, self-hosted로 직접 구성하거나 LangGraph Platform이 영속화 인프라를 대신 운영한다.[^langgraph]

## 결정 표

주관적 순위가 아니라, 요구 특성 → 구조적 적합성의 매핑이다.

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| AWS 위 배포 + 개인화(사용자 프로필) 중심 | AgentCore Memory + `userPreferenceMemoryStrategy` | 추출·통합·저장·검색이 완전관리형. actor 스코프 네임스페이스가 기본 제공[^strategies] | 추출 로직은 프롬프트 append 수준까지만 제어(스키마 고정)[^custom] |
| 워크플로우 감사·재현 필요(이벤트 순서 보존) | AgentCore 단기 raw 이벤트 + `episodicMemoryStrategy` | 이벤트가 원본·순서 그대로 남고(`ListEvents`), episode가 의도→행동→결과 구조를 보존[^types][^strategies] | raw 이벤트는 `eventExpiryDuration` 내에서만 보존 — 장기 감사는 별도 아카이빙 필요 |
| "언제 참이었는가"가 중요한 도메인(정책·가격·조직 변경 추적) | Zep/Graphiti | bi-temporal + invalidation이 사실의 시간 이력을 1급 개념으로 취급[^graphiti] | 그래프 DB 운영(self-hosted 시) 또는 외부 SaaS 의존(Zep) |
| AWS 밖(멀티클라우드·온프레미스) 배포 | Mem0 OSS / Graphiti / Letta / LangGraph | 전부 self-hosted 가능[^mem0][^graphiti][^letta][^langgraph] | 추출 LLM 비용·레이트리밋·프롬프트 품질을 직접 운영 |
| 이미 LangGraph 기반, 상태 복구·HITL이 주 목적 | LangGraph checkpointer(+Store) | 그래프 상태와 메모리가 같은 트랜잭션 경계. time travel 내장[^langgraph] | "메모리"라기보다 상태 영속화 — 사실 추출·통합은 직접 구현 |
| 에이전트가 스스로 기억을 큐레이션해야 하는 장수명 에이전트 | Letta | self-editing memory block이 설계의 중심[^letta] | 메모리 품질이 에이전트(모델)의 편집 판단에 의존 |
| Harness로 빠르게 시작, 세부 제어는 나중에 | Harness managed memory → 필요 시 BYO 전환 | 기본값(semantic+summarization, 30일)으로 즉시 동작. `UpdateHarness`로 `agentCoreMemoryConfiguration` 전환 가능[^harness] | managed memory는 Memory API로 직접 삭제 불가, 커스텀 네임스페이스·KMS는 BYO로만[^harness] |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 방금 말한 선호를 다음 세션에서 모름 | 장기 추출은 **비동기** — record 생성 전에 조회함[^types] | `ListMemoryRecords`로 record 생성 시점 확인 | 즉시성 요구는 단기(`ListEvents`)로 커버, 장기 조회에 폴백/로딩 설계[^blog] |
| `InvokeHarness`가 스트림 중 `runtimeClientError` | 실행 역할에 `bedrock-agentcore:CreateEvent`/`ListEvents` 등 memory 액션 누락 | CloudTrail의 AccessDeniedException 이벤트 | 실행 정책에 memory 액션을 리소스 스코프로 추가 (demo의 `execution-policy.json` 참고) |
| Harness 재생성 시 `Memory with name ... already exists` | `DeleteHarness`의 managed memory cascade 삭제가 비동기, `DELETING`은 아직 존재하는 상태 | `list-memories`로 상태 폴링 | not-found까지 폴링 후 재생성. 보존이 목적이면 `deleteManagedMemory=false`로 분리[^harness] |
| 세션 복원이 조용히 빈 컨텍스트로 시작 | raw 이벤트가 `eventExpiryDuration`(managed 기본 30일[^harness]) 경과로 만료 | `ListEvents` 결과 건수 vs 기대 턴 수 비교 | 만료 기간 상향 또는 만료를 명시적으로 처리(사용자에게 고지) |
| 검색 결과에 낡은/모순된 사실 혼재 | consolidation이 아직 안 돌았거나(비동기), 시스템이 시간 이력 개념이 없음 | 같은 주제 record의 중복 여부를 `ListMemoryRecords`로 검사 | 추출 패턴 정기 리뷰[^blog]; 시간 이력이 요구사항이면 Graphiti류 재검토 |
| BYO memory에 전략을 추가했는데 Harness가 새 네임스페이스를 검색 안 함 | BYO는 전략 변경 시 retrieval config 자동 갱신 안 됨 | Harness의 retrieval 대상 네임스페이스 확인 | 전략 변경 후 `UpdateHarness` 호출로 retrieval config 갱신 (managed는 자동)[^harness] |
| 관련 기억이 검색에 안 잡히거나 잡음이 과다 | `topK`/`relevanceScore` 기본값(10/0.2[^harness])이 도메인과 안 맞음 | 대표 쿼리 셋으로 검색 스코어 분포 측정 | `retrievalConfig`로 네임스페이스별 튜닝[^harness] |
| Override 전략 도입 후 예상 밖 Bedrock 비용 | 추출·통합 LLM 호출이 내 계정 과금으로 전환됨[^custom] | Cost Explorer에서 Bedrock 사용량을 `memoryExecutionRoleArn` 기준 분리 | 추출 빈도·모델 티어 조정, 배치 이벤트 설계 |

## 안티패턴

- ❌ 대화 원문을 event metadata에 넣기 → ✅ metadata는 필터링용 키만. metadata는 CMK로 암호화되지 않는다[^types] — 본문은 payload에.
- ❌ 장기 memory record를 유일한 기록으로 삼고 raw 이벤트 만료 방치 → ✅ record는 파생물이다. 감사·재추출이 요구사항이면 raw 이벤트 보존 기간을 요구사항에 맞추거나 별도 아카이빙.
- ❌ `CreateEvent` 직후 `RetrieveMemoryRecords`를 호출하고 결과가 비면 버그로 취급 → ✅ 추출은 비동기가 정상 동작이다.[^types] 동기적 기대를 코드에 심지 말 것.
- ❌ override 프롬프트를 백지에서 새로 작성, 스키마·연산명까지 수정 → ✅ 빌트인 지시문에 append. 스키마와 `AddMemory`/`UpdateMemory` 연산명은 불변 계약이다.[^custom]
- ❌ "일단 Harness managed로 시작했으니 계속 managed" 관성으로 KMS CMK·커스텀 네임스페이스 요구를 우회 구현 → ✅ 그 요구가 생기는 시점이 BYO(`agentCoreMemoryConfiguration`) 전환 시점이다.[^harness]
- ❌ 프레임워크 checkpointer(LangGraph)를 "장기 기억"으로 오용 → ✅ checkpointer는 thread 스코프 상태 복구용. cross-thread 지식은 Store 또는 전용 메모리 시스템으로.[^langgraph]
- ❌ 벤치마크 수치 하나로 메모리 시스템 우열 판정 → ✅ 스키마(사실/요약/그래프/블록)와 운영 책임 경계로 판단. 벤더 벤치마크는 대부분 자사 최적 조건이다.

::: warning 미정착 영역
에이전트 메모리 품질의 표준 벤치마크는 아직 정착되지 않았다. 각 벤더(Mem0, Zep, Letta)가 서로 다른 데이터셋·채점 방식으로 자사 우위를 주장하는 결과를 발표해 왔고, 상호 반박도 있었다. 이 장이 성능 순위를 제시하지 않는 이유다 — 도입 전 자사 트래픽 샘플로 자체 평가 셋을 만들어 검증하라.
:::

## 계측 (SLI)

메모리 계층은 "조용히 나빠지는" 컴포넌트다. 다음 SLI를 권한다.

- **추출 반영 지연(extraction lag)**: `CreateEvent` 시각 → 해당 내용이 `ListMemoryRecords`에 나타나는 시각. 비동기 파이프라인의 건강 지표. 임계는 UX 요구(예: "다음 세션 시작 전")에서 역산.
- **세션 복원 성공률**: 재시작·재접속 후 `ListEvents` 재구성으로 직전 컨텍스트가 복원된 비율. 만료·권한·sessionId 전달 누락이 모두 여기서 드러난다.
- **retrieval 유효율**: `RetrieveMemoryRecords` 응답 중 relevanceScore 임계 통과 건수 / topK. 지속적으로 낮으면 네임스페이스 설계나 추출 품질 문제.
- **memory 권한 거부율**: 실행 역할의 `bedrock-agentcore:*` 계열 AccessDenied 건수(CloudTrail). demo에서 확인했듯 스트림 중간 실패로 나타나 원인 추적이 어렵다.
- **추출 LLM 비용**: override/self-managed 전략 사용 시 `memoryExecutionRoleArn` 기준 Bedrock 사용량 분리 계측.[^custom]
- **record 증가율 vs consolidation 감소율**: actor당 record 수의 순증 추이. 통합이 못 따라가면 검색 잡음과 비용이 함께 는다. 추출 패턴 정기 리뷰는 AWS도 권장하는 운영 관행이다.[^blog]

AgentCore가 자동 발행하는 Invocations/Latency/Errors/Throttles/Session Count 메트릭과 달리, 위 SLI 대부분은 직접 계측해야 한다. 토큰 사용량 역시 자동 발행되지 않으므로 ADOT SDK 계측이 필요하다.

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요: 자동 발행 메트릭 목록과 토큰 미발행 사실은 로컬 운영 노트(agentcore-memory-observability 스킬 문서) 기준이다. CloudWatch 네임스페이스는 대소문자 구분이 있으므로 `aws cloudwatch list-metrics --namespace "Bedrock-AgentCore"`(없으면 소문자)로 실측 확인하라.

## 체크리스트

- [ ] 요구를 세 질문으로 분류했다: 개인화 중심인가(→ User Preference 전략), 이벤트 순서·감사 보존인가(→ raw 이벤트 + episodic), AWS 밖 배포인가(→ 대안 라이브러리)
- [ ] 장기 추출의 **비동기성**을 UX에 반영했다(폴백, 즉시성은 단기 이벤트로)
- [ ] raw 이벤트 만료(`eventExpiryDuration`)를 감사·재추출 요구사항과 대조했다
- [ ] 실행 역할에 memory 액션을 리소스 스코프로 부여했다 (`CreateEvent`/`ListEvents`/`RetrieveMemoryRecords` 등 — `bedrock-agentcore:*` 금지)
- [ ] event metadata에 민감 정보를 넣지 않는다는 규칙을 코드 리뷰 체크에 넣었다 (CMK 미암호화[^types])
- [ ] Harness 사용 시: managed/BYO/disabled 중 어느 것인지 명시적으로 결정하고 기록했다 (기본값 방치 금지)
- [ ] Harness BYO 사용 시: Memory 전략 변경 → `UpdateHarness` 호출 절차가 런북에 있다
- [ ] `DeleteHarness` ↔ 재생성 시나리오에서 managed memory `DELETING` 폴링을 자동화했다
- [ ] override 전략 사용 시: `memoryExecutionRoleArn` 과금 분리 계측이 있다
- [ ] 대안 라이브러리 채택 시: 추출 LLM의 비용·레이트리밋·프롬프트 버저닝 책임자가 정해져 있다
- [ ] 테넌트 격리 네임스페이스 설계는 [메모리 조회 스코핑](/07-memory/memory-retrieval-scoping) 기준을 따랐다
- [ ] 추출 반영 지연·세션 복원 성공률·retrieval 유효율 SLI가 대시보드에 있다

## 참고

[^types]: AWS, "Memory types" (AgentCore Developer Guide) — 단기 raw 이벤트와 `CreateEvent`/`ListSessions`/`ListEvents`/`GetEvent`, 장기 추출·통합의 비동기성, `GetMemoryRecord`/`ListMemoryRecords`/`RetrieveMemoryRecords`, event metadata의 CMK 미암호화. https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-types.html
[^listevents]: AWS boto3, `bedrock-agentcore` `list_events` — 파라미터·필터 명세. https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/list_events.html / 개요: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/short-term-list-events.html
[^strategies]: AWS, "Configure built-in strategies" — `userPreferenceMemoryStrategy`/`semanticMemoryStrategy`/`summaryMemoryStrategy`/`episodicMemoryStrategy` 설정 키와 네임스페이스 예시, episodic의 reflection. https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/long-term-configuring-built-in-strategies.html
[^custom]: AWS, "Customize a built-in strategy or create your own strategy" — `appendToPrompt`, 모델 override, 스키마·연산명 불변, `memoryExecutionRoleArn`과 별도 과금, self-managed strategy, 전략별 override 가능 단계. https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-custom-strategy.html
[^harness]: AWS, "Memory" (AgentCore Harness) — memory 유니온(`managedMemoryConfiguration`/`agentCoreMemoryConfiguration`/`disabled`), managed 기본값(semantic+summarization, 30일 만료), `strategies` enum, retrieval 기본값(`topK=10`, `relevanceScore=0.2`), `deleteManagedMemory=false`, BYO 전략 변경 시 `UpdateHarness` 필요, truncation 전략. https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-memory.html
[^blog]: AWS ML Blog, "Building smarter AI agents: AgentCore long-term memory deep dive" — 비동기 처리 대비 설계, 추출 패턴 정기 리뷰 권장. https://aws.amazon.com/blogs/machine-learning/building-smarter-ai-agents-agentcore-long-term-memory-deep-dive/
[^mem0]: Mem0 Docs, "Memory operations" — LLM 추출 파이프라인, additive 저장, `infer` 플래그, `user_id`/`agent_id`/`run_id` 스코프, Platform vs OSS. https://docs.mem0.ai/core-concepts/memory-operations
[^graphiti]: getzep/graphiti (GitHub) — bi-temporal 모델, invalidation, 하이브리드 검색(시맨틱+BM25+그래프), Neo4j/FalkorDB/Neptune 백엔드, Zep과의 관계. https://github.com/getzep/graphiti
[^letta]: Letta Docs, "MemGPT" — self-editing memory block, core/archival·recall 계층, 서버 측 영속 에이전트, OSS + Letta Cloud. https://docs.letta.com/concepts/memgpt
[^langgraph]: LangChain Docs, "LangGraph persistence" — checkpointer(thread 스코프)와 Store(cross-thread), `PostgresSaver`/`SqliteSaver`/`InMemorySaver`, self-hosted vs LangGraph Platform. https://docs.langchain.com/oss/python/langgraph/persistence

관련 장: [메모리 유형](/07-memory/memory-types) · [메모리 쓰기 정책](/07-memory/memory-write-policies) · [메모리 조회 스코핑](/07-memory/memory-retrieval-scoping) · [메모리 보안과 프라이버시](/07-memory/memory-security-privacy)
