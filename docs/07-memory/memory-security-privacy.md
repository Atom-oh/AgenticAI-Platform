---
title: 메모리 보안과 프라이버시
description: 메모리 포이즈닝의 지속성 공격 모델과 방어(승격 게이트·출처 추적·격리), PII 수명 관리와 잊힐 권리 이행 절차, KMS 암호화·접근 제어를 다룬다.
outline: [2, 3]
---

# 메모리 보안과 프라이버시

::: tip 이 장에서 얻는 것
- 메모리 포이즈닝이 일반 프롬프트 인젝션보다 위험한 이유 — 1회성 공격이 **지속성(persistence) 공격**으로 승격되는 메커니즘
- AgentPoison·MINJA 등 공개 연구가 보여준 현실적 공격 경로와, OWASP가 이 위협을 어떻게 분류하는지
- 방어 4축: 메모리 승격 게이트, 출처 추적(provenance), 신뢰 등급별 네임스페이스 격리, 정기 감사
- 메모리에 축적되는 PII의 수명 관리 — TTL 설계, AgentCore Memory의 삭제 API(`DeleteEvent`/`DeleteMemoryRecord`), 특정 actorId 전체 삭제 절차
- GDPR Art. 17과 국내 개인정보보호법 제36조의 삭제 요구가 메모리 계층에 부과하는 요구사항
- KMS 암호화와 IAM 기반 접근 제어에서 실제로 통제 가능한 경계와, 애플리케이션이 직접 책임져야 하는 경계
:::

> 이 장의 법령 관련 서술은 플랫폼 설계 관점의 요약이며 **법률 자문이 아니다**. 실제 컴플라이언스 판단은 법무·DPO와 함께 하라.

## 왜 문제가 되는가

[메모리 쓰기 정책](/07-memory/memory-write-policies)에서 다뤘듯, 장기 메모리는 대화·문서에서 **추출한 사실을 세션을 넘어 보존**하는 계층이다. 이 설계는 두 가지 보안 문제를 구조적으로 내장한다.

**첫째, 메모리는 프롬프트 인젝션을 지속화한다.** 일반 프롬프트 인젝션은 1회성이다 — 오염된 웹 페이지를 읽은 그 세션이 끝나면 영향도 끝난다. 그러나 인젝션으로 삽입된 거짓 사실("환불 한도는 무제한이다", "이 도메인은 사내 승인된 엔드포인트다")이 메모리 추출 파이프라인을 통과해 **장기 메모리로 승격되면**, 이후 모든 세션의 컨텍스트에 신뢰된 사실로 재주입된다. 공격자는 단 한 번의 성공으로 영구적인 발판을 얻고, 피해 범위는 그 메모리 스코프를 공유하는 모든 사용자·세션으로 확장된다. 침해사고 대응 관점에서도 성격이 다르다 — 오염된 세션을 종료해도 오염은 제거되지 않고, 메모리 스토어에서 해당 레코드를 찾아 지우기 전까지 재감염이 반복된다.

