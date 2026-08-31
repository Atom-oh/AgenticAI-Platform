---
title: 오토스케일 신호
description: LLM 서빙의 오토스케일 신호는 CPU/GPU utilization이 아니라 큐 깊이와 KV 캐시 활용률이라는 입장을 근거와 함께 정리하고, KEDA/HPA 기반 구현과 드레이닝 정책까지 다룬다.
outline: [2, 3]
---

# 오토스케일 신호

::: tip 이 장에서 얻는 것
- 왜 CPU/GPU utilization % 기반 HPA가 LLM 서빙 워크로드에서 사실상 무용한지 — GPU utilization 지표의 정의 자체에서 출발하는 논증
- 진짜 포화 신호인 큐 깊이(`vllm:num_requests_waiting`)와 KV 캐시 활용률(`vllm:kv_cache_usage_perc`)의 의미와 이를 스케일링 트리거로 조합하는 방법
- Kubernetes(EKS)에서 KEDA + Prometheus로 이 신호를 HPA에 연결하는 구체적 구성 — AWS 공식 문서의 예시 임계값 포함
- 프리스케일링(예측 기반 선제 확장)과 스케일다운 드레이닝(진행 중인 긴 생성 요청의 우아한 종료) 설계
- 이 문제가 관리형(Bedrock, AgentCore Runtime)에서는 누구의 책임이고 자체 호스팅(vLLM/SGLang)에서는 왜 플랫폼 엔지니어의 책임인지의 경계선
:::

## 왜 문제가 되는가

