---
title: 생성된 에이전트 가드레일
description: 빌더 에이전트가 생성한 에이전트에 플랫폼이 강제로 주입하는 불변 가드레일 계층과 생성물 검증·차등 승인 체계를 다룬다.
outline: [2, 3]
---

# 생성된 에이전트 가드레일

::: tip 이 장에서 얻는 것
- "생성된 에이전트는 사람이 짠 코드보다 검증이 **더** 필요하다"는 전제가 왜 성립하는지 — 생성물이 플랫폼 정책을 위반하는 3대 경로(과도한 권한, 안전하지 않은 시스템 프롬프트, 무제한 예산)
- 플랫폼이 생성물에 강제 주입하는 **4계층 불변 가드레일**: 실행 한도(Harness 설정) → 최소 권한(Cedar 자동 생성 + LOG_ONLY) → Bedrock Guardrails(콘텐츠·PII) → 네트워크 egress 제한
- 생성된 시스템 프롬프트의 2단계 검증 파이프라인 — 정적 검사(금지 패턴·필수 섹션)와 다른 모델 계열을 쓴 LLM 리뷰(자기선호 편향 회피)
- 생성물 위험 등급(read-only vs 쓰기/결제)에 따른 차등 승인 게이트 설계
- `InvokeHarness`의 caller-supplied override(`model`/`tools`/`skills`)가 왜 신뢰 경계이고, 호출 앱 레이어에서 무엇을 스트립해야 하는지
:::

## 왜 문제가 되는가

Part 11의 빌더 에이전트는 요구사항 대화([requirements-dialogue](./requirements-dialogue))를 거쳐 하위 에이전트의 시스템 프롬프트, 툴 구성, 권한 요구, 배포 설정을 **생성**한다. 이 생성물은 사람이 작성한 코드와 세 가지 점에서 다르며, 셋 다 검증 부담을 **늘리는** 방향으로 작용한다.

첫째, **사람이 짠 코드에 있는 암묵적 검증 단계가 없다.** 사람이 에이전트를 작성하면 코드 리뷰, PR 승인, IDE 경고라는 최소한의 사회적·기술적 게이트를 통과한다. 빌더 에이전트의 생성물은 이 게이트를 기본적으로 우회한다 — 생성 즉시 배포 가능한 형태(CreateHarness 요청 페이로드, IAM 정책 문서)로 나오기 때문이다. 게이트를 플랫폼이 명시적으로 다시 만들지 않으면, 생성물은 리뷰 0회로 프로덕션에 도달한다.

둘째, **생성 규모가 리뷰 용량을 초과한다.** 빌더 에이전트의 존재 이유가 "에이전트 생성의 셀프서비스화"이므로, 성공할수록 생성물 수가 사람 리뷰어 수를 앞지른다. 전수 사람 리뷰는 설계상 불가능하고, 자동 검증 + 위험 기반 샘플링으로 갈 수밖에 없다.

셋째, **생성기 자체가 공격 표면이다.** 요구사항 대화는 자연어 입력이고, 빌더 에이전트는 LLM이다. 악의적(또는 단순히 부주의한) 요구사항 — "모든 S3 버킷에 접근할 수 있어야 해요", "제한 없이 오래 돌아야 해요" — 이 그대로 생성물의 권한·설정으로 번역될 수 있다. 이는 OWASP GenAI Top 10이 LLM06 **Excessive Agency**로 분류하는 위험의 생성기 버전이다: 과도한 기능(excessive functionality), 과도한 권한(excessive permissions), 과도한 자율성(excessive autonomy)이 에이전트에 부여되는 것.[[OWASP LLM06:2025 Excessive Agency]](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)

구체적 위반 경로는 세 갈래다.