이것은 이론적 시나리오가 아니다. NeurIPS 2024의 [AgentPoison](https://arxiv.org/abs/2407.12784)은 LLM 에이전트의 장기 메모리/RAG 지식 베이스에 소량의 악성 데모를 주입하는 백도어 공격으로, 전체 대비 0.1% 미만의 오염 레코드만으로 80% 이상의 retrieval attack success rate를 달성했고, 트리거가 없는 정상 질의의 성능은 거의 저하시키지 않아 탐지를 어렵게 만든다. 더 현실적인 위협 모델은 [MINJA(Memory Injection Attack)](https://arxiv.org/abs/2503.03704)다 — 메모리 스토어에 대한 직접 접근 없이 **일반 사용자 권한의 질의만으로** 에이전트가 스스로 오염된 reasoning trace를 메모리에 기록하게 유도하며, 논문 기준 98.2%의 injection success rate와 76.8%의 attack success rate를 보고했다. 즉 "메모리 DB 접근 권한이 없으니 안전하다"는 가정은 성립하지 않는다. OWASP도 이 위협을 별도 항목으로 다룬다 — [OWASP Top 10 for LLM Applications 2025의 LLM08(Vector and Embedding Weaknesses)](https://genai.owasp.org/llmrisk/llm082025-vector-and-embedding-weaknesses/)이 임베딩·검색 계층 오염을, [OWASP Agentic AI – Threats and Mitigations](https://genai.owasp.org/resource/agentic-ai-threats-and-mitigations/)의 T1이 Memory Poisoning을 명시한다.

**둘째, 메모리는 PII 축적기다.** 메모리 추출 전략(user preference, semantic fact)은 본질적으로 "사용자에 대한 사실"을 모으는 장치이고, 그 사실에는 이름·연락처·건강 상태·재무 정보가 섞여 들어온다. 원본 대화는 TTL로 만료되어도 **추출된 장기 메모리 레코드는 별도 수명을 가지므로**, 삭제 요구가 들어왔을 때 원본만 지우면 파생 레코드가 남는다. 이는 [벡터 인덱스의 삭제 전파](/06-vector-search/index-freshness-migration)와 정확히 같은 구조의 문제다 — 파생 저장소(derived data)는 삭제 의무의 예외가 아니다. GDPR Art. 17(right to erasure)과 개인정보보호법 제36조(정정·삭제 요구)는 저장 형태를 가리지 않는다.

두 문제의 공통 뿌리는 하나다: **메모리 쓰기는 권한 경계를 넘는 데이터 이동**이라는 사실을 설계가 인식하지 않는 것. untrusted 입력(사용자 발화, 웹 콘텐츠, 도구 출력)이 trusted 컨텍스트(이후 세션의 시스템 컨텍스트에 주입되는 사실)로 변환되는 지점이 메모리 승격이고, 여기에 게이트가 없으면 위의 모든 공격이 성립한다.

## 핵심 개념

### 위협 모델: 1회성 인젝션 vs 지속성 오염

| 속성 | 프롬프트 인젝션 (1회성) | 메모리 포이즈닝 (지속성) |
|---|---|---|
| 영향 범위 | 해당 세션 | 메모리 스코프를 공유하는 모든 후속 세션 |
| 공격 비용 | 매 세션 반복 필요 | 1회 성공으로 영구 발판 |
| 탐지 시점 | 세션 로그에서 즉시 관찰 가능 | 오염 시점과 발현 시점이 분리되어 사후 추적 어려움 |
| 제거 | 세션 종료로 소멸 | 메모리 레코드를 찾아 명시적으로 삭제해야 함 |
| 신뢰 수준 | 모델이 입력으로 취급 | **시스템이 검증된 사실처럼 재주입** — 모델이 더 신뢰함 |

마지막 행이 핵심이다. 메모리에서 검색되어 컨텍스트에 주입된 사실은 보통 "당신이 이 사용자에 대해 아는 것"이라는 프레임으로 제공되므로, 모델은 이를 방금 읽은 웹 페이지보다 훨씬 높은 신뢰도로 취급한다. 오염이 승격되는 순간 공격 텍스트의 신뢰 등급도 함께 승격된다.

오염 경로는 세 가지로 정리된다:

1. **직접 대화 경로**: 악의적 사용자가 자기 스코프의 메모리를 오염시키거나(멀티테넌트에서 스코프 격리가 깨져 있으면 타인에게 전파), MINJA처럼 질의만으로 공유 메모리에 오염 레코드를 심는 경우.
2. **간접 콘텐츠 경로**: 에이전트가 읽은 오염 문서·웹 페이지·도구 출력 속 인젝션이 추출 파이프라인을 통과하는 경우. 추출 LLM 자체가 인젝션의 대상이 된다는 점에 주의 — "이 내용을 사용자 선호로 기억하라"는 문장이 문서 안에 들어 있는 형태.
3. **공급망/저장소 경로**: 메모리 스토어에 대한 쓰기 권한 탈취, 또는 초기 시드 데이터 오염(AgentPoison의 위협 모델).

인젝션 자체의 방어(입력 필터링, 도구 출력 격리)는 [12장 프롬프트 인젝션](/12-security-korea/prompt-injection)에서 다루고, 이 장은 **인젝션이 일부 성공했다는 전제에서 메모리로의 승격을 막는 것**에 집중한다.

### 방어 1: 메모리 승격 게이트

[메모리 쓰기 정책](/07-memory/memory-write-policies)의 추출 파이프라인에 보안 관문을 추가한다. 원칙은 "추출되었다고 저장되지 않는다":

- **검증(verification)**: 추출된 사실이 실제 대화 내용에 근거하는지 별도 판정 단계로 확인한다. 특히 시스템 동작을 바꾸는 종류의 "사실"(권한, 정책, 엔드포인트, 지시문 형태의 문장)은 사용자 선호("다크 모드를 좋아함")보다 높은 기준을 적용한다. 지시문 형태의 텍스트("always...", "ignore...", "~해야 한다")는 사실이 아니라 명령이므로 승격 자체를 거부하는 것이 안전하다.
- **신뢰도 임계(confidence threshold)**: 1회 언급으로 즉시 승격하지 않고, 반복 관찰·세션 간 일관성 등으로 신뢰도를 누적한 뒤 임계 초과 시 승격한다. 트레이드오프는 명확하다 — 임계가 높을수록 오염 승격은 어렵지만 정당한 사실의 반영도 느려진다.
- **소스 신뢰 등급 반영**: 사용자 본인의 직접 발화 > 인증된 시스템의 도구 출력 > 외부 웹 콘텐츠 순으로 승격 요건을 차등화한다. 외부 콘텐츠에서 "사용자에 대한 사실"이 추출되는 것 자체가 이상 신호다.

### 방어 2: 출처 추적 (provenance)

모든 메모리 레코드에 **어느 세션의 어느 입력에서 추출되었는지**를 메타데이터로 남긴다: 원본 세션 ID, 이벤트/턴 ID, 소스 유형(사용자 발화/도구 출력/문서), 추출 시각, 추출에 사용한 전략. 이것이 있어야 가능한 것들:

- **오염 발견 시 역추적과 일괄 소독**: 오염된 세션 하나를 특정하면 그 세션에서 파생된 모든 레코드를 찾아 삭제할 수 있다. provenance가 없으면 어떤 레코드가 오염됐는지 알 방법이 전수 감사뿐이다.
- **삭제 전파의 완전성**: 원본 이벤트 삭제 시 파생 레코드를 함께 지울 수 있다(아래 PII 절).
- **감사 시 우선순위**: 신뢰 등급이 낮은 소스에서 온 레코드부터 검사한다.

AgentCore Memory를 쓴다면 이벤트(단기)와 memory record(장기)가 분리된 리소스이므로, record가 어느 이벤트들에서 추출되었는지의 연결은 애플리케이션 레벨에서 유지해야 하는 설계 항목이다.

### 방어 3: 신뢰 등급별 네임스페이스 격리

untrusted 콘텐츠에서 추출된 사실을 사용자 직접 발화에서 추출된 사실과 **같은 네임스페이스에 섞지 않는다**. [메모리 검색 스코핑](/07-memory/memory-retrieval-scoping)의 스코프 설계에 신뢰 축을 추가하는 것이다:

- `/{actorId}/facts/verified` — 승격 게이트를 통과한 사실. 시스템 컨텍스트로 주입 가능.
- `/{actorId}/facts/unverified` — 외부 콘텐츠 유래 등 낮은 신뢰 등급. 주입 시 "미검증 출처" 라벨을 붙이거나, 행동 결정(도구 호출 파라미터 등)의 근거로는 사용 금지.

여기에 멀티테넌트 격리가 전제된다: actorId 간 메모리 격리가 깨지면 한 사용자의 오염이 조직 전체로 퍼진다. 공유 메모리(팀 지식 등)는 오염의 blast radius가 가장 큰 자산이므로 승격 게이트를 가장 엄격하게 적용해야 한다.

### 방어 4: 정기 감사

메모리는 축적형 자산이므로 쓰기 시점 방어만으로는 부족하다 — 게이트 도입 이전의 레거시 레코드, 게이트를 우회한 레코드가 남는다. 주기적으로: (1) 지시문 패턴·URL·비정상 길이 레코드 스캔, (2) 신규 승격 레코드 샘플링 검토, (3) provenance 무결성 확인(출처 없는 레코드는 그 자체가 침해 지표), (4) [OWASP Agentic AI T1의 완화책](https://genai.owasp.org/resource/agentic-ai-threats-and-mitigations/)이 권고하는 메모리 스냅샷 롤백 지점 유지 — 오염 확인 시 특정 시점 이전으로 복원할 수 있어야 한다.

### PII 수명 관리와 잊힐 권리

법적 배경을 먼저 요약한다. [GDPR Art. 17](https://gdpr-info.eu/art-17-gdpr/)의 right to erasure는 정보주체 요구 시 지체 없는(undue delay 없는) 삭제를 요구하고, [개인정보보호법 제36조](https://www.law.go.kr/법령/개인정보보호법/제36조)는 정보주체의 정정·삭제 요구에 대해 지체 없이 조치하고 결과를 통지할 것, 그리고 삭제 시 **복구·재생되지 아니하도록** 조치할 것을 요구한다. 국내 규제 전반(금융권 특수성 포함)은 [12장 한국 규제 환경](/12-security-korea/korea-fsc-regulation)에서 다룬다. 메모리 설계에 떨어지는 요구사항은 세 가지다.

**1) TTL 설계 — 기본은 만료다.** 보존 기간이 정당화되지 않는 데이터는 자동 만료시킨다. AgentCore Memory는 단기 메모리(이벤트)에 `eventExpiryDuration`(1–365일)을 지원한다([Memory API Reference](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_Memory.html)). 주의할 함정: **이벤트 TTL은 장기 memory record에 적용되지 않는다.** 추출된 record는 원본 이벤트가 만료된 뒤에도 남는 것이 장기 메모리의 존재 이유이므로, 장기 레코드의 수명 정책(주기적 relevance 검토, 최종 접근 기반 만료 등)은 별도로 설계해야 한다. "원본은 30일 만료니까 PII도 30일이면 사라진다"는 가정은 틀렸다.

**2) 삭제 API 경로.** AgentCore Memory 데이터 플레인은 [`DeleteEvent`](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_DeleteEvent.html)(단기 이벤트, `memoryId`/`actorId`/`sessionId`/`eventId`로 지정)와 [`DeleteMemoryRecord`](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_DeleteMemoryRecord.html)(장기 레코드, `memoryRecordId`로 지정)를 제공하며, 두 API 모두 영구 삭제다. 자체 구축 메모리(DynamoDB + 벡터 스토어 등)라면 이벤트 저장소·추출 레코드·벡터 인덱스·캐시의 4개 계층 각각에 삭제 경로가 있는지 확인하라.

**3) actorId 전체 삭제 절차 (잊힐 권리 이행).** 삭제 API가 개별 이벤트/레코드 단위이므로, "사용자 X의 모든 것을 지워라"는 열거→삭제의 조합으로 구현한다:

