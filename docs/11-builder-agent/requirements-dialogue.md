---
title: 요구사항 대화
description: 빌더 에이전트가 명확화 질문으로 요구사항을 반복적으로 좁히고, 그 산출물을 구조화된 요구사항 파일로 응고시키는 대화 설계를 다룬다.
outline: [2, 3]
---

# 요구사항 대화

::: tip 이 장에서 얻는 것
- 빌더 에이전트가 **언제 명확화 질문을 던지고 언제 가정하고 진행할지**를 정책으로 설계하는 방법 — 질문 피로(과다 질문 → 사용자 이탈)와 과소 명세(과소 질문 → 잘못된 에이전트 생성) 사이의 트레이드오프를 명시적 규칙으로 바꾼다
- 대화의 산출물이 "즉시 배포"가 아니라 **구조화된 요구사항 파일(spec)**이어야 하는 이유와, 그 파일에 반드시 들어가야 할 필드(목적/성공 기준, 툴·데이터 접근, 예산 상한, 위험 등급)
- 요구사항 파일이 [Part 5의 initializer agent 패턴](../05-context/context-isolation-offloading.md)과 동형(isomorphic)이라는 관점 — 새 컨텍스트 윈도우에서 상태를 복원하는 앵커
- 이 책의 데모(`demo/builder-harness`)가 이 대화 설계를 AgentCore Harness로 어떻게 구현했는지
- 요구사항 대화를 계측하는 SLI와 실패 모드
:::

## 왜 문제가 되는가

빌더 에이전트(agent that builds agents)의 파이프라인은 요구사항 대화에서 시작한다. 이 단계에서 생긴 오해는 하류 전체 — [카탈로그 재사용 검토](./catalog-registry.md), [가드레일 부착](./generated-agent-guardrails.md), [CI/CD 게이트](./agent-cicd.md) — 를 통과한 뒤에야 "만들어 놓고 보니 원하는 게 아니었다"는 가장 비싼 형태로 발견된다. 코드 생성에서 요구사항 결함이 늦게 발견될수록 수정 비용이 커진다는 오래된 교훈이, 에이전트 생성에서는 **권한과 비용이 붙은 실행 주체를 잘못 만든다**는 더 위험한 형태로 재현된다.

문제를 어렵게 만드는 것은 사용자 측이 아니라 모델 측의 기본 성향이다. LLM은 명시적으로 물으면 입력의 모호함을 잘 판별하면서도, 실제 응답 상황에서는 명확화 질문을 던지는 대신 압도적으로 "일단 답하는" 쪽을 선택한다는 것이 반복적으로 관찰되었다.[^knowing] 즉 빌더 에이전트에게 "모호하면 물어봐"라고만 지시하면, 모델은 모호함을 인지하고도 그럴듯한 가정으로 채워 넣고 진행한다. 프롬프트의 과소 명세(underspecification)는 LLM 애플리케이션의 만성 문제로, 대화형 애플리케이션에서는 명확화 질문이 그 폴백 메커니즘이 된다는 분석도 같은 방향을 가리킨다.[^underspec] 질문 성향은 우연히 생기지 않는다 — **설계하고 계측해야 하는 정책**이다.

반대 방향의 실패도 있다. 빌더 에이전트의 사용자에는 비개발자가 포함된다("계약서 검토를 도와주는 에이전트를 만들고 싶어요" 수준의 발화에서 시작한다). 이들에게 스무 개의 질문을 순차적으로 던지면 대화는 심문이 되고 사용자는 이탈한다. 질문 피로(question fatigue)와 과소 명세는 하나의 다이얼의 양 끝이며, 이 장의 핵심은 그 다이얼을 어디에 둘지 결정하는 기준을 세우는 것이다.