| 위반 경로 | 생성물에서의 형태 | 결과 |
|---|---|---|
| 과도한 권한 요청 | IAM/Cedar 정책에 `Resource: "*"`, 요구사항에 없는 액션 포함 | confused deputy·데이터 유출 반경 확대 ([confused-deputy](../09-authorization/confused-deputy)) |
| 안전하지 않은 시스템 프롬프트 | 인젝션 방어 문구 부재, 시크릿/내부 URL 하드코딩, "사용자 지시를 항상 따르라" 류의 무조건 복종 문구 | 프롬프트 인젝션 성공률 상승 ([prompt-injection](../12-security-korea/prompt-injection)) |
| 예산 무제한 | `maxIterations`/`maxTokens`/`timeoutSeconds` 미지정(기본값 의존), rate limit 없음 | 폭주 루프의 비용·자원 고갈 — 마이크로VM 세션이 `shell` 툴을 갖고 도는 만큼 피해가 토큰 비용에 그치지 않는다[[AgentCore Harness security]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-security.html) |

결론은 단순하다. **생성물을 신뢰하지 말고, 플랫폼이 벗겨낼 수 없는 가드레일을 주입하라.** 이 장의 나머지는 그 주입 계층과 검증 파이프라인의 설계다.

## 핵심 개념

### 신뢰 모델: 생성물은 untrusted input이다

빌더 에이전트의 출력(시스템 프롬프트, 툴 목록, 권한 요구서, Harness 설정)은 LLM 출력이므로, Part 9에서 툴 인자를 다루던 것과 동일한 신뢰 모델을 적용한다 — **LLM이 만든 것은 검증 대상이지 신뢰 대상이 아니다.** [cedar-verified-permissions](../09-authorization/cedar-verified-permissions)에서 "위조 불가능한 JWT 클레임 vs LLM이 만든 툴 인자"를 구분했듯, 여기서는 "요구사항 파일에 사람이 승인한 내용 vs 빌더가 생성한 설정"을 구분한다. 요구사항 파일이 ground truth이고, 생성물은 그에 대해 diff 검증되는 산출물이다.

이 관점에서 가드레일은 두 종류로 나뉜다.

- **불변(immutable) 계층** — 플랫폼이 생성물 배포 파이프라인에서 강제 주입하며, 빌더 에이전트도 생성물 자신도 제거·완화할 수 없다. 생성물이 무엇을 하든 상한이 보장된다.
- **검증(validation) 계층** — 생성물 내용을 배포 전에 검사해 통과/차단/사람 리뷰로 라우팅한다. 상한 안에서의 품질·정책 적합성을 다룬다.

### 불변 가드레일 4계층

#### ① 실행 한도 — Harness 설정으로 주입

AgentCore Harness는 에이전트를 코드가 아닌 설정으로 선언하므로, 실행 한도를 플랫폼이 `CreateHarness` 페이로드에 주입하기가 구조적으로 쉽다. `maxIterations`, `maxTokens`, `timeoutSeconds`는 CreateHarness의 최상위 필드이며,[[CreateHarness API]](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateHarness.html) AWS 공식 보안 가이드는 이 세 값을 기본값에 의존하지 말고 비용·남용 가드레일로 명시 설정할 것을 권고한다.[[AgentCore Harness security]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-security.html)

이 책의 데모가 실례다. `demo/builder-harness/create-harness.json`은 빌더 에이전트 자체에 다음 한도를 명시한다.

```json
{
  "maxIterations": 10,
  "maxTokens": 4096,
  "timeoutSeconds": 120
}
```

빌더가 생성한 하위 에이전트에도 같은 원칙을 적용하되, 주입 방식이 핵심이다: 빌더가 생성한 페이로드에 한도가 있는지 검사하는 것이 아니라, **배포 파이프라인이 위험 등급별 상한 테이블에서 값을 읽어 페이로드를 덮어쓴다.** 빌더가 `maxIterations: 500`을 생성해도 파이프라인이 등급 상한(예: read-only 등급 25)으로 치환한다. "생성물이 올바른 값을 갖도록 프롬프트를 잘 쓰자"는 검증 계층의 접근이고, 여기서 필요한 것은 불변 계층 — 프롬프트가 실패해도 상한이 남아야 한다.

