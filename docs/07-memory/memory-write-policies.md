---
title: 메모리 쓰기 정책
description: 무엇을 언제 장기 메모리에 쓸 것인가 — 명시적 쓰기, 암묵적 추출, 세션 요약과 extraction→consolidation 파이프라인의 설계를 다룬다.
outline: [2, 3]
---

# 메모리 쓰기 정책

::: tip 이 장에서 얻는 것
- 메모리 쓰기의 세 경로 — **명시적 쓰기**(사용자 지시), **암묵적 추출**(자동 사실 추출), **세션 종료 요약** — 를 언제 어떻게 조합할지 판단하는 기준
- AgentCore Memory의 **extraction → consolidation 파이프라인** 동작 방식과 built-in / built-in overrides / self-managed 전략의 비용·제어권 트레이드오프
- 과잉 저장, 과소 저장, 모순 미해소라는 세 가지 정책 실패를 계측으로 잡아내는 방법
- 메모리 항목에 provenance(출처·시점) 태깅을 강제해야 하는 이유와 구현 위치
:::

## 왜 문제가 되는가

에이전트 메모리를 append-only 로그처럼 다루면 두 방향으로 동시에 망가진다. 모든 대화 턴을 장기 메모리로 승격하면 retrieval 시점에 노이즈가 신호를 압도하고, 사용자가 지나가듯 말한 민감 정보까지 세션 경계를 넘어 영속화되어 프라이버시 리스크가 된다. 반대로 쓰기를 지나치게 보수적으로 잡으면 "저는 P사의 SDK는 라이선스 문제로 못 씁니다" 같은 결정적 제약을 다음 세션에서 잊어버리고, 사용자는 같은 교정을 반복하게 된다.

더 까다로운 문제는 **모순**이다. 사용자의 사실은 시간에 따라 변한다("팀을 옮겼어요", "이제 us-west-2가 기본 리전입니다"). 새 사실을 쓰기만 하고 옛 사실을 무효화하지 않으면 retrieval이 두 버전을 함께 돌려주고, 에이전트는 어느 쪽을 믿을지 임의로 고른다. 즉 메모리 쓰기는 "저장할 것인가"의 단일 결정이 아니라, **추출(무엇이 사실인가) → 통합(기존 기억과 어떻게 합치는가) → 태깅(나중에 감사·정정이 가능한가)** 의 파이프라인 설계 문제다.

플랫폼 관점에서 이 결정을 각 에이전트 팀에 방치하면, 팀마다 다른 휴리스틱으로 메모리를 오염시키고 공유 메모리 스토어의 품질이 최저 팀 수준으로 수렴한다. 쓰기 정책은 플랫폼이 표준으로 제공해야 하는 계층이다.

## 핵심 개념

### 쓰기의 세 경로

**1. 명시적 쓰기 (explicit write).** 사용자가 "기억해줘", "다음부터는 이렇게 해줘"라고 지시하거나, 에이전트가 저장 여부를 되물어 확인받는 경로. 신뢰도가 가장 높고 프라이버시 동의가 내재되어 있다. AgentCore Memory의 semantic memory 추출 시스템 프롬프트도 "Remember that...", "Don't forget that..." 같은 명시적 기억 요청을 우선 추출 대상으로 명시한다([공식 문서](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-system-prompt.html)). 단점은 커버리지 — 사용자는 대부분의 유용한 사실을 명시적으로 저장 지시하지 않는다.