마지막으로, 대화는 휘발된다. 30턴의 훌륭한 명확화 대화도 컨텍스트 윈도우가 닫히는 순간 사라진다. 대화 안의 암묵적 합의를 **다음 컨텍스트 윈도우(그리고 다음 파이프라인 단계, 그리고 사람 리뷰어)가 재구성할 수 있는 명시적 아티팩트**로 응고시키지 않으면, 요구사항 대화는 아무것도 산출하지 않은 것과 같다. 이것이 이 장의 두 번째 축인 요구사항 파일이다.

## 핵심 개념

### 1. 질문 정책: 질문 / 가정-후-명시 / 거절의 3분기

모든 모호함에 대해 빌더 에이전트가 취할 수 있는 행동은 세 가지다.

1. **질문한다** — 답에 따라 생성될 에이전트의 구조(툴 세트, 권한, 위험 등급)가 갈라지는 모호함.
2. **가정하고 진행하되, 가정을 spec에 명시한다** — 답이 무엇이든 되돌리기 쉬운 모호함. 가정은 반드시 요구사항 파일의 `assumptions` 필드에 기록되어 사용자가 확정 전에 리뷰할 수 있어야 한다.
3. **거절한다** — 조직 정책상 처리 불가능한 요구(예: 허용되지 않은 데이터 소스 접근). 질문으로 우회할 문제가 아니다.

1과 2를 가르는 실용적 기준은 **되돌림 비용(reversibility)** 과 **분기 폭**이다. "보고서를 매일 아침 보낼까요, 매주 월요일에 보낼까요?"는 가정하고 진행해도 된다 — 스케줄은 한 줄 수정이다. "이 에이전트가 사내 인사 데이터에 접근해야 하나요?"는 반드시 물어야 한다 — 답에 따라 [Part 9의 권한 검토](../09-authorization/index.md) 대상과 위험 등급이 달라지고, 잘못 가정하면 배포 후 회수해야 한다. 명확화 질문의 가치를 "그 답이 최종 목표에 대한 불확실성을 얼마나 줄이는가"(information gain)로 정량화하려는 연구 흐름도 같은 직관을 형식화한 것이다.[^ig]

> ⚠️ 비공식 출처 기반 — 위 각주의 명확화 질문 연구들은 arXiv 프리프린트다. 방향성(모델은 기본적으로 질문하지 않는다, 질문 가치는 불확실성 감소로 평가한다)은 복수 연구가 일치하지만, 개별 수치는 특정 벤치마크·모델에 한정되므로 공식 문서·자체 평가로 교차확인이 필요하다.

질문 피로 쪽 다이얼은 형식으로 제어한다.

- **라운드 상한**: 명확화 라운드는 2~3회로 상한을 두고, 상한 도달 시 남은 모호함은 가정-후-명시로 전환한다. 상한 자체를 위험 등급에 연동할 수 있다 — 높은 위험 등급(쓰기 권한, 대외 발신)일수록 질문 상한을 높이고 가정을 금지한다.
- **묶음 질문**: 질문을 한 턴에 하나씩 흘리지 말고, 관련 질문을 묶어 선택지와 함께 제시한다. "A/B/C 중 무엇에 가깝습니까? 잘 모르겠으면 '모름'도 답입니다"가 열린 질문 스무 개보다 이탈률이 낮다.
- **초안 우선(draft-first)**: 질문만 던지는 대신, 현재 이해를 기반으로 한 spec 초안을 먼저 보여주고 "여기서 틀린 곳을 고쳐 달라"고 요청한다. 사람은 백지 질문보다 틀린 초안 교정에 훨씬 잘 반응한다. 이 책의 데모 harness가 취하는 방식이기도 하다(아래 §4).

### 2. 산출물은 배포가 아니라 요구사항 파일이다

요구사항 대화의 종착점은 "에이전트를 생성했습니다"가 아니라 **구조화된 요구사항 파일**이다. 이 분리에는 세 가지 이유가 있다.