세션당 한도만으로는 부족하다. 한도 내 세션을 무한히 여는 폭주는 여전히 가능하므로, 공식 가이드가 권고하듯 Harness 앞에 rate limiting(API Gateway throttling 또는 앱 레이어)을 별도로 둔다.[[AgentCore Harness security]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-security.html) 비용 관측 관점은 Part 8 [08-scaling-cost](../08-scaling-cost/)와 연결된다.

#### ② 권한 — 요구사항 파일 기반 최소 권한 + Cedar LOG_ONLY 시작

생성된 에이전트의 권한은 빌더가 "필요할 것 같은" 권한이 아니라, **요구사항 대화의 산출물인 요구사항 파일에 명시된 툴·리소스의 최소 집합**에서 기계적으로 도출한다. 파이프라인은 두 산출물을 만든다.

1. **실행 역할(IAM)** — 요구사항 파일의 툴 목록에서 필요한 AWS 액션만 생성. `Resource: "*"`는 린트 단계에서 무조건 차단. 실행 역할 trust policy에는 `aws:SourceAccount`/`aws:SourceArn` confused-deputy 조건을 파이프라인이 강제 삽입한다 — 이 조건이 없으면 어떤 계정의 어떤 harness든 `bedrock-agentcore.amazonaws.com` principal을 통해 역할을 assume할 수 있다.[[AgentCore Harness security]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-security.html)
2. **Cedar 정책(툴콜 레벨)** — 요구사항 파일의 "허용 툴 + 인자 제약"에서 AgentCore Policy용 Cedar 정책을 자동 생성한다. AgentCore Policy의 정책 작성 집행점 자체가 자연어→Cedar 번역과 schema validation·automated reasoning 검증을 제공하므로,[[Policy core concepts]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-core-concepts.html) 빌더 파이프라인은 이를 그대로 활용한다. 상세는 [cedar-verified-permissions](../09-authorization/cedar-verified-permissions) 참고.

핵심 운영 원칙은 **LOG_ONLY로 시작**하는 것이다. 생성된 Cedar 정책은 요구사항 파일만큼만 정확하고, 요구사항 대화는 실제 사용 패턴을 완벽히 예측하지 못한다. LOG_ONLY 기간에 "정책이 거부했을 호출" 로그를 수집해 오탐(정당한 호출이 거부됨)과 미탐(요구사항 밖 호출이 허용됨)을 함께 교정한 뒤 ENFORCE로 전환한다 — 전환 절차와 IAM 체크포인트는 [cedar-verified-permissions](../09-authorization/cedar-verified-permissions)의 운영 권고 절에서 이미 다뤘다.

#### ③ 콘텐츠·PII — Bedrock Guardrails

시스템 프롬프트와 Cedar가 "무엇을 할 수 있는가"를 제약한다면, Amazon Bedrock Guardrails는 "무엇이 오갈 수 있는가"를 제약한다 — 콘텐츠 필터, denied topics, sensitive information(PII) 필터를 모델 호출의 입출력 양쪽에 적용한다.[[Bedrock Guardrails]](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails.html) 플랫폼은 생성된 모든 에이전트의 모델 경로에 조직 표준 guardrail을 부착하며, 이 부착 여부는 빌더의 생성물이 아니라 배포 파이프라인의 소관이다.

한국 규제 맥락(개인정보보호법)에서의 PII 필터 구성, 마스킹 vs 차단 선택, 커스텀 regex 엔티티 설계는 [guardrails-pii](../12-security-korea/guardrails-pii)에서 다룬다.

#### ④ 네트워크 — egress 제한

생성된 에이전트가 프롬프트 인젝션에 당하더라도 데이터를 내보낼 곳이 없으면 유출은 미수에 그친다. 생성된 에이전트의 실행 환경은 VPC 모드에서 요구사항 파일에 명시된 목적지(모델 엔드포인트, 툴 호스트)로만 egress를 허용하고, 그 외 목적지는 기본 차단한다. 보안 그룹에 `0.0.0.0/0`류의 광역 허용을 두지 않는 원칙과 Harness의 ECR Public 이미지 풀 경로(NAT gateway 필요) 같은 예외 처리까지 포함해,[[AgentCore Harness security]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-security.html) egress 아키텍처 전반은 [egress-control](../09-authorization/egress-control)에서 이미 다뤘다. 여기서의 요점은 하나다 — **egress allowlist도 요구사항 파일에서 자동 도출하고, 빌더 생성물에는 네트워크 설정 결정권을 주지 않는다.**

