---
title: vLLM KV 캐시
description: PagedAttention과 Automatic Prefix Caching으로 자체 호스팅 LLM 서빙의 KV 캐시를 관리하는 방법을 다룬다.
outline: [2, 3]
---

# vLLM KV 캐시

::: tip 이 장에서 얻는 것
- PagedAttention이 KV 캐시 프래그멘테이션을 해결하는 메커니즘과, 그것이 왜 처리량 상한을 좌우하는지
- Automatic Prefix Caching(APC)의 블록 해싱·LRU eviction 동작과 히트율에 영향을 주는 실제 요인
- SGLang RadixAttention과의 구조적 차이, 그리고 어느 상황에서 어느 쪽이 유리한지
- vLLM이 노출하는 KV 캐시 관련 Prometheus 지표와, 이를 오토스케일 신호로 연결하는 지점(정본은 Part 8)
:::

## 왜 문제가 되는가

이 장은 Part 4의 다른 챕터([프롬프트 캐싱 기초](/04-caching/prompt-caching-basics))와 레이어가 다르다. Anthropic/Bedrock API 레벨 프롬프트 캐싱은 "요청 바디의 어느 구간을 캐시 포인트로 표시할지"를 호출자가 API 계약으로 통제하는 문제다. 반면 이 장은 Llama 등 오픈소스 모델을 vLLM이나 SGLang 같은 서빙 엔진으로 **직접 호스팅**할 때, 그 엔진 내부에서 KV(Key-Value) 캐시를 GPU 메모리에 어떻게 배치·재사용·회수하는지를 다룬다. API 레벨 캐싱은 벤더가 이미 구현한 캐시를 어떻게 "잘 얻어맞힐지"의 문제이고, 이 장은 그 캐시 구현체 자체를 우리가 운영해야 하는 문제다. 관리형 API를 쓰는 팀이라면 이 장은 참고용이고, 셀프호스팅 서빙 인프라를 운영하는 팀이라면 이 장이 SLO에 직결된다.