첫째, **세션 경계를 넘는 상태 복원**. Anthropic의 "Effective harnesses for long-running agents"가 제시하는 initializer agent 패턴 — 첫 세션이 요구사항을 테스트 가능한 항목들의 구조화 파일로 확장해 남기고, 이후 세션들은 그 파일을 읽으며 시작한다 — 은 [Part 5](../05-context/context-isolation-offloading.md)에서 상세히 다뤘다.[^harness] 빌더 에이전트의 요구사항 파일은 정확히 같은 역할을 한다: 대화가 어느 컨텍스트 윈도우에서 이뤄졌든, 실제 생성 작업을 수행하는 에이전트(또는 며칠 뒤 재개된 세션)는 요구사항 파일 하나를 앵커로 상태를 복원한다. `claude-progress.txt`와 동형인 원칙 — 사람도 diff로 리뷰할 수 있는 평문/JSON, git으로 버전 관리 — 을 그대로 적용한다.

둘째, **리뷰 게이트의 물리적 대상**. 대화 로그는 리뷰할 수 없지만 파일은 리뷰할 수 있다. 요구사항 파일은 사용자 확정(sign-off), [카탈로그 재사용 검토](./catalog-registry.md), 권한 심사, [CI/CD 게이트](./agent-cicd.md)가 모두 참조하는 단일 아티팩트가 된다. spec을 먼저 확정하고 구현을 그로부터 파생시키는 spec-driven development 접근은 코딩 에이전트 생태계에서 이미 도구화되어 있다 — GitHub의 Spec Kit이 의도(spec)를 실행 가능한 정본 아티팩트로 두는 워크플로를,[^speckit] Kiro가 `requirements.md`(EARS 표기 수용 기준)/`design.md`/`tasks.md` 3파일 구조를 제품화했다.[^kiro] 빌더 에이전트의 요구사항 파일은 이 계보의 "에이전트 생성" 특화판이다.

셋째, **성공 기준의 조기 확보**. 요구사항 파일의 성공 기준(success criteria)은 나중에 [Part 3에서 다룬 AgentCore Evaluations](../03-accuracy-eval/aws-evaluations.md)의 `Builtin.GoalSuccessRate`가 소비하는 assertions의 원천이 된다 — GoalSuccessRate는 세션이 사용자 목표를 달성했는지를 assertions 기반 LLM-judge로 평가한다.[^agentcore-eval] 즉 대화 단계에서 "성공이 무엇인가"를 검증 가능한 문장으로 받아내면, 그 문장이 그대로 evals-as-gate의 게이트 조건이 된다. 성공 기준 없는 요구사항 파일은 배포는 시켜도 검증은 못 시킨다.

### 3. 요구사항 파일 스키마

최소 스키마는 다음과 같다. 형식(JSON/YAML/Markdown+frontmatter)보다 필드의 존재가 중요하다.

```yaml
# agent-spec.yaml — 요구사항 대화의 산출물
agent_name: contract-review-assistant
requested_by: legal-team@example.com
status: draft            # draft → user_confirmed → catalog_checked → approved

purpose: >
  사내 표준 계약서 템플릿 대비 신규 계약서의 편차 조항을 식별하고
  위험도별로 요약한다. 법적 판단은 하지 않는다.

success_criteria:        # → GoalSuccessRate assertions의 원천
  - "업로드된 계약서에서 표준 템플릿과 다른 조항을 모두 나열한다"
  - "각 편차 조항에 표준 조항 원문을 함께 인용한다"
  - "법적 자문으로 해석될 표현('계약해도 됩니다' 등)을 생성하지 않는다"

tools_and_data:          # → Part 9 권한 검토 대상
  - source: s3://legal-templates/          # 읽기
  - source: contract-upload (사용자 제공)   # 세션 한정
  - denied: 인사/재무 데이터 전체

budget:
  max_cost_per_session_usd: 0.50
  max_iterations: 10

risk_tier: medium        # 읽기 전용 + 내부 사용자 한정 → medium
assumptions:             # 가정-후-명시로 처리한 항목
  - "한국어 계약서만 대상 (사용자 미확인, 기본값)"
open_questions: []       # 확정 전 반드시 비어 있어야 함
```