1. 해당 `actorId`의 세션을 열거하고(List 계열 API), 각 세션의 이벤트를 열거해 `DeleteEvent`로 삭제.
2. 해당 actor의 네임스페이스(`/{actorId}/...` 프리픽스) 하위 memory record를 열거해 `DeleteMemoryRecord`로 삭제. 네임스페이스에 actorId가 포함되도록 설계해 두었다면 이 단계가 프리픽스 조회 하나로 끝난다 — **잊힐 권리 이행 비용은 네임스페이스 설계 시점에 결정된다.**
3. 메모리 밖 사본 정리: 대화 로그 아카이브, 관측 트레이스(LLM 호출 로그에 대화 원문이 남는다), 시맨틱 캐시, 백업. 이 목록을 절차서에 명시하지 않으면 반드시 누락된다.
4. **삭제 검증**: 삭제 후 해당 actorId로 retrieval을 실행해 0건임을 확인하고 증적을 남긴다. 개인정보보호법 제36조의 "복구·재생 방지" 관점에서, 소프트 삭제(플래그만 변경)로 이행을 갈음할 수 있는지는 법무 검토 사항이다.

::: warning 미정착 영역 — 백업·모델 내부의 삭제
관리형 서비스의 내부 백업/복제본에서 삭제가 언제 완결되는지, 그리고 메모리 내용이 이미 컨텍스트로 주입되어 생성된 과거 출력·파인튜닝된 모델에 대한 삭제 의무의 범위는 규제 해석이 정착되지 않은 영역이다. 계약(DPA)과 서비스 문서에서 삭제 시멘틱스를 확인하고, 불확실하면 보수적으로(애초에 덜 저장하는 쪽으로) 설계하라.
:::