**2. 암묵적 추출 (implicit extraction).** 대화 스트림에서 LLM이 자동으로 사실·선호·패턴을 추출하는 경로. AgentCore Memory의 built-in 전략(semantic / summary / user preference)이 이 방식이며, Mem0·Zep 같은 오픈소스 메모리 계층도 같은 패턴이다 — Mem0는 extraction phase와 update phase(ADD/UPDATE/DELETE/NOOP 연산 결정)로 파이프라인을 명시적으로 분리한다([Mem0 논문, arXiv:2504.19413](https://arxiv.org/abs/2504.19413)). 커버리지는 높지만 추출 LLM의 판단 오류(과잉 추출, 잘못된 일반화)가 그대로 장기 메모리 품질이 된다.

**3. 세션 종료 요약 (session summary).** 세션 단위로 대화를 압축해 "이 세션에서 무엇을 했고 무엇이 결정됐는가"를 남기는 경로. AgentCore의 summarization 전략이 해당하며, MemGPT 계열 아키텍처가 context window 압박 시 수행하는 recursive summarization과 같은 계열이다([MemGPT, arXiv:2310.08560](https://arxiv.org/abs/2310.08560)). 사실 단위 retrieval에는 부적합하지만 "지난번에 어디까지 했죠?" 류의 세션 연속성에는 가장 효율적이다.

세 경로는 배타적이지 않다. 실무 기본형은 **암묵적 추출 + 세션 요약을 기본으로 깔고, 명시적 쓰기를 최고 신뢰도 등급으로 오버레이**하는 구성이다.

### Extraction → consolidation 파이프라인

AgentCore Memory 기준으로 파이프라인은 다음과 같이 동작한다([공식 문서: Memory types](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-types.html)):

1. 에이전트가 `CreateEvent`로 raw 대화 이벤트를 short-term memory에 저장한다(또는 `IngestData`로 직접 제출).
2. 장기 메모리 생성이 **비동기 백그라운드 프로세스**로 실행된다 — 라이브 응답 경로를 막지 않는다.
3. **Extraction**: raw 상호작용에서 정보를 추출한다.
4. **Consolidation**: 새로 추출된 정보를 기존 메모리 레코드와 통합한다 — 여기서 중복 제거와 기존 사실과의 병합이 일어난다.
5. 생성된 memory record는 `GetMemoryRecord` / `ListMemoryRecords` / `RetrieveMemoryRecords`(semantic search)로 조회한다.

설계 함의가 두 가지 있다. 첫째, **전략(strategy)을 하나도 설정하지 않으면 장기 메모리 레코드는 아예 생성되지 않는다**([공식 문서: Memory strategies](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-strategies.html)) — `CreateEvent`만 호출하고 "왜 장기 기억이 안 되지"라고 묻는 것이 가장 흔한 초기 설정 실수다. 둘째, 비동기이므로 **쓰기 직후 read-your-writes를 보장하지 않는다** — 같은 세션에서 방금 말한 사실은 장기 메모리가 아니라 short-term memory(raw 이벤트)에서 읽어야 한다.

### 추출 대상의 제약: 어떤 메시지가 파이프라인에 들어가는가

`CreateEvent`의 payload는 **conversational**(USER/ASSISTANT/TOOL 등 역할 기반 메시지)과 **blob**(체크포인트·에이전트 상태 등 바이너리) 두 타입이 있고, **장기 메모리 추출에는 conversational 이벤트만 사용된다**([AWS 공식 블로그](https://aws.amazon.com/blogs/machine-learning/amazon-bedrock-agentcore-memory-building-context-aware-agents/)). 또한 추출 시스템 프롬프트는 **user 메시지를 사실의 1차 소스로, assistant 메시지는 보조 컨텍스트로만** 취급하도록 지시한다([공식 문서: System prompt for semantic memory strategy](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-system-prompt.html)).

실무 함의: **툴 실행 결과에 담긴 사실은 자동으로 장기 메모리로 승격되지 않는다고 가정하고 설계하라.** 예컨대 DB 조회 툴이 반환한 "이 고객의 플랜은 Enterprise"라는 사실을 세션 넘어 기억시키고 싶다면, 그 사실을 USER/ASSISTANT 대화 턴으로 재서술해 `CreateEvent`에 넣거나(AWS 공식 예제도 인사이트를 USER 메시지로 감싸 저장하는 패턴을 쓴다 — [Deep Agents 블로그](https://aws.amazon.com/blogs/machine-learning/build-context-rich-research-agents-with-deep-agents-and-bedrock-agentcore/)), self-managed 전략으로 직접 처리해야 한다.

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요
> "추출 파이프라인이 USER/ASSISTANT 역할만 처리하고 TOOL 역할은 무시한다"는 진술은 초기 문서·커뮤니티 자료 기준이다. 현행 semantic 전략 시스템 프롬프트는 `<tool>` 요소와 JSON payload를 입력에 포함하도록 갱신되어 있어, 정확한 역할별 처리 범위는 배포 시점의 [공식 시스템 프롬프트 문서](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-system-prompt.html)로 재확인하라. blob 이벤트가 추출 대상에서 제외된다는 점은 일관되게 확인된다.

### 전략 선택: built-in vs built-in overrides vs self-managed

[공식 문서](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-strategies.html)가 구분하는 세 계층:

- **Built-in**: extraction과 consolidation을 AgentCore가 사전 정의된 알고리즘으로 전부 처리. 설정 최소, 커스터마이징 제한, 스토리지 비용은 세 옵션 중 가장 높음.
- **Built-in overrides**: AgentCore 관리형 추출 파이프라인을 쓰되 프롬프트를 수정할 수 있고, **Bedrock 모델이 고객 계정에서 호출된다**(모델 호출 비용이 고객 청구서에 잡힌다).
- **Self-managed**: extraction/consolidation 알고리즘, 모델, 프롬프트, 레코드 스키마 전부 고객 소유. 인프라 구축·운영 부담을 감수하는 대신 완전한 제어.

비용 구조를 정확히 하면: built-in 전략의 추출 추론은 서비스 관리형으로 실행되며, **cross-region inference로 처리되고 CRIS 사용 자체에는 추가 비용이 없다**([공식 문서: Cross-region inference](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/cross-region-inference.html)). CRIS 요청은 데이터가 원래 위치한 지리권(geography) 내 리전으로만 라우팅된다(미국 발 요청은 미국 리전 내 유지) — 단, 저장은 primary region에만 되지만 추론 중 입출력이 primary region 밖으로 이동할 수 있으므로 데이터 주권 요건이 있는 워크로드는 built-in with overrides로 모델 선택을 직접 관리해 CRIS를 우회할 수 있다(동일 문서). 한편 **장기 메모리 자체의 과금은 무료가 아니다**: 처리·저장되는 memory record 수(시간당 청구)와 retrieval 호출 수 기준으로 과금된다([AgentCore Pricing](https://aws.amazon.com/bedrock/agentcore/pricing/)). "빌트인 전략의 LLM 추론 비용이 별도 Bedrock 모델 호출로 청구되지 않는다"와 "장기 메모리가 공짜다"를 혼동하지 말 것.

### 신뢰도·출처 태깅 (provenance)

메모리 레코드에 최소한 다음을 남겨야 감사와 정정이 가능하다:

- **어느 대화에서**: `actorId` / `sessionId` / 원본 event 참조. AgentCore는 actor–session–namespace 계층으로 이를 구조화한다([공식 문서: Memory organization](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-organization.html)).
- **언제**: 추출 시각과 원 발화 시각. 추출 프롬프트도 temporal grounding(타임스탬프 기준 상대 시제 해석)을 지시한다([시스템 프롬프트 문서](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-system-prompt.html)).
- **어떤 경로로**: 명시적 지시였는지 암묵 추출이었는지. 명시적 쓰기 유래 레코드는 모순 발생 시 암묵 추출 레코드보다 우선해야 하며, 이 우선순위는 태그 없이는 구현할 수 없다.

Provenance는 프라이버시 대응의 전제이기도 하다 — "내 정보 지워줘" 요청을 받았을 때 어떤 레코드가 어느 대화에서 유래했는지 모르면 선별 삭제가 불가능하다. 삭제권·보존 기한·암호화는 [메모리 보안과 프라이버시](./memory-security-privacy)에서 다룬다.

### 쓰기 게이트: 무엇을 승격하지 않을 것인가

쓰기 정책의 절반은 배제 규칙이다. 최소 배제 목록:

- **자격 증명·시크릿**: 대화에 등장한 API key, 비밀번호는 추출 전 단계에서 마스킹. 추출 LLM의 판단에 맡기지 않는다.
- **일시적 상태**: "지금 CPU가 80%예요" 같은 시점성 사실. AWS 공식 사례도 semantic 전략이 transient metric을 건너뛰고 durable pattern만 보존하는 것을 올바른 동작으로 든다([RDS 운영 에이전트 블로그](https://aws.amazon.com/blogs/database/building-agentic-ai-for-amazon-rds-for-sql-server-with-strands-and-agentcore/)).
- **미검증 외부 입력**: 웹 검색 결과, 제3자 문서에서 온 주장은 사용자 사실과 같은 신뢰 등급으로 승격하면 안 된다. 오염된 입력이 장기 메모리로 승격되는 **memory poisoning** 공격 벡터의 정본 논의는 [메모리 보안과 프라이버시](./memory-security-privacy) 챕터를 보라 — 이 장에서는 "쓰기 게이트가 1차 방어선"이라는 점만 짚는다.

::: warning 미정착 영역
Consolidation에서의 모순 해소 수준 — "옛 사실을 자동으로 supersede하는가, 병존시키는가" — 는 구현체마다 다르고 공식 문서도 알고리즘 내부를 보증 수준으로 명세하지 않는다. AgentCore built-in의 consolidation이 특정 모순 케이스를 어떻게 처리하는지는 벤치마크로 직접 검증하고, 도메인 크리티컬한 사실(권한, 컴플라이언스 제약)은 LLM consolidation에 맡기지 말고 구조화된 스토어(예: DynamoDB의 사용자 프로필 테이블)에 명시적 upsert로 관리하는 이중 트랙이 현재로선 안전하다.
:::

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 표준 대화형 에이전트, 빠른 출시 | Built-in 전략 (semantic + summary) | 추출·consolidation 무설정, 벤치마크된 기본 알고리즘 | 커스터마이징 제한, 스토리지 단가 최고, CRIS로 지리권 내 리전 간 데이터 이동 |
| 도메인 특화 추출 규칙 필요 (예: 의료·법률 용어) | Built-in overrides | 관리형 파이프라인 유지하며 프롬프트 교체 | 모델 호출이 고객 계정 과금으로 이동, 프롬프트 품질 책임도 이동 |
| 데이터 주권/모델 선택 완전 통제, 자체 스키마 | Self-managed 전략 | 파이프라인 전체 소유, 외부 시스템 통합 가능 | 인프라 구축·운영, consolidation 품질 자체 책임 |
| 사용자가 "기억해줘"라고 말함 | 명시적 쓰기 + 최고 신뢰 등급 태깅 | 동의 내재, 모순 시 우선권 부여 근거 | 커버리지 낮음 — 단독 사용 불가 |
| 툴 결과의 사실을 세션 넘어 보존 | USER/ASSISTANT 턴으로 재서술 후 `CreateEvent`, 또는 self-managed 추출 | 관리형 추출은 conversational 이벤트 중심, blob 미처리 | 재서술 프롬프트 비용, 원문 대비 정보 손실 |
| "지난 세션 어디까지 했죠" 지원 | Summarization 전략 (사실 추출과 병행) | 세션 연속성엔 요약이 토큰 효율 최고 | 사실 단위 검색 불가 — semantic 전략 대체재 아님 |
| 방금 말한 사실을 같은 세션에서 참조 | Short-term memory (raw event) 직접 조회 | 장기 추출은 비동기 — read-your-writes 미보장 | 세션 종료 후엔 event expiry에 따라 소멸 |
| 권한·컴플라이언스 등 크리티컬 제약 | LLM 추출 대신 구조화 스토어에 명시적 upsert | 모순 해소를 확률적 파이프라인에 맡길 수 없음 | 스키마 관리 비용, 메모리 계층 이원화 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| retrieval 결과가 잡동사니 — 관련 없는 기억이 프롬프트를 점유 | 과잉 저장: 쓰기 게이트 없이 모든 턴 승격 | memory record 증가율 vs 실제 retrieval hit에서 사용된 레코드 비율 측정 | 추출 프롬프트에 배제 규칙 추가(overrides), 일시적 상태·잡담 필터링 |
| 사용자가 알려준 제약을 다음 세션에서 재질문 | 과소 저장: 전략 미설정이거나 추출 기준 과도하게 보수적 | 해당 memory에 전략이 붙어 있는지 확인 — 전략 없으면 LTM 레코드 자체가 생성 안 됨 | 전략 추가(`CreateMemory`/`UpdateMemory`), 추출 recall 벤치마크 |
| 옛 사실과 새 사실이 함께 retrieval됨 | consolidation이 supersede 대신 병존 선택, 또는 서로 다른 namespace에 중복 저장 | 동일 주제 레코드를 `ListMemoryRecords`로 나열해 모순 쌍 탐지 | 크리티컬 사실은 구조화 스토어로 이관, timestamp 기반 우선순위를 retrieval 단에서 강제 |
| 장기 기억이 "가끔만" 안 됨 | 추출 job의 산발적 실패 — 실패 이벤트가 미처리로 누적 | 실패 추출 잔량 확인 후 `StartMemoryExtractionJob`으로 재처리([API 문서](https://docs.aws.amazon.com/powershell/v4/reference/index.html?page=Start-BACMemoryExtractionJob.html&tocid=Start-BACMemoryExtractionJob)) | 재추출 job 정기 실행, 추출 실패율 알람 |
| 방금 저장한 사실이 즉시 검색 안 됨 | 비동기 추출을 동기로 오해 | 이벤트 생성 직후 `RetrieveMemoryRecords` 호출 타이밍 확인 | 세션 내 참조는 short-term memory로, LTM은 eventual로 설계 |
| 툴이 조회한 사실이 다음 세션에 없음 | blob/툴 결과는 관리형 추출 파이프라인의 1차 대상이 아님 | 해당 사실이 conversational 이벤트로 저장됐는지 이벤트 payload 검사 | 사실을 USER/ASSISTANT 턴으로 재서술해 저장 |
| 삭제 요청에 어떤 레코드를 지울지 특정 불가 | provenance 태깅 부재 | 레코드에 세션/시각/경로 메타데이터 존재 여부 감사 | 쓰기 시점에 actorId/sessionId/추출경로 태깅 의무화 |
| 조작된 입력이 장기 기억으로 굳음 | memory poisoning — 쓰기 게이트에서 외부 입력 신뢰 등급 미분리 | [메모리 보안과 프라이버시](./memory-security-privacy) 참조 | 동 챕터의 방어 체계 적용 |

## 안티패턴

- ❌ 전략 없이 `CreateEvent`만 호출하고 장기 기억을 기대 → ✅ `CreateMemory` 시점에 최소 semantic 또는 summary 전략을 명시하고, 전략 없는 memory 리소스를 린트로 차단
- ❌ 모든 대화 턴을 "혹시 필요할지 몰라" 승격 → ✅ 배제 규칙(시크릿·일시 상태·미검증 외부 입력)을 먼저 정의하고, 저장량이 아니라 retrieval 유효 hit로 정책을 평가
- ❌ 새 사실을 쓰면 옛 사실이 알아서 사라진다고 가정 → ✅ consolidation의 모순 해소를 벤치마크로 검증하고, 크리티컬 사실은 구조화 스토어 upsert로 이원화
- ❌ 명시적 "기억해줘"와 암묵 추출 결과를 같은 신뢰 등급으로 저장 → ✅ 쓰기 경로를 태그로 구분하고 모순 시 명시적 쓰기 우선
- ❌ 툴 결과를 저장했으니 기억될 것이라 가정 → ✅ conversational 이벤트로 재서술하거나 self-managed 추출로 명시 처리
- ❌ 쓰기 직후 장기 메모리 조회로 통합 테스트 작성(간헐 실패 플레이크 양산) → ✅ 비동기 추출을 전제로 폴링+타임아웃 또는 short-term 검증으로 테스트 설계
- ❌ provenance 없이 텍스트만 저장 → ✅ 세션·시각·경로 메타데이터를 스키마 필수 필드로 강제

## 계측 (SLI)

쓰기 정책은 계측 없이는 튜닝할 수 없다. 최소 SLI 세트:

- **추출 지연 (extraction lag)**: `CreateEvent` 시각 → memory record 생성 시각. 비동기 파이프라인의 체감 신선도를 결정. p50/p99로 추적.
- **추출 실패율**: 실패로 남은 이벤트 비율. `StartMemoryExtractionJob` 재처리 대상 잔량을 주기적으로 확인하고 임계 초과 시 알람.
- **저장 증가율 대비 유효 hit율**: 월간 신규 memory record 수(과금 지표이기도 하다 — LTM은 processed/stored 레코드 수로 청구, [Pricing](https://aws.amazon.com/bedrock/agentcore/pricing/)) 대비, retrieval 결과 중 실제 응답에 기여한 레코드 비율. 증가율만 오르고 hit율이 떨어지면 과잉 저장 신호.
- **모순 잔존율**: 샘플링한 actor의 동일 주제 레코드 중 상호 모순 쌍 비율. consolidation 품질의 직접 지표 — 오프라인 배치로 주기 측정.
- **재질문율 (re-ask rate)**: 이미 저장됐어야 할 정보를 사용자에게 다시 묻는 빈도. 과소 저장의 사용자 체감 지표로, 대화 로그에서 LLM-judge로 샘플 평가.
- **provenance 커버리지**: 필수 메타데이터가 결락된 레코드 비율. 목표 100%.

AgentCore 배포라면 이 SLI들을 CloudWatch 커스텀 메트릭으로 발행하고, 플랫폼 공통 대시보드에 에이전트별로 태깅해 팀 간 정책 품질을 비교 가능하게 만든다. 토큰·트레이스 계측의 일반론은 관측성 파트에서 다루므로 여기서는 쓰기 정책 고유 지표만 짚었다.

## 체크리스트

- [ ] 메모리 리소스 생성 시 전략(semantic/summary/user preference 중 최소 1개)이 명시되어 있는가 — 전략 없는 리소스는 LTM을 만들지 않는다
- [ ] 명시적 쓰기 / 암묵 추출 / 세션 요약 각각의 역할과 신뢰 등급이 문서화되어 있는가
- [ ] 배제 규칙(시크릿, 일시적 상태, 미검증 외부 입력)이 추출 이전 단계에서 강제되는가
- [ ] 툴 결과 유래 사실의 승격 경로(재서술 또는 self-managed)가 정의되어 있는가
- [ ] 모든 레코드에 actorId·sessionId·시각·쓰기 경로 태그가 있는가 (삭제 요청 대응 가능 여부로 검증)
- [ ] 크리티컬 사실(권한·컴플라이언스)은 LLM consolidation이 아닌 구조화 스토어로 관리되는가
- [ ] read-your-writes 미보장을 전제로 세션 내 참조가 short-term memory를 사용하는가
- [ ] 추출 실패 이벤트의 재처리(`StartMemoryExtractionJob`) 루틴과 알람이 있는가
- [ ] 데이터 주권 요건이 있다면 CRIS 라우팅 범위를 검토하고 필요 시 overrides로 우회했는가
- [ ] SLI(추출 지연, 실패율, 유효 hit율, 모순 잔존율, 재질문율)가 대시보드에 있는가
- [ ] memory poisoning 방어가 [메모리 보안과 프라이버시](./memory-security-privacy) 기준으로 적용되어 있는가

## 참고

- [Memory types — Amazon Bedrock AgentCore Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-types.html) — extraction/consolidation 파이프라인, 비동기 동작
- [Memory strategies — Amazon Bedrock AgentCore Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-strategies.html) — built-in / overrides / self-managed 비교
- [System prompt for semantic memory strategy](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-system-prompt.html) — 추출 프롬프트 원문, 역할별 처리
- [Memory organization in AgentCore Memory](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-organization.html) — actor/session/namespace 계층
- [Cross-region inference in AgentCore Memory](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/cross-region-inference.html) — CRIS 무추가비용, 지리권 제한, overrides 우회
- [Amazon Bedrock AgentCore Pricing](https://aws.amazon.com/bedrock/agentcore/pricing/) — STM/LTM 과금 구조
- [Amazon Bedrock AgentCore Memory: Building context-aware agents (AWS ML Blog)](https://aws.amazon.com/blogs/machine-learning/amazon-bedrock-agentcore-memory-building-context-aware-agents/) — conversational vs blob 이벤트
- [Building agentic AI for Amazon RDS for SQL Server with Strands and AgentCore (AWS Database Blog)](https://aws.amazon.com/blogs/database/building-agentic-ai-for-amazon-rds-for-sql-server-with-strands-and-agentcore/) — transient metric 배제 사례
- [Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory (arXiv:2504.19413)](https://arxiv.org/abs/2504.19413) — extraction/update 2단 파이프라인
- [MemGPT: Towards LLMs as Operating Systems (arXiv:2310.08560)](https://arxiv.org/abs/2310.08560) — 계층적 메모리와 요약 승격