- **`purpose` / `success_criteria`**: 성공 기준은 "좋은 요약"이 아니라 judge가 채점 가능한 단언(assertion) 형태로 쓴다. 부정형 기준("~를 생성하지 않는다")은 [가드레일 챕터](./generated-agent-guardrails.md)의 입력이기도 하다.
- **`tools_and_data`**: 필요한 접근뿐 아니라 **명시적으로 거부된 접근**(`denied`)도 기록한다. 권한 심사는 "무엇이 필요한가"만큼 "무엇이 필요 없다고 합의했는가"를 소비한다. 상세는 [Part 9](../09-authorization/index.md).
- **`budget`**: 세션당 비용 상한과 반복 상한. AgentCore Harness라면 `maxIterations`·`timeoutSeconds`가 이 필드의 런타임 대응물이다.[^create-harness]
- **`risk_tier`**: 읽기/쓰기, 내부/대외, 데이터 민감도의 조합으로 산정한다. 이 값이 이후 파이프라인의 엄격도(사람 승인 필수 여부, 가드레일 프로파일, CI/CD 게이트 강도)를 결정하는 라우팅 키가 된다.
- **`assumptions` / `open_questions`**: 3분기 정책(§1)의 흔적이 남는 곳. `open_questions`가 비어 있지 않은 spec은 `user_confirmed`로 전이할 수 없다는 상태 기계 규칙을 두면, "미해결 모호함을 안고 배포"하는 경로가 구조적으로 막힌다.

::: warning 미정착 영역
요구사항 파일의 표준 스키마는 업계에 아직 없다. Kiro의 EARS 기반 `requirements.md`,[^kiro] Spec Kit의 spec 산출물,[^speckit] Anthropic harness 글의 JSON 기능 목록[^harness]은 모두 "코드 생성"용이고, 에이전트 생성에 특화된 필드(위험 등급, 권한 스코프, 예산)는 각 조직이 자체 정의하는 단계다. 위 스키마는 이 책의 제안이지 표준이 아니다.
:::

### 4. 데모: AgentCore Harness로 구현한 요구사항 대화

이 책의 `demo/builder-harness`는 이 대화 설계를 최소 형태로 배포한 것이다. 오케스트레이션 코드를 작성하지 않고 **AgentCore Harness**(config 기반 managed agent loop)의 `create-harness` 설정만으로 구성했다.[^create-harness] 핵심은 시스템 프롬프트가 대화 정책 자체를 지시한다는 점이다 — 데모의 `create-harness.json`에서 발췌:

> "사용자와 명확화 질문을 주고받으며 요구사항을 반복적으로 좁히고, 최종적으로 어떤 하위 에이전트를 만들지, 어떤 툴/MCP 서버/스킬이 필요한지, AgentCore의 어떤 구성요소(Runtime, Gateway, Identity, Memory)로 배포할지를 **구조화된 계획으로 제안**하세요. 실제로 배포를 실행하지는 말고, **배포 계획만 제안**하세요."

이 두 문장이 이 장의 두 원칙 — 명확화 질문 루프, 그리고 "산출물은 배포가 아니라 구조화된 계획" — 의 실행 형태다. 설정에서 읽을 것들:

- `maxIterations: 10`, `timeoutSeconds: 120` — spec의 `budget` 필드가 런타임 상한으로 내려온 모습.
- `temperature: 0.4` — 요구사항 수집은 창의성보다 일관성이 필요한 작업이다.
- 멀티턴은 같은 `runtimeSessionId`로 `InvokeHarness`를 재호출해 잇고, Harness가 자동 생성한 managed memory가 대화 상태를 유지한다.[^invoke-harness] 단, **memory가 유지하는 것은 대화이지 spec이 아니다** — 세션이 끝나면 요구사항 파일로 응고시키는 책임은 여전히 파이프라인에 있다.

