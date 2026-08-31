---
title: 평가 하니스
description: 에이전트의 정확도를 회귀 가능하게 측정하는 평가 하니스의 구조 — 무엇을, 언제, 어떻게 돌리고 그 결과로 배포를 게이팅하는가.
outline: [2, 3]
---

# 평가 하니스

::: tip 이 장에서 얻는 것
- 에러 복합(error compounding)의 p^n 수학과 "단계 수 축소가 가장 큰 정확도 레버"라는 결론 — 이 책에서 p^n 논의의 정본은 이 장이다
- "가끔 됨"과 "안정적으로 됨"을 구분하는 tau-bench의 pass^k 지표와 그 사용법
- 평가 하니스의 3계층 구조(단위 평가 · 궤적 평가 · 온라인 평가)와 각 계층을 언제 돌리는지에 대한 결정 표
- Ragas / LangSmith / Braintrust / Arize Phoenix / DeepEval / TruLens 도구 지형 비교표
- 실패 사례 → 회귀 테스트 축적, held-out 유지 등 평가 데이터셋 구축 원칙과 evals-as-gate 개념
:::

## 왜 문제가 되는가

에이전트는 데모에서 항상 잘 된다. 문제는 "잘 된다"가 확률 변수라는 것이다. 전통 소프트웨어의 테스트는 결정론적 코드에 대한 결정론적 검증이지만, 에이전트의 한 번의 성공 실행은 아무것도 증명하지 않는다 — 같은 입력을 열 번 넣으면 열 개의 서로 다른 궤적(trajectory)이 나오고, 그중 몇 개는 실패한다. tau-bench 논문의 실측이 이 간극을 정량화한다: 당시 state-of-the-art function calling 에이전트(gpt-4o)조차 과제의 50% 미만에서 성공했고, 같은 과제를 8번 반복했을 때 8번 모두 성공할 확률(pass^8)은 retail 도메인에서 25% 미만이었다([arXiv:2406.12045](https://arxiv.org/abs/2406.12045)).

플랫폼 관점에서 하니스 없는 에이전트 운영은 세 가지 구체적 비용으로 나타난다. 첫째, **회귀 감지 불가** — 프롬프트 한 줄, 모델 버전 하나, 툴 스키마 하나를 바꿀 때마다 무엇이 나빠졌는지 알 수 없으므로 모든 변경이 도박이 된다. 둘째, **개선 방향 상실** — "정확도를 올려라"는 요구가 측정 없이는 프롬프트 미신(voodoo prompting)으로 귀결된다. 셋째, **배포 판단 불능** — "이 버전을 프로덕션에 내보내도 되는가"에 답할 데이터가 없어, 판단이 데모 시연과 직감으로 회귀한다.

[에이전트 vs 워크플로우](/01-agent-design/agent-vs-workflow)에서 "에이전트 허용 = 궤적 평가 하니스가 갖춰진 뒤"라는 게이트 조건을 세웠다. 이 장은 그 하니스의 구조 — 무엇을 언제 어떻게 돌리는가 — 를 다룬다. 개별 평가 기법의 상세(LLM-as-a-judge 설계, 궤적 평가 지표의 내부)는 [LLM-as-a-judge와 궤적 평가](/03-accuracy-eval/llm-judge-trajectory)가, AWS 관리형 서비스로 구현하는 방법은 [AWS 평가 서비스](/03-accuracy-eval/aws-evaluations)가 정본이다.

## 핵심 개념

### 에러 복합: p^n 감쇠

멀티스텝 에이전트의 정확도를 지배하는 산수부터 확정한다. 스텝당 성공 확률이 p이고 각 스텝의 성패가 독립이라고 가정하면, n스텝 궤적이 끝까지 성공할 확률은 p<sup>n</sup>이다. 이것은 경험 법칙이 아니라 계산이다:

| 스텝당 성공률 p | n=1 | n=5 | n=10 | n=20 | n=50 | n=100 |
|---|---|---|---|---|---|---|
| 0.95 | 95% | 77.4% | **59.9%** | **35.8%** | 7.7% | **0.59%** |
| 0.99 | 99% | 95.1% | 90.4% | 81.8% | 60.5% | 36.6% |
| 0.999 | 99.9% | 99.5% | 99.0% | 98.0% | 95.1% | 90.5% |

(0.95<sup>10</sup> = 0.5987…, 0.95<sup>20</sup> = 0.3585…, 0.95<sup>100</sup> = 0.00592… — 표의 모든 값은 p<sup>n</sup> 직접 계산이다.)

이 표에서 두 가지 결론이 나온다.

**첫째, 단계 수를 줄이는 것이 가장 큰 정확도 레버다.** p를 0.95에서 0.99로 올리는 것(모델 개선, 프롬프트 개선 — 어렵고 비싸다)보다 n을 20에서 10으로 줄이는 것(툴 통합, 스텝 사이 결정론적 검증 게이트, 서브태스크의 워크플로우 강등 — 설계로 가능하다)이 궤적 성공률에 미치는 영향이 크거나 비슷하면서 훨씬 싸다. p=0.95, n=20의 35.8%를 n=10으로 줄이면 59.9%가 된다 — p를 그대로 두고 성공률을 24%p 끌어올린 것이다. 하니스가 스텝 수 분포를 계측해야 하는 이유가 여기에 있다: n은 최적화 대상이다.

**둘째, 스텝 단위 평가 점수는 궤적 성공률을 예측하지 못한다.** "각 툴 호출 정확도 95%"는 스텝 단위로는 A학점이지만 100스텝 궤적에서는 0.59%다. 스텝 평가와 궤적 평가는 하니스에서 별도 계층이어야 한다.

단, 이 모델의 독립성 가정은 실제 에이전트에서 성립하지 않는다는 점을 명시해 둔다. 두 방향 모두로 어긋난다: 에이전트는 실패한 스텝을 감지하고 **복구**(재시도, 대안 경로)할 수 있어 실측 성공률이 p<sup>n</sup>보다 높게 나오기도 하고, 반대로 한 스텝의 오염된 출력이 이후 모든 스텝의 컨텍스트에 남아 오류가 **상관**되면 더 낮게 나오기도 한다. 따라서 p^n은 "측정 없이 스텝 수를 늘리면 안 되는 이유"를 설명하는 1차 근사이지, 실측을 대체하는 예측 모델이 아니다. 실측이 하니스의 존재 이유다. ([에이전트 vs 워크플로우](/01-agent-design/agent-vs-workflow)의 에러 복합 절과 같은 개념이며, 수치 표의 정본은 이 장이다.)

### pass^k: "가끔 됨"과 "안정적으로 됨"의 격차

한 번 성공하는 능력과 매번 성공하는 능력은 다른 지표로 측정해야 한다. tau-bench 논문([arXiv:2406.12045](https://arxiv.org/abs/2406.12045))은 이를 위해 pass^k를 도입했다: **"the chance that all k i.i.d. task trials are successful, averaged across tasks"** — 같은 과제를 독립적으로 k번 시도했을 때 k번 **모두** 성공할 확률의 과제 평균이다. n번 시도 중 c번 성공했을 때의 불편 추정량은 다음과 같다:

```text
pass^k = E_task[ C(c, k) / C(n, k) ]          # k회 모두 성공
pass@k = 1 − E_task[ C(n−c, k) / C(n, k) ]    # k회 중 1회 이상 성공 (비교용)
```

코드 생성 벤치마크에서 익숙한 pass@k(k번 중 **한 번이라도** 성공)와 방향이 정반대다. pass@k는 k가 커질수록 올라가고(탐색 능력), pass^k는 k가 커질수록 내려간다(일관성). 논문의 표현으로 pass^k는 "the reliability of the agent at handling variations in conversations with the same underlying semantics"를 포착한다.

프로덕션 에이전트에 필요한 것은 pass^k 쪽이다. 사용자는 리트라이 버튼을 8번 누르지 않는다. 앞서 인용한 tau-bench 실측 — pass^1은 50% 근처인데 pass^8은 25% 미만 — 이 보여주는 것은, **1회 시도 성공률만 보고하는 하니스는 신뢰성을 과대평가한다**는 사실이다. 하니스는 핵심 시나리오에 대해 시행을 반복(각 과제당 n회)하여 pass^k 곡선(k=1, 2, 4, 8)을 산출해야 하고, 배포 게이트 기준은 pass^1이 아니라 최소 pass^4 이상에 걸어야 한다.

### 하니스의 3계층 구조

평가 하니스는 단일 도구가 아니라 실행 시점과 대상이 다른 세 계층의 조합이다.

**계층 1 — 단위 평가 (컴포넌트 레벨, 매 커밋)**: 궤적을 구성하는 개별 능력을 분리해 측정한다. 툴 선택이 맞는가, 인자가 맞는가, retrieval이 맞는 문서를 가져오는가, 단일 응답이 grounded인가. 결정론적 채점(exact match, 스키마 검증, 코드 assertion)이 가능한 부분은 결정론적으로 채점한다 — 빠르고 싸고 flaky하지 않으므로 CI의 매 커밋에 돌린다. p^n의 p를 측정하는 계층이다.

**계층 2 — 궤적 평가 (end-to-end, 배포 전)**: 실제 또는 시뮬레이션 환경에서 전체 과제를 실행하고 (a) 최종 목표 달성 여부(goal accuracy — 가능하면 환경 상태 검증으로 결정론적으로: "DB에 환불 레코드가 생겼는가"), (b) 궤적 자체의 품질(불필요한 스텝, 금지된 툴 호출, 정책 위반)을 채점한다. 비싸고 느리므로 매 커밋이 아니라 배포 후보 버전에 대해 돌리고, 핵심 시나리오는 반복 시행으로 pass^k를 산출한다. 채점기 설계는 [LLM-as-a-judge와 궤적 평가](/03-accuracy-eval/llm-judge-trajectory) 참조. p^n의 실측값을 얻는 계층이다.

**계층 3 — 온라인 평가 (프로덕션, 상시)**: 실트래픽 궤적을 샘플링해 오프라인과 동일한 채점기를 적용하고, 사용자 피드백·에스컬레이션·수동 개입을 실패 신호로 수집한다. 여기서 발견된 실패 사례가 계층 1·2의 데이터셋으로 환류된다(아래 데이터셋 절). LangSmith 문서의 구분으로는 offline("test before you ship")과 online("monitor in production") 평가에 해당한다([docs.langchain.com](https://docs.langchain.com/langsmith/evaluation)).

### 평가 데이터셋 구축 원칙

하니스의 가치는 러너가 아니라 데이터셋에서 나온다. 원칙은 세 가지다.

1. **실패 사례에서 회귀 테스트를 축적한다.** 프로덕션에서 실패한 궤적(계층 3에서 수집)은 입력·기대 결과·실패 원인을 붙여 회귀 스위트에 추가한다. 시간이 지나면 데이터셋이 "우리 워크로드가 실제로 어려워하는 것"의 분포를 반영하게 된다 — 처음부터 합성 데이터로 완벽한 커버리지를 노리는 것보다 이 축적 루프를 빨리 돌리는 것이 낫다.
2. **held-out 세트를 유지한다.** 프롬프트를 평가 점수에 맞춰 반복 수정하면 평가 세트에 과적합된다(사실상의 training on test set). 개발 중 참조하는 dev 세트와, 배포 게이트에서만 채점하고 개별 케이스를 들여다보지 않는 held-out 세트를 분리한다. held-out 점수와 dev 점수의 괴리가 벌어지면 과적합 신호다.
3. **케이스마다 채점 방법을 명시한다.** "기대 출력"만 있는 케이스는 채점기가 바뀔 때마다 의미가 흔들린다. 결정론적 assertion으로 채점 가능한 케이스(환경 상태, exact match)와 LLM judge가 필요한 케이스를 데이터셋 수준에서 구분해 둔다.

### evals-as-gate: 평가를 배포 게이트로

하니스의 최종 형태는 리포트가 아니라 **게이트**다: 평가 점수가 기준을 통과하지 못하면 배포 파이프라인이 실패한다 — 단위 테스트가 빌드를 막는 것과 같은 방식으로. Braintrust는 이 패턴을 제품 차원에서 지원한다: "Run evals on every pull request to catch regressions before they reach production"([braintrust.dev](https://www.braintrust.dev/docs/guides/evals)). 게이트 기준은 절대 점수(예: held-out goal accuracy ≥ X)와 회귀 조건(직전 프로덕션 버전 대비 하락 금지) 두 층으로 건다. LLM judge 점수의 노이즈 때문에 단일 실행 점수로 게이팅하면 게이트 자체가 flaky해지므로, 반복 시행 평균이나 결정론적 채점 케이스만으로 hard gate를 걸고 judge 점수는 soft gate(경고 + 사람 리뷰)로 두는 구성이 안전하다. CI/CD 파이프라인에의 통합 상세는 [에이전트 CI/CD](/11-builder-agent/agent-cicd)에서 다룬다.

### 도구 지형

아래 표는 각 도구의 공식 문서에서 확인한 특징만 담는다. 도구는 하니스의 구조를 대체하지 않는다 — 어느 것을 쓰든 3계층·데이터셋 원칙·게이트는 직접 설계해야 한다.

| 도구 | 성격 | 에이전트 평가 관점의 핵심 특징 | 출처 |
|---|---|---|---|
| Ragas | 오픈소스 메트릭 라이브러리 | Tool Call Accuracy(인자 정확도 × 시퀀스 정렬 — strict/flexible 순서 모드), Tool Call F1(순서 무관 precision/recall), Agent Goal Accuracy(이진, reference 유/무 두 변형), Topic Adherence | [docs.ragas.io](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/agents/) |
| LangSmith | 상용 관측·평가 플랫폼 | trajectory 캡처(스텝·툴 호출 전체) 기반 평가, evaluator 유형으로 human review / code(heuristic) / LLM-as-judge / pairwise 지원, offline·online 평가 구분, 궤적 매칭용 오픈소스 `agentevals` 제공 | [docs.langchain.com](https://docs.langchain.com/langsmith/evaluation), [trajectory-evals](https://docs.langchain.com/langsmith/trajectory-evals), [agentevals](https://github.com/langchain-ai/agentevals) |
| Braintrust | 상용 평가 플랫폼 | Eval = data + task + scorers 구조, PR마다 eval을 돌리는 CI/CD 통합으로 회귀 게이팅 — evals-as-gate에 가장 직접적 | [braintrust.dev](https://www.braintrust.dev/docs/guides/evals) |
| Arize Phoenix | 오픈소스 관측·평가 | OpenTelemetry 기반 트레이싱 native — OpenInference semantic convention으로 LLM/에이전트 span을 표준화, 수집한 트레이스 위에서 평가 실행 | [Phoenix docs](https://arize.com/docs/phoenix), [OpenInference](https://github.com/Arize-ai/openinference) |
| DeepEval | 오픈소스 평가 프레임워크 | pytest 스타일로 LLM 테스트를 작성·실행("unit testing for LLMs"), 다수의 내장 지표 제공 | [github.com/confident-ai/deepeval](https://github.com/confident-ai/deepeval) |
| TruLens | 오픈소스 평가·트레이싱 | RAG Triad — context relevance, groundedness, answer relevance 세 지표로 RAG 파이프라인의 환각 지점을 삼각측량 | [trulens.org](https://www.trulens.org/getting_started/core_concepts/rag_triad/) |

::: warning 미정착 영역
에이전트 평가 도구 시장은 빠르게 통합·변동 중이다(관측 플랫폼이 평가를 흡수하고, 평가 라이브러리가 트레이싱을 추가하는 방향). 위 표는 집필 시점의 공식 문서 기준이며, 도구 선택보다 **하니스 구조와 데이터셋 소유권을 도구 중립적으로 유지하는 것** — 궤적을 OTel 등 표준 포맷으로 남기고, 데이터셋을 특정 플랫폼에 가두지 않는 것 — 이 더 오래가는 결정이다.
:::

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 프롬프트·툴 스키마 등 매 커밋 수준의 변경 검증 | 계층 1 단위 평가 (결정론적 채점 위주) | 빠르고 싸고 flaky하지 않아 CI에 넣을 수 있음 | 궤적 수준 회귀는 못 잡음 — p만 보고 p^n을 못 봄 |
| 모델 버전 교체, 시스템 프롬프트 대개편, 배포 후보 | 계층 2 궤적 평가 + 핵심 시나리오 pass^k (k≥4) | 1회 성공률은 신뢰성을 과대평가 — pass^8 < pass^1의 절반인 실측 존재 | 비용·시간 — 후보 버전에만 실행 |
| 채점 기준을 코드로 쓸 수 있다 (환경 상태, exact match, 스키마) | 결정론적 채점 | 노이즈 없음, hard gate에 사용 가능 | 기준의 코드화 비용 |
| 채점 기준이 정성적이다 (톤, 요약 품질, 정책 준수의 뉘앙스) | LLM-as-judge ([상세](/03-accuracy-eval/llm-judge-trajectory)) | 사람 없이 스케일 | judge 자체의 노이즈·편향 — hard gate에 단독 사용 금지 |
| 궤적 성공률이 낮은데 원인을 모른다 | 계층 1로 내려가 스텝별 p 분해 + 스텝 수 분포 확인 | p가 낮은 스텝을 고치거나 n을 줄이는 것이 레버 | 분해 계측이 선행되어야 함 |
| 평가 인프라를 어디까지 직접 만들지 | 러너·트레이싱은 도구 도입, 데이터셋·게이트 기준은 직접 소유 | 데이터셋이 경쟁력이고 도구는 교체 가능 | 도구 통합 비용 |
| AWS 관리형으로 구현하고 싶다 | [AWS 평가 서비스](/03-accuracy-eval/aws-evaluations) 참조 | 이 책의 대상 플랫폼 기준 정본 | — |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 스텝별 점수는 전부 90%대인데 end-to-end 성공률이 절반 이하 | 에러 복합 — 궤적 계층 부재, p만 측정하고 p^n을 안 봄 | 평균 스텝 수 n과 스텝 성공률 p로 p^n을 계산해 실측과 대조 | 계층 2 도입, 스텝 수 축소(툴 통합·검증 게이트)를 최우선 레버로 |
| 데모·1회 평가는 통과하는데 프로덕션에서 간헐 실패 | pass^1만 측정 — 일관성 미계측 | 핵심 시나리오를 n회 반복해 pass^k 곡선 산출 | pass^k(k≥4)를 배포 게이트 기준으로 승격 |
| 평가 점수는 계속 오르는데 프로덕션 품질은 제자리 | 평가 세트 과적합 — held-out 부재 | dev 세트와 신규 수집 실패 사례에서의 점수 괴리 확인 | held-out 분리, 프로덕션 실패 사례의 정기 환류 |
| 평가 게이트가 같은 커밋에서 통과/실패를 오간다 | LLM judge 노이즈로 hard gate 구성 | 동일 버전 반복 채점의 점수 분산 측정 | 결정론적 채점만 hard gate, judge는 반복 평균 + soft gate |
| 회귀가 배포 후에야 발견된다 | 평가가 수동 실행 — 파이프라인에 게이트 없음 | 최근 배포들의 평가 실행 시점 확인 | evals-as-gate — CI에서 자동 실행·차단 ([에이전트 CI/CD](/11-builder-agent/agent-cicd)) |
| 평가 케이스가 6개월째 그대로 | 실패 사례 환류 루프 부재 | 데이터셋 커밋 이력과 프로덕션 인시던트 목록 대조 | 계층 3에서 실패 궤적을 회귀 케이스로 전환하는 절차를 온콜 프로세스에 포함 |
| 궤적 평가가 특정 프레임워크 포맷에 묶여 도구 교체 불가 | 트레이스 포맷 비표준 | 궤적 로그가 OTel/OpenInference 등 표준으로 export 되는지 확인 | 표준 semantic convention으로 계측 ([Phoenix/OpenInference](https://github.com/Arize-ai/openinference) 등) |

## 안티패턴

- ❌ "각 스텝이 95%니까 전체도 대략 95%" → ✅ p^n을 계산하라 — 0.95<sup>20</sup> ≈ 35.8%다. 스텝 평가와 궤적 평가는 별도 계층이다.
- ❌ 정확도 개선을 프롬프트 튜닝(p 올리기)에서만 찾기 → ✅ 스텝 수 n 축소가 더 큰 레버 — 툴 통합, 결정론적 검증 게이트, 서브태스크의 워크플로우 강등부터 검토
- ❌ 1회 실행 성공을 "된다"로 보고 → ✅ pass^k로 일관성을 보고 — "가끔 됨"(pass@k)과 "안정적으로 됨"(pass^k)은 다른 지표다
- ❌ LLM judge 점수 단독으로 배포 차단 → ✅ 결정론적 채점으로 hard gate, judge는 반복 평균 + soft gate
- ❌ 평가 세트를 보면서 프롬프트를 반복 수정 → ✅ held-out 분리 — 평가 세트 과적합은 test set 훈련과 같다
- ❌ 합성 데이터로 완벽한 커버리지를 만든 뒤 시작하려는 계획 → ✅ 작게 시작하고 프로덕션 실패 사례를 회귀 케이스로 환류하는 루프를 먼저 돌려라
- ❌ 평가를 분기별 리포트로 운영 → ✅ evals-as-gate — 매 배포 후보에 자동 실행되고 실패 시 차단

## 계측 (SLI)

하니스 자체도 계측 대상이다. 다음을 대시보드로 유지한다.

- **궤적 성공률 (goal accuracy)**: held-out 기준 end-to-end 성공률. 배포 게이트의 1차 지표.
- **pass^k 곡선**: 핵심 시나리오의 k=1,2,4,8 값. pass^1과 pass^4의 격차가 일관성 부채의 크기다.
- **스텝당 성공률 p (툴 호출 정확도 등)와 스텝 수 분포 (p50/p95)**: p^n 분해의 재료. n의 p95가 늘어나는 추세는 성공률 하락의 선행 지표.
- **실측 궤적 성공률 vs p^n 예측의 괴리**: 양의 괴리는 복구 능력, 음의 괴리는 오류 상관(컨텍스트 오염)의 신호.
- **dev vs held-out 점수 괴리**: 과적합 감시.
- **평가 게이트 자체의 재현성**: 동일 버전 반복 채점의 점수 분산. 분산이 크면 게이트가 flaky한 것.
- **데이터셋 신선도**: 최근 30일 내 추가된 회귀 케이스 수, 프로덕션 실패 중 회귀 케이스로 전환된 비율.
- **평가 비용·소요 시간**: 계층 2 실행당 비용과 wall-clock — 게이트가 배포 속도의 병목이 되면 계층 1로 내릴 수 있는 케이스를 찾는다.

## 체크리스트

- [ ] 계층 1(단위)·계층 2(궤적)·계층 3(온라인)이 각각 존재하고 실행 트리거(매 커밋 / 배포 후보 / 상시)가 정의되어 있는가?
- [ ] 궤적 성공률을 스텝 성공률과 **별도 지표**로 계측하는가?
- [ ] 핵심 시나리오에 대해 반복 시행으로 pass^k를 산출하는가? 배포 기준이 pass^1이 아니라 k≥4에 걸려 있는가?
- [ ] 스텝 수 분포를 계측하고, 정확도 개선 검토 시 "n을 줄일 수 있는가"를 p 개선보다 먼저 묻는가?
- [ ] 결정론적으로 채점 가능한 케이스와 LLM judge가 필요한 케이스가 데이터셋에서 구분되어 있는가?
- [ ] hard gate는 결정론적 채점(또는 반복 평균)에만 걸려 있는가?
- [ ] held-out 세트가 dev 세트와 분리되어 있고, held-out의 개별 케이스를 개발 중에 들여다보지 않는가?
- [ ] 프로덕션 실패 궤적을 회귀 케이스로 전환하는 절차가 온콜/운영 프로세스에 포함되어 있는가?
- [ ] 평가가 배포 파이프라인의 자동 게이트인가, 아니면 수동 실행 리포트에 머물러 있는가? ([에이전트 CI/CD](/11-builder-agent/agent-cicd))
- [ ] 궤적 로그가 도구 중립적 표준(OTel/OpenInference 등)으로 남아 도구 교체가 가능한가?
- [ ] judge 설계와 궤적 지표 상세는 [LLM-as-a-judge와 궤적 평가](/03-accuracy-eval/llm-judge-trajectory), AWS 구현은 [AWS 평가 서비스](/03-accuracy-eval/aws-evaluations)의 기준을 따랐는가?

## 참고

- Yao et al., "τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains" — pass^k 정의와 gpt-4o pass^8 < 25% (retail) 실측: <https://arxiv.org/abs/2406.12045>
- Ragas — Agents or Tool Use Cases 메트릭 (Tool Call Accuracy, Agent Goal Accuracy 등): <https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/agents/>
- LangSmith — Evaluation 개요(offline/online, evaluator 유형): <https://docs.langchain.com/langsmith/evaluation>, trajectory 평가: <https://docs.langchain.com/langsmith/trajectory-evals>, agentevals: <https://github.com/langchain-ai/agentevals>
- Braintrust — Eval 구조(data/task/scorers)와 CI/CD 통합: <https://www.braintrust.dev/docs/guides/evals>
- Arize Phoenix: <https://arize.com/docs/phoenix>, OpenInference semantic conventions: <https://github.com/Arize-ai/openinference>
- DeepEval: <https://github.com/confident-ai/deepeval>
- TruLens — RAG Triad: <https://www.trulens.org/getting_started/core_concepts/rag_triad/>
- 이 책 내 관련 챕터: [에이전트 vs 워크플로우](/01-agent-design/agent-vs-workflow) (에러 복합의 설계 함의), [LLM-as-a-judge와 궤적 평가](/03-accuracy-eval/llm-judge-trajectory) (채점기 상세 정본), [AWS 평가 서비스](/03-accuracy-eval/aws-evaluations) (AWS 구현 정본), [에이전트 CI/CD](/11-builder-agent/agent-cicd) (evals-as-gate 파이프라인)