이 챕터는 단호한 기술 입장을 가진 챕터다. **LLM 서빙의 오토스케일 신호는 CPU/GPU utilization %가 아니라 큐 깊이(대기 요청 수)와 KV 캐시 활용률이다.** 이것은 취향의 문제가 아니라, GPU utilization이라는 지표가 측정하는 것과 LLM 추론의 병목이 실제로 발생하는 지점이 구조적으로 어긋나 있기 때문에 나오는 결론이다. AWS의 EKS 공식 문서 역시 vLLM 추론 배포의 오토스케일 신호로 CPU가 아닌 "request queue depth와 end-to-end latency"를 사용한다 [[AWS EKS 공식 문서: Autoscale AI model inference on GPUs]](https://docs.aws.amazon.com/eks/latest/userguide/ml-inference-autoscaling.html).

먼저 책임 경계를 명확히 하자. 이 문제는 **누가 서빙 인프라를 소유하느냐**에 따라 완전히 다른 문제가 된다.

- **Bedrock 같은 관리형 추론 API**: 모델 서버의 스케일링은 AWS의 책임이다. 호출자 입장에서 보이는 것은 쿼터·스로틀링·티어 선택뿐이며, 이는 [동시성·쿼터·스로틀링](/08-scaling-cost/concurrency-quotas-throttling)과 [Bedrock 추론 티어](/08-scaling-cost/bedrock-inference-tiers)에서 다룬다. 이 챕터의 신호 설계는 관리형 API 사용자에게는 해당하지 않는다.
- **AgentCore Runtime 위의 에이전트 코드**: 에이전트 애플리케이션 컨테이너의 스케일링은 세션(`runtimeSessionId`) 단위로 AWS가 관리한다 — 세션마다 전용 microVM이 프로비저닝되고 온디맨드로 확장된다 [[AWS 공식 문서: AgentCore Runtime sessions]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-sessions.html). 여기서 플랫폼 엔지니어가 통제하는 것은 스케일링 신호가 아니라 세션 수명·재사용 전략과 쿼터이며, 정본은 [AgentCore 쿼터와 가격](/10-agentcore/quotas-pricing)이다.
- **자체 호스팅 LLM 서빙(vLLM/SGLang on EKS 등)**: 스케일링 신호 선택, 임계값 결정, 스케일다운 정책 전부가 플랫폼 엔지니어의 책임이다. **이 챕터의 대상 독자가 바로 이 경우다.**

자체 호스팅에서 이 문제를 잘못 풀면 결과는 두 방향 모두 비싸다. 신호가 포화를 늦게 감지하면 사용자는 TTFT(Time To First Token)가 수십 초로 늘어난 뒤에야 replica가 추가되는 것을 경험하고, 신호가 노이즈에 과민하면 GPU 노드가 불필요하게 프로비저닝되어 — GPU 인스턴스는 시간당 비용이 크고 기동도 느리므로 — 비용과 안정성이 동시에 나빠진다. 잘못된 신호(CPU/GPU %)를 쓰면 이 두 실패가 **동시에** 일어난다: 포화는 못 잡고, 노이즈에는 반응한다.

## 핵심 개념

### GPU utilization %는 무엇을 측정하는가 — 그리고 왜 포화 신호가 아닌가

NVIDIA가 NVML로 노출하는 `utilization.gpu`(nvidia-smi의 GPU-Util, DCGM의 `DCGM_FI_DEV_GPU_UTIL`)의 공식 정의는 "지난 샘플 구간 동안 하나 이상의 커널이 GPU에서 실행 중이었던 시간의 비율"이다 [[NVIDIA NVML API Reference: nvmlUtilization_t]](https://docs.nvidia.com/deploy/nvml-api/structnvmlUtilization__t.html). 즉 이 지표는 "GPU가 얼마나 일하고 있는가"가 아니라 "GPU에 커널이 하나라도 떠 있는가"를 시간 비율로 잰 것이다. SM 수천 개 중 하나만 점유한 커널이 계속 돌고 있어도 100%로 찍힌다.

LLM 추론에 이것을 대입하면 문제가 선명해진다. LLM 서빙은 두 단계로 나뉜다 — prefill(프롬프트 전체를 한 번에 처리, compute-bound)과 decode(토큰을 하나씩 autoregressive하게 생성, memory-bandwidth-bound). Splitwise 논문(Microsoft/UW)은 두 단계의 자원 프로파일이 상이함을 정량적으로 보이며, decode 단계가 메모리 대역폭에 묶여 GPU 연산 자원을 충분히 활용하지 못한다는 점을 phase 분리 설계의 근거로 삼는다 [[Splitwise, arXiv:2311.18677]](https://arxiv.org/abs/2311.18677). decode 중인 GPU는 연속 배칭(continuous batching)으로 배치가 반쯤 비어 있든 가득 차 있든 **커널은 계속 실행 중**이므로 utilization.gpu는 양쪽 모두에서 높게 나온다. 다시 말해:

- 배치에 여유가 남아 요청을 더 받을 수 있는 GPU → utilization 높음
- 배치가 가득 차 대기열이 쌓이기 시작한 GPU → utilization 높음

같은 지표 값이 "여유"와 "포화"를 구분하지 못한다면 그 지표는 스케일링 신호로 쓸 수 없다. CPU %는 더 나쁘다 — 추론 연산이 GPU에서 일어나므로 vLLM pod의 CPU는 토크나이징과 스케줄링 오버헤드만 반영하고, CPU가 위험 수위에 도달할 때쯤이면 추론 큐는 이미 깊어진 뒤다.

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요
> "kernel이 peak FLOPS의 5%로 100ms를 돌아도 그 구간의 SM_ACTIVE는 100%로 찍힌다"는 식의 utilization vs. saturation 구분에 대한 상세 논의는 커뮤니티 기술 블로그에 잘 정리되어 있다 [[Understanding NVIDIA GPU Performance: Utilization vs. Saturation]](https://arthurchiao.art/blog/understanding-gpu-performance/). 지표의 공식 정의 자체는 위의 NVML 문서로 확인되지만, 세부 수치·사례는 비공식 출처다.

### 진짜 포화 신호 1: 큐 깊이

vLLM의 스케줄러는 배치에 넣을 수 없는 요청을 대기열에 쌓고, 이 대기열 길이를 `vllm:num_requests_waiting` (Gauge)로 노출한다. 실행 중 요청 수는 `vllm:num_requests_running` (Gauge)이다 [[vLLM 공식 문서: Metrics]](https://docs.vllm.ai/en/stable/design/metrics/). vLLM 공식 문서는 메트릭의 대표적 사용처가 "vLLM 인스턴스의 자동 스케일링 지원"이며, 관리자가 포화 지점을 감지해 오토스케일 규칙을 구현할 수 있도록 필요한 지표를 노출하는 것이 목표라고 명시한다 [[vLLM 공식 문서: Metrics]](https://docs.vllm.ai/en/stable/design/metrics/).

큐 깊이가 좋은 신호인 이유는 정의상 **수요가 현재 용량을 초과한 양** 그 자체이기 때문이다. GPU %처럼 해석이 필요 없다 — 대기열에 요청이 쌓인다는 것은 지금 이 순간 배치에 자리가 없다는 뜻이고, 대기열이 비어 있는데 utilization이 100%라는 것은 그냥 GPU가 정상적으로 일하고 있다는 뜻이다. AWS EKS 문서도 큐 깊이를 "primary demand signal"로 규정한다 [[AWS EKS 공식 문서: Autoscale AI inference with HPA and KEDA]](https://docs.aws.amazon.com/eks/latest/userguide/ml-inference-autoscaling-hpa-keda.html).

### 진짜 포화 신호 2: KV 캐시 활용률

`vllm:kv_cache_usage_perc` (Gauge, 0~1)는 사용 중인 KV 캐시 블록의 비율이다. V1에서 `vllm:gpu_cache_usage_perc`를 대체한 이름이므로 구버전 대시보드를 이식할 때 주의한다 [[vLLM 공식 문서: Metrics]](https://docs.vllm.ai/en/stable/design/metrics/). KV 캐시의 메모리 구조(PagedAttention, 블록 할당)는 [vLLM KV 캐시](/04-caching/vllm-kv-cache)가 정본이고, 이 챕터는 그 지표를 스케일링 신호로 소비하는 쪽의 정본이다.

큐 깊이가 "지금 못 들어온 요청"이라면 KV 캐시 활용률은 **"곧 못 들어오게 될 이유"**를 미리 보여주는 선행 지표다. decode 단계의 동시 처리 상한은 GPU 연산이 아니라 KV 캐시 블록 공급이 결정한다. `kv_cache_usage_perc`가 포화에 접근하면 신규 시퀀스의 블록 할당이 막히고, vLLM은 실행 중 요청을 preempt(축출 후 재계산)하기 시작하며 — V1은 swap을 제거하고 preemption+recompute 전략을 쓴다 [[vLLM 공식 문서: Metrics]](https://docs.vllm.ai/en/stable/design/metrics/) — prefix cache 블록도 밀려나 히트율과 처리량이 함께 무너진다([vLLM KV 캐시](/04-caching/vllm-kv-cache)의 "eviction은 조용한 킬러" 참조). 즉 KV 캐시 압박은 큐가 쌓이기 **전에** 나타나는 용량 신호이며, 특히 긴 컨텍스트 워크로드에서는 큐 깊이보다 먼저 반응한다.

두 신호는 상호 보완이다. 짧은 요청이 폭주하면 큐 깊이가 먼저 움직이고, 긴 컨텍스트·긴 생성 요청이 늘면 KV 캐시가 먼저 움직인다. 하나만 보면 반대쪽 워크로드 변화에 늦는다.

### 보조 신호: 레이턴시 SLO 가드레일

큐 깊이·KV 캐시가 "원인" 지표라면, `vllm:e2e_request_latency_seconds`·`vllm:time_to_first_token_seconds` 히스토그램은 "결과" 지표다 [[vLLM 공식 문서: Metrics]](https://docs.vllm.ai/en/stable/design/metrics/). AWS EKS 문서의 권장 구성은 큐 깊이를 primary trigger로, p95 end-to-end latency를 SLO guardrail trigger로 병행한다 — 큐가 아직 쌓이지 않았어도 p95 레이턴시가 임계값을 넘으면 스케일업한다 [[AWS EKS 공식 문서: HPA and KEDA]](https://docs.aws.amazon.com/eks/latest/userguide/ml-inference-autoscaling-hpa-keda.html). 레이턴시만 단독으로 쓰지 않는 이유는 결과 지표라서 반응이 늦고, 요청 길이 분포 변화(더 긴 생성 요청)에도 움직여 용량 신호로는 오염되기 때문이다.

### Kubernetes 구현: KEDA + Prometheus → HPA

구현 경로는 두 가지다 — HPA custom/external metrics(Prometheus Adapter 경유)와 KEDA. AWS EKS 공식 문서는 GPU 추론 워크로드에 KEDA를 권장하는데, 이유는 (1) idle 시 scale-to-zero로 GPU 비용 절감, (2) activation threshold와 target threshold의 분리로 일시적 스파이크에 GPU를 깨우지 않음, (3) 별도 metrics adapter 없이 Prometheus를 직접 조회하는 단순한 구성이다 [[AWS EKS 공식 문서: HPA and KEDA]](https://docs.aws.amazon.com/eks/latest/userguide/ml-inference-autoscaling-hpa-keda.html). KEDA는 결국 표준 HPA를 생성·관리하므로 HPA behavior 설정을 그대로 쓸 수 있다.

AWS 문서의 예시 ScaledObject 구성(값은 해당 문서의 실측 절차에서 나온 예시이며, 자기 모델·GPU·요청 분포로 재측정해야 한다) [[AWS EKS 공식 문서: HPA and KEDA]](https://docs.aws.amazon.com/eks/latest/userguide/ml-inference-autoscaling-hpa-keda.html):

```yaml
triggers:
  # Primary: 큐 깊이 (수요 > 용량)
  - type: prometheus
    metricType: AverageValue        # 합계를 replica 수로 나눠 per-pod 임계값으로 해석
    metadata:
      query: sum(vllm:num_requests_waiting) or vector(0)
      threshold: "25"               # pod당 대기 25건에서 스케일업
      activationThreshold: "1"      # 대기 0건이면 minReplica 유지 (idle에 GPU 안 깨움)
  # SLO guardrail: p95 e2e latency
  - type: prometheus
    metricType: AverageValue
    metadata:
      query: histogram_quantile(0.95, sum(rate(vllm:e2e_request_latency_seconds_bucket[1m])) by (le)) or vector(0)
      threshold: "5"                # p95 5초 초과 시 스케일업
```

주의할 디테일 세 가지, 모두 위 AWS 문서에서 확인된다.

- `or vector(0)`: 대기 요청이 없을 때 Prometheus가 빈 결과를 반환하면 trigger가 inactive로 빠지므로 0으로 강제한다.
- **비대칭 behavior**: scale-up은 공격적으로(stabilization 30초, 분당 최대 2 pod), scale-down은 보수적으로(stabilization 300초, 2분당 1 pod). 큐가 쌓였다는 것은 사용자가 이미 기다리고 있다는 뜻이고, GPU pod는 기동(노드 프로비저닝 + 모델 로드)이 느려서 성급하게 줄인 용량은 곧 다시 필요해진다.
- **2계층 스케일링**: KEDA/HPA는 pod를 늘릴 뿐이고, pod가 Pending이면 Karpenter가 GPU 노드를 프로비저닝한다. pod 스케일링 신호와 노드 스케일링은 별개 레이어다.

KV 캐시 trigger는 같은 패턴으로 `avg(vllm:kv_cache_usage_perc)`를 세 번째 Prometheus trigger로 추가하면 된다 — KEDA는 여러 trigger 중 가장 많은 replica를 요구하는 쪽을 따른다 [[AWS EKS 공식 문서: HPA and KEDA]](https://docs.aws.amazon.com/eks/latest/userguide/ml-inference-autoscaling-hpa-keda.html). Red Hat도 KServe 환경에서 동일하게 KEDA + vLLM 메트릭 조합을 문서화하고 있어, 이 패턴은 특정 벤더에 갇힌 구성이 아니다 [[Red Hat Developer: KServe autoscaling for vLLM with KEDA]](https://developers.redhat.com/articles/2025/09/23/how-set-kserve-autoscaling-vllm-keda).

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요
> KV 캐시 활용률의 구체적 임계값으로 "85% 초과 시 경보"를 제시하는 자료가 있으나 [[ScaleOps: vLLM on Kubernetes]](https://scaleops.com/blog/vllm-kubernetes/), 이는 vLLM 공식 권고값이 아니다. 적정 임계값은 요청 길이 분포와 `gpu_memory_utilization` 설정에 좌우되므로, preemption 발생이 시작되는 지점을 자기 워크로드에서 실측해 그보다 낮게 잡는 것이 원칙이다.

### 프리스케일링: 반응형 신호의 한계 보완

큐 깊이 기반 스케일링은 반응형이다 — 큐가 쌓인 뒤에 replica를 추가하고, GPU 노드 프로비저닝 + 모델 로드가 끝날 때까지(수 분 단위) 그 요청들은 기다린다. 트래픽 패턴이 예측 가능하다면(업무 시간 시작, 정기 배치) 반응하지 말고 선제하라. 가장 단순하고 견고한 도구는 KEDA의 cron scaler로, 지정 시간 구간 동안 최소 replica 수를 끌어올린다 [[KEDA 공식 문서: Cron scaler]](https://keda.sh/docs/2.17/scalers/cron/). cron trigger를 Prometheus trigger와 같은 ScaledObject에 병치하면 "예측 구간에는 바닥을 올리고, 예측 밖 수요는 큐 깊이가 잡는" 구성이 된다. ML 기반 트래픽 예측으로 더 정교하게 갈 수도 있으나, 예측 모델의 오류가 곧 비용/SLO 오류가 되므로 cron으로 충분한 조직이 대부분이다.

콜드스타트 비용(모델 로드 시간 단축, 이미지/모델 캐싱)은 이 신호 설계와 독립적인 최적화 축이며 [콜드스타트와 세션 유휴](/08-scaling-cost/coldstart-session-idle)에서 다룬다. 콜드스타트가 길수록 프리스케일링의 가치와 scale-down 보수성의 근거가 커진다는 관계만 기억하면 된다.

### 스케일다운과 드레이닝: 긴 생성 요청을 죽이지 않고 줄이기

LLM 서빙의 스케일다운이 일반 stateless 서비스와 다른 점은 **요청 하나가 수십 초~수 분짜리 스트리밍 생성**일 수 있다는 것이다. pod를 즉시 죽이면 진행 중이던 생성이 클라이언트 입장에서 스트림 중단으로 나타난다. 설계 원칙은 Kubernetes의 표준 종료 시퀀스를 LLM 요청 길이에 맞게 늘려 쓰는 것이다.

1. **HPA behavior로 빈도를 통제한다**: `scaleDown.stabilizationWindowSeconds`(위 예시에서 300초)로 flapping을 막는다. 이는 Kubernetes HPA의 표준 기능이다 [[Kubernetes 공식 문서: HPA configurable scaling behavior]](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/#configurable-scaling-behavior).
2. **종료 시퀀스를 이해하고 활용한다**: pod 삭제가 시작되면 kubelet이 컨테이너에 TERM 신호를 보내기 전에 `preStop` hook을 실행하며, 동시에 pod는 Service 엔드포인트에서 제거되어 신규 트래픽이 끊긴다. `terminationGracePeriodSeconds`(기본 30초)가 지나면 KILL이 강제된다 [[Kubernetes 공식 문서: Pod termination]](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-termination). LLM 서빙에서는 기본 30초가 거의 항상 부족하다 — 최대 생성 시간(max_tokens × TPOT + 여유)을 커버하도록 `terminationGracePeriodSeconds`를 늘리고, `preStop`에서 "신규 수락 중단 후 in-flight 완료 대기"를 구현한다.
3. **드레이닝 판단은 서버의 지표로 한다**: `vllm:num_requests_running`이 0이 될 때까지(또는 grace period 상한까지) 대기하는 preStop 스크립트가 가장 정직한 구현이다. 로드밸런서/게이트웨이 레이어에서도 deregistration 지연이 스트리밍 응답 길이를 커버하는지 함께 확인해야 한다 — 세션 어피니티가 걸린 라우팅이라면 pod 제거가 어피니티 재배치를 유발한다는 점도 [게이트웨이 세션 어피니티](/04-caching/gateway-session-affinity)와 엮인다.

::: warning 미정착 영역
어디까지 기다려주고 어디서 끊을지 — "드레이닝 상한을 최대 생성 시간만큼 늘린다 vs. 일정 시점에 끊고 클라이언트 재시도로 넘긴다"는 업계 합의가 없는 영역이다. grace period를 수 분으로 늘리면 노드 회수(특히 Spot 중단, 노드 드레인)와 충돌하고, 짧게 끊으면 스트림 중단이 사용자에게 노출된다. 절충안으로 "신규 수락 즉시 중단 + in-flight는 N초 상한까지 대기 + 초과분은 클라이언트 측 재개(resumable stream) 처리"가 논의되지만 표준 패턴으로 정착하지 않았다. 자기 워크로드의 생성 길이 p99와 인프라의 노드 회수 정책 사이에서 명시적으로 결정하고 문서화하라.
:::

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| Bedrock 등 관리형 추론 API 사용 | 이 챕터의 신호 설계 불필요 — 쿼터/티어 관리로 대체 | 서버 스케일링은 AWS 책임 | 스케일링 통제권 없음, 스로틀링 대응 필요([동시성·쿼터](/08-scaling-cost/concurrency-quotas-throttling)) |
| AgentCore Runtime 위 에이전트 코드 | 세션 수명/재사용 전략 관리 | 세션 단위 microVM 스케일링은 AWS 관리 | 신호 튜닝 불가, 쿼터가 상한([AgentCore 쿼터](/10-agentcore/quotas-pricing)) |
| 자체 호스팅 vLLM/SGLang, 짧은 요청 위주 | `vllm:num_requests_waiting` primary + p95 latency guardrail | 큐 깊이가 수요 초과분을 직접 측정 | 임계값을 워크로드별 실측으로 도출해야 함 |
| 자체 호스팅, 긴 컨텍스트/긴 생성 위주 | 위 조합에 `vllm:kv_cache_usage_perc` trigger 추가 | KV 캐시가 큐보다 먼저 포화하는 워크로드 | trigger가 늘수록 튜닝 축도 늘어남 |
| 구현 도구 선택 | KEDA (Prometheus Adapter 기반 HPA custom metrics 대신) | scale-to-zero, activation threshold, adapter 불필요 | KEDA 오퍼레이터 운영 부담 추가 |
| 트래픽이 시간대 패턴을 가짐 | KEDA cron scaler로 프리스케일링 병행 | GPU 기동이 느려 반응형만으로는 SLO 방어 불가 | 예측 빗나가면 유휴 GPU 비용 |
| 유휴 시간대가 길고 콜드스타트를 감내 가능 | `minReplicaCount: 0` (scale-to-zero) | 유휴 GPU 비용 제거 | 첫 요청이 전체 콜드스타트를 맞음([콜드스타트](/08-scaling-cost/coldstart-session-idle)) |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| GPU util 90%+인데 replica가 늘지 않고 TTFT 폭증 | CPU/GPU % 기반 HPA — utilization은 포화를 표현하지 못함 | `vllm:num_requests_waiting` 상승과 HPA TARGETS 무반응 대조 | 큐 깊이 + KV 캐시 기반 trigger로 교체 |
| 스케일업은 됐는데 새 pod가 오래 Pending | pod 스케일링 신호만 설계하고 노드 레이어(GPU 노드 프로비저닝) 미설계 | `kubectl get pods`의 Pending 사유가 GPU 리소스 부족인지 확인 | Karpenter 등으로 노드 오토스케일링 연동 [[AWS EKS 문서]](https://docs.aws.amazon.com/eks/latest/userguide/ml-inference-autoscaling.html) |
| 트래픽 스파이크마다 replica가 요동(flapping) | scale-down stabilization이 짧거나 대칭적 behavior | HPA 이벤트에서 up/down 반복 주기 확인 | scale-up 공격적/scale-down 보수적 비대칭 behavior 적용 |
| 큐는 안 쌓이는데 처리량 하락·레이턴시 상승 | KV 캐시 포화로 preemption/재계산 발생, 큐 trigger만으로는 미감지 | `vllm:kv_cache_usage_perc` 포화 여부, prefix cache 히트율 하락 확인 | KV 캐시 활용률 trigger 추가, `gpu_memory_utilization`/max concurrency 재조정 |
| 대기 0건인데 KEDA가 스케일업 시도 또는 trigger inactive | PromQL 빈 결과 처리 누락 | 쿼리를 Prometheus에서 직접 실행해 빈 벡터 확인 | `or vector(0)` 추가 [[AWS EKS 문서]](https://docs.aws.amazon.com/eks/latest/userguide/ml-inference-autoscaling-hpa-keda.html) |
| 스케일다운 때마다 클라이언트 스트림 끊김 | 기본 30초 grace period가 생성 시간보다 짧음, 드레이닝 없음 | pod 종료 타임스탬프와 클라이언트 에러 로그 대조 | `terminationGracePeriodSeconds` 상향 + preStop 드레이닝 [[Kubernetes 문서]](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-termination) |
| vLLM 업그레이드 후 KV 캐시 지표가 사라짐 | V0→V1에서 `gpu_cache_usage_perc` → `kv_cache_usage_perc` 개명 | `/metrics` 엔드포인트에서 실제 노출 이름 확인 | 대시보드/trigger 쿼리를 신규 이름으로 이관 [[vLLM Metrics 문서]](https://docs.vllm.ai/en/stable/design/metrics/) |

## 안티패턴

- ❌ 일반 웹 서비스에서 쓰던 CPU % 기반 HPA 매니페스트를 vLLM Deployment에 재사용 → ✅ `vllm:num_requests_waiting` + `vllm:kv_cache_usage_perc` + p95 latency guardrail 조합으로 재설계
- ❌ `DCGM_FI_DEV_GPU_UTIL`을 "GPU가 바쁜 정도"로 읽고 스케일링 신호로 채택 → ✅ 이 지표는 "커널이 하나라도 실행 중인 시간 비율"임을 인지하고 관측 용도로만 사용 [[NVIDIA NVML]](https://docs.nvidia.com/deploy/nvml-api/structnvmlUtilization__t.html)
- ❌ AWS 문서의 예시 임계값(대기 25건/pod, p95 5초)을 실측 없이 복사 → ✅ 자기 모델·GPU·요청 분포로 부하 테스트를 돌려 포화 지점을 측정한 뒤 임계값 결정 — AWS 문서 자체가 이를 선행 단계로 요구한다
- ❌ scale-up/scale-down behavior를 대칭으로 두기 → ✅ up은 빠르게(사용자가 이미 대기 중), down은 느리게(GPU 기동이 느림)
- ❌ 레이턴시 p95를 유일한 trigger로 사용 → ✅ 레이턴시는 결과 지표이자 요청 길이 분포에도 흔들리므로 guardrail로만 쓰고, 원인 지표(큐·KV 캐시)를 primary로
- ❌ pod 스케일링만 설계하고 스케일다운 시 진행 중 생성 요청은 방치 → ✅ preStop 드레이닝 + 생성 길이를 커버하는 grace period + LB deregistration 지연 정렬
- ❌ Bedrock을 쓰면서 이 챕터의 커스텀 신호 인프라를 구축하려 시도 → ✅ 관리형에서는 쿼터·티어·스로틀링 대응이 올바른 레버 ([동시성·쿼터·스로틀링](/08-scaling-cost/concurrency-quotas-throttling))

## 계측 (SLI)

스케일링 신호 자체가 SLI 후보이므로, "신호로 쓰는 지표"와 "신호의 품질을 감시하는 지표"를 구분해 둔다. 지표 이름은 모두 vLLM V1 기준이다 [[vLLM 공식 문서: Metrics]](https://docs.vllm.ai/en/stable/design/metrics/).

**스케일링 입력(원인 지표)**
- `vllm:num_requests_waiting` — replica당 평균으로 정규화(`metricType: AverageValue`)해 감시. 지속 상승 추세는 용량 부족.
- `vllm:kv_cache_usage_perc` — 포화 접근 여부. preemption이 시작되는 실측 지점을 알람 임계값의 근거로 삼는다.
- `vllm:num_requests_running` — 드레이닝 완료 판단과 배치 점유 추세 관찰용.

**SLO 결과 지표**
- `histogram_quantile(0.95, rate(vllm:e2e_request_latency_seconds_bucket[1m]))` — guardrail trigger 겸 사용자 체감 SLO.
- `vllm:time_to_first_token_seconds`, `vllm:inter_token_latency_seconds` — TTFT는 큐잉+prefill 지연을, ITL은 배치 포화를 반영하므로 분리해서 본다.

**스케일러 자체의 건강**
- HPA `TARGETS` 값과 실제 Prometheus 쿼리 결과의 일치 여부(수집 파이프라인 단절 감지). 쿼리가 빈 결과를 반환하면 스케일러가 조용히 멈추므로, 지표 부재 자체에 알람을 건다.
- 스케일 이벤트 빈도 — flapping은 stabilization 설정 결함의 신호.
- pod 종료 시 강제 KILL 발생률 — 0이 아니면 grace period가 생성 길이를 커버하지 못하고 있다는 뜻.

## 체크리스트

- [ ] 우리 워크로드가 관리형(Bedrock/AgentCore — AWS 책임)인지 자체 호스팅(우리 책임)인지 명확히 구분했는가
- [ ] CPU/GPU utilization %가 스케일링 trigger에서 제거되었는가 (관측 대시보드에만 남기고)
- [ ] `vllm:num_requests_waiting`이 primary trigger이고 per-pod 값으로 정규화되어 있는가
- [ ] `vllm:kv_cache_usage_perc` trigger 또는 알람이 있고, 임계값이 preemption 실측 지점에 근거하는가
- [ ] p95 레이턴시가 guardrail trigger로 병행되고, 단독 신호로 쓰이지 않는가
- [ ] 임계값이 AWS 문서 예시의 복사가 아니라 자기 모델·GPU·요청 분포의 부하 테스트로 도출되었는가
- [ ] scale-up은 공격적, scale-down은 보수적인 비대칭 behavior가 설정되어 있는가
- [ ] PromQL trigger에 `or vector(0)` 등 빈 결과 방어가 들어 있는가
- [ ] pod 레이어(KEDA/HPA)와 노드 레이어(Karpenter)의 스케일링이 모두 연결되어 있는가
- [ ] `terminationGracePeriodSeconds`가 최대 생성 시간을 커버하고, preStop 드레이닝이 `vllm:num_requests_running`을 참조하는가
- [ ] 시간대 패턴 트래픽에 대해 cron 기반 프리스케일링을 검토했는가
- [ ] scale-to-zero 사용 시 첫 요청의 콜드스타트가 SLO 내인지 확인했는가 ([콜드스타트와 세션 유휴](/08-scaling-cost/coldstart-session-idle))
- [ ] vLLM 버전 업그레이드 시 지표 이름 변화(`gpu_cache_usage_perc`→`kv_cache_usage_perc` 등)를 릴리스 노트에서 확인하는 절차가 있는가

## 참고

- [AWS EKS 공식 문서: Autoscale AI model inference on GPUs with Amazon EKS](https://docs.aws.amazon.com/eks/latest/userguide/ml-inference-autoscaling.html) — 큐 깊이 + e2e latency 신호, KEDA/Karpenter 2계층 구조
- [AWS EKS 공식 문서: Autoscale AI inference with HPA and KEDA](https://docs.aws.amazon.com/eks/latest/userguide/ml-inference-autoscaling-hpa-keda.html) — ScaledObject 전체 예시, 임계값·behavior 설정 근거
- [AWS EKS 공식 문서: Find scaling metric thresholds](https://docs.aws.amazon.com/eks/latest/userguide/ml-inference-autoscaling-thresholds.html) — 임계값을 실측으로 도출하는 절차
- [vLLM 공식 문서: Metrics](https://docs.vllm.ai/en/stable/design/metrics/) — 지표 전체 목록, V1 개명 이력, 오토스케일링 사용을 명시한 설계 문서
- [NVIDIA NVML API Reference: nvmlUtilization_t](https://docs.nvidia.com/deploy/nvml-api/structnvmlUtilization__t.html) — utilization.gpu의 공식 정의
- [Splitwise: Efficient generative LLM inference using phase splitting (arXiv:2311.18677)](https://arxiv.org/abs/2311.18677) — prefill/decode 자원 프로파일 차이
- [Kubernetes 공식 문서: Pod Lifecycle — Termination of Pods](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-termination) — preStop, terminationGracePeriodSeconds
- [Kubernetes 공식 문서: HPA — Configurable scaling behavior](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/#configurable-scaling-behavior)
- [KEDA 공식 문서: Cron scaler](https://keda.sh/docs/2.17/scalers/cron/) — 프리스케일링
- [Red Hat Developer: How to set up KServe autoscaling for vLLM with KEDA](https://developers.redhat.com/articles/2025/09/23/how-set-kserve-autoscaling-vllm-keda)
- [AWS 공식 문서: AgentCore Runtime — Use isolated sessions for agents](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-sessions.html) — 세션=microVM 단위 관리형 스케일링
- [Understanding NVIDIA GPU Performance: Utilization vs. Saturation](https://arthurchiao.art/blog/understanding-gpu-performance/) (비공식 출처)
- [ScaleOps: vLLM on Kubernetes](https://scaleops.com/blog/vllm-kubernetes/) (비공식 출처)
- [vLLM KV 캐시](/04-caching/vllm-kv-cache) — KV 캐시 메모리 구조와 지표의 정본
- [게이트웨이 세션 어피니티](/04-caching/gateway-session-affinity) — 스케일 이벤트와 어피니티 재배치의 상호작용
- [콜드스타트와 세션 유휴](/08-scaling-cost/coldstart-session-idle) — scale-to-zero/프리스케일링의 비용 반대축
- [동시성·쿼터·스로틀링](/08-scaling-cost/concurrency-quotas-throttling) — 관리형 API에서의 대응 레버
- [AgentCore 쿼터와 가격](/10-agentcore/quotas-pricing) — AgentCore Runtime의 세션 기반 스케일링과 쿼터