데모 README에는 직접 겪은 함정도 기록되어 있다(신뢰 정책 `aws:SourceArn`에 `harness/*`만 넣으면 role validation 실패 — `runtime/*`도 필요, 실행 역할에 memory 관련 액션 누락 시 스트림 중 AccessDeniedException 등). Harness 운영 일반론은 [Part 10](../10-agentcore/index.md)을 보라.

### 5. 파이프라인에서의 위치: 대화가 끝나도 배포는 멀었다

요구사항 대화 → spec 확정은 파이프라인의 **첫 게이트일 뿐**이다. 확정된 spec은 순서대로 다음을 통과한다.

1. **[카탈로그 재사용 검토](./catalog-registry.md)** — 새로 만들기 전에, spec의 purpose/tools 시그니처로 기존 에이전트를 검색한다. 요구사항 파일이 구조화되어 있어야 이 매칭이 기계적으로 가능하다.
2. **[가드레일 부착](./generated-agent-guardrails.md)** — spec의 `risk_tier`와 부정형 성공 기준이 가드레일 프로파일 선택의 입력이 된다.
3. **[CI/CD 게이트](./agent-cicd.md)** — `success_criteria`에서 파생된 eval assertions가 배포 게이트로 실행된다.

대화에서 곧바로 프로덕션 배포로 점프하는 빌더 에이전트는, 요구사항 대화를 아무리 잘해도 이 세 게이트가 잡아줄 결함(중복 생성, 무가드레일 배포, 미검증 배포)을 그대로 통과시킨다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 모호함이 툴 세트·권한·위험 등급을 가른다 | 즉시 질문 (가정 금지) | 잘못 가정하면 배포 후 권한 회수라는 최악의 수정 비용 | 대화 길이 증가 |
| 모호함이 파라미터 수준(스케줄, 출력 형식 등) | 가정하고 진행 + `assumptions`에 명시 | 되돌림 비용이 낮고, 초안 리뷰에서 교정 가능 | 사용자가 assumptions를 안 읽으면 놓침 — 확정 단계에서 강제 표시 필요 |
| 사용자가 비개발자이고 열린 질문에 답을 못함 | 초안 우선: spec 초안 제시 후 교정 요청 | 백지 질문보다 틀린 초안 교정이 응답률·정확도 모두 높음 | 초안의 앵커링 효과 — 사용자가 초안에 끌려갈 수 있음 |
| 명확화 라운드가 상한(2~3회)에 도달 | 남은 모호함을 가정-후-명시로 일괄 전환 | 질문 피로로 인한 이탈이 과소 명세보다 손실이 큰 지점 | 가정 밀도가 높은 spec — `risk_tier` 상향으로 보상 |
| 요구가 조직 정책 위반(금지 데이터, 금지 액션) | 거절 + 사유 기록 | 질문·가정으로 우회할 문제가 아님 | 사용자 경험 저하 — 대안 제시로 완화 |
| 대화 산출물의 저장 형식 선택 | 평문/JSON/YAML + git 버전 관리 | 사람 diff 리뷰와 기계 파싱을 동시에 만족 (initializer agent 패턴[^harness]) | DB 저장 대비 동시성·검색 기능 약함 — 카탈로그 등록 시점에 인덱싱으로 보완 |
| 성공 기준을 받는 시점 | 대화 단계에서 assertion 형태로 확보 | `GoalSuccessRate` assertions로 직결,[^agentcore-eval] 사후 작성은 생성물에 맞춘 순환 검증이 됨 | 비개발자에게 assertion 작성은 어려움 — 에이전트가 초안을 만들고 사용자는 승인만 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 생성된 에이전트가 요청과 다른 일을 함 | 모델이 모호함을 인지하고도 질문 없이 가정으로 진행 (LLM 기본 성향[^knowing]) | 대화 로그에서 명확화 질문 수 확인 — 0이면 정책 미작동 | 질문 정책을 시스템 프롬프트에 명시 + 질문 수를 SLI로 계측 |
| 사용자가 대화 중간에 이탈 | 질문 피로 — 열린 질문을 한 턴에 하나씩 직렬로 투하 | 이탈 세션의 턴 수·질문 수 분포를 완료 세션과 비교 | 라운드 상한, 묶음 질문(선택지 제시), 초안 우선으로 전환 |
| spec은 확정됐는데 다음 세션/단계가 대화를 처음부터 다시 함 | 산출물이 대화 로그(또는 managed memory)에만 있고 파일로 응고되지 않음 | 파이프라인 다음 단계의 입력이 무엇인지 추적 — 파일 참조가 없으면 실패 | 세션 종료 조건에 "spec 파일 작성·커밋 완료"를 포함 (initializer agent 패턴[^harness]) |
| eval 게이트에서 채점 불가능 판정 | `success_criteria`가 "좋은 요약을 한다"류의 비검증형 문장 | assertions를 `GoalSuccessRate`에 넣어 judge가 판정 가능한지 사전 dry-run | 대화 단계에서 assertion 형식(관찰 가능한 행동 서술)을 강제하는 스키마 검증 |
| 배포 후 권한 회수 소동 | 툴/데이터 접근이 spec에 없거나, 생성 단계에서 spec보다 넓은 권한을 부여 | 생성된 에이전트의 실제 권한과 spec `tools_and_data`의 diff | spec을 권한 발급의 유일한 근거로 강제 — spec에 없는 접근은 CI/CD 게이트에서 차단 ([Part 9](../09-authorization/index.md)) |
| 같은 목적의 에이전트가 중복 생성됨 | 대화에서 곧바로 생성으로 점프 — 카탈로그 검토 단계 생략 | 카탈로그에서 purpose 유사도 상위 항목과 비교 | spec 상태 기계에 `catalog_checked` 상태를 필수 경유지로 삽입 ([카탈로그](./catalog-registry.md)) |
| `assumptions`에 적힌 가정이 그대로 프로덕션까지 감 | 확정(sign-off) 단계에서 가정 목록을 사용자에게 표시하지 않음 | 확정 시점 UI/메시지에 assumptions 노출 여부 확인 | 확정 요청 메시지에 assumptions·open_questions를 명시 나열, open_questions 비어있지 않으면 확정 차단 |