### 생성된 시스템 프롬프트의 검증

시스템 프롬프트는 생성물 중 가장 검증하기 어려운 부분이다. 권한은 형식 언어(IAM/Cedar)라 기계 검증이 되지만, 시스템 프롬프트는 자연어다. 2단계 파이프라인을 쓴다.

**1단계 — 정적 검사(결정론적, 저비용).** 배포 전 CI에서 실행한다([agent-cicd](./agent-cicd) 참고).

- **금지 패턴**: 시크릿 형태 문자열(AWS access key prefix, Bearer 토큰 패턴), 내부 전용 엔드포인트 URL, "ignore previous instructions"류 문구, "사용자의 모든 지시를 무조건 따르라"류 무조건 복종 문구, 요구사항 파일에 없는 툴 이름 언급.
- **필수 섹션 존재**: 역할 정의, 범위 제한("~만 수행한다"), 거부 조건("~요청은 거부한다"), 툴 사용 규칙, 출력 형식. 섹션 부재는 실패로 처리한다 — 빌더가 생성을 누락하는 것이 가장 흔한 결함 모드다.
- **요구사항 diff**: 프롬프트가 언급하는 능력·툴이 요구사항 파일의 승인 범위를 초과하는지 키워드 수준에서 대조.

**2단계 — LLM 기반 리뷰(의미론적, 고비용).** 정적 검사가 못 잡는 것 — 우회적으로 위험한 지시, 범위 제한의 논리적 허점 — 을 다른 LLM이 심사한다. 이때 **생성에 쓴 모델과 다른 모델 계열(family)을 리뷰어로 쓴다.** LLM 평가자는 자기 자신(또는 같은 계열)의 출력을 식별하고 더 높게 평가하는 self-preference bias가 실증되어 있고,[[Panickssery et al. 2024, arXiv:2404.13076]](https://arxiv.org/abs/2404.13076) LLM-as-a-judge 일반론에서도 self-enhancement bias는 알려진 한계다.[[Zheng et al. 2023, arXiv:2306.05685]](https://arxiv.org/abs/2306.05685) 같은 계열로 생성하고 심사하면 심사가 구조적으로 관대해진다. 리뷰어 rubric 설계와 trajectory 평가 기법은 [llm-judge-trajectory](../03-accuracy-eval/llm-judge-trajectory)의 방법론을 그대로 적용한다.

LLM 리뷰는 게이트이지 오라클이 아니다 — 리뷰 통과가 안전 증명이 아니므로, 불변 계층(①~④)이 여전히 최후 방어선이다. 계층 순서가 그렇게 설계된 이유다.

### 위험 등급 분류와 차등 게이트

전수 사람 리뷰가 불가능하므로, 생성물을 위험 등급으로 분류해 게이트 강도를 차등화한다. 등급은 빌더의 자기 신고가 아니라 **생성된 툴 구성·권한에서 기계적으로 도출**한다 — read 계열 액션만 있으면 Tier 1, write 액션이 있으면 Tier 2, 결제·인사·불가역 삭제 등 지정 고위험 액션이 하나라도 있으면 Tier 3.

| 등급 | 예 | 게이트 |
|---|---|---|
| **Tier 1** read-only 조회 | 사내 위키 Q&A, 로그 조회 에이전트 | 정적 검사 + LLM 리뷰 통과 시 자동 승인. 불변 계층 기본 상한 |
| **Tier 2** 쓰기 가능 | 티켓 생성, 문서 갱신 에이전트 | 자동 검증 + 사람 리뷰 1인(권한 diff와 시스템 프롬프트 중심) |
| **Tier 3** 결제·불가역 작업 | 환불 처리, 리소스 삭제 에이전트 | 사람 리뷰 필수 + 런타임 HITL 승인(고위험 액션마다) + 감사 로그 |

Tier 3의 런타임 승인·감사 체계는 [hitl-audit](../09-authorization/hitl-audit)에서 다룬 human-in-the-loop 패턴을 그대로 쓴다. 등급은 배포 시점에 고정되지 않는다 — 생성된 에이전트의 툴 구성이 갱신되면 등급을 재산출하고, **등급 상승은 항상 상위 게이트를 다시 통과해야 한다.** "Tier 1로 승인받고 나중에 write 툴을 추가"하는 우회를 막는 규칙이다.

::: warning 미정착 영역
생성형 에이전트의 위험 등급 분류 체계는 업계 표준이 없다. 위 3단계는 이 책의 제안이며, 조직에 따라 데이터 민감도 축(PII 접근 여부)이나 외부 노출 축(내부 전용 vs 고객 대면)을 별도 차원으로 추가하는 설계도 타당하다. NIST AI RMF, EU AI Act의 위험 분류는 시스템 단위 규제 프레임이라 개별 생성 에이전트의 배포 게이트 기준으로 바로 쓰기에는 입도가 다르다 — 매핑을 시도한다면 규제 대응 문서와 배포 게이트 기준을 별도로 유지하라.
:::

### InvokeHarness의 caller-supplied override는 신뢰 경계다

지금까지의 계층이 모두 갖춰져도 마지막 구멍이 남는다. `InvokeHarness`는 호출 시점에 harness 기본 설정을 덮어쓰는 필드를 받는다 — `model`(엔드포인트·파라미터 포함), `tools`, `skills`.[[InvokeHarness API]](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_InvokeHarness.html) 인바운드 인증(SigV4 또는 OAuth JWT)을 통과한 caller의 입력은 harness 입장에서 전부 신뢰된다 — harness는 입력을 sanitize하지도, override 필드를 필터링하지도 않는다.[[AgentCore Harness security]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-security.html)

즉 배포 시점 가드레일이 아무리 견고해도, 생성된 에이전트를 호출하는 경로에서 caller가 override를 넣을 수 있으면:

- `model.additionalParams`의 LiteLLM `apiBase`로 **요청을 외부 엔드포인트로 리다이렉트**해 프롬프트·데이터를 유출하거나 임의 헤더를 주입할 수 있고,
- invoke-time `skills`는 세션마다 Git/S3 등에서 새로 fetch되어 스크립트까지 신뢰된 컨텍스트로 주입되며, **같은 이름의 skill이 harness 기본 skill을 덮어쓴다**. `skills` 필드를 invocation 단위로 제한하는 IAM condition key는 존재하지 않으므로 IAM으로는 못 막는다.[[AgentCore Harness security]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-security.html)

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요: override 필드별 공격 시나리오(apiBase 리다이렉트, invoke-time skill의 이름 충돌 우선순위)의 세부는 로컬 레퍼런스(`~/.claude/skills/amazon-bedrock/references/agentcore-harness.md`, 공식 harness-security 문서를 요약한 자료)에 근거했다. 필드 존재와 신뢰 경계 원칙 자체는 위 공식 URL로 확인 가능하나, 세부 동작은 최신 [harness-security 문서](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-security.html)로 재확인하라.

따라서 **생성된 에이전트를 최종 사용자에게 노출하는 앱 레이어가 신뢰 경계의 집행점이다.** 앱 레이어는 사용자 입력에서 messages만 추출해 자체적으로 `InvokeHarness` 요청을 조립하고, `model`/`tools`/`skills` override는 스트립하거나(권장 기본) 플랫폼 관리 allowlist로만 채운다. 사용자 요청 JSON을 그대로 포워딩하는 패스스루 프록시는 이 경계를 무너뜨린다. 방어 심화로, role switching이 불필요한 실행 역할에는 `sts:AssumeRole`을 명시적으로 deny한다.[[AgentCore Harness security]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-security.html)

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 실행 한도를 어디서 강제할까 | 배포 파이프라인이 등급별 상한으로 페이로드를 덮어씀 (빌더 프롬프트 지시 ❌) | 프롬프트 준수는 확률적, 파이프라인 치환은 결정론적 | 정당한 고한도 요구는 예외 승인 절차 필요 |
| 생성된 Cedar 정책의 초기 모드 | LOG_ONLY 시작 → 로그 교정 후 ENFORCE | 요구사항 파일이 실사용 패턴을 완벽히 예측 못 함 — 오탐·미탐을 실측으로 교정 | LOG_ONLY 기간엔 툴콜 인가가 미집행 — 기간 상한과 만료 알람 필수 |
| 시스템 프롬프트 LLM 리뷰어 모델 | 생성 모델과 다른 계열 | self-preference bias 회피[[arXiv:2404.13076]](https://arxiv.org/abs/2404.13076) | 이종 모델 운영 비용, rubric을 계열 중립적으로 유지해야 함 |
| Tier 1 생성물의 승인 방식 | 자동 승인 (정적 + LLM 리뷰 통과 시) | read-only 반경 내에서 사람 리뷰의 한계 비용 > 한계 효용 | 등급 산출 로직 자체가 신뢰 앵커가 됨 — 산출 로직은 사람이 리뷰 |
| caller override 처리 | 앱 레이어에서 스트립, 필요 시 allowlist | harness는 인증 통과 입력을 전부 신뢰 — IAM condition key로 `skills` 제한 불가 | 정당한 per-invocation 모델 전환 유스케이스는 allowlist 관리 부담 |
| PII 필터 위치 | 플랫폼 표준 Bedrock Guardrails를 파이프라인이 부착 | 생성물마다 개별 구성하면 누락·편차 발생 | 조직 단일 정책이 팀별 세분화 요구와 충돌 가능 — [guardrails-pii](../12-security-korea/guardrails-pii) 참고 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 생성된 에이전트가 무한 루프성 재시도로 비용 폭증 | `maxIterations`/`timeoutSeconds`를 기본값에 의존, 파이프라인 주입 없음 | `get-harness`로 실제 한도 필드 확인, CloudWatch 세션 지속시간 분포 | 등급별 상한 테이블로 페이로드 강제 치환, harness 앞 rate limiting 추가 |
| 요구사항에 없는 리소스 접근 성공 | 빌더가 광역 권한(`Resource: "*"` 등) 생성, 린트 부재 | IAM Access Analyzer / 정책 diff를 요구사항 파일과 대조 | 요구사항 파일 → 정책 자동 도출로 전환, 광역 리소스 린트를 CI 필수 게이트로 |
| ENFORCE 전환 직후 정당한 툴콜 대량 거부 | LOG_ONLY 기간에 거부-예정 로그를 교정하지 않고 전환 | LOG_ONLY 기간의 would-deny 로그와 실제 거부 로그 비교 | 전환 전 would-deny 로그 제로화(또는 설명 가능화)를 전환 조건으로 명문화 |
| 리뷰 통과한 프롬프트에서 인젝션 취약 문구 발견 | 생성·리뷰를 같은 모델 계열로 수행 — 심사가 구조적으로 관대 | 리뷰어 모델 계열 설정 감사, 이종 모델로 재심사해 판정 차이 측정 | 리뷰어를 다른 계열로 고정, 판정 불일치 샘플을 rubric 개선에 환류 |
| Tier 1로 승인된 에이전트가 write 작업 수행 | 툴 구성 갱신 시 등급 재산출 누락 | 현재 툴 구성에서 등급을 재계산해 등록 등급과 대조 | 구성 변경 이벤트에 등급 재산출 훅, 등급 상승 시 상위 게이트 재통과 강제 |
| 세션마다 의도하지 않은 skill이 로드됨 | 앱 레이어가 caller JSON을 패스스루 — invoke-time `skills`가 기본 skill을 덮어씀 | CloudTrail의 `InvokeHarness` 페이로드에서 override 필드 존재 확인 | 앱 레이어에서 `model`/`tools`/`skills` 스트립, 관측 트레이스에 로드된 skill 소스 기록 |
| 다른 계정 harness가 실행 역할을 assume | trust policy에 `aws:SourceAccount`/`aws:SourceArn` 조건 누락 | 실행 역할 trust policy 정적 검사 | 파이프라인이 confused-deputy 조건을 강제 삽입, 조건 없는 역할 생성을 차단 |

## 안티패턴

- ❌ 빌더 프롬프트에 "한도를 꼭 설정하세요"라고 지시하고 끝 → ✅ 배포 파이프라인이 등급별 상한으로 한도 필드를 결정론적으로 덮어쓴다. 프롬프트 지시는 생성 품질 개선용이지 집행 수단이 아니다.
- ❌ 빌더가 "필요할 것 같은" 권한을 재량 생성 → ✅ 요구사항 파일에 명시된 툴·리소스에서만 기계적으로 도출하고, 요구사항 밖 권한은 diff에서 차단한다.
- ❌ 생성 모델로 생성물을 셀프 리뷰 → ✅ 다른 모델 계열로 리뷰한다. self-preference bias는 실증된 현상이다.[[arXiv:2404.13076]](https://arxiv.org/abs/2404.13076)
- ❌ "LLM 리뷰 통과 = 안전" 취급 → ✅ LLM 리뷰는 확률적 게이트다. 불변 계층(한도·권한·Guardrails·egress)이 리뷰 실패를 전제로 존재한다.
- ❌ 모든 생성물에 동일한 사람 리뷰 요구 → ✅ 위험 등급 차등 게이트. 전수 리뷰는 규모에서 붕괴하고, 실질적으로 rubber-stamp 리뷰로 퇴화한다.
- ❌ 사용자 요청 JSON을 그대로 `InvokeHarness`에 포워딩 → ✅ 앱 레이어가 messages만 추출해 요청을 재조립하고 override 필드를 스트립한다.
- ❌ Cedar 정책을 처음부터 ENFORCE로 배포 → ✅ LOG_ONLY로 시작해 would-deny 로그를 교정한 뒤 전환한다. 단, LOG_ONLY 만료 기한 없이 방치하는 것도 반대편 안티패턴이다.

## 계측 (SLI)

배포 파이프라인과 런타임 양쪽에서 가드레일 자체의 건강도를 측정한다. 관측 스택 일반론은 Part 10을 따른다.

**파이프라인 SLI**
- **가드레일 주입률**: 배포된 생성 에이전트 중 4계층(한도·권한·Guardrails·egress)이 모두 파이프라인 주입으로 확인되는 비율. 목표는 100%이며, 미달 1건이 곧 인시던트다.
- **정적 검사 차단률 / LLM 리뷰 차단률**: 게이트별 차단 비율의 추세. 급락은 게이트 무력화(검사 우회, rubric 열화)의 신호일 수 있다.
- **권한 diff 초과율**: 생성된 정책이 요구사항 파일 범위를 초과한 비율 — 빌더 프롬프트 품질의 대리 지표.
- **등급 분포와 등급 상승 재게이트 준수율**: Tier 상승 이벤트 중 상위 게이트를 재통과한 비율(목표 100%).

**런타임 SLI**
- **한도 도달률**: `maxIterations`/`timeoutSeconds` 도달로 종료된 세션 비율. 높으면 한도가 과소하거나 에이전트가 폭주 중 — 어느 쪽인지 trajectory로 판별([llm-judge-trajectory](../03-accuracy-eval/llm-judge-trajectory)).
- **Cedar 거부율(ENFORCE) / would-deny율(LOG_ONLY)**: 에이전트별 추세. 특정 에이전트의 급증은 프롬프트 인젝션 시도 또는 요구사항-실사용 괴리의 신호.
- **Guardrails 개입률**: 콘텐츠·PII 필터 발동 비율 — 입력측과 출력측을 분리 집계.
- **override 시도 탐지 수**: 앱 레이어가 스트립한 `model`/`tools`/`skills` 필드 발생 건수. 0이 정상 상태이며, 발생 자체가 조사 대상이다.
- **인가 실패·호출 급증 알람**: CloudTrail의 harness API 감사와 CloudWatch 알람은 공식 권고사항이다.[[AgentCore Harness security]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-security.html)

## 체크리스트

**배포 파이프라인 (생성물 1건마다)**
- [ ] `maxIterations`/`maxTokens`/`timeoutSeconds`가 등급별 상한 테이블 값으로 치환되었다 (빌더 생성값 신뢰 ❌)
- [ ] IAM/Cedar 정책이 요구사항 파일에서 자동 도출되었고, 요구사항 diff 초과분이 0이다
- [ ] `Resource: "*"` 등 광역 권한 린트를 통과했다
- [ ] 실행 역할 trust policy에 `aws:SourceAccount`/`aws:SourceArn` confused-deputy 조건이 있다
- [ ] Cedar 정책이 LOG_ONLY로 시작하며, ENFORCE 전환 기한과 would-deny 제로화 조건이 티켓으로 존재한다
- [ ] 조직 표준 Bedrock Guardrails가 모델 경로에 부착되었다 ([guardrails-pii](../12-security-korea/guardrails-pii))
- [ ] egress allowlist가 요구사항 파일의 목적지로 한정되었다 ([egress-control](../09-authorization/egress-control))
- [ ] 시스템 프롬프트가 정적 검사(금지 패턴 0건, 필수 섹션 전부 존재)를 통과했다
- [ ] LLM 리뷰가 생성 모델과 **다른 계열**로 수행되었고 판정 기록이 남았다
- [ ] 위험 등급이 툴 구성에서 기계 산출되었고, 등급에 맞는 게이트(자동/사람 1인/HITL)를 통과했다

**호출 앱 레이어**
- [ ] `InvokeHarness` 요청을 앱이 재조립하며, caller의 `model`/`tools`/`skills` override를 스트립(또는 allowlist)한다
- [ ] 스트립 발생이 계측·알람된다
- [ ] harness 앞에 rate limiting이 있다
- [ ] role switching 불필요 시 실행 역할에 `sts:AssumeRole` deny가 있다

**운영**
- [ ] 툴 구성 변경 시 등급 재산출 훅이 동작하고, 등급 상승은 상위 게이트를 재통과한다
- [ ] 파이프라인·런타임 SLI 대시보드가 있고, 가드레일 주입률 100% 미달에 알람이 걸려 있다
- [ ] CloudTrail이 `CreateHarness`/`UpdateHarness`/`InvokeHarness`를 감사하고 있다

## 참고

- [AgentCore Harness security and access controls — AWS 공식 문서](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-security.html)
- [AgentCore Harness overview — AWS 공식 문서](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness.html)
- [CreateHarness API Reference](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateHarness.html) · [InvokeHarness API Reference](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_InvokeHarness.html)
- [Amazon Bedrock Guardrails — AWS 공식 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails.html)
- [Policy in AgentCore core concepts — AWS 공식 문서](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-core-concepts.html)
- [OWASP LLM06:2025 Excessive Agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)
- Panickssery et al., *LLM Evaluators Recognize and Favor Their Own Generations*, 2024 — [arXiv:2404.13076](https://arxiv.org/abs/2404.13076)
- Zheng et al., *Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena*, 2023 — [arXiv:2306.05685](https://arxiv.org/abs/2306.05685)
- 이 책 데모: `demo/builder-harness/create-harness.json` (실행 한도 명시 예)
- 관련 장: [requirements-dialogue](./requirements-dialogue) · [agent-cicd](./agent-cicd) · [cedar-verified-permissions](../09-authorization/cedar-verified-permissions) · [egress-control](../09-authorization/egress-control) · [hitl-audit](../09-authorization/hitl-audit) · [confused-deputy](../09-authorization/confused-deputy) · [llm-judge-trajectory](../03-accuracy-eval/llm-judge-trajectory) · [guardrails-pii](../12-security-korea/guardrails-pii) · [prompt-injection](../12-security-korea/prompt-injection)
