---
title: 메모리 유형
description: 에이전트 메모리를 단기(세션 내)와 장기(세션 간)로 나누고, semantic·episodic·procedural 유형학과 RAG·컨텍스트 엔지니어링과의 경계를 정리하는 Part 7 서론.
outline: [2, 3]
---

# 메모리 유형

::: tip 이 장에서 얻는 것
- 단기 메모리(세션 내 대화 이력 = 컨텍스트 윈도우)와 장기 메모리(세션 간 지속)의 경계를 정확히 긋고, 팀 내에서 "메모리"라는 단어가 가리키는 대상을 통일한다.
- 장기 메모리의 유형학 — semantic(사실), episodic(상호작용 이력), procedural(방법·선호) — 과 working memory의 구분, 그리고 이 인지과학 차용 용어의 한계를 파악한다.
- 메모리 vs RAG, 메모리 vs 컨텍스트 엔지니어링([Part 5](../05-context/context-engineering-discipline.md))의 경계와, 그 경계가 흐려지는 사례를 식별한다.
- AgentCore Memory의 단기(이벤트)/장기(전략) 2계층 구조 개요와, Part 7 나머지 챕터([쓰기 정책](./memory-write-policies.md), [검색 스코핑](./memory-retrieval-scoping.md), [AgentCore Memory와 대안](./agentcore-memory-alternatives.md), [보안·프라이버시](./memory-security-privacy.md))로 가는 로드맵을 얻는다.
:::

## 왜 문제가 되는가

LLM 추론은 stateless다. 모델은 매 호출마다 컨텍스트 윈도우에 들어온 토큰만 본다. 세션 안에서는 대화 이력을 매 턴 다시 넣어주는 것으로 "기억하는 것처럼" 만들 수 있지만, 세션이 끝나는 순간 그 이력은 사라진다. AWS는 AgentCore Memory 문서에서 이를 명시적으로 "agentic AI의 근본 과제: statelessness"라고 규정한다 — 메모리 없이는 에이전트가 모든 상호작용을 이전 대화에 대한 지식이 전혀 없는 새 인스턴스로 취급한다.[^agentcore]

플랫폼 엔지니어 입장에서 이것이 문제가 되는 지점은 세 가지다.