## 안티패턴

- ❌ "모호하면 질문해"라는 한 줄 지시로 질문 행동이 생기리라 기대한다 → ✅ 질문/가정/거절의 3분기 기준, 라운드 상한, 묶음 질문 형식을 시스템 프롬프트에 명시하고 질문 수를 계측한다. 모델의 기본값은 질문하지 않는 것이다.[^knowing]
- ❌ 요구사항이 확정되는 즉시 같은 대화에서 에이전트를 생성·배포한다 → ✅ 대화의 산출물은 spec 파일이고, 생성은 카탈로그 검토·가드레일·CI/CD 게이트를 거친 별도 단계다.
- ❌ 대화 상태를 managed memory(또는 세션 저장소)에 있다고 믿고 파일로 남기지 않는다 → ✅ memory는 대화 연속성용이다. 파이프라인·리뷰·다음 세션의 앵커는 diff 가능한 spec 파일이다.[^harness]
- ❌ 성공 기준을 에이전트 생성 후에 생성물을 보고 작성한다 → ✅ 대화 단계에서 assertion 형태로 받는다. 사후 작성은 "만들어진 것"을 기준으로 삼는 순환 검증이다.
- ❌ 비개발자 사용자에게 "필요한 IAM 권한을 말씀해 주세요"류의 질문을 던진다 → ✅ 사용자에게는 업무 언어로 묻고("이 에이전트가 봐야 하는 문서는 어디 있나요?"), 권한 스코프 번역은 에이전트와 권한 심사 단계가 한다.
- ❌ 모든 요구를 동일한 대화 깊이로 처리한다 → ✅ `risk_tier`에 따라 질문 상한·가정 허용 범위·사람 승인 요건을 차등화한다. 읽기 전용 조회 에이전트와 대외 발신 에이전트는 같은 대화가 아니다.