문제의 본질은 GPU 메모리다. Transformer의 autoregressive 디코딩은 이전 토큰들의 attention key/value 텐서를 매 스텝마다 재사용해야 하며, 이 KV 캐시는 시퀀스 길이와 배치 크기에 비례해 커진다. 초기 서빙 시스템들은 각 요청에 대해 미리 정해진 최대 길이만큼 연속된(contiguous) 메모리를 예약했는데, 실제 생성 길이는 요청마다 다르므로 예약된 공간의 상당 부분이 낭비됐다. vLLM 논문은 기존 시스템들이 KV 캐시 메모리의 60~80%를 낭비한다고 보고한다 [[vLLM: PagedAttention 논문]](https://dl.acm.org/doi/10.1145/3600006.3613165). 이 낭비가 곧 동시 처리 가능한 요청 수의 상한이며, 낭비를 줄이는 것이 처리량과 직결된다.

## 핵심 개념

### PagedAttention: 블록 단위 메모리 관리

PagedAttention은 OS의 가상 메모리 페이징에서 아이디어를 가져와, KV 캐시를 고정 크기 블록(page) 단위로 나누고 논리적 블록을 물리적 블록에 매핑하는 페이지 테이블을 둔다. 시퀀스는 더 이상 연속된 메모리를 요구하지 않으며, 필요한 만큼만 블록을 할당한다. 이를 통해 external fragmentation을 없애고 internal fragmentation을 블록 하나 크기 이하로 제한한다 [[vLLM: PagedAttention 논문]](https://dl.acm.org/doi/10.1145/3600006.3613165). 논문은 이 접근으로 메모리 낭비를 4% 미만으로 낮추고, Orca·FasterTransformer 등 동시대 시스템 대비 2~4배의 처리량 향상을 달성했다고 보고한다 [[vLLM: PagedAttention 논문]](https://dl.acm.org/doi/10.1145/3600006.3613165) [[arXiv:2309.06180]](https://arxiv.org/pdf/2309.06180).

블록 단위 관리의 부수 효과가 더 중요하다. 논리적 블록이 물리적 블록을 가리키는 간접 참조 구조이므로, 여러 시퀀스가 동일한 블록을 공유(copy-on-write)할 수 있다. 이 공유 가능성이 다음 절의 Automatic Prefix Caching의 토대다.

### Automatic Prefix Caching (APC)

APC는 각 KV 캐시 블록을 "블록 내 토큰 + 그 앞의 전체 프리픽스"로 체인 해싱하여 식별한다. 블록 해시는 부모 블록의 해시, 해당 블록의 토큰 ID, 그리고 LoRA ID나 멀티모달 입력 해시 같은 부가 키를 함께 넣어 계산되며(기본 SHA-256), 완성된(full) 블록만 캐시 대상이 된다 [[vLLM 공식 문서: Automatic Prefix Caching]](https://docs.vllm.ai/en/stable/design/prefix_caching/). 새 요청이 들어오면 동일한 해시를 가진 블록이 이미 존재하는지 조회하고, 있으면 해당 블록의 KV 계산을 건너뛴다.

Eviction 정책은 reference count와 LRU의 조합이다. 각 블록은 현재 몇 개의 요청이 참조 중인지를 나타내는 `ref_cnt`를 갖는다. `ref_cnt > 0`인 블록은 eviction 대상에서 제외되며, 요청이 끝나 `ref_cnt`가 0이 되면 블록은 즉시 지워지지 않고 해시를 유지한 채 free queue의 꼬리로 이동해 향후 히트를 기다린다. 새 블록 할당이 필요할 때는 free queue의 head(가장 오래 미참조된 블록)부터 회수한다 [[vLLM 공식 문서: Automatic Prefix Caching]](https://docs.vllm.ai/en/stable/design/prefix_caching/). 즉 "참조 중이 아니면서 가장 오래된 블록"이 먼저 죽는다 — ref count 0이 LRU 후보군에 들어가는 선결 조건이다.

이 설계에서 히트율은 워크로드의 프리픽스 구조에 강하게 좌우된다.

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요
> 벤더 블로그들이 보고하는 히트율/prefill 절감률은 워크로드마다 크게 갈린다. 한 비공식 벤치마크는 안정적인 6k 토큰 정적 컨텍스트를 공유하는 RAG 워크로드에서 88~94%의 prefill 계산 절감을, 장문 문서에 대한 후속 질의(long-document QA)에서는 첫 질의 이후 92~97%를 보고한 반면, 공유 프리픽스가 없는 open-ended Q&A에서는 0~5%에 그쳤다고 밝힌다 [[dev.to: Prefix caching at scale]](https://dev.to/tech_nuggets/prefix-caching-at-scale-when-it-saves-you-80-of-prefill-cost-and-the-eviction-policies-that-5e8). 즉 "잘 구조화된 프롬프트(고정 시스템 프롬프트, 안정적 few-shot, 반복 조회되는 정적 컨텍스트)"라면 80%대 후반~90%대 히트율이 비현실적인 수치는 아니지만, 이는 vLLM이 공식적으로 보증하는 값이 아니라 특정 벤치마크 조건에서의 벤더 측 결과다. 자신의 워크로드로 반드시 재측정해야 한다.

같은 출처는 eviction을 "조용한 킬러"로 지목한다 — GPU 메모리 압박 상황에서는 가장 긴(즉 가장 재사용 가치가 높은) 프리픽스 블록이 먼저 밀려나는 경향이 있고, 이 경우 히트율이 워밍업 후 서서히 감소하는 패턴으로 나타난다 [[dev.to: Prefix caching at scale]](https://dev.to/tech_nuggets/prefix-caching-at-scale-when-it-saves-you-80-of-prefill-cost-and-the-eviction-policies-that-5e8).

### SGLang RadixAttention과의 비교

SGLang은 KV 캐시 블록을 해시 테이블이 아니라 **radix tree**로 관리한다. 트리의 각 엣지가 토큰 시퀀스를 나타내고, 새 요청이 오면 트리를 순회하며 가장 긴 공통 프리픽스(Longest Prefix Match, LPM)를 찾아 그 지점까지의 KV 캐시를 재사용한다. 캐시된 KV 텐서 전체에 대해 LRU eviction을 적용하는 것도 radix tree 구조 위에서 이루어진다 [[SGLang 논문, arXiv:2312.07104]](https://arxiv.org/pdf/2312.07104) [[SGLang 공식 문서: RadixAttention]](https://sgl-project-sglang-93.mintlify.app/concepts/radix-attention).

두 접근의 실질적 차이:

- **vLLM APC**: 블록 해시 기반 exact match. 블록 경계(고정 크기)에서만 공유가 성립하므로, 프리픽스가 블록 경계와 어긋나면(off-by-a-few-tokens) 그 블록 전체가 미스로 처리된다.
- **SGLang RadixAttention**: 트리 구조이므로 토큰 단위로 더 유연하게 공유 지점을 찾을 수 있고, 여러 브랜치(few-shot 예시가 갈라지는 분기형 워크로드 등)의 공유 관리가 트리 순회로 자연스럽게 표현된다.

::: warning 미정착 영역
"vLLM APC vs SGLang RadixAttention 중 어느 쪽이 실제로 더 높은 캐시 효율을 내는가"는 워크로드 의존적이며 공개 합의가 없다. 벤더 블로그류 벤치마크가 다수 존재하지만 방법론이 제각각이라 이 챕터에서 우열을 단정하지 않는다. 자체 워크로드로 두 엔진을 나란히 벤치마크하는 것이 유일하게 신뢰할 수 있는 판단 방법이다.
:::

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 고정 시스템 프롬프트/도구 정의를 다수 요청이 공유 | APC 활성화(vLLM 기본값) | 공유 블록의 prefill 재계산을 스킵 | 블록 경계 불일치 시 부분 미스 발생 가능 |
| few-shot 예시가 요청마다 분기(branching)하는 워크로드 | SGLang RadixAttention 검토 | radix tree가 분기 공유를 자연스럽게 표현 | 엔진 교체 비용, 운영 노하우 재구축 필요 |
| 이미지/LoRA adapter가 요청마다 자주 바뀜 | 캐시 salt 키 분리 또는 APC 범위 제한 | salt 없이 섞으면 캐시 thrashing으로 히트율이 오히려 하락 | salt 세분화는 캐시 재사용 범위를 줄임 |
| 멀티 pod/replica 배포 | 세션 어피니티 있는 라우팅([게이트웨이 세션 어피니티](/04-caching/gateway-session-affinity) 참조) | 관련 요청이 흩어지면 프리픽스 지역성이 깨짐 | 어피니티 라우팅은 로드밸런싱 유연성을 희생 |
| GPU 메모리가 빡빡하고 처리량이 우선 | `gpu_memory_utilization` 상향(예: 0.85→0.92) | 캐시 가능한 프리픽스 작업 집합이 커짐 | OOM 여유 감소, 버스트 트래픽에 취약 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 히트율이 워밍업 후 서서히 감소 | 메모리 압박으로 긴(고가치) 프리픽스 블록이 먼저 evict | `vllm:kv_cache_usage_perc`와 히트율 상관관계 확인 | `gpu_memory_utilization` 상향, 배치 크기/max concurrency 하향 |
| 배포 직후 지연시간 급증 | 롤링 배포로 캐시가 통째로 초기화됨 | 배포 타임스탬프와 `vllm:prefix_cache_hits` 급락 시점 대조 | 배포 직후 일정 시간 prefill-bound 구간을 SLO 예외로 처리하거나 캐시 워밍 요청 선주입 |
| 멀티모달/LoRA 워크로드에서 히트율이 예상보다 낮음 | salt 키 없이 서로 다른 adapter/이미지 입력이 같은 텍스트 프리픽스와 뒤섞여 캐시 thrashing | 요청별 extra hash key(LoRA ID, 이미지 해시) 로깅 후 블록 재사용 패턴 확인 | salt로 캐시 네임스페이스 분리, 또는 adapter별 캐시 예산 별도 관리 |
| 로드밸런서 뒤 멀티 replica에서 히트율이 단일 replica보다 현저히 낮음 | 관련 요청이 서로 다른 pod로 라우팅되어 프리픽스 지역성 소실 | replica별 `vllm:prefix_cache_hits`/`queries` 분산도 비교 | 프리픽스 인지 라우팅 또는 세션 어피니티 도입 |
| APC를 켰는데도 히트가 거의 없음 | 프롬프트 구조상 매 요청 프리픽스가 실질적으로 다름(동적 타임스탬프·요청ID가 앞부분에 삽입 등) | 프롬프트 템플릿에서 가변 요소의 위치 점검 | 가변 요소를 프롬프트 뒤쪽으로 이동, 고정 프리픽스를 앞쪽에 배치 |

## 안티패턴

- ❌ 히트율 지표 없이 "APC를 켰으니 캐시가 알아서 될 것"이라고 가정 → ✅ `vllm:prefix_cache_hits`/`vllm:prefix_cache_queries` 비율을 대시보드화하고 워밍업 이후 추세를 감시
- ❌ 프롬프트 템플릿 앞부분에 타임스탬프·요청 ID·랜덤 nonce를 넣어 매 요청 프리픽스를 사실상 무효화 → ✅ 가변 요소는 프롬프트 뒤쪽(생성 대상에 가까운 위치)에 배치해 앞부분 고정 프리픽스를 보존
- ❌ 멀티 replica 환경에서 라운드로빈으로만 라우팅 → ✅ 프리픽스 인지 라우팅 또는 세션 어피니티로 관련 요청을 같은 replica에 모음
- ❌ `gpu_memory_utilization`을 안전 마진 없이 최대치로 올려 캐시 작업 집합만 극대화 → ✅ 버스트 시 OOM 여유와 캐시 크기의 트레이드오프를 SLO 기준으로 결정
- ❌ vLLM APC와 SGLang RadixAttention 중 벤더 벤치마크 수치만 보고 엔진을 선택 → ✅ 자체 워크로드로 재현 가능한 벤치마크를 돌려 결정

## 계측 (SLI)

vLLM은 V1 메트릭 스펙에서 다음 Prometheus 지표를 노출한다 [[vLLM 공식 문서: Metrics]](https://docs.vllm.ai/en/latest/design/v1/metrics.html):

- `vllm:kv_cache_usage_perc` (Gauge) — 사용 중인 KV 캐시 블록의 비율(0~1). 이 지표가 곧 Part 8에서 다루는 오토스케일 신호의 핵심 입력이다. KV 캐시 활용률을 큐 깊이(`vllm:num_requests_waiting`)와 결합해 스케일링 트리거로 쓰는 상세 설계는 [/08-scaling-cost/autoscaling-signals](/08-scaling-cost/autoscaling-signals)를 정본으로 참고한다. 이 챕터에서는 KV 캐시 관점만 짚는다 — `kv_cache_usage_perc`가 포화에 가까워지면 신규 시퀀스의 블록 할당이 실패하거나 캐시된 블록이 강제로 evict되어 히트율과 처리량이 동시에 악화된다.
- `vllm:prefix_cache_queries`, `vllm:prefix_cache_hits` (Counter) — 히트율 자체는 게이지로 제공되지 않으므로 `rate(vllm:prefix_cache_hits[5m]) / rate(vllm:prefix_cache_queries[5m])` 형태로 직접 계산한다 [[vLLM 공식 문서: Metrics]](https://docs.vllm.ai/en/latest/design/v1/metrics.html).
- `vllm:num_requests_waiting`, `vllm:num_requests_running` (Gauge) — 큐 깊이와 동시 처리 요청 수. KV 캐시 압박과 결합해 봐야 스케일링 판단이 의미를 갖는다.

권장 관측 패턴: 배포 직후 60초는 별도 구간으로 분리해 워밍업 노이즈를 제거하고, 그 이후 히트율의 시간축 추세(감소 여부)를 별도로 알람 조건으로 둔다. 절대 히트율 수치보다 "감소 추세"가 eviction 문제의 선행 신호다.

## 체크리스트

- [ ] APC가 활성화되어 있고 `vllm:prefix_cache_hits`/`queries` 비율이 대시보드에 노출되는가
- [ ] 프롬프트 템플릿에서 가변 요소(타임스탬프, 요청 ID 등)가 프리픽스 앞부분을 오염시키지 않는가
- [ ] `gpu_memory_utilization` 설정값과 OOM 여유 마진이 SLO 기준으로 명시적으로 결정되었는가
- [ ] 멀티 replica 환경에서 프리픽스 지역성을 보존하는 라우팅(세션 어피니티 등)이 적용되어 있는가 — [게이트웨이 세션 어피니티](/04-caching/gateway-session-affinity) 참조
- [ ] `kv_cache_usage_perc`가 Part 8 오토스케일 신호([/08-scaling-cost/autoscaling-signals](/08-scaling-cost/autoscaling-signals))에 실제로 연결되어 있는가
- [ ] LoRA adapter/멀티모달 입력이 섞인 워크로드라면 캐시 salt 분리가 되어 있는가
- [ ] 배포 직후 워밍업 구간을 SLO 예외 처리하거나 캐시 워밍 절차가 있는가
- [ ] vLLM APC vs SGLang RadixAttention 선택이 벤더 벤치마크가 아닌 자체 재현 벤치마크에 근거하는가

## 참고

- [vLLM: Efficient Memory Management for Large Language Model Serving with PagedAttention (SOSP '23)](https://dl.acm.org/doi/10.1145/3600006.3613165)
- [PagedAttention 논문 (arXiv 사본)](https://arxiv.org/pdf/2309.06180)
- [vLLM 공식 문서: Automatic Prefix Caching](https://docs.vllm.ai/en/stable/design/prefix_caching/)
- [vLLM 공식 문서: V1 Metrics 설계](https://docs.vllm.ai/en/latest/design/v1/metrics.html)
- [SGLang: Efficient Execution of Structured Language Model Programs (arXiv:2312.07104)](https://arxiv.org/pdf/2312.07104)
- [SGLang 공식 문서: RadixAttention](https://sgl-project-sglang-93.mintlify.app/concepts/radix-attention)
- [dev.to: Prefix caching at scale — when it saves you 80% of prefill cost, and the eviction policies that quietly turn it into 5%](https://dev.to/tech_nuggets/prefix-caching-at-scale-when-it-saves-you-80-of-prefill-cost-and-the-eviction-policies-that-5e8) (비공식 출처)
- [프롬프트 캐싱 기초](/04-caching/prompt-caching-basics) — API 레벨 캐싱, 이 챕터와 다른 레이어
- [오토스케일 신호](/08-scaling-cost/autoscaling-signals) — KV 캐시 활용률을 스케일링 신호로 쓰는 정본 챕터
- [게이트웨이 세션 어피니티](/04-caching/gateway-session-affinity) — 멀티 replica 환경의 프리픽스 지역성 보존
