---
title: 게이트웨이 세션 어피니티
description: 게이트웨이/로드밸런서 레벨의 세션 어피니티로 KV 재사용률을 올리는 방법과, 프리픽스 레벨 라우팅과 세션 레벨 KV 유지를 혼동했을 때 생기는 함정을 다룬다.
outline: [2, 3]
---

# 게이트웨이 세션 어피니티

::: tip 이 장에서 얻는 것
- 로드밸런서/게이트웨이의 스티키 라우팅이 KV 캐시 재사용률에 미치는 영향과, 그것이 KV 캐시 자체의 관리와 어떻게 다른 레이어인지 구분한다.
- "PrefixCacheAffinityRouter"가 실제로 어느 프로젝트 소속이며 어떤 레벨(prefix vs session)에서 동작하는지, 왜 툴콜 경계를 넘는 세션 어피니티의 대안이 될 수 없는지 이해한다.
- ALB/NLB 스티키 세션, SGLang cache-aware load balancer, LMCache/KVFlow 같은 세션·워크플로 레벨 대안의 위치를 결정 표로 정리한다.
- AgentCore Gateway의 MCP 세션 기능이 "세션 어피니티"처럼 보이지만 실제로는 다른 계층의 기능이라는 점을 명확히 한다.
:::

## 왜 문제가 되는가

[vLLM KV 캐시](/04-caching/vllm-kv-cache) 챕터는 **단일 서빙 인스턴스 내부**에서 PagedAttention과 Automatic Prefix Caching(APC)이 KV 캐시 블록을 어떻게 관리하고 재사용하는지를 다룬다. 그 메커니즘은 정확히 그 프로세스/GPU 메모리 안에서만 유효하다.

문제는 프로덕션에서 vLLM(또는 다른 추론 엔진) 인스턴스가 항상 2개 이상 뜬다는 점이다. 로드밸런서가 라운드로빈이나 최소 연결(least-connections)로 요청을 분산하면, 같은 세션의 두 번째 요청이 첫 번째 요청과 다른 인스턴스로 갈 수 있다. 그 인스턴스에는 첫 요청이 만든 KV 캐시 블록이 없으므로 prefill을 처음부터 다시 계산해야 한다 — 캐시가 무의미해진다. TrueFoundry는 이 문제를 "표준 로드밸런서가 prefix caching을 깨뜨린다"고 설명한다.

> 이 장의 범위는 **여러 서버/인스턴스에 걸친 요청 라우팅** 레이어다. "각 인스턴스가 KV를 어떻게 저장·축출하는가"는 다루지 않는다 — 그건 짝 챕터인 [vLLM KV 캐시](/04-caching/vllm-kv-cache)의 몫이다. 두 레이어를 섞어서 설계하면 "라우팅은 맞게 했는데 캐시가 계속 비어 있다" 또는 반대로 "캐시 설정은 맞는데 라우팅이 매번 흩어놓는다" 같은 디버깅 미로에 빠진다.

에이전틱 워크로드에서는 이 문제가 특히 심하다. 하나의 에이전트 세션이 시스템 프롬프트 + 대화 히스토리 + 여러 번의 툴콜 결과를 계속 누적하면서 컨텍스트가 길어진다. 매 턴마다 다른 인스턴스로 튀면 그 누적된 prefix 전체가 매번 재계산된다 — 턴이 길어질수록 손실이 커진다.

이 문제를 푸는 레이어는 크게 세 가지로 나뉜다.

1. **게이트웨이/로드밸런서 스티키 라우팅** — 클라이언트/세션 식별자를 특정 백엔드에 고정한다 (이 장의 주제).
2. **추론 엔진의 prefix-aware 라우터** — 요청의 프롬프트 prefix를 보고 그 prefix를 이미 캐시한 replica로 보낸다.
3. **분리된 KV 캐시 레이어(LMCache 등)** — KV 캐시 자체를 인스턴스 로컬 메모리 밖으로 꺼내 여러 replica가 공유하게 만든다.

이 셋은 겹치지만 동일하지 않다. 아래에서 각각의 정확한 동작 범위를 정리한다.