## 계측 (SLI)

요구사항 대화는 "잘 되는 것 같다"는 인상이 아니라 다음 지표로 관리한다. 세션 계층 지표는 AgentCore Observability/Evaluations의 session-trace-span 계층에 자연스럽게 얹힌다.[^agentcore-eval]

- **명확화 질문 수 분포 (세션당)**: 0에 몰려 있으면 질문 정책 미작동(과소 명세 위험), 상한을 상시 초과하면 질문 피로 위험. 분포의 양 꼬리를 함께 본다.
- **대화 완료율 / 이탈 지점**: spec 확정까지 도달한 세션 비율. 이탈 세션의 마지막 턴이 질문 턴인지 확인하면 질문 피로 기여도를 분리할 수 있다.
- **spec 필수 필드 충족률**: 확정된 spec 중 success_criteria·tools_and_data·budget·risk_tier가 모두 채워진 비율. 스키마 검증으로 100%를 강제하는 것이 목표.
- **assumption 밀도와 assumption 기인 수정률**: spec당 가정 수, 그리고 배포 후 수정 요청 중 원인이 `assumptions` 항목이었던 비율. 후자가 높으면 3분기 기준(§1)에서 "가정 허용" 쪽이 과도하게 넓다는 신호.
- **재대화율(re-elicitation rate)**: 생성된 에이전트에 대해 N일 내 요구사항 수준의 수정 요청이 들어온 비율. 요구사항 대화 품질의 최종 결과 지표.
- **spec→assertion 전환 성공률**: success_criteria가 `GoalSuccessRate` assertions로 변환되어 judge 채점이 가능했던 비율.[^agentcore-eval] 낮으면 대화 단계의 성공 기준 수집 형식을 교정한다.
- **세션 예산 상한 도달률**: `maxIterations`/`timeoutSeconds` 상한에 걸려 종료된 대화 비율.[^create-harness] 높으면 상한이 낮거나 대화 설계가 발산하고 있다.

## 체크리스트

- [ ] 질문/가정-후-명시/거절의 3분기 기준이 시스템 프롬프트에 명시되어 있는가? (한 줄 "모호하면 질문해"가 아니라)
- [ ] 권한·위험 등급을 가르는 모호함은 가정 금지 대상으로 분류되어 있는가?
- [ ] 명확화 라운드 상한과 묶음 질문(선택지 제시) 형식이 정의되어 있는가?
- [ ] 대화의 산출물이 diff 리뷰 가능한 spec 파일로 응고되며, git 등으로 버전 관리되는가?[^harness]
- [ ] spec에 success_criteria(assertion 형식)·tools_and_data(denied 포함)·budget·risk_tier·assumptions·open_questions 필드가 있는가?
- [ ] `open_questions`가 비어 있지 않으면 사용자 확정으로 전이할 수 없도록 상태 기계가 강제하는가?
- [ ] 확정 요청 시 assumptions 목록이 사용자에게 명시적으로 표시되는가?
- [ ] success_criteria가 `GoalSuccessRate` assertions로 변환 가능한 형식인지 사전 검증하는가?[^agentcore-eval]
- [ ] spec 확정 → 카탈로그 재사용 검토 → 가드레일 → CI/CD 게이트의 경유가 파이프라인으로 강제되는가? (대화 → 즉시 배포 경로 부재)
- [ ] 질문 수 분포, 이탈률, 재대화율, spec 필드 충족률이 계측되고 있는가?

