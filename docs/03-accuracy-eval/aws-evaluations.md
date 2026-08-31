---
title: AWS 평가 서비스
description: AgentCore Evaluations의 telemetry 기반 3계층 평가와 Bedrock Evaluations의 RAG·LLM-as-a-judge 평가를 플랫폼 관점에서 비교하고 운영 전략을 정리한다.
outline: [2, 3]
---

# AWS 평가 서비스

::: tip 이 장에서 얻는 것
- AgentCore Evaluations가 **라이브 페이로드가 아니라 CloudWatch에 적재된 OTel 스팬을 평가한다**는 아키텍처적 함의를 이해한다
- session·trace·tool call **3계층 평가 모델**과 13개 빌트인 평가자, ground truth 기반 trajectory 평가자의 정확한 사용처를 구분한다
- online / on-demand / batch **세 가지 실행 유형**을 파이프라인 단계(운영 모니터링 / CI 스팟체크 / 회귀 테스트)에 매핑한다
- Bedrock Evaluations의 **retrieve-only vs retrieve-and-generate** 잡 유형으로 검색 문제와 생성 문제를 분리 진단하는 방법을 익힌다
- 자기선호 편향(self-preference bias)을 피하는 judge 모델 선택 원칙과 evals-as-gate로 가는 로드맵을 확보한다
:::

## 왜 문제가 되는가

에이전트 정확도 평가를 자체 구축하면 세 가지 비용이 반복적으로 발생한다. 첫째, **계측 파이프라인**: trace 수집 → 세션 단위 재구성 → judge 호출 → 점수 집계까지의 배관을 직접 유지해야 한다. 둘째, **judge 프롬프트의 품질 관리**: correctness, faithfulness 같은 평가 기준을 프롬프트로 안정화하는 작업은 그 자체가 별도의 prompt engineering 프로젝트다. 셋째, **운영 통합**: 평가 점수를 알람·대시보드·배포 게이트에 연결하는 glue code가 늘어난다.

AWS는 이 스택을 두 서비스로 나눠 관리형으로 제공한다.