## 핵심 개념

### 게이트웨이 스티키 세션 (ALB/NLB)

AWS Application Load Balancer(ALB)는 두 가지 쿠키 기반 스티키니스를 지원한다.

- **Duration-based (로드밸런서 생성 쿠키)**: ALB가 `AWSALB` 쿠키를 생성해 클라이언트에 내려주고, 이후 요청은 같은 타깃으로 매핑된다. 스티키니스 지속 시간은 1초~7일(604,800초)로 설정 가능하지만, 쿠키 자체의 만료는 7일로 고정되어 있다. ([AWS 공식 문서](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/sticky-sessions.html))
- **Application-based (애플리케이션 쿠키)**: 애플리케이션이 발급한 쿠키 이름을 지정하면 ALB가 그 쿠키에 `AWSALBAPP-` 접두사를 붙여 매핑에 활용한다. ([AWS 공식 문서](https://docs.aws.amazon.com/prescriptive-guidance/latest/load-balancer-stickiness/app-cookies-stickiness.html))
- **제약**: cross-zone load balancing이 꺼져 있으면 스티키니스를 활성화할 수 없다(활성화 시도 시 실패). ([AWS 공식 문서](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/sticky-sessions.html))

Network Load Balancer(NLB)는 계층이 다르므로(L4) 쿠키를 쓰지 않고 **source IP 기반** 스티키니스를 제공한다. 타깃 그룹 속성 `stickiness.enabled=true`와 `stickiness.type=source_ip`로 설정하며, 프로토콜/소스 IP/소스 포트/대상 IP/대상 포트(TCP는 시퀀스 넘버 포함)로 흐름을 해싱해 같은 타깃에 매핑한다. ([AWS 공식 문서](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/edit-target-group-attributes.html))

> ⚠️ NLB의 source IP 스티키니스는 클라이언트 앞에 NAT/프록시나 이동통신 네트워크가 있으면 소스 IP가 바뀌어 스티키니스가 깨질 수 있다. 이는 AWS 공식 트러블슈팅 문서에도 원인으로 명시되어 있다 ([참고](https://repost.aws/knowledge-center/elb-nlb-stickiness-issues)) — 에이전트 클라이언트가 사내 프록시나 모바일 게이트웨이를 거치는 구성이면 세션 식별자를 쿠키/헤더 기반으로 옮기는 것을 검토해야 한다.

이 레이어는 "세션"을 클라이언트 연결/쿠키 단위로 고정할 뿐, 그 세션이 실제로 어떤 prefix를 누적하고 있는지는 전혀 모른다. 즉 **완전히 라우팅 레이어**이고 KV 인식이 전혀 없다 — 그래서 스티키니스가 걸려 있어도 "같은 세션 → 같은 인스턴스"라는 보장만 있을 뿐, 그 인스턴스가 캐시를 얼마나 잘 유지하는지는 추론 엔진의 몫이다.

### Prefix-aware 라우팅과 "PrefixCacheAffinityRouter"의 정확한 소속

여기서 흔한 오해가 있다. **`PrefixCacheAffinityRouter`는 vLLM 자체의 컴포넌트가 아니라 Ray Serve LLM(`ray.serve.llm.request_router`)이 제공하는 라우터**다. Ray 공식 문서는 이를 명시적으로 `ray.serve.llm.request_router`에서 임포트하는 클래스로 문서화하고 있으며, vLLM 엔진 위에서 동작하되(`enable_prefix_caching=True`를 vLLM engine_kwargs에 설정해야 효과가 있음을 명시) Ray Serve가 관리하는 replica 계층에서 작동한다. ([Ray 공식 문서](https://docs.ray.io/en/latest/serve/llm/prefix-aware-request-router.html))

vLLM 프로젝트 자체에는 "prefix cache aware load balancing"이 이슈로 제안된 적은 있지만([vllm-project/vllm#11477](https://github.com/vllm-project/vllm/issues/11477)), vLLM 엔진 문서(Automatic Prefix Caching)는 단일 엔진 내부 동작만 설명하며 멀티 인스턴스 라우팅을 다루지 않는다 ([vLLM 공식 문서](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/)). 별도로 vLLM 생태계의 `vllm-project/production-stack`이 prefix-aware routing을 라우터 서비스 형태로 제공하며 ([공식 문서](https://docs.vllm.ai/projects/production-stack/en/latest/use_cases/prefix-aware-routing.html)), 2025년 말에는 Rust로 작성된 별도의 "vLLM Router"가 consistent hashing/PoT/round-robin/random 정책을 지원하는 형태로 공개됐다 ([vLLM 블로그](https://vllm.ai/blog/2025-12-13-vllm-router-release)).

정리하면 이름이 비슷한 컴포넌트가 최소 세 갈래로 존재한다.

- Ray Serve LLM의 `PrefixCacheAffinityRouter` — Ray Serve 계층, vLLM engine_kwargs에 의존.
- `vllm-project/production-stack`의 prefix-aware router — vLLM 생태계 프로젝트지만 vLLM 코어와는 별도 레포.
- vLLM Router(Rust, 2025-12) — consistent hashing 기반, prefix cache 인식이 아니라 라우팅 키 기반 sticky 정책.

> ⚠️ 이 셋 모두 **동작 단위는 "prefix"** 다. 즉 라우터는 들어온 요청의 토큰 시퀀스(또는 라우팅 키)를 보고 "이 prefix를 이미 처리한 적 있는 replica가 어디인가"를 판단해 그쪽으로 보낸다. 세션이라는 상위 개념을 추적하거나, 하나의 에이전트 세션이 여러 툴콜을 거치며 쌓아온 컨텍스트를 "이 세션은 계속 인스턴스 A로 보내라"는 식으로 명시적으로 고정하는 것이 아니다. 실무에서는 결과적으로 비슷하게 동작할 때가 많지만(같은 prefix가 반복되면 자연히 같은 replica로 수렴), 이는 **부작용이지 보장이 아니다**. prefix 트리 매칭 결과가 로드 불균형 임계값에 걸리거나, TTL로 캐시 블록이 축출되거나, prefix 매치율이 낮게 나오면 같은 세션의 다음 턴이 다른 replica로 넘어갈 수 있다.

::: warning 미정착 영역
"prefix-aware 라우팅이 있으면 세션 어피니티가 필요 없다"는 주장은 위 이유로 부정확하다. 그런데 정반대로 "prefix-aware 라우팅과 세션 어피니티를 항상 같이 써야 한다"도 과장이다 — 짧은 요청·시스템 프롬프트 공유가 지배적인 워크로드에서는 prefix-aware 라우팅만으로 충분한 사례가 보고된다. 툴콜을 여러 번 거치는 장시간 에이전트 세션에서 세션 단위 보장이 실제로 얼마나 필요한지는 워크로드 프로파일(턴당 컨텍스트 증가량, 인스턴스 수, 캐시 축출 정책)에 따라 달라지며, 공개된 벤치마크가 이 둘을 나란히 비교한 사례는 아직 충분하지 않다. 직접 A/B로 캐시 히트율을 재는 것을 권장한다.
:::

### 세션 단위 KV 재사용이 필요하면: LMCache / KVFlow / SGLang

prefix 레벨 라우팅의 한계를 넘어 세션·워크플로 단위로 KV를 관리하려는 별도 레이어가 있다.

- **LMCache**: KV 캐시를 인스턴스 로컬 메모리 밖(CPU/디스크/원격 스토어)으로 꺼내 저장하고, 토큰 시퀀스를 키로 하는 인덱스를 유지해 **어떤 서빙 프로세스에서든** 재사용 가능하게 만든다. 멀티 프로세스(MP) 모드는 여러 vLLM 인스턴스가 공유 메모리 레이어를 통해 캐시를 공유하도록 지원한다 ([LMCache 공식 문서](https://docs.lmcache.ai/), [예제: 여러 LLM 간 KV 캐시 공유](https://docs.lmcache.ai/getting_started/quickstart/share_kv_cache.html)). 이 접근은 라우팅이 매번 다른 인스턴스로 튀어도 KV 자체가 공유되므로 세션 어피니티의 필요성을 줄여준다 — 다만 원격 캐시 조회/전송 오버헤드가 새로 생긴다는 트레이드오프가 있다.
- **KVFlow**: NeurIPS 2025에 발표된 워크플로 인식 KV 캐시 관리 기법으로, 에이전트 실행 스케줄을 Agent Step Graph로 추상화해 "다음에 재사용될 가능성이 높은" KV 블록을 우선 보존하는 축출 정책과 프리페칭을 제안한다 ([arXiv:2507.07400](https://arxiv.org/abs/2507.07400)). 멀티 에이전트 워크플로 시뮬레이션에서 SGLang 계층형 radix cache 대비 최대 1.83~2.19배 속도 향상을 보고했다.

  > ⚠️ KVFlow는 연구 논문 단계의 결과이며, 이 문서 작성 시점에 vLLM/SGLang 코어에 머지된 프로덕션 기능이 아니다. 프로덕션 채택 전에 자체 벤치마크가 필요하다.

- **SGLang**: RadixAttention이 캐시된 활성값을 전역 radix tree로 관리하고, v0.4부터 **cache-aware load balancer**를 자체 제공해 요청을 "가장 캐시가 warm할 것 같은" replica로 보낸다. SGLang 쪽 자료들은 "로드밸런서가 한 사용자의 세션을 여러 replica에 흩어놓으면 prefix 히트율이 떨어지고 처리량 이득이 줄어든다"고 명시한다.

  > ⚠️ 비공식 출처 기반 — 위 SGLang cache-aware load balancer 관련 수치·설명은 서드파티 블로그/벤치마크 글에서 수집한 것으로, SGLang 공식 문서에서 v0.4 변경 로그와 로드밸런서 세부 파라미터를 직접 교차확인이 필요하다.

### AgentCore Gateway와 세션 어피니티: 다른 문제를 혼동하지 말 것

Amazon Bedrock AgentCore Gateway는 MCP 세션 기능을 제공한다. 공식 문서에 따르면:

- Gateway는 `sessionConfiguration`을 활성화하면 `initialize` 요청 시 `Mcp-Session-Id`를 발급하고, 이후 요청은 이 헤더를 포함해야 한다.
- Gateway는 **MCP 서버 타깃의 세션 ID를 저장하고 재사용**해서, 매 툴콜마다 타깃과의 연결을 재초기화하지 않도록 한다 — 이는 "AgentCore Runtime 타깃에서 콜드스타트를 피해 응답이 빨라진다"는 효과로 문서화되어 있다.
- 세션 타임아웃은 기본 3600초(1시간), 설정 가능 범위는 900~28800초다.
- 인증된 Gateway에서는 세션이 사용자 신원(OIDC `sub` 클레임 또는 IAM Principal ARN)에 스코프되어 세션 하이재킹을 방지한다.

([AWS 공식 문서](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-sessions.html))

**이것은 이 장에서 다루는 "서빙 인스턴스에 대한 KV 캐시 스티키 라우팅"과는 다른 개념이다.** AgentCore Gateway의 MCP 세션은 (1) Gateway가 MCP 프로토콜의 `initialize` 상태와 (2) Gateway ↔ MCP 서버 타깃 사이의 연결/세션을 재사용하기 위한 것으로, MCP 툴 호출을 인가·변환·중계하는 계층의 상태 관리다. 이것이 그 뒤에 있는 **LLM 추론 서버(vLLM 등)의 KV 캐시를 특정 인스턴스에 고정**하는 것을 의미하지는 않는다 — 문서상 그런 연결에 대한 명시적 언급은 없다.

::: warning 미정착 영역
AgentCore Gateway 뒤에 자체 호스팅한 LLM 추론 플릿(vLLM 클러스터 등)을 붙이는 구성에서, Gateway의 MCP 세션 어피니티가 그 추론 플릿에 대한 라우팅에 어떤 영향을 주는지(혹은 전혀 무관한지)는 AWS 공식 문서에서 명시적으로 확인되지 않는다. AgentCore Gateway는 문서상 주로 MCP 툴 변환/인가 계층으로 설명되며, 모델 추론 자체는 AgentCore Runtime이나 별도의 모델 호스팅(Bedrock 관리형 모델, 자체 vLLM 플릿 등)에서 이뤄진다. 이 둘을 같은 "세션 어피니티"로 뭉뚱그려 설계하면 안 되고, **각각 별도로 확인·설계**해야 한다.
:::

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 단일 리전, 인스턴스 수 적음(2~4), 세션당 컨텍스트가 짧고 시스템 프롬프트 공유 위주 | ALB duration-based 스티키 세션(`AWSALB`) | 구현이 가장 단순하고 인프라 레벨에서 즉시 적용 가능 ([AWS 공식 문서](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/sticky-sessions.html)) | 인스턴스 장애/스케일인 시 재분배되면서 캐시가 리셋됨; cross-zone LB 필요 |
| L4에서 TCP/UDP 직접 종단, 클라이언트가 NAT/모바일망 뒤에 없음 | NLB source IP 스티키니스 | 쿠키 없이 흐름 해시로 고정 ([AWS 공식 문서](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/edit-target-group-attributes.html)) | 클라이언트 IP가 바뀌는 환경(모바일, 프록시)에서 어피니티가 깨짐 |
| vLLM 기반, 프롬프트 prefix 공유가 많고 Ray Serve로 서빙 | Ray Serve LLM `PrefixCacheAffinityRouter` | prefix 트리 매칭 + 로드 밸런스 하이브리드 ([Ray 공식 문서](https://docs.ray.io/en/latest/serve/llm/prefix-aware-request-router.html)) | vLLM 코어가 아닌 Ray Serve 의존성 추가; alpha API로 문서에 명시됨 |
| SGLang 기반 멀티 replica | SGLang 내장 cache-aware load balancer | RadixAttention과 통합된 자체 라우터 | vLLM 대비 생태계/운영 경험이 상대적으로 적음(장의 다른 곳에서 다룸) |
| 인스턴스 재시작·오토스케일링이 빈번하고 세션 KV를 잃으면 비용이 큼(긴 에이전트 워크플로) | LMCache로 KV를 인스턴스 로컬 밖으로 이전 | 라우팅이 흩어져도 캐시 재사용 가능 ([LMCache 공식 문서](https://docs.lmcache.ai/)) | 원격 캐시 조회/전송 지연, 운영 복잡도 증가 |
| MCP 툴 변환/인가만 필요, 추론 서버 스티키 라우팅은 별개로 이미 처리됨 | AgentCore Gateway MCP 세션 활성화 | 타깃 재초기화 방지, 사용자 스코프 보안 ([AWS 공식 문서](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-sessions.html)) | Gateway 세션 ≠ 추론 인스턴스 어피니티라는 점을 팀 전체가 인지해야 함 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| KV 캐시 히트율이 기대보다 낮음(멀티 인스턴스 도입 후 급락) | 라운드로빈/최소연결 로드밸런서가 세션을 매턴 다른 인스턴스로 분산 | 인스턴스별 vLLM 메트릭(prefix cache hit rate)과 게이트웨이 액세스 로그의 타깃 IP를 세션 ID로 조인 | ALB duration-based 스티키니스 또는 prefix-aware 라우터 도입 |
| ALB 스티키니스를 켰는데 적용이 안 됨 | cross-zone load balancing이 비활성화된 상태로 스티키니스 활성화를 시도 | 타깃 그룹 속성에서 `load_balancing.cross_zone.enabled` 확인 | cross-zone 활성화 후 스티키니스 재설정 ([AWS 공식 문서](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/sticky-sessions.html)) |
| NLB 스티키니스가 간헐적으로 깨짐 | 클라이언트가 NAT/프록시/모바일망 뒤에 있어 소스 IP가 바뀜 | 클라이언트 네트워크 경로 확인, 소스 IP 변동 로그 확인 | 쿠키 기반 스티키니스가 가능한 계층(ALB)으로 전환하거나 애플리케이션 레벨 세션 식별자 사용 ([AWS re:Post](https://repost.aws/knowledge-center/elb-nlb-stickiness-issues)) |
| "PrefixCacheAffinityRouter를 켰는데 세션 중간에 캐시가 끊긴다" | prefix 레벨 라우터를 세션 레벨 보장으로 오해; 로드 불균형 임계값 초과나 매치율 저하로 다른 replica로 이동 | 라우터 로그에서 같은 세션의 연속 요청이 실제로 같은 replica로 갔는지 추적 | 세션 레벨 보장이 필요하면 게이트웨이 스티키 라우팅을 병행하거나 LMCache 같은 공유 캐시 레이어 도입 |
| AgentCore Gateway 세션을 켰는데 추론 서버 캐시 히트율은 그대로 낮음 | Gateway MCP 세션과 추론 인스턴스 라우팅을 같은 것으로 오인 — Gateway 세션은 MCP 타깃 연결 재사용일 뿐 | 추론 플릿 앞의 로드밸런서/라우터 설정을 별도로 점검 | 추론 플릿 라우팅은 이 장의 ALB/NLB/prefix-aware 라우터 결정 표를 따로 적용 |
| 특정 인스턴스로 트래픽이 쏠려 GPU 메모리 압박 발생 | 스티키니스만 켜고 로드 인식(load-aware) 로직이 없어 hot session들이 한 replica에 몰림 | 인스턴스별 큐 길이/GPU 메모리 사용률 모니터링 | 로드 임계값 초과 시 스티키니스를 일시 무시하는 하이브리드 정책(Ray Serve LLM 방식) 채택 |

## 안티패턴

- ❌ "prefix-aware 라우터를 켰으니 세션 어피니티는 필요 없다" → ✅ prefix 레벨 라우팅은 세션 경계를 보장하지 않는다는 점을 인지하고, 긴 멀티턴 에이전트 워크로드에서는 실측 캐시 히트율로 검증한다.
- ❌ 이름만 보고 vLLM 자체 기능이라 가정하고 `PrefixCacheAffinityRouter`를 vLLM engine_kwargs에서 찾는다 → ✅ 이는 Ray Serve LLM 컴포넌트이므로 Ray Serve 배포 스택을 쓸 때만 해당한다는 것을 먼저 확인한다.
- ❌ AgentCore Gateway MCP 세션을 켜면 뒤의 LLM 추론 서버까지 자동으로 세션 어피니티가 생긴다고 가정한다 → ✅ Gateway 세션(MCP 타깃 연결 재사용)과 추론 서버 라우팅(KV 캐시 재사용)을 별도 레이어로 설계하고 각각 검증한다.
- ❌ 오토스케일링 정책이 스티키니스를 고려하지 않아 스케일인 시 활성 세션이 강제로 재분배된다 → ✅ 스케일인 시 connection draining과 스티키니스 세션 만료 정책을 함께 설계한다.
- ❌ 스티키니스만 걸고 인스턴스 간 로드 불균형을 방치한다 → ✅ 큐 길이/메모리 기반 로드 인식 로직을 병행해 hot replica 집중을 막는다.

## 계측 (SLI)

- **KV cache hit rate (per instance)**: 추론 엔진이 노출하는 prefix cache hit 비율. 라우팅 변경 전/후를 비교해 스티키니스·prefix-aware 라우팅의 실효성을 검증하는 1차 지표.
- **Session-to-instance stability**: 동일 세션 ID의 연속 요청이 같은 백엔드 인스턴스로 간 비율. 게이트웨이 액세스 로그(타깃 IP/ID)와 세션 ID를 조인해 계산.
- **TTFT(Time to First Token) p50/p95, per-turn**: 세션의 턴이 진행될수록 TTFT가 안정적으로 낮아지는지(캐시 재사용이 실제로 작동하는지) 확인.
- **Stickiness cookie/session expiry rate**: ALB `AWSALB` 만료(7일 고정) 또는 AgentCore Gateway 세션 타임아웃(900~28800초)으로 인한 강제 재초기화 빈도.
- **Load imbalance across replicas (queue length / GPU mem)**: 스티키니스로 인한 hot replica 편중을 조기에 감지하는 지표.

## 체크리스트

- [ ] 이 워크로드가 "단일 인스턴스 KV 관리" 문제인지 "여러 인스턴스 간 라우팅" 문제인지 먼저 구분했는가 — [vLLM KV 캐시](/04-caching/vllm-kv-cache) 챕터와 혼동하지 않았는가.
- [ ] 로드밸런서 계층(ALB vs NLB)에 맞는 스티키니스 방식(쿠키 vs source IP)을 선택했는가.
- [ ] ALB 스티키니스 사용 시 cross-zone load balancing이 활성화되어 있는가.
- [ ] `PrefixCacheAffinityRouter` 같은 이름의 컴포넌트를 도입하기 전에 그것이 실제로 어느 프로젝트(Ray Serve LLM / vLLM production-stack / SGLang) 소속이며 어떤 레벨(prefix vs session)에서 동작하는지 확인했는가.
- [ ] 멀티턴 에이전트 세션에서 실제 KV 캐시 히트율을 턴 번호별로 측정해 라우팅 전략의 실효성을 검증했는가.
- [ ] AgentCore Gateway MCP 세션(타깃 연결 재사용)과 추론 서버 라우팅(KV 재사용)을 서로 다른 레이어로 명시적으로 설계·문서화했는가.
- [ ] 세션 어피니티가 필요한 정도가 LMCache 같은 공유 캐시 레이어로 대체 가능한지 비용/복잡도 대비 검토했는가.
- [ ] 스티키니스와 로드 인식(load-aware) 로직을 함께 설계해 hot replica 편중을 방지했는가.
- [ ] 오토스케일링/인스턴스 교체 시 활성 세션의 스티키니스 만료·재배치 정책을 정의했는가.

## 참고

- [Sticky sessions for your Application Load Balancer — AWS 공식 문서](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/sticky-sessions.html)
- [Sticky sessions with application-based cookies — AWS Prescriptive Guidance](https://docs.aws.amazon.com/prescriptive-guidance/latest/load-balancer-stickiness/app-cookies-stickiness.html)
- [Edit target group attributes for your Network Load Balancer — AWS 공식 문서](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/edit-target-group-attributes.html)
- [Troubleshoot stickiness issues on your Network Load Balancer — AWS re:Post](https://repost.aws/knowledge-center/elb-nlb-stickiness-issues)
- [PrefixCacheAffinityRouter for LLM inference optimization — Ray 공식 문서](https://docs.ray.io/en/latest/serve/llm/prefix-aware-request-router.html)
- [Automatic Prefix Caching — vLLM 공식 문서](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/)
- [Feature: Prefix cache aware load balancing — vllm-project/vllm#11477](https://github.com/vllm-project/vllm/issues/11477)
- [Prefix Aware Routing — vLLM production-stack 공식 문서](https://docs.vllm.ai/projects/production-stack/en/latest/use_cases/prefix-aware-routing.html)
- [vLLM Router: A High-Performance and Prefill/Decode Aware Load Balancer — vLLM 블로그](https://vllm.ai/blog/2025-12-13-vllm-router-release)
- [LMCache 공식 문서](https://docs.lmcache.ai/)
- [Example: Share KV cache across multiple LLMs — LMCache 공식 문서](https://docs.lmcache.ai/getting_started/quickstart/share_kv_cache.html)
- [KVFlow: Efficient Prefix Caching for Accelerating LLM-Based Multi-Agent Workflows — arXiv:2507.07400](https://arxiv.org/abs/2507.07400)
- [KV Cache Routing: Why Standard Load Balancers Break Prefix Caching — TrueFoundry](https://www.truefoundry.com/blog/kv-cache-routing-why-standard-load-balancers-break-prefix-caching-and-how-to-fix-it)
- [Use MCP sessions with your AgentCore gateway — AWS 공식 문서](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-sessions.html)
- [Amazon Bedrock AgentCore Gateway: A secure AI gateway for agents, tools, and models — AWS 공식 문서](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