## 참고

[^knowing]: ["Knowing but Not Showing: LLMs Recognize Ambiguity but Rarely Ask Clarifying Questions"](https://arxiv.org/abs/2605.25284), arXiv:2605.25284 — 모델이 명시적 판별 과제에서는 모호함을 인지하면서도 QA 상황에서는 압도적으로 직접 답변을 택한다는 관찰.
[^underspec]: ["What Prompts Don't Say: Understanding and Managing Underspecification in LLM Prompts"](https://arxiv.org/abs/2505.13360), arXiv:2505.13360 — 프롬프트 과소 명세의 분석과, 대화형 애플리케이션에서 명확화 질문을 폴백으로 쓰는 관리 전략.
[^ig]: ["Uncertainty-Aware Clarification in LLM Agents with Information Gain"](https://arxiv.org/abs/2606.03135), arXiv:2606.03135 — 명확화 질문의 효용을 목표에 대한 belief 갱신량(information gain)으로 정량화하는 프레임워크.
[^harness]: Anthropic, ["Effective harnesses for long-running agents"](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents), Anthropic Engineering Blog — initializer agent가 요구사항을 구조화 파일로 남기고 이후 세션이 그것으로 상태를 복원하는 패턴. 상세 논의는 [컨텍스트 격리와 오프로딩](../05-context/context-isolation-offloading.md).
[^speckit]: GitHub, [Spec Kit](https://github.com/github/spec-kit) — spec을 실행 가능한 정본 아티팩트로 두는 spec-driven development 툴킷.
[^kiro]: Kiro, [Specs 문서](https://kiro.dev/docs/specs/) — `requirements.md`(EARS 표기)/`design.md`/`tasks.md` 3파일 spec 워크플로.
[^agentcore-eval]: AWS, [Amazon Bedrock AgentCore FAQs](https://aws.amazon.com/bedrock/agentcore/faqs/) 및 [Build reliable AI agents with Amazon Bedrock AgentCore Evaluations](https://aws.amazon.com/blogs/machine-learning/build-reliable-ai-agents-with-amazon-bedrock-agentcore-evaluations/) — session/trace/span 계층과 `Builtin.GoalSuccessRate`(assertions 기반) 등 빌트인 평가자. 상세는 [AWS 평가 서비스](../03-accuracy-eval/aws-evaluations.md).
[^create-harness]: AWS, [Amazon Bedrock AgentCore Control API — CreateHarness](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateHarness.html) — `systemPrompt`, `maxIterations`, `timeoutSeconds` 등 harness 설정 필드.
[^invoke-harness]: AWS, [Amazon Bedrock AgentCore API — InvokeHarness](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_InvokeHarness.html) — `runtimeSessionId` 기반 멀티턴 호출.

- [컨텍스트 격리와 오프로딩](../05-context/context-isolation-offloading.md) — initializer agent 패턴과 세션 간 상태 브리징의 정본 논의
- [AWS 평가 서비스](../03-accuracy-eval/aws-evaluations.md) — success_criteria가 assertions로 소비되는 `GoalSuccessRate`와 evals-as-gate
- [카탈로그와 레지스트리](./catalog-registry.md) — spec 확정 후 첫 경유지: 재사용 검토
- [생성된 에이전트의 가드레일](./generated-agent-guardrails.md) — risk_tier·부정형 성공 기준의 소비처
- [에이전트 CI/CD](./agent-cicd.md) — spec 파생 assertions가 배포 게이트로 실행되는 단계
- [Part 9 — 인가](../09-authorization/index.md) — tools_and_data 필드의 권한 심사
- [Part 10 — AgentCore](../10-agentcore/index.md) — Harness 운영 일반론
- `demo/builder-harness` (저장소 루트) — 이 장의 대화 설계를 배포한 최소 데모