1. **사용자 경험의 비용 전가.** 메모리가 없으면 사용자가 매 세션 선호·제약·이전 결정을 다시 설명해야 한다. 이 반복 입력은 UX 문제인 동시에 토큰 비용 문제다 — 같은 정보를 매번 컨텍스트에 재주입하는 비용을 사용자와 시스템이 나눠 낸다.
2. **"전체 이력 재주입"은 스케일하지 않는다.** 세션 간 연속성을 위해 과거 대화 전체를 컨텍스트에 밀어 넣는 naive full-context 접근은 토큰 비용과 지연을 선형 이상으로 키우고, [context rot](../05-context/context-rot.md)으로 품질까지 떨어뜨린다. Mem0 논문은 선택적 메모리 추출 방식이 full-context 대비 p95 latency를 91% 낮추고 토큰 비용을 90% 이상 절감했다고 보고한다(단, 아래 주의 블록 참고).[^mem0]
3. **메모리는 저장이 아니라 파이프라인이다.** "무엇을 기억할지 결정하고(쓰기), 언제 무엇을 불러올지 결정하고(검색), 낡은 기억을 갱신·폐기하는(수명 관리)" 각 단계가 독립적인 실패 모드를 가진다. 이 단계들을 설계하지 않고 벡터 DB 하나를 붙이는 것은 메모리 시스템이 아니라 로그 덤프다.

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요
>
> 위 Mem0 수치(26% 상대 개선, 91% p95 latency 감소, 90%+ 토큰 절감)는 Mem0 벤더가 저술한 arXiv preprint([2504.19413](https://arxiv.org/abs/2504.19413))의 자체 평가로, LOCOMO 벤치마크·특정 모델 구성에 한정된다. peer review를 거치지 않았고 벤더 이해관계가 있으므로, 도입 판단에는 자체 워크로드 기준 재측정이 필요하다.

이 챕터는 Part 7의 서론으로서 **분류와 경계**를 담당한다. 각 유형을 언제 어떻게 쓰고 저장할지는 [메모리 쓰기 정책](./memory-write-policies.md), 무엇을 어느 범위에서 검색해 올지는 [메모리 검색과 스코핑](./memory-retrieval-scoping.md), 구현 선택지는 [AgentCore Memory와 대안](./agentcore-memory-alternatives.md), 기억이 만드는 새로운 공격면은 [메모리 보안·프라이버시](./memory-security-privacy.md)가 다룬다.

## 핵심 개념

### 단기 vs 장기: 유일하게 견고한 경계선

메모리 유형학에서 업계 합의가 가장 견고한 축은 **지속 범위(persistence scope)** 하나다.

- **단기 메모리(short-term)**: 세션(스레드) 안의 대화 이력. 구현체는 결국 컨텍스트 윈도우에 재주입되는 메시지 배열이다. LangChain은 이를 "세션 내에서 message history를 유지하며 진행 중인 대화를 추적"하는 것으로, 단일 스레드에 스코프된다고 정의한다.[^langchain-mem]
- **장기 메모리(long-term)**: 세션 경계를 넘어 지속되는 상태. LangChain 정의로는 "스레드를 가로질러(across threads) 공유되며 어느 스레드에서든 회상 가능한" 데이터다.[^langchain-lt]

이 구분이 견고한 이유는 아키텍처 함의가 명확히 갈리기 때문이다. 단기 메모리의 병목은 컨텍스트 윈도우이고 해법은 Part 5의 [compaction](../05-context/compaction-summarization.md)·[오프로딩](../05-context/context-isolation-offloading.md)이다. 장기 메모리의 병목은 저장소가 아니라 **선별**이다 — 무엇을 남기고(쓰기 정책), 무엇을 언제 불러올지(검색 스코핑)가 품질을 결정한다.

### 장기 메모리의 유형학: semantic / episodic / procedural

장기 메모리를 더 쪼개는 분류로는 인지과학 용어를 차용한 3분류가 사실상의 lingua franca다. 학술 쪽 기원은 CoALA 논문(Cognitive Architectures for Language Agents)이고,[^coala] 프레임워크 쪽에서는 LangChain/LangGraph 문서가 같은 분류를 채택한다.[^langchain-mem]

| 유형 | 정의 (CoALA / LangChain) | 에이전트에서의 예 | 전형적 표현 형태 |
|---|---|---|---|
| **Semantic** | "세계와 자기 자신에 대한 지식"[^coala] / "구체적 사실과 개념의 보유"[^langchain-mem] | 사용자의 직무·기술 스택, "우리 팀 표준 리전은 us-east-1" | 사실 트리플, 프로필 문서, 벡터 검색 대상 레코드 |
| **Episodic** | "이전 decision cycle의 경험 저장"[^coala] / "과거 사건·행동의 회상"[^langchain-mem] | 지난 세션의 태스크 시도와 결과, 성공한 해결 궤적(trajectory) | 세션 요약, 상호작용 로그, few-shot으로 재주입되는 과거 사례 |
| **Procedural** | "태스크 수행에 쓰는 규칙의 기억"[^langchain-mem] — LLM 가중치의 암묵지 + 에이전트 코드·프롬프트의 형식지[^coala] | "이 사용자는 답변을 bullet으로", 스스로 개정하는 시스템 프롬프트, 학습된 도구 사용 순서 | 시스템 프롬프트/지침 파일, CLAUDE.md류 규칙 문서 |
| **Working** | "현재 decision cycle의 활성 정보를 심볼릭 변수로 유지"[^coala] | 현재 턴의 중간 추론, 도구 호출 결과, 루프 변수 | 컨텍스트 윈도우 내 상태 — **장기 메모리가 아님** |

working memory는 이 표에 넣었지만 범주가 다르다. CoALA에서 working memory는 저장소가 아니라 "언어 에이전트의 구성요소들을 잇는 중앙 허브"[^coala] — 즉 지금 이 순간의 추론 상태다. 플랫폼 관점에서 working memory 관리는 곧 컨텍스트 엔지니어링이며 [Part 5](../05-context/context-engineering-discipline.md)의 영역이다. Part 7에서 "메모리"라고 말할 때는 기본적으로 장기 메모리를 가리킨다.

실무적으로 이 3분류가 유용한 이유는 **쓰기·갱신·검색 정책이 유형마다 다르기 때문**이다. semantic 사실은 모순 발생 시 갱신(consolidation)이 필요하고, episodic 기록은 append-only에 가깝되 수명 관리가 필요하며, procedural 지침은 갯수가 적은 대신 잘못 쓰면 모든 세션을 오염시킨다. 이 차이는 [쓰기 정책 챕터](./memory-write-policies.md)의 뼈대가 된다.

::: warning 미정착 영역
semantic/episodic/procedural은 인간 기억 연구(Tulving의 기억 분류)에서 차용한 은유이지 업계 표준 분류가 아니다. 경계 판정이 갈리는 사례가 많다 — "사용자가 window seat을 선호한다"는 semantic(사실)인가 procedural(선호·방법)인가? AgentCore는 이를 별도의 User Preference 전략으로 두고, LangChain은 선호를 procedural의 예로 들기도 한다. 또 CoALA는 LLM 가중치 자체를 procedural memory에 포함시키지만, 대부분의 벤더 제품은 가중치를 메모리로 취급하지 않는다. 벤더마다 용어 매핑이 다르므로(Mem0, Zep, Letta, AgentCore 모두 상이) 팀 문서에는 유형 이름과 함께 **구체적 스키마 예시**를 병기하고, 벤더 용어를 무비판적으로 이식하지 마라. 상세 비교는 [AgentCore Memory와 대안](./agentcore-memory-alternatives.md)에서 다룬다.
:::

### 메모리 vs RAG

둘 다 "외부 저장소에서 텍스트를 검색해 컨텍스트에 주입"하므로 파이프라인 형태는 유사하지만, 구분 기준은 **데이터의 출처와 생애주기**다.

| 축 | RAG | 에이전트 메모리 |
|---|---|---|
| 코퍼스 출처 | 사전 구축된 정적 문서 집합(사내 위키, 매뉴얼) | 에이전트-사용자 **상호작용에서 축적**된 상태 |
| 쓰기 주체 | 오프라인 ingestion 파이프라인 | 에이전트 런타임(대화 중 추출·기록) |
| 갱신 빈도 | 배치 재색인 | 세션마다, 턴마다 |
| 스코프 | 대체로 전역(모든 사용자 공유) | 사용자/에이전트/태스크 단위로 격리 |
| 실패 시 증상 | 오답·환각 | 오답 + **개인화 오염**(남의 기억, 낡은 기억) |

경계가 흐려지는 사례는 실재한다. 에이전트가 세션 중 알아낸 사실을 팀 공유 knowledge base에 적재하면(쓰기 주체는 런타임인데 스코프는 전역) 메모리인가 RAG인가? CoALA 관점에서는 둘 다 semantic memory이고 — 전통적 RAG는 semantic memory를 외부 DB로 초기화하는 특수 사례, 에이전트가 경험에서 지식을 축적하는 것은 같은 저장소에 대한 learning 쓰기다.[^coala] 즉 "RAG vs 메모리"는 저장소의 종류가 아니라 **쓰기 경로와 스코프 정책의 차이**로 이해하는 것이 정확하며, 그래서 이 책은 검색 품질 일반은 RAG 챕터에 맡기고 Part 7에서는 쓰기 정책과 스코핑에 집중한다.

### 메모리 vs 컨텍스트 엔지니어링 (Part 5와의 경계)

Part 5의 compaction·오프로딩과 Part 7의 메모리는 모두 "컨텍스트 윈도우 밖에 상태를 두는" 기법이라 혼동하기 쉽다. 경계는 **세션 내 vs 세션 간**이다.

- [Compaction](../05-context/compaction-summarization.md)은 진행 중인 세션의 이력을 손실 압축해 같은 세션을 이어가는 기법이다.
- [오프로딩](../05-context/context-isolation-offloading.md)은 세션 안에서 대량 중간 산물을 파일 시스템 등으로 빼내는 기법이다.
- **메모리는 세션이 끝나도 살아남아 다음 세션에 주입되는 상태**다.

다만 이 경계 위에 걸친 기법이 있다. Anthropic은 long-horizon 태스크 기법으로 structured note-taking(agentic memory)을 든다 — "에이전트가 컨텍스트 윈도우 밖의 메모리에 노트를 주기적으로 기록하고, 나중에 다시 컨텍스트로 불러온다."[^ctx-eng] Claude가 Pokémon을 플레이하며 수천 스텝에 걸쳐 자기 노트로 일관성을 유지하고, 컨텍스트 리셋 후에도 노트를 읽어 멀티시간 시퀀스를 이어간 사례가 대표적이다.[^ctx-eng] 컨텍스트 리셋을 "세션 경계"로 보면 이것은 이미 메모리다. Anthropic이 Claude Developer Platform에 공개한 file 기반 memory tool도 같은 메커니즘 — 컨텍스트 윈도우 밖 저장·조회 — 을 제품화한 것이다.[^ctx-eng] 실무 기준: **압축·이동의 대상이 "지금 세션을 완주하기 위한 상태"면 Part 5, "다음 세션·다음 사용자 상호작용을 위한 상태"면 Part 7**로 설계 문서를 나눠라.

### AgentCore Memory의 2계층 구조 (개요만)

관리형 구현의 대표 사례로 Amazon Bedrock AgentCore Memory는 정확히 이 장의 2분류를 API로 노출한다.[^agentcore-types]

- **단기(이벤트) 계층**: 원시 상호작용을 `CreateEvent`로 이벤트로 적재하고 `sessionId`로 세션에 묶는다. `ListSessions`/`ListEvents`/`GetEvent`로 원문 이력을 재로딩한다.
- **장기(전략) 계층**: 단기 이벤트에서 비동기 백그라운드 프로세스가 인사이트를 자동 추출(extraction)하고 기존 기억과 통합(consolidation)해 memory record로 저장하며, `RetrieveMemoryRecords`가 semantic search로 조회한다. 보존 대상은 "대화 요약, 사실과 지식, 사용자 선호" — 즉 episodic/semantic/procedural 3유형과 자연스럽게 대응한다.

전략(strategy) 구성, 대안 제품과의 비교, 자체 구축 판단 기준은 [AgentCore Memory와 대안](./agentcore-memory-alternatives.md)이 정본으로 다룬다. 이 장에서는 "관리형 제품도 결국 단기/장기 2계층 + 유형별 추출 전략이라는 같은 모델로 수렴한다"는 것만 확인하면 된다.

### Part 7 로드맵

| 챕터 | 담당 질문 |
|---|---|
| **메모리 유형** (이 장) | 무엇을 메모리라 부르고, 어떻게 분류하는가 |
| [메모리 쓰기 정책](./memory-write-policies.md) | 무엇을, 언제, 어떤 형태로 기억에 남기는가 — 추출·통합·수명 관리 |
| [메모리 검색과 스코핑](./memory-retrieval-scoping.md) | 어떤 기억을, 어느 격리 범위에서, 언제 불러오는가 |
| [AgentCore Memory와 대안](./agentcore-memory-alternatives.md) | 관리형 vs 자체 구축 — AgentCore, Mem0, Zep, Letta, 파일 기반 |
| [메모리 보안·프라이버시](./memory-security-privacy.md) | 기억이 만드는 공격면 — memory poisoning, 테넌트 격리, PII·삭제권 |

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 단일 세션 내 이력 관리만 필요 | 메모리 시스템 도입하지 않음 — [Part 5 기법](../05-context/context-engineering-discipline.md)으로 해결 | 세션 내 문제에 세션 간 인프라는 과잉 | 세션 간 연속성 요구가 생기면 재설계 필요 |
| 사용자 프로필·선호의 세션 간 유지 | semantic + procedural 유형의 장기 메모리 (구조화 프로필 우선) | 사실·선호는 소수·고가치 — 추출형 저장이 검색보다 안정적 | 추출·통합 파이프라인의 정확도 관리 부담 |
| "지난번에 하던 작업 이어서" 요구 | episodic 메모리(세션 요약) + 단기 이력 재로딩 | 원문 재로딩만으로는 토큰 폭발, 요약만으로는 디테일 손실 — 병행 필요 | 요약 시점·품질 관리(→ [쓰기 정책](./memory-write-policies.md)) |
| 전 사용자 공통 도메인 지식 주입 | 메모리가 아니라 RAG/knowledge base | 상호작용에서 축적된 상태가 아님 — 스코프도 전역 | 에이전트가 발견한 지식의 환류 경로는 별도 설계 |
| 단일 장시간 태스크의 컨텍스트 리셋 생존 | structured note-taking (file 기반 agentic memory)[^ctx-eng] | 세션 내/간 경계에 걸친 문제 — 무거운 추출 파이프라인 불필요 | 노트 품질이 전적으로 모델 행동에 의존 |
| 팀 규모 작고 인프라 여력 없음 | 관리형(예: [AgentCore Memory](./agentcore-memory-alternatives.md)) | 추출·통합·저장·검색을 위임 | 추출 로직 커스텀 한계, 벤더 종속 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 에이전트가 매 세션 같은 질문을 반복 | 장기 메모리 부재 또는 쓰기 정책이 해당 정보를 버림 | 세션 로그에서 동일 정보의 반복 입력 빈도 측정 | 유형 분류 후 쓰기 정책에 추출 규칙 추가([쓰기 정책](./memory-write-policies.md)) |
| 낡은 선호·사실을 계속 주장 ("이사했는데 옛 주소 사용") | consolidation 없는 append-only 저장 — 모순 기억 공존 | 동일 키에 상충 레코드 존재 여부 조회 | 갱신·모순 해소 단계 도입, 최신성 가중 검색 |
| 다른 사용자/테넌트의 기억이 응답에 등장 | 검색 스코프 미격리 — namespace 없이 전역 검색 | 교차 사용자 카나리 레코드 심고 유출 테스트 | 사용자·테넌트 단위 namespace 강제([스코핑](./memory-retrieval-scoping.md), [보안](./memory-security-privacy.md)) |
| 세션이 길어질수록 응답 품질 저하 | 장기 메모리 문제가 아니라 세션 내 [context rot](../05-context/context-rot.md) — 진단 오류 | 새 세션에서 재현 여부로 세션 내/간 분리 | Part 5 기법(compaction 등) 적용 — 메모리 인프라 증설로 풀지 말 것 |
| 기억 주입 후 오히려 태스크 정확도 하락 | 무관·저품질 기억의 과다 주입 (recall 최적화 편향) | 메모리 on/off A/B로 태스크 성공률 비교 | 주입 상한·관련도 임계치 설정, 유형별 주입 위치 분리 |
| 대화에 심은 악성 지시가 이후 모든 세션에서 실행 | memory poisoning — 쓰기 경로가 신뢰 경계를 넘음 | 주입 문자열이 장기 저장소에 기록되는지 감사 | 쓰기 검증·출처 태깅([보안·프라이버시](./memory-security-privacy.md)) |

## 안티패턴

- ❌ 대화 전체를 벡터 DB에 그대로 embedding해 "메모리"라고 부르기 → ✅ 유형(semantic/episodic/procedural)별로 추출·구조화해 저장하고, 원문 이력은 단기 계층에 분리 보관.
- ❌ 세션 내 품질 문제(context rot)를 장기 메모리 도입으로 해결 시도 → ✅ 먼저 [Part 5](../05-context/context-engineering-discipline.md)의 compaction·오프로딩으로 세션 내 문제인지 분리 진단.
- ❌ RAG 파이프라인에 사용자 기억을 같은 인덱스로 합쳐 넣기 → ✅ 정적 코퍼스와 상호작용 유래 상태는 저장소·스코프·수명 정책을 분리.
- ❌ 벤더의 메모리 유형 용어를 사내 설계 문서에 정의 없이 이식 → ✅ 용어마다 구체적 스키마 예시와 쓰기·검색 정책을 병기 (업계 표준 분류가 없으므로).
- ❌ working memory(현재 턴 상태)까지 외부 저장소에 동기 기록 → ✅ 세션 내 상태는 컨텍스트/파일 오프로딩으로, 외부 기록은 세션 간 가치가 있는 것만 비동기로.
- ❌ 모든 기억을 모든 턴에 주입 → ✅ 유형·태스크별 주입 조건과 상한을 두고, 검색 스코핑은 [별도 챕터](./memory-retrieval-scoping.md)의 정책을 따름.

## 계측 (SLI)

메모리는 "붙였다"가 아니라 "효과가 측정된다"가 완료 조건이다. 유형별로 최소 다음을 계측한다.

- **메모리 기여도**: 메모리 주입 on/off 조건의 태스크 성공률·사용자 재입력률 차이 (A/B). 이 지표 없이는 메모리가 순기능인지 판단 불가.
- **검색 품질**: 주입된 기억 중 응답에 실제 인용·활용된 비율(precision proxy), 사용자가 "전에 말했는데"라고 정정하는 빈도(recall proxy).
- **신선도(staleness)**: 검색된 기억의 age 분포, 모순 레코드 공존 건수.
- **비용·지연**: 턴당 메모리 주입 토큰 수, 메모리 검색 latency(p50/p95) — 장기 메모리 검색이 에이전트 루프의 critical path에 있으므로 별도 추적.
- **쓰기 파이프라인 건전성**: 추출 시도 대비 저장 성공률, 비동기 consolidation 지연 (AgentCore처럼 추출이 비동기인 구조에서는 "방금 말한 선호가 아직 반영 안 됨" 구간이 존재한다[^agentcore-types]).
- **격리 위반**: 교차 사용자/테넌트 카나리 유출 건수 — 목표는 0, 상세는 [보안·프라이버시](./memory-security-privacy.md).

## 체크리스트

- [ ] 설계 문서에서 "메모리"가 단기(세션 내)인지 장기(세션 간)인지 매 사용처마다 명시했다.
- [ ] 저장할 정보를 semantic/episodic/procedural로 분류하고, 유형별 스키마 예시를 병기했다.
- [ ] working memory(현재 턴 상태)는 메모리 시스템 범위에서 제외하고 Part 5 기법으로 처리함을 확인했다.
- [ ] 정적 코퍼스(RAG)와 상호작용 유래 상태(메모리)의 저장소·스코프·수명 정책을 분리했다.
- [ ] 세션 내 품질 문제와 세션 간 연속성 문제를 분리 진단하는 절차(새 세션 재현 테스트)가 있다.
- [ ] 메모리 on/off A/B로 기여도를 측정할 수단이 배포 전에 준비됐다.
- [ ] 쓰기 정책(무엇을·언제 남기나)과 검색 스코핑(무엇을·언제 불러오나)을 각각 [해당](./memory-write-policies.md) [챕터](./memory-retrieval-scoping.md) 기준으로 리뷰했다.
- [ ] 관리형 vs 자체 구축 판단을 [AgentCore Memory와 대안](./agentcore-memory-alternatives.md)의 결정 표로 검토했다.
- [ ] memory poisoning·테넌트 격리 위험을 [보안·프라이버시](./memory-security-privacy.md) 체크리스트로 검토했다.

## 참고

- Sumers, Yao, Narasimhan, Griffiths, "Cognitive Architectures for Language Agents (CoALA)" — <https://arxiv.org/abs/2309.02427>
- LangChain Docs, "Memory" (short-term/long-term, semantic/episodic/procedural) — <https://docs.langchain.com/oss/python/concepts/memory>
- LangChain Docs, "Long-term memory" — <https://docs.langchain.com/oss/python/langchain/long-term-memory>
- Anthropic, "Effective context engineering for AI agents" (structured note-taking, memory tool) — <https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents>
- AWS, "Add memory to your Amazon Bedrock AgentCore agent" — <https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html>
- AWS, "AgentCore Memory types" — <https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-types.html>
- Chhikara et al., "Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory" (벤더 preprint) — <https://arxiv.org/abs/2504.19413>

[^agentcore]: AWS, "Add memory to your Amazon Bedrock AgentCore agent": "AgentCore Memory addresses a fundamental challenge in agentic AI: statelessness. Without memory capabilities, AI agents treat each interaction as a new instance with no knowledge of previous conversations." <https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html>
[^mem0]: Chhikara et al., "Mem0" abstract: "Mem0 achieves 26% relative improvements in the LLM-as-a-Judge metric over OpenAI", "91% lower p95 latency", "saves more than 90% token cost" (vs full-context). 벤더 자체 평가. <https://arxiv.org/abs/2504.19413>
[^langchain-mem]: LangChain Docs, "Memory": semantic — "retention of specific facts and concepts", episodic — "recalling past events or actions", procedural — "remembering the rules used to perform tasks"; short-term memory는 "tracks the ongoing conversation by maintaining message history within a session". <https://docs.langchain.com/oss/python/concepts/memory>
[^langchain-lt]: LangChain Docs, "Long-term memory": long-term memory는 "persists across threads and can be recalled at any time". <https://docs.langchain.com/oss/python/langchain/long-term-memory>
[^coala]: Sumers et al., "Cognitive Architectures for Language Agents (CoALA)": working memory — "maintains active and readily available information as symbolic variables for the current decision cycle", "the central hub connecting different components of a language agent"; episodic — "stores experience from earlier decision cycles"; semantic — "stores an agent's knowledge about the world and itself"; procedural — LLM 가중치의 암묵지와 에이전트 코드의 형식지를 포함하며 "must be initialized by the designer". <https://arxiv.org/abs/2309.02427>
[^ctx-eng]: Anthropic, "Effective context engineering for AI agents": structured note-taking은 "the agent regularly writes notes persisted to memory outside of the context window. These notes get pulled back into the context window at later times."; Claude의 Pokémon 플레이 사례(컨텍스트 리셋 후 자기 노트로 멀티시간 시퀀스 지속); file 기반 memory tool의 public beta 공개. <https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents>
[^agentcore-types]: AWS, "AgentCore Memory types": 단기 계층은 `CreateEvent`/`ListSessions`/`ListEvents`/`GetEvent`로 원시 이벤트를 세션 단위 관리; 장기 계층은 "an asynchronous process that runs in the background"로 extraction과 consolidation을 수행하고 `RetrieveMemoryRecords`가 semantic search를 제공; 보존 대상은 "summaries of the conversations, facts and knowledge, or user preferences". <https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-types.html>