- **Amazon Bedrock AgentCore Evaluations** — 에이전트의 실행 궤적(session/trace/tool call)을 평가한다. 평가 대상은 에이전트가 CloudWatch에 남긴 OpenTelemetry(OTel) 스팬이다. ([AgentCore Developer Guide — Evaluation types](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/evaluations-types.html))
- **Amazon Bedrock Evaluations** — 모델 응답 품질과 RAG 파이프라인(Knowledge Bases 또는 외부 RAG)을 LLM-as-a-judge 및 프로그램적 지표로 평가한다. 2025년 3월 GA. ([AWS ML Blog — Evaluate models or RAG systems using Amazon Bedrock Evaluations, now generally available](https://aws.amazon.com/blogs/machine-learning/evaluate-models-or-rag-systems-using-amazon-bedrock-evaluations-now-generally-available/))

두 서비스는 대체재가 아니라 계층이 다르다. Bedrock Evaluations는 "이 모델/이 RAG 파이프라인이 좋은 답을 내는가"를, AgentCore Evaluations는 "이 에이전트가 올바른 도구를 올바른 순서로 호출해 목표를 달성했는가"를 본다. 플랫폼 팀은 둘을 어디에 배치할지 결정해야 하고, 그 결정의 축이 이 장의 내용이다.

## 핵심 개념

### AgentCore Evaluations: telemetry를 평가한다

가장 중요한 아키텍처 특성부터 짚는다. **AgentCore Evaluations는 에이전트의 라이브 요청/응답 페이로드를 가로채서 평가하는 것이 아니라, 에이전트가 CloudWatch Logs에 적재한 OTel 스팬을 평가한다.** online 평가는 지정한 log group을 관찰(watch)하고, batch 평가는 CloudWatch Logs에서 세션을 스스로 발견(discover)하며, on-demand 평가는 호출자가 스팬을 직접 전달한다. ([Batch evaluation — 비교 표](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/batch-evaluations.html))

이 설계의 실무적 함의는 세 가지다.

1. **계측이 전제 조건이다.** OTel 계측이 없거나 스팬 스키마가 깨져 있으면 평가할 대상 자체가 없다. 평가 도입의 첫 단계는 evaluator 선택이 아니라 observability 파이프라인 점검이다.
2. **평가는 요청 경로 밖에서 일어난다.** 평가 지연이 사용자 응답 지연에 얹히지 않는다. 반대로, 평가 결과로 실시간 차단(inline guardrail)을 하는 용도가 아니다 — 그건 Guardrails의 영역이다.
3. **trace에 남는 데이터가 곧 평가 입력이다.** 스팬에 user query, 응답, tool call 파라미터가 담기므로 PII가 그대로 평가 서비스와 CloudWatch에 흐른다. 계측 단계에서 PII 스크러빙을 설계해야 한다.

#### 3계층 평가 모델

AgentCore Evaluations는 에이전트 상호작용을 **session → trace → span**의 계층으로 조직한다. session은 사용자와 에이전트의 대화 전체, trace는 한 번의 요청-응답 왕복, span은 그 안의 개별 작업(tool 호출, retrieval, 텍스트 생성)이다. 평가자는 각 계층에서 동작하며, 계층별로 독립적으로 평가하면 문제의 근원이 tool 선택인지, 응답 생성인지, 세션 수준 계획인지 분리 진단할 수 있다. ([AWS ML Blog — Build reliable AI agents with Amazon Bedrock AgentCore Evaluations](https://aws.amazon.com/blogs/machine-learning/build-reliable-ai-agents-with-amazon-bedrock-agentcore-evaluations/))

#### 13개 빌트인 평가자

13개 빌트인 평가자가 계층별로 배치되어 있다 ([AgentCore FAQs](https://aws.amazon.com/bedrock/agentcore/faqs/), [CDK aws_bedrockagentcore 모듈 문서](https://docs.aws.amazon.com/cdk/api/v2/docs/aws-cdk-lib.aws_bedrockagentcore-readme.html)):

| 계층 | 평가자 | 측정 대상 |
|---|---|---|
| Session | `Builtin.GoalSuccessRate` | 대화가 사용자 목표를 달성했는가 (assertions 기반, LLM-judge) |
| Trace | `Builtin.Correctness` | 사실적 정확성 (ground truth `expectedResponse` 지원, LLM-judge) |
| Trace | `Builtin.Faithfulness` | 제공된 context/source에 근거하는가 |
| Trace | `Builtin.Helpfulness` | 사용자 관점의 유용성 |
| Trace | `Builtin.ResponseRelevance` / `Builtin.Coherence` / `Builtin.Conciseness` / `Builtin.InstructionFollowing` / `Builtin.Refusal` | 응답 품질·지시 준수·부적절한 거부 |
| Trace | `Builtin.Harmfulness` / `Builtin.Stereotyping` | 안전성 |
| Tool call | `Builtin.ToolSelectionAccuracy` | 올바른 tool을 선택했는가 |
| Tool call | `Builtin.ToolParameterAccuracy` | 파라미터를 정확히 추출했는가 |

빌트인 외에 LLM-as-a-judge 또는 custom code 기반의 **custom evaluator**를 session/trace/span 계층에 정의할 수 있다.

#### Trajectory 평가자: LLM 없는 프로그램적 매칭

`expectedTrajectory`(기대 tool 호출 시퀀스)를 ground truth로 주면 세 가지 trajectory 평가자를 쓸 수 있다. **셋 다 session 레벨이며, 프로그램적 매칭이라 LLM을 호출하지 않는다(토큰 사용량 0).** ([Ground truth evaluations](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/ground-truth-evaluations.html))

| 평가자 | 매칭 규칙 |
|---|---|
| `Builtin.TrajectoryExactOrderMatch` | 동일한 tool, 동일한 순서, 추가 호출 불허 |
| `Builtin.TrajectoryInOrderMatch` | 기대 tool이 순서대로 등장하면 통과, 사이에 다른 tool 허용 |
| `Builtin.TrajectoryAnyOrderMatch` | 기대 tool이 모두 등장하면 순서 무관 통과 |

결정론적이고 저렴하므로 CI 회귀 테스트의 1차 게이트로 적합하다. LLM-judge 계열은 그 위에 얹는다 — 이 조합 전략은 [LLM 판정과 trajectory 평가](/03-accuracy-eval/llm-judge-trajectory)에서 상세히 다룬다.

AgentCore CLI로 특정 세션을 즉시 평가하는 형태는 다음과 같다 ([Ground truth evaluations](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/ground-truth-evaluations.html) 문서의 CLI 예시 기준):

```bash
agentcore run eval \
  --agent MY_AGENT \
  --session-id SESSION_ID \
  --evaluator-arn "arn:aws:bedrock-agentcore:::evaluator/Builtin.TrajectoryExactOrderMatch" \
  --expected-trajectory "calculator,weather"   # tool 이름은 comma-separated
```

> ⚠️ `agentcore run eval` 명령과 `--evaluator-arn` 플래그, "tool 이름을 comma-separated 리스트로 전달"하는 방식은 공식 문서에서 확인했으나, `--expected-trajectory` 플래그의 정확한 표기는 CLI 버전에 따라 다를 수 있다 — 사용 전 `agentcore run eval --help`로 확인 필요.

#### 세 가지 실행 유형

([Evaluation types](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/evaluations-types.html), [Batch evaluation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/batch-evaluations.html))

| 항목 | On-demand | Online | Batch |
|---|---|---|---|
| 트리거 | 호출자 개시, 동기 | 지속적, 이벤트 기반 | 호출자 개시, 비동기 |
| 세션 소스 | 호출자가 스팬을 인라인 제공 | log group 관찰 + 샘플링 규칙 | 서비스가 CloudWatch Logs에서 발견 |
| 범위 | 단일 세션 | 샘플링 규칙에 걸리는 모든 세션 | 다수 세션 (기간·session ID·log group 전체) |
| Ground truth | `evaluationReferenceInputs` | 미지원 | `sessionMetadata` 인라인 |
| 결과 | 동기 응답 | CloudWatch metrics·대시보드 | 평가자별 평균 집계 + 세션별 상세(CloudWatch) |
| 용도 | 개발 중 스팟체크, CI | 프로덕션 품질 모니터링 | 베이스라인 측정, pre/post 비교, 회귀 테스트 |

주의할 비대칭이 하나 있다: **online 평가는 ground truth를 지원하지 않는다.** 라이브 트래픽에는 정답이 없기 때문이다. 따라서 `Builtin.Correctness`의 `expectedResponse` 비교나 trajectory 매칭 같은 reference 기반 평가는 on-demand/batch에서만 가능하고, online에는 reference-free 평가자(Helpfulness, Faithfulness 등)를 배치한다. online 평가는 모든 호출을 평가하면 비용이 커지므로(LLM-judge 평가자는 평가 1건 = judge 모델 호출 1건 이상) 샘플링 비율을 설정한다.

batch 평가 CLI는 프로젝트 설정에서 log group을 자동 해석한다 ([Start batch evaluation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/batch-evaluations-start.html)):

```bash
agentcore run batch-evaluation \
  --runtime MyAgent \
  --evaluator Builtin.GoalSuccessRate Builtin.Correctness \
  --ground-truth ground-truth.json \
  --lookback-days 1 --wait
```

### Bedrock Evaluations: 모델과 RAG 파이프라인 평가

Bedrock Evaluations는 LLM-as-a-judge 기반 모델 평가와 RAG 평가를 제공하며 2025년 3월 GA되었다. Bring Your Own Inference responses(BYOI)를 지원하므로 Bedrock 외부 모델·외부 RAG 시스템의 응답도 평가할 수 있다. ([GA 발표 블로그](https://aws.amazon.com/blogs/machine-learning/evaluate-models-or-rag-systems-using-amazon-bedrock-evaluations-now-generally-available/))

#### LLM-as-a-judge 모델 평가의 지표 4범주

judge 모델이 generator 모델의 응답을 채점하는 잡으로, 지표는 **quality, user experience, instruction following, safety** 네 범주로 조직된다 ([AWS ML Blog — LLM-as-a-judge on Amazon Bedrock Model Evaluation](https://aws.amazon.com/blogs/machine-learning/llm-as-a-judge-on-amazon-bedrock-model-evaluation/), [Evaluate model performance using another LLM as a judge](https://docs.aws.amazon.com/bedrock/latest/userguide/evaluation-judge.html)). RAG(Knowledge Base) 평가 기준으로는 response quality 5종(helpfulness, correctness, logical coherence, completeness, faithfulness)과 responsible AI 3종(harmfulness, stereotyping, refusal)이 제공된다 ([AWS ML Blog — Evaluate and improve performance of Amazon Bedrock Knowledge Bases](https://aws.amazon.com/blogs/machine-learning/evaluate-and-improve-performance-of-amazon-bedrock-knowledge-bases/)). ground truth(`referenceResponses`)는 correctness류 지표에 필요하며, judge 평가 자체는 ground truth 없이도 가능하다. custom metric(자체 프롬프트·채점 스케일)도 정의할 수 있다.

judge 방식과 별개로 **프로그램적(computed) 지표** 경로도 있다. Bedrock의 programmatic model evaluation job은 BERTScore·F1 계열의 자동 계산 지표를 태스크 유형별로 제공한다 ([Evaluate the performance of Amazon Bedrock resources](https://docs.aws.amazon.com/bedrock/latest/userguide/evaluation.html)). 단, ROUGE/BLEU/BERTScore류는 표면적 어휘 유사성에 속을 수 있어 의미가 다른 답에도 높은 점수를 줄 수 있다는 한계가 공식 블로그에서도 지적된다 ([Knowledge Base evaluation 블로그](https://aws.amazon.com/blogs/machine-learning/evaluating-rag-applications-with-amazon-bedrock-knowledge-base-evaluation/)).

#### retrieve-only vs retrieve-and-generate — 이 구분이 진단의 핵심이다

RAG 평가 잡은 두 유형이다 ([Evaluate the performance of RAG sources](https://docs.aws.amazon.com/bedrock/latest/userguide/evaluation-kb.html)):

- **Retrieve only** — 검색 단계만 격리 평가. RAG 소스가 반환한 passage의 품질을 본다.
- **Retrieve and generate** — 검색 + 생성 파이프라인 전체를 평가. 최종 응답의 품질을 본다.

이 구분이 중요한 이유는 **오류의 귀속(attribution)** 때문이다. retrieve-and-generate 점수가 낮을 때, 원인이 "검색이 엉뚱한 passage를 가져와서"인지 "검색은 맞았는데 생성이 무시하거나 왜곡해서"인지는 전체 평가만으로는 구분되지 않는다. retrieve-only 점수가 함께 있으면: retrieve-only가 낮으면 chunking·임베딩·인덱스 문제(→ [벡터 검색](/06-vector-search/) 계층), retrieve-only는 높은데 retrieve-and-generate가 낮으면 생성 프롬프트·모델·faithfulness 문제로 진단이 갈린다. RAG 품질 이슈가 오면 두 잡을 항상 쌍으로 돌리는 것이 기본기다.

#### Citation coverage / citation precision

인용을 반환하는 RAG 시스템(Bedrock Knowledge Bases 또는 BYOI로 인용 정보를 제공하는 외부 시스템)에는 **citation precision**(인용한 passage가 실제로 응답을 뒷받침하는가)과 **citation coverage**(응답의 주장이 인용으로 커버되는가) 지표를 쓸 수 있으며, 공식 권고는 두 지표를 항상 함께 보는 것이다 — 한쪽만 보면 "인용은 정확하지만 대부분의 주장이 무인용"인 상태를 놓친다. ([GA 발표 블로그](https://aws.amazon.com/blogs/machine-learning/evaluate-models-or-rag-systems-using-amazon-bedrock-evaluations-now-generally-available/))

### Judge 모델 선택: 자기선호 편향

LLM-as-a-judge 계열 평가자(두 서비스 모두)에서 실무 권고는 하나로 요약된다: **평가 대상 모델과 judge 모델을 다른 모델 계열(family)로 분리하라.** 같은 계열의 judge는 자기 계열 특유의 문체·논증 패턴을 선호하는 방향으로 점수를 왜곡할 수 있다. Bedrock Evaluations는 judge 모델을 여러 종에서 선택하도록 설계되어 있으므로([GA 발표 블로그](https://aws.amazon.com/blogs/machine-learning/evaluate-models-or-rag-systems-using-amazon-bedrock-evaluations-now-generally-available/)), 이 분리는 설정 한 줄의 문제다. 편향의 근거와 완화 전략의 정본은 [LLM 판정과 trajectory 평가](/03-accuracy-eval/llm-judge-trajectory)를 보라 — 이 장에서는 "교차 계열이 기본값"이라는 운영 규칙만 가져간다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 에이전트 tool 호출 순서/선택의 회귀 검증 | AgentCore **on-demand/batch** + `Builtin.Trajectory*Match` | 프로그램적 매칭, LLM 미사용(토큰 0), 결정론적 | expectedTrajectory 데이터셋 유지 비용; 자연어 품질은 못 봄 |
| 프로덕션 에이전트 품질의 지속 모니터링 | AgentCore **online** + reference-free 평가자(Helpfulness, Faithfulness 등) | 라이브 트래픽 샘플링, CloudWatch metrics로 알람 연동 | ground truth 미지원 → Correctness·trajectory 불가; judge 호출 비용은 샘플링 비율에 비례 |
| 프롬프트/모델 변경 전후 비교, 정기 품질 감사 | AgentCore **batch** | 서버 측에서 세션 발견·집계까지 처리, `sessionMetadata`로 ground truth 주입 가능 | 비동기 — 결과까지 폴링 필요 |
| RAG 응답 품질 저하의 원인 격리 | Bedrock RAG eval **retrieve-only + retrieve-and-generate 쌍** | 검색 문제 vs 생성 문제 분리 진단 | 잡 2개 운영; 데이터셋에 ground truth 필요 |
| Bedrock 외부 모델/자체 RAG의 평가 | Bedrock Evaluations **BYOI** | 인퍼런스 응답만 JSONL로 제출하면 동일 지표 적용 | 응답 수집 파이프라인은 직접 구축 |
| 인용 기반 답변의 신뢰성 검증 | citation precision + coverage **동시 사용** | 한쪽만 보면 무인용 주장 또는 허위 인용을 놓침 | 인용 메타데이터가 데이터셋에 있어야 함 |
| 도메인 고유 품질 기준 | 두 서비스 모두 **custom evaluator/metric** | 빌트인이 못 담는 업무 규칙(예: 규정 문구 포함 여부) | judge 프롬프트 품질 관리가 자체 부담으로 회귀 |
| 실시간 유해 응답 차단 | 평가 서비스가 아니라 **Guardrails** | 평가는 telemetry 기반 사후 채점 — 요청 경로에 없다 | — |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| online 평가를 켰는데 점수가 하나도 안 쌓임 | OTel 계측 누락 또는 log group/serviceName 불일치 — 평가는 CloudWatch의 스팬을 읽는다 | 해당 log group에 스팬이 실제 적재되는지 CloudWatch Logs 직접 조회 | ADOT/OTel 계측 먼저 수리; online config의 `logGroupNames`/`serviceNames`를 실제 값과 일치시킴 |
| online 평가에서 Correctness/trajectory가 동작 안 함 | online은 ground truth 미지원 ([Batch evaluation 비교 표](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/batch-evaluations.html)) | evaluation config의 유형 확인 | reference 기반 평가자는 on-demand(`evaluationReferenceInputs`) 또는 batch(`sessionMetadata`)로 이동 |
| judge 점수는 높은데 사용자 불만 증가 | judge와 대상 모델이 같은 계열 → 자기선호 편향, 또는 judge 프롬프트가 실제 품질 기준과 불일치 | 교차 계열 judge로 동일 데이터셋 재평가, 인간 스팟체크와 상관 확인 | judge를 다른 모델 계열로 교체; [llm-judge-trajectory](/03-accuracy-eval/llm-judge-trajectory)의 캘리브레이션 절차 적용 |
| `TrajectoryExactOrderMatch`가 정상 세션도 계속 Fail | 에이전트가 재시도·보조 tool을 호출 — Exact는 추가 호출도 불허 | 실패 세션의 실제 tool 시퀀스를 trace에서 확인 | 재시도가 정상 동작이면 `InOrderMatch`/`AnyOrderMatch`로 완화; 재시도 자체가 문제면 별도 SLI로 추적 |
| retrieve-and-generate 점수 하락, 원인 불명 | 검색/생성 오류가 한 점수에 뭉개짐 | retrieve-only 잡을 같은 데이터셋으로 병행 실행 | retrieve-only 낮음 → 인덱스/chunking 수정; retrieve-only 높음 → 생성 프롬프트·모델·faithfulness 조사 |
| 평가 비용이 급증 | online 샘플링 비율 과다 — LLM-judge 평가 1건마다 모델 호출 발생 | 평가자별 토큰 사용량(batch 결과에 포함)·호출량 확인 | 샘플링 비율 하향, 저렴한 trajectory(프로그램적) 평가자 비중 확대, 고비용 judge는 batch 정기 실행으로 |
| BERTScore/F1은 좋은데 오답이 통과 | 표면적 어휘 유사성 기반 지표의 한계 ([KB evaluation 블로그](https://aws.amazon.com/blogs/machine-learning/evaluating-rag-applications-with-amazon-bedrock-knowledge-base-evaluation/)) | 고점수 오답 샘플을 LLM-judge Correctness로 교차 평가 | 프로그램적 지표는 스크리닝용으로 강등, 최종 판정은 judge + 인간 스팟체크 |
| 평가 결과에 PII 노출 | OTel 스팬에 user query·tool 파라미터가 원문으로 담김 | CloudWatch에 적재된 스팬 원문 감사 | 계측 단계 PII 스크러빙, 평가 결과 log group KMS 암호화·접근 통제 |

## 안티패턴

- ❌ 평가자부터 고르고 계측은 나중에 → ✅ OTel 스팬이 CloudWatch에 올바른 스키마로 쌓이는지부터 검증한다. 평가 대상은 telemetry다.
- ❌ 대상 모델과 같은 계열의 모델을 judge로 사용 → ✅ 교차 모델 계열 judge를 기본값으로 한다 (정본: [llm-judge-trajectory](/03-accuracy-eval/llm-judge-trajectory)).
- ❌ 모든 프로덕션 호출을 LLM-judge로 평가 → ✅ online은 샘플링 + reference-free 평가자, 전수 검증이 필요한 회귀는 batch + trajectory(프로그램적)로 분리한다.
- ❌ retrieve-and-generate 점수 하나로 RAG 품질을 판단 → ✅ retrieve-only 잡을 쌍으로 돌려 검색/생성 오류를 귀속시킨다.
- ❌ citation precision만 추적 → ✅ coverage와 함께 본다 — 인용이 정확해도 대부분의 주장이 무인용이면 신뢰할 수 없는 답이다.
- ❌ 평가 점수를 대시보드에만 두고 배포는 감으로 → ✅ batch 평가를 CI의 게이트로 승격한다(아래 로드맵).
- ❌ `bedrock-agentcore:*` 와일드카드 IAM으로 평가 파이프라인 운영 → ✅ 읽기/쓰기 권한 분리, 평가 결과 log group 접근 통제 — 평가 데이터에는 사용자 대화 원문이 들어 있다.

::: warning 미정착 영역
LLM-judge 점수의 절대값을 조직 간·모델 간 비교 가능한 지표로 취급할 수 있는지는 정착되지 않았다. judge 모델·프롬프트·rating scale이 바뀌면 점수 분포 자체가 이동하므로, 현재의 안전한 사용법은 **동일 judge 구성 하에서의 상대 비교(pre/post, A/B)와 추세 감시**까지다. 절대 임계값 기반 SLO는 judge 구성을 고정하고 인간 평가와의 상관을 주기적으로 재검증하는 조건에서만 의미가 있다.
:::

## 계측 (SLI)

AgentCore Evaluations의 점수는 CloudWatch metrics로 게시되므로, 평가 지표를 일반 SLI처럼 다룰 수 있다. 권장 구성:

- **품질 SLI**: online 평가의 `GoalSuccessRate`(세션 목표 달성), `Faithfulness`(근거성), `ToolSelectionAccuracy` 3종을 1차 지표로. 계층이 다른 지표를 섞어야 회귀의 위치를 좁힐 수 있다.
- **알람**: 평가자별 평균 점수에 대해 임계값 하회 알람. CloudWatch namespace는 케이스 센시티브이므로 `aws cloudwatch list-metrics`로 실제 namespace를 먼저 발견하고 알람을 건다.
- **평가 파이프라인 자체의 건강성**: "평가된 세션 수 / 샘플링 기대치" 비율을 감시한다 — 이 값이 떨어지면 품질이 아니라 계측이 죽은 것이다(위 실패 모드 1행).
- **비용 SLI**: batch 결과에 포함되는 평가자별 토큰 사용량을 집계해 "평가 비용 / 서빙 비용" 비율을 추적한다. trajectory 평가자는 토큰 0이므로 분모 확대에 유리하다.
- **RAG SLI**: 정기 batch로 retrieve-only와 retrieve-and-generate 점수를 각각 시계열화 — 두 곡선의 괴리가 커지는 시점이 생성 계층 회귀의 신호다.

배포 파이프라인 연동: batch 평가(`--wait --json`)를 CI 단계로 넣어 베이스라인 대비 하락 시 배포를 차단하는 **evals-as-gate** 패턴은 [에이전트 CI/CD](/11-builder-agent/agent-cicd)에서 구체화하며, 전사 로드맵상 위치는 [여섯 가지 통점](/00-intro/six-pain-points)의 3단계에 해당한다.

## 체크리스트

- [ ] 에이전트가 OTel 계측되어 스팬이 CloudWatch Logs에 적재되는가 (평가의 전제 조건)
- [ ] 스팬에 담기는 user query·tool 파라미터에 PII 스크러빙을 적용했는가, 평가 결과 log group에 KMS 암호화·접근 통제를 걸었는가
- [ ] online 평가: 샘플링 비율을 명시적으로 설정했는가, reference-free 평가자만 배치했는가
- [ ] 회귀 테스트: expectedTrajectory 데이터셋과 `Builtin.Trajectory*Match`(매칭 강도는 재시도 정책에 맞게)를 batch/on-demand로 구성했는가
- [ ] Correctness류 reference 기반 평가에 ground truth(`evaluationReferenceInputs` / `sessionMetadata` / `referenceResponses`)를 공급하는가
- [ ] judge 모델이 평가 대상 모델과 **다른 계열**인가
- [ ] RAG: retrieve-only와 retrieve-and-generate 잡을 쌍으로 운영하는가
- [ ] 인용 기반 시스템이면 citation precision과 coverage를 함께 추적하는가
- [ ] 평가자별 평균 점수에 CloudWatch 알람이 걸려 있는가, "평가된 세션 수" 자체도 감시하는가
- [ ] 프로그램적 지표(BERTScore/F1)를 최종 판정이 아닌 스크리닝으로만 쓰는가
- [ ] batch 평가를 배포 전 게이트로 연결할 계획이 로드맵에 있는가 ([agent-cicd](/11-builder-agent/agent-cicd))

## 참고

- [Amazon Bedrock AgentCore — Evaluation types](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/evaluations-types.html)
- [Amazon Bedrock AgentCore — Ground truth evaluations (trajectory 평가자, CLI 예시)](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/ground-truth-evaluations.html)
- [Amazon Bedrock AgentCore — Batch evaluation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/batch-evaluations.html) / [Start batch evaluation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/batch-evaluations-start.html)
- [Amazon Bedrock AgentCore FAQs (13개 빌트인 평가자)](https://aws.amazon.com/bedrock/agentcore/faqs/)
- [AWS ML Blog — Build reliable AI agents with Amazon Bedrock AgentCore Evaluations (3계층 모델)](https://aws.amazon.com/blogs/machine-learning/build-reliable-ai-agents-with-amazon-bedrock-agentcore-evaluations/)
- [AWS CDK — aws_bedrockagentcore 모듈 (OnlineEvaluationConfig, 평가자 계층)](https://docs.aws.amazon.com/cdk/api/v2/docs/aws-cdk-lib.aws_bedrockagentcore-readme.html)
- [Amazon Bedrock — Evaluate the performance of Amazon Bedrock resources](https://docs.aws.amazon.com/bedrock/latest/userguide/evaluation.html)
- [Amazon Bedrock — Evaluate the performance of RAG sources (retrieve-only vs retrieve-and-generate)](https://docs.aws.amazon.com/bedrock/latest/userguide/evaluation-kb.html)
- [Amazon Bedrock — Evaluate model performance using another LLM as a judge](https://docs.aws.amazon.com/bedrock/latest/userguide/evaluation-judge.html)
- [AWS ML Blog — Evaluate models or RAG systems using Amazon Bedrock Evaluations, now generally available (2025-03, citation metrics, BYOI)](https://aws.amazon.com/blogs/machine-learning/evaluate-models-or-rag-systems-using-amazon-bedrock-evaluations-now-generally-available/)
- [AWS ML Blog — LLM-as-a-judge on Amazon Bedrock Model Evaluation (지표 4범주)](https://aws.amazon.com/blogs/machine-learning/llm-as-a-judge-on-amazon-bedrock-model-evaluation/)
- [AWS ML Blog — Evaluating RAG applications with Amazon Bedrock knowledge base evaluation](https://aws.amazon.com/blogs/machine-learning/evaluating-rag-applications-with-amazon-bedrock-knowledge-base-evaluation/)
- [AWS ML Blog — Evaluate and improve performance of Amazon Bedrock Knowledge Bases (RAG 지표 8종)](https://aws.amazon.com/blogs/machine-learning/evaluate-and-improve-performance-of-amazon-bedrock-knowledge-bases/)
- 관련 장: [LLM 판정과 trajectory 평가](/03-accuracy-eval/llm-judge-trajectory) · [에이전트 CI/CD](/11-builder-agent/agent-cicd) · [여섯 가지 통점](/00-intro/six-pain-points)
