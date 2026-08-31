---
title: LLM 판정과 trajectory 평가
description: LLM-as-a-judge의 편향과 방어 설계, 그리고 에이전트 trajectory 평가 방법론을 다룬다.
outline: [2, 3]
---

# LLM 판정과 trajectory 평가

::: tip 이 장에서 얻는 것
- LLM-as-a-judge를 언제 써야 하고 언제 쓰면 안 되는지 판단하는 기준
- self-preference / position / verbosity 편향의 연구 근거와 각각의 완화 기법
- 최종 답이 아니라 경로(trajectory)를 평가하는 두 가지 축 — 프로그램적 매칭 vs LLM-judge — 의 구분과 선택 기준
- rubric·rationale-first·pairwise/pointwise 등 판정 프롬프트 설계 원칙
- judge 자체를 사람 라벨과의 상관(Cohen's kappa)으로 검증하는 meta-evaluation 절차
:::

> 이 챕터는 "judge와 trajectory 평가의 방법론"에 집중한다. 평가 하니스의 구조와 도구 선택은 [eval-harness](/03-accuracy-eval/eval-harness), AgentCore Evaluations 등 AWS 관리형 평가 서비스의 상세는 [aws-evaluations](/03-accuracy-eval/aws-evaluations)가 정본이다.

## 왜 문제가 되는가

에이전트의 출력은 정답이 하나가 아니다. "고객 환불 요청을 처리하라"는 태스크의 올바른 응답은 수십 가지 표현으로 존재하고, exact match나 BLEU 같은 문자열 기반 메트릭은 이 다양성 앞에서 무력하다. 그래서 열린 출력(open-ended output)의 품질을 다른 LLM에게 채점시키는 LLM-as-a-judge가 사실상 표준이 됐다. Zheng et al.은 GPT-4급 judge가 사람 선호와 80% 이상 일치함을 보였고, 이는 사람 평가자 간 상호 일치율과 비슷한 수준이었다([arXiv:2306.05685](https://arxiv.org/abs/2306.05685)).

문제는 두 가지다.

첫째, **judge 자체가 편향된 측정기다.** 같은 논문이 position bias, verbosity bias, self-enhancement bias를 실증했고, Wang et al.은 후보 응답의 제시 순서만 바꿔서 80개 질의 중 66개에서 Vicuna-13B가 ChatGPT를 이기게 만드는 데 성공했다([arXiv:2305.17926](https://arxiv.org/abs/2305.17926)). 편향된 judge 위에 세운 평가 파이프라인은 회귀를 놓치거나, 더 나쁘게는 잘못된 방향으로 모델·프롬프트를 최적화하게 만든다.

둘째, **최종 답만 보는 평가는 에이전트에서 성립하지 않는다.** 에이전트는 툴 호출의 연쇄를 거쳐 답에 도달한다. 결과가 우연히 맞았지만 결제 API를 두 번 호출한 실행, 결과는 틀렸지만 경로는 올바랐고 외부 API 장애로 실패한 실행 — 이 둘을 구분하지 못하면 개선 루프가 돌지 않는다. τ-bench는 state-of-the-art function calling 에이전트조차 태스크 성공률이 50% 미만이고, 같은 태스크를 8회 반복했을 때 전부 성공하는 비율(pass^8)이 retail 도메인에서 25% 미만으로 떨어짐을 보였다([arXiv:2406.12045](https://arxiv.org/abs/2406.12045)). 이런 비일관성은 최종 답 채점만으로는 원인을 특정할 수 없고, 경로 수준의 계측이 필요하다.

## 핵심 개념

### LLM-as-a-judge

LLM-as-a-judge는 평가 대상 출력(과 필요 시 입력·컨텍스트·참조 답안)을 평가 전용 프롬프트와 함께 별도의 LLM에 넣어 점수·선호·판정 라벨을 받는 기법이다. 쓰는 조건은 명확하다.

- **써야 할 때**: 정답이 하나로 수렴하지 않는 열린 출력 — 요약 품질, 응답의 helpfulness, 톤 준수, 근거 충실성(faithfulness) — 을 대규모로 채점해야 할 때. 사람 평가는 정확하지만 확장이 안 되고, 규칙 기반 메트릭은 확장되지만 의미를 못 본다.
- **쓰지 말아야 할 때**: 프로그램적으로 검증 가능한 속성 — JSON schema 준수, 툴 인자의 타입, 상태 머신의 최종 상태, 코드의 테스트 통과 — 은 결정적(deterministic) 검사로 채점한다. 검증 가능한 것을 LLM에 묻는 것은 비용과 노이즈를 동시에 추가하는 안티패턴이다.

판정 형식은 두 축으로 나뉜다.

- **pointwise (single-answer grading)**: 출력 하나에 rubric 기반 절대 점수를 매긴다. 프로덕션 트래픽의 상시 모니터링, 회귀 게이트처럼 "기준선 대비 절대 품질"이 필요할 때 쓴다. 점수 스케일의 캘리브레이션이 어렵다는 약점이 있다.
- **pairwise comparison**: 두 출력 중 어느 쪽이 나은지 고른다. 절대 점수보다 판정이 안정적이어서 모델·프롬프트 A/B 비교에 적합하지만, position bias에 직접 노출되고 n개 후보 비교 시 호출 수가 조합적으로 늘어난다([arXiv:2306.05685](https://arxiv.org/abs/2306.05685)).

### judge의 알려진 편향들

**Self-preference bias (자기선호 편향).** 평가 모델이 자기 자신(또는 같은 계열 모델)의 출력을 체계적으로 선호한다. Panickssery et al.은 GPT-4, GPT-3.5, Llama 2가 요약 태스크에서 자기 출력을 불균형하게 선호하며, GPT-4가 자기 출력을 다른 모델·사람의 출력과 73.5% 정확도로 구분해내고, fine-tuning 실험에서 **자기 인식(self-recognition) 능력과 자기선호 강도 사이에 선형 상관**이 있음을 보였다([arXiv:2404.13076](https://arxiv.org/abs/2404.13076)). 단순한 스타일 우연이 아니라 "자기 글임을 알아보고 더 높게 준다"는 인과 구조가 있다는 뜻이다.

이 챕터에서 가장 실무적인 권고 하나를 꼽으라면 이것이다: **평가 대상 모델과 judge 모델은 다른 모델 계열(family)로 고른다.** Claude 기반 에이전트를 Claude judge로만 채점하면 자기선호 편향이 평가 점수에 그대로 섞인다. 최소한 cross-family judge 하나를 반드시 포함하고, 가능하면 서로 다른 계열의 judge 2개 이상으로 합의(ensemble)시켜 계열 편향을 상쇄한다.

**Position bias (위치 편향).** pairwise 비교에서 특정 위치(주로 첫 번째)의 응답을 선호한다. Wang et al.은 순서 조작만으로 판정을 뒤집을 수 있음을 보이고, 두 순서로 모두 평가해 집계하는 Balanced Position Calibration 등을 제안했다([arXiv:2305.17926](https://arxiv.org/abs/2305.17926)). 실무 완화책은 단순하다: 모든 pairwise 판정을 (A,B)와 (B,A) 두 번 실행하고, 판정이 뒤집히면 tie로 처리하거나 재판정에 보낸다. 비용이 2배가 되지만, 편향된 판정으로 잘못된 배포 결정을 내리는 비용보다 싸다.

**Verbosity bias (장황함 편향).** 길고 상세해 보이는 응답을 품질과 무관하게 선호한다. AlpacaEval 팀은 길이 편향을 회귀분석으로 통제한 length-controlled 버전에서 사람 선호(Chatbot Arena)와의 Spearman 상관이 0.94에서 0.98로 오르고, "장황하게 답하라"는 프롬프트 조작에 대한 강건성이 크게 개선됨을 보였다([arXiv:2404.04475](https://arxiv.org/abs/2404.04475)). 완화책: rubric에 "길이는 품질이 아니다, 불필요한 반복·수식은 감점"을 명시하고, 평가 리포트에 응답 길이를 공변량으로 함께 기록해 점수-길이 상관을 주기적으로 점검한다.

이 밖에 Zheng et al.은 수학·추론 문제에서 judge가 자기도 못 푸는 문제의 채점을 틀리는 **limited reasoning capability**도 보고했다 — 참조 답안(reference-guided grading)을 제공하면 크게 완화된다([arXiv:2306.05685](https://arxiv.org/abs/2306.05685)).

### 판정 프롬프트 설계

judge 프롬프트는 평가 시스템의 스펙이다. 원칙 네 가지:

1. **Rubric을 명시한다.** "품질을 1~10으로 평가하라"는 프롬프트는 캘리브레이션 불가능한 노이즈를 만든다. 평가 차원(정확성, 근거 충실성, 정책 준수 등)을 분리하고, 각 점수 구간이 무엇을 의미하는지 앵커 예시와 함께 적는다. G-Eval은 평가 기준에서 세부 평가 단계를 CoT로 전개시키고 form-filling으로 점수를 받는 구조로 NLG 평가에서 사람 상관을 끌어올렸다([arXiv:2303.16634](https://arxiv.org/abs/2303.16634)).
2. **Rationale-first (근거 먼저, 점수 나중).** 점수를 먼저 뱉게 하면 이후의 설명은 사후 합리화가 된다. "각 기준에 대한 분석을 먼저 서술한 뒤 마지막에 구조화된 점수를 출력하라"로 강제한다. 부수 효과로, 사람이 judge의 실패를 디버깅할 때 rationale이 감사 로그가 된다.
3. **출력을 구조화한다.** 점수는 자유 텍스트가 아니라 파싱 가능한 구조(JSON 등)로 받고, 파싱 실패는 조용히 버리지 말고 별도 실패율 메트릭으로 계측한다.
4. **스케일은 좁게.** 1~10보다 1~4나 pass/fail + 사유가 재현성이 높은 경우가 많다. 스케일을 넓힐수록 judge 내 일관성(같은 입력 재평가 시 같은 점수)이 떨어진다. temperature는 0 또는 최저로 고정하고, judge 모델 버전을 pin해서 평가 기준선이 모델 업데이트로 흔들리지 않게 한다.

### Trajectory 평가

trajectory 평가는 최종 답이 아니라 **에이전트가 거친 경로 — 툴 호출의 순서, 선택, 인자 —** 를 채점한다. 접근은 두 갈래다.

**프로그램적 매칭 (deterministic matching).** 태스크별로 기대 trajectory(expected tool sequence)를 ground truth로 정의하고, 실제 호출 시퀀스와 규칙으로 비교한다. 매칭 엄격도는 세 단계가 표준이다.

- **exact order**: 같은 툴, 같은 순서, 여분 호출 없음.
- **in-order**: 기대한 툴들이 순서대로 등장하면 통과, 사이에 다른 호출 허용.
- **any-order**: 기대한 툴이 모두 등장하면 순서 무관 통과.

AgentCore Evaluations의 `Builtin.TrajectoryExactOrderMatch` / `TrajectoryInOrderMatch` / `TrajectoryAnyOrderMatch`가 대표 구현으로, 셋 다 LLM 호출 없이 프로그램적으로 채점한다([AWS docs — Evaluators](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/evaluators.html)). 서비스 구성·요금 등 상세는 [aws-evaluations](/03-accuracy-eval/aws-evaluations) 참조.

프로그램적 매칭의 장점은 결정성 — 싸고, 빠르고, 재현 가능하며, CI 게이트로 바로 쓸 수 있다. 한계는 **유효한 대안 경로를 실패로 채점**한다는 것. 캐시 히트로 조회를 건너뛴 실행, 두 조회의 순서를 바꾼 실행은 의미상 동등해도 exact match에서 떨어진다. 그래서 엄격도 선택이 곧 설계 결정이다: 결제·삭제처럼 순서와 횟수 자체가 계약인 경로엔 exact order, "필요한 정보를 다 모았는가"가 본질인 조회성 태스크엔 any-order를 쓴다.

**LLM-judge 기반 trajectory 평가.** trajectory 전체(사용자 요청, 각 스텝의 reasoning, 툴 호출과 인자, 툴 응답)를 judge에게 주고 "이 경로가 태스크에 대해 합리적이었는가"를 rubric으로 채점한다. 기대 시퀀스를 일일이 정의할 수 없는 열린 태스크, 대안 경로가 많은 태스크, 그리고 "왜 이 툴을 골랐는가"라는 의도 수준의 평가가 필요할 때 쓴다. 대신 위에서 다룬 judge 편향이 trajectory 판정에도 그대로 적용되고 — 특히 스텝이 많고 장황한 trajectory가 후하게 채점되는 verbosity bias 변형 — 토큰 비용이 trajectory 길이에 비례해 커진다. 긴 trajectory는 스텝·구간 단위로 잘라 채점하고 집계하는 편이 판정 품질과 비용 양쪽에 낫다.

두 접근은 대체재가 아니라 **레이어**다. 프로그램적 매칭이 "계약 위반"을 결정적으로 잡고, LLM-judge가 그 위에서 "경로의 질"을 채점한다. 툴 인자에 대해서도 같은 원리가 적용된다 — 인자의 스키마·타입·값 범위는 결정적 검증, 인자의 의미적 적절성("이 검색 쿼리가 사용자 의도를 반영하는가")은 judge. 인자 환각 문제 자체는 [retrieval-and-hallucinated-args](/03-accuracy-eval/retrieval-and-hallucinated-args)에서 다룬다.

### Meta-evaluation: 판정의 판정

judge는 도입하는 순간부터 그 자체가 검증 대상인 측정기다. 원칙: **judge의 점수를 신뢰하려면, judge가 사람 라벨과 얼마나 일치하는지 먼저 측정해야 한다.**

절차는 이렇다.

1. 평가 대상 분포에서 샘플(수백 건 규모)을 뽑아 사람이 같은 rubric으로 라벨링한다. 사람 라벨러도 2인 이상으로 두고 라벨러 간 일치도를 먼저 확인한다 — 사람끼리 합의 못 하는 rubric으로는 judge를 검증할 수 없다.
2. judge 판정과 사람 라벨의 일치도를 계산한다. 클래스 불균형이 있는 채점(대부분 pass인 데이터셋 등)에서는 raw agreement가 착시를 만들므로, 우연 일치를 보정하는 **Cohen's kappa**를 함께 본다. kappa 해석 관례(예: 0.61–0.80을 substantial로 보는 Landis–Koch 척도)와 그 한계는 McHugh의 리뷰를 참조하라([McHugh 2012, PMC3900052](https://pmc.ncbi.nlm.nih.gov/articles/PMC3900052/)). 순위·연속 점수 비교에는 Spearman 상관을 쓴다 — AlpacaEval이 벤치마크 품질을 Chatbot Arena와의 Spearman 상관으로 보고하는 것이 그 예다([arXiv:2404.04475](https://arxiv.org/abs/2404.04475)).
3. 일치도가 기준(팀이 정한 kappa 임계값) 미만이면 rubric과 프롬프트를 수정하고 재측정한다. judge 프롬프트 변경은 코드 변경과 동일하게 취급한다 — 버전 관리하고, 변경 시 사람 라벨 셋에 대해 회귀 테스트를 돌린다.
4. 배포 후에도 judge 판정의 일부를 주기적으로 사람이 스팟 감사(spot audit)해서 drift를 감시한다.

::: warning 미정착 영역
judge ensemble의 구성 방법(몇 개의 judge를, 어떤 계열 조합으로, 어떤 집계 규칙 — 다수결, 평균, 최저점 — 으로 묶는 것이 최적인가)과 kappa 임계값의 절대 기준은 아직 업계 합의가 없다. bias mitigation 전략의 체계적 비교 연구가 진행 중인 영역이며([arXiv:2411.15594 — A Survey on LLM-as-a-Judge](https://arxiv.org/abs/2411.15594)), 현재로서는 "cross-family 최소 1개 + 사람 라벨 대비 정기 검증"이 가장 방어 가능한 보수적 구성이다.
:::

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| JSON schema·타입·상태 등 검증 가능한 속성 | 결정적 검사 (judge 금지) | 재현 가능, 무비용, 노이즈 없음 | 의미적 품질은 못 봄 |
| 열린 출력의 품질(요약, 톤, faithfulness) 대규모 채점 | LLM-as-a-judge + rubric | 사람 평가와 80%+ 일치 가능, 확장성 ([arXiv:2306.05685](https://arxiv.org/abs/2306.05685)) | 편향 관리·meta-evaluation 비용 |
| 모델/프롬프트 A/B 비교 | pairwise + 양방향(순서 교환) 평가 | 절대 점수보다 안정적 판정 | 호출 수 2배, position bias 관리 필요 ([arXiv:2305.17926](https://arxiv.org/abs/2305.17926)) |
| 프로덕션 상시 품질 모니터링 | pointwise + 좁은 스케일 | 기준선 대비 추세 추적 가능 | 점수 캘리브레이션 어려움 |
| judge 모델 선정 | 대상 모델과 **다른 계열** | self-preference bias 차단 ([arXiv:2404.13076](https://arxiv.org/abs/2404.13076)) | 계열별 judge 품질 편차 존재 |
| 순서·횟수가 계약인 경로 (결제, 삭제) | trajectory exact order match | 계약 위반을 결정적으로 검출 | 유효한 대안 경로도 실패 처리 |
| 필수 스텝 사이 유연성 허용 | in-order match | 재시도·보조 호출 허용 | 여분 호출의 낭비는 못 잡음 |
| 정보 수집형 태스크 (순서 무관) | any-order match | 대안 경로에 강건 | 순서 의존 버그를 놓침 |
| 기대 시퀀스 정의 불가능한 열린 태스크 | LLM-judge trajectory 평가 | 의도·합리성 수준 채점 가능 | judge 편향 + trajectory 길이 비례 비용 |
| judge 신뢰성 확보 | 사람 라벨 셋 + Cohen's kappa 정기 측정 | 우연 일치 보정된 검증 ([PMC3900052](https://pmc.ncbi.nlm.nih.gov/articles/PMC3900052/)) | 라벨링 비용, rubric 합의 선행 필요 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| judge 점수는 상승하는데 사용자 만족·태스크 성공률은 정체 | judge에 최적화(Goodhart) — verbosity 등 편향을 학습 | 점수-응답길이 상관 측정, 사람 스팟 감사와 비교 | length-controlled 분석 도입([arXiv:2404.04475](https://arxiv.org/abs/2404.04475)), rubric에 길이 중립 명시 |
| 같은 모델 쌍의 A/B 결과가 실행마다 뒤집힘 | position bias, 단방향 pairwise 평가 | (A,B)/(B,A) 양방향 평가 후 판정 일치율 확인 | 양방향 평가 + 불일치 시 tie 처리 ([arXiv:2305.17926](https://arxiv.org/abs/2305.17926)) |
| 자사 에이전트가 벤치마크에서 유독 후하게 채점됨 | judge가 대상 모델과 같은 계열 — self-preference | cross-family judge로 재채점해 점수 차 비교 | judge를 다른 계열로 교체, ensemble 구성 ([arXiv:2404.13076](https://arxiv.org/abs/2404.13076)) |
| judge 점수 분포가 특정 점수(예: 7/10)에 뭉침 | rubric 부재, 넓은 스케일, 앵커 예시 없음 | 점수 히스토그램 + 동일 입력 반복 채점 분산 확인 | 차원 분리된 rubric, 좁은 스케일, 앵커 예시 추가 |
| 최종 답 정답률은 높은데 프로덕션에서 중복 결제·과다 호출 발생 | 최종 답만 채점, trajectory 미평가 | 트레이스에서 툴 호출 시퀀스 추출해 기대 시퀀스와 대조 | 계약 경로에 exact order match 게이트 추가 |
| trajectory 평가에서 유효한 실행이 대량 실패 | 유연한 태스크에 exact order 적용 (과잉 엄격) | 실패 케이스의 실제 시퀀스를 사람이 리뷰 | in-order/any-order로 완화하거나 대안 경로를 ground truth에 추가 |
| judge 버전 업데이트 후 기준선 전체가 이동 | judge 모델 unpinned, 평가 기준선 미고정 | 고정 golden set을 신·구 judge로 채점해 점수 diff | judge 모델 버전 pin, 교체 시 사람 라벨 셋 재검증 |
| kappa는 높은데 프로덕션 실패를 못 잡음 | 사람 라벨 셋이 프로덕션 분포를 대표하지 못함 | 라벨 셋과 프로덕션 트래픽의 태스크 분포 비교 | 프로덕션 실패 케이스를 라벨 셋에 지속 편입 |

## 안티패턴

- ❌ 에이전트와 같은 계열 모델을 judge로 사용 → ✅ cross-family judge를 최소 1개 포함, 자기선호 편향을 구조적으로 차단 ([arXiv:2404.13076](https://arxiv.org/abs/2404.13076))
- ❌ "1~10점으로 품질을 평가하라" 한 줄 프롬프트 → ✅ 차원 분리 rubric + 앵커 예시 + rationale-first + 구조화 출력
- ❌ pairwise 비교를 한 방향으로만 실행 → ✅ 순서를 바꿔 2회 실행, 불일치는 tie 또는 재판정 ([arXiv:2305.17926](https://arxiv.org/abs/2305.17926))
- ❌ schema 준수·타입 검증까지 judge에게 질문 → ✅ 검증 가능한 것은 결정적 검사, judge는 의미적 품질에만
- ❌ judge 점수를 검증 없이 배포 게이트로 사용 → ✅ 사람 라벨과의 kappa/Spearman으로 judge를 먼저 검증하고, 통과한 judge만 게이트에 편입
- ❌ 최종 답 정답률 하나로 에이전트 품질을 보고 → ✅ trajectory 매칭 + 최종 답 채점 + 반복 실행 일관성(pass^k류, [arXiv:2406.12045](https://arxiv.org/abs/2406.12045))을 분리 보고
- ❌ judge 프롬프트를 스프레드시트에서 수정하고 바로 적용 → ✅ 프롬프트를 코드로 버전 관리, 변경마다 golden set 회귀 테스트

## 계측 (SLI)

judge와 trajectory 평가를 운영에 편입했다면, 평가 시스템 자체의 SLI를 정의한다.

- **judge-사람 일치도**: 사람 라벨 셋 대비 Cohen's kappa(분류 판정) / Spearman(순위·점수). 분기 또는 judge·rubric 변경 시마다 측정. 하락 추세는 rubric drift 또는 분포 이동 신호.
- **judge 자기일관성(self-consistency)**: 동일 입력 반복 채점 시 판정 일치율. temperature 0 고정 후에도 낮다면 rubric이 모호하다는 뜻.
- **position 일관성**: pairwise 양방향 평가에서 판정이 유지되는 비율. 급락하면 두 후보의 품질이 실제로 근접했거나 judge가 열화된 것.
- **점수-길이 상관**: judge 점수와 응답 토큰 수의 상관계수. 유의미하게 양(+)으로 지속되면 verbosity bias 의심 ([arXiv:2404.04475](https://arxiv.org/abs/2404.04475)).
- **trajectory match rate**: 엄격도별(exact/in-order/any-order) 통과율을 분리 집계. exact는 낮고 any-order는 높다면 경로 다양성이 크다는 신호로, ground truth 보강 대상.
- **판정 파싱 실패율**: judge 출력이 구조화 스키마 파싱에 실패하는 비율. 상승은 judge 프롬프트 또는 모델 변경의 조기 경보.
- **평가 비용·지연**: 평가 1건당 judge 토큰 비용과 p95 지연. LLM-judge trajectory 평가는 길이 비례 비용이므로 태스크당 예산 상한을 둔다.

수집 지점은 평가 하니스의 트레이스 계층이며, 하니스 설계는 [eval-harness](/03-accuracy-eval/eval-harness) 참조.

## 체크리스트

- [ ] 검증 가능한 속성(스키마·타입·상태)은 전부 결정적 검사로 분리했고, judge는 의미적 품질에만 쓴다
- [ ] judge 모델이 평가 대상 모델과 **다른 계열**이다 (또는 cross-family ensemble이다)
- [ ] rubric이 평가 차원별로 분리되어 있고, 점수 구간마다 앵커 예시가 있다
- [ ] judge 프롬프트가 rationale-first, 구조화 출력, temperature 고정, 모델 버전 pin 상태다
- [ ] pairwise 평가는 항상 양방향으로 실행하고 불일치 처리 규칙이 정의돼 있다
- [ ] 사람 라벨 셋이 존재하고, judge와의 kappa/Spearman을 측정해 기준을 통과했다
- [ ] 사람 라벨러 간 일치도를 먼저 확인했다 (사람끼리 합의 안 되는 rubric은 재작성)
- [ ] 순서·횟수가 계약인 경로에 trajectory exact order match 게이트가 있다
- [ ] 태스크 성격별로 매칭 엄격도(exact/in-order/any-order)를 구분해 적용했다
- [ ] 최종 답 정답률·trajectory 매칭·반복 일관성을 분리된 지표로 보고한다
- [ ] 점수-길이 상관, 자기일관성, 파싱 실패율을 SLI로 계측 중이다
- [ ] judge 프롬프트·rubric이 버전 관리되고, 변경 시 golden set 회귀 테스트가 돈다
- [ ] 프로덕션 judge 판정의 일부를 주기적으로 사람이 스팟 감사한다

## 참고

- Zheng et al., *Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena* (2023) — <https://arxiv.org/abs/2306.05685>
- Wang et al., *Large Language Models are not Fair Evaluators* (2023) — <https://arxiv.org/abs/2305.17926>
- Panickssery et al., *LLM Evaluators Recognize and Favor Their Own Generations* (2024) — <https://arxiv.org/abs/2404.13076>
- Dubois et al., *Length-Controlled AlpacaEval: A Simple Way to Debias Automatic Evaluators* (2024) — <https://arxiv.org/abs/2404.04475>
- Liu et al., *G-Eval: NLG Evaluation using GPT-4 with Better Human Alignment* (2023) — <https://arxiv.org/abs/2303.16634>
- Yao et al., *τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains* (2024) — <https://arxiv.org/abs/2406.12045>
- Gu et al., *A Survey on LLM-as-a-Judge* (2024) — <https://arxiv.org/abs/2411.15594>
- McHugh, *Interrater reliability: the kappa statistic* (2012) — <https://pmc.ncbi.nlm.nih.gov/articles/PMC3900052/>
- Amazon Bedrock AgentCore — Evaluators (built-in trajectory matchers) — <https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/evaluators.html>
- Amazon Bedrock AgentCore Evaluations GA announcement — <https://aws.amazon.com/about-aws/whats-new/2026/03/agentcore-evaluations-generally-available/>
- 이 책의 관련 챕터: [eval-harness](/03-accuracy-eval/eval-harness) (하니스 구조·도구), [aws-evaluations](/03-accuracy-eval/aws-evaluations) (AgentCore Evaluations 상세), [retrieval-and-hallucinated-args](/03-accuracy-eval/retrieval-and-hallucinated-args) (인자 환각)