### 암호화와 접근 제어

**저장 암호화**: AgentCore Memory는 기본적으로 AWS owned KMS key로 저장 데이터를 암호화하며, 메모리 생성 시 `encryptionKeyArn`으로 customer managed key(CMK)를 지정할 수 있다([공식 문서](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/storage-encryption.html)). CMK의 실익은 암호화 자체보다 **통제와 감사**다 — 키 정책으로 접근 주체를 한 번 더 좁히고, CloudTrail로 복호화 이벤트를 추적하고, 유사시 키 비활성화로 데이터 접근을 일괄 차단할 수 있다. PII가 축적되는 메모리라면 CMK가 기본값이어야 한다.

**접근 제어의 실제 경계**: IAM은 API 액션(`bedrock-agentcore:RetrieveMemoryRecords`, `DeleteEvent` 등)과 메모리 리소스 ARN 수준에서 권한을 통제한다. 즉 "이 role은 이 메모리 스토어를 읽을 수 있다"까지가 IAM의 기본 통제 단위다. **"이 호출자는 자신의 actorId 메모리만 읽을 수 있다"는 테넌트 격리는 애플리케이션 계층의 책임**으로 남는 경우가 일반적이다 — 에이전트 런타임이 인증된 사용자 ID에서 actorId/네임스페이스를 도출하고, 사용자 입력이 actorId 파라미터에 영향을 주지 못하게 해야 한다(사용자가 지정한 값을 그대로 actorId로 쓰는 순간 수평 권한 상승이 열린다). 서비스가 제공하는 IAM condition key로 actorId·네임스페이스 수준 제어가 가능한지는 [Service Authorization Reference](https://docs.aws.amazon.com/service-authorization/latest/reference/)에서 해당 서비스의 condition key 목록을 확인하라.

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요: AgentCore Memory가 resource-based policy를 지원해 크로스 계정 접근 통제가 가능하다는 실무 리포트가 있다([DevelopersIO](https://dev.classmethod.jp/en/articles/bedrock-agentcore-memory-resource-based-policy/)). 도입 전 AgentCore 공식 문서의 지원 여부와 정책 문법을 확인하라.

읽기 경로의 최소 권한도 잊지 말 것: 관측·디버깅 도구, 오프라인 평가 파이프라인, 관리 콘솔 접근자가 메모리 전체를 읽을 수 있다면 그것이 실질적 PII 유출 표면이다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 사용자 직접 발화에서 선호·사실 추출 | 승격 게이트(검증 + 반복 관찰 임계) 후 verified 네임스페이스 | 가장 신뢰도 높은 소스지만 MINJA류 질의 기반 오염이 가능 | 정당한 사실의 반영 지연 |
| 웹·문서 등 외부 콘텐츠에서 사실 추출 | 별도 unverified 네임스페이스 + 행동 근거 사용 금지 | untrusted → trusted 승격이 지속성 공격의 본체 | 메모리 활용도 저하, 스코프 관리 복잡도 |
| 지시문 형태 텍스트("always X하라")의 승격 요청 | 승격 거부 | 사실이 아니라 명령 — 저장되면 영구 인젝션 | 정당한 운영 지침도 메모리로는 못 다룸(설정으로 다뤄야 함) |
| 팀·조직 공유 메모리 | 가장 엄격한 게이트 + 승격 승인 워크플로 검토 | blast radius가 전체 조직 | 공유 지식 축적 속도 저하 |
| PII 포함 가능성이 있는 메모리 스토어 | CMK 암호화 + actorId 포함 네임스페이스 + 삭제 절차 문서화 | 삭제 요구 이행 비용은 설계 시점에 결정됨 | 키 관리 운영 부담 |
| 세션 원문 보존 기간 | `eventExpiryDuration` 최소화(추출 파이프라인이 필요한 기간만) | 원문은 PII 밀도가 가장 높은 계층 | 만료 후 재추출·감사 불가 |
| 오염 의심 사고 발생 | provenance 역추적 → 파생 레코드 일괄 삭제 → 스냅샷 복원 검토 | 세션 종료로는 오염이 제거되지 않음 | provenance를 안 남겼다면 전수 감사뿐 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 에이전트가 세션마다 같은 거짓 사실을 반복 주장 | 오염된 레코드가 장기 메모리로 승격됨 | 해당 주장으로 memory retrieval 실행, 레코드의 provenance 확인 | 레코드 삭제 + 원본 세션 파생분 일괄 소독 + 승격 게이트 보강 |
| 특정 트리거 문구가 포함된 질의에서만 비정상 행동 | AgentPoison류 백도어 — 트리거가 오염 레코드의 retrieval을 유도 | 트리거 유무만 다른 질의 쌍으로 retrieval 결과 diff | 오염 레코드 제거, 임베딩 이상치(고립 클러스터) 스캔 |
| 삭제 요구 이행 후에도 에이전트가 해당 사용자 정보를 언급 | 원본 이벤트만 삭제, 파생 record·캐시·트레이스 잔존 | 해당 actorId로 retrieval + 캐시·로그 조회 | 4계층(이벤트/레코드/캐시/로그) 삭제 절차로 재이행, 삭제 검증 자동화 |
| TTL을 설정했는데 오래된 PII가 계속 검색됨 | 이벤트 TTL이 장기 record에 미적용, 또는 TTL 변경이 기존 이벤트에 소급되지 않음 | record의 생성 시각 확인, `eventExpiryDuration` 변경 이력 대조 | 장기 record 별도 수명 정책 수립, 기존 데이터는 명시 삭제 |
| 사용자 A의 대화에 사용자 B의 선호가 반영됨 | actorId/네임스페이스 격리 붕괴 — 사용자 입력이 actorId에 영향 | retrieval 호출 로그에서 actorId 파라미터의 유래 추적 | actorId를 인증 컨텍스트에서만 도출, 격리 테스트를 CI에 추가 |
| 감사에서 출처 불명 레코드 다수 발견 | provenance 미기록 또는 게이트 우회 쓰기 경로 존재 | 쓰기 경로 전수 조사(직접 API 호출 포함) | 모든 쓰기를 게이트 경유로 강제(IAM으로 직접 쓰기 차단), 레거시는 재검증 또는 폐기 |

## 안티패턴

- ❌ 추출 LLM의 출력을 그대로 저장 → ✅ 검증·임계·소스 등급을 거치는 승격 게이트를 통과한 것만 저장
- ❌ 모든 사실을 하나의 네임스페이스에 저장 → ✅ 신뢰 등급(verified/unverified)과 actorId로 네임스페이스 분리
- ❌ "메모리 DB에 쓰기 권한이 없으니 포이즈닝 불가" → ✅ MINJA가 보여줬듯 질의만으로 오염 가능 — 쓰기 파이프라인 자체를 방어
- ❌ 삭제 요구를 원본 대화 로그 삭제로 종결 → ✅ 이벤트·장기 레코드·캐시·관측 로그 4계층 삭제 + 검증 쿼리
- ❌ 이벤트 TTL 하나로 PII 수명 관리 완료 선언 → ✅ 장기 레코드의 별도 수명 정책
- ❌ 사용자 요청 파라미터의 ID를 그대로 actorId로 사용 → ✅ 인증 컨텍스트에서만 actorId 도출
- ❌ 오염 발견 시 해당 레코드 1건만 삭제 → ✅ provenance로 동일 출처 파생분 전체 소독 + 유입 경로 차단

## 계측 (SLI)

- **승격 게이트 거부율**: 추출된 후보 중 게이트에서 기각된 비율. 급락은 게이트 우회/오작동, 급등은 인젝션 시도 증가의 신호일 수 있다. 소스 유형별로 분리 계측.
- **미검증 소스 유래 주입 비율**: 컨텍스트에 주입된 메모리 중 unverified 네임스페이스 출신 비율 — 0이어야 하는 경로(도구 호출 근거 등)에서 0인지 감시.
- **삭제 요구 이행 시간(erasure lag)**: 삭제 요구 접수부터 검증 쿼리 0건 확인까지. 규제 대응 SLO의 근거 지표이며, [벡터 인덱스의 deletion propagation lag](/06-vector-search/index-freshness-migration)과 함께 관리.
- **삭제 검증 실패율**: 삭제 절차 실행 후 검증 쿼리에서 잔존 레코드가 발견된 비율. 0이 아니면 절차에 누락 계층이 있다.
- **provenance 커버리지**: 전체 장기 레코드 중 출처 메타데이터가 완전한 비율. 100% 미만이면 사고 시 역추적 불가 구간이 존재.
- **레코드 연령 분포**: 장기 레코드의 생성 후 경과 시간 분포. 꼬리가 길어지면 수명 정책이 작동하지 않는 것.
- **CMK 복호화 이상 감지**: CloudTrail의 KMS Decrypt 이벤트에서 평소 없던 주체·볼륨 — 메모리 대량 열람의 조기 신호.

## 체크리스트

- [ ] 메모리 승격 게이트가 존재하고, 게이트를 우회하는 쓰기 경로(직접 API 호출 포함)가 IAM으로 차단되어 있다
- [ ] 지시문 형태 텍스트의 승격이 거부된다 (테스트 케이스로 검증)
- [ ] untrusted 콘텐츠 유래 사실은 별도 네임스페이스에 저장되고, 행동 결정의 근거로 사용되지 않는다
- [ ] 모든 장기 레코드에 원본 세션·이벤트·소스 유형의 provenance가 기록된다
- [ ] 네임스페이스에 actorId가 포함되어 "특정 사용자 전체 삭제"가 프리픽스 조회로 가능하다
- [ ] actorId는 인증 컨텍스트에서만 도출되며, 크로스 테넌트 격리 테스트가 CI에 있다
- [ ] `eventExpiryDuration`(또는 자체 TTL)이 명시 설정되어 있고, 장기 레코드의 별도 수명 정책이 문서화되어 있다
- [ ] 삭제 절차가 이벤트·장기 레코드·캐시·관측 로그 4계층을 모두 커버하고, 삭제 후 검증 쿼리가 자동화되어 있다
- [ ] PII 취급 메모리는 customer managed KMS key로 암호화되어 있다
- [ ] 메모리 읽기 권한 보유 주체(디버깅 도구, 평가 파이프라인, 콘솔 접근자 포함) 목록이 최소 권한으로 검토되었다
- [ ] 오염 사고 대응 runbook(역추적 → 일괄 소독 → 스냅샷 복원)과 메모리 스냅샷/백업 지점이 있다
- [ ] 정기 메모리 감사(지시문 패턴 스캔, 신규 승격 샘플링)가 일정에 올라 있다

## 참고

- [AgentPoison: Red-teaming LLM Agents via Poisoning Memory or Knowledge Bases (arXiv:2407.12784, NeurIPS 2024)](https://arxiv.org/abs/2407.12784)
- [MINJA: Memory Injection Attacks on LLM Agents via Query-Only Interaction (arXiv:2503.03704)](https://arxiv.org/abs/2503.03704)
- [OWASP LLM08:2025 Vector and Embedding Weaknesses](https://genai.owasp.org/llmrisk/llm082025-vector-and-embedding-weaknesses/)
- [OWASP LLM01:2025 Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)
- [OWASP Agentic AI – Threats and Mitigations (T1: Memory Poisoning)](https://genai.owasp.org/resource/agentic-ai-threats-and-mitigations/)
- [AgentCore Memory — DeleteEvent API](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_DeleteEvent.html) / [DeleteMemoryRecord API](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_DeleteMemoryRecord.html)
- [AgentCore Memory — 저장 암호화 (KMS)](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/storage-encryption.html)
- [AgentCore Memory 리소스 정의 (`eventExpiryDuration`)](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_Memory.html)
- [GDPR Art. 17 — Right to erasure](https://gdpr-info.eu/art-17-gdpr/)
- [개인정보보호법 제36조 (개인정보의 정정·삭제) — 국가법령정보센터](https://www.law.go.kr/법령/개인정보보호법/제36조)
- 관련 장: [메모리 쓰기 정책](/07-memory/memory-write-policies) · [메모리 검색 스코핑](/07-memory/memory-retrieval-scoping) · [인덱스 신선도와 마이그레이션](/06-vector-search/index-freshness-migration) · [프롬프트 인젝션](/12-security-korea/prompt-injection) · [한국 금융권 규제](/12-security-korea/korea-fsc-regulation)
