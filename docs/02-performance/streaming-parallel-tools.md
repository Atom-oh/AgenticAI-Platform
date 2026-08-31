---
title: 스트리밍과 병렬 툴 호출
description: 스트리밍으로 체감 지연을, 병렬 툴 호출로 실제 지연을 줄이는 두 가지 레버를 프로토콜 수준에서 분해한다.
outline: [2, 3]
---

# 스트리밍과 병렬 툴 호출

::: tip 이 장에서 얻는 것
- 스트리밍이 줄이는 것(체감 지연)과 줄이지 못하는 것(총 완료 시간)의 명확한 구분, 그리고 TTFT/TTLT 중심의 계측 기준
- Anthropic Messages API와 Bedrock `ConverseStream`의 SSE 이벤트 구조 — `content_block_delta`/`contentBlockDelta`, `input_json_delta`의 partial JSON 누적 규칙까지
- 병렬 툴 호출의 지연 산식: 라운드트립당 툴 시간이 `sum(t_i)`에서 `max(t_i)`로 바뀌는 조건과, 그 조건이 깨지는 경우
- 메시지 히스토리 포맷 실수 하나가 이후 턴의 병렬성 자체를 무너뜨리는 메커니즘과 그 방지법
- 스트리밍 중 가드레일 검증의 sync/async 트레이드오프
:::

## 왜 문제가 되는가

에이전트의 지연은 단일 LLM 호출의 지연이 아니라 **루프의 지연**이다. 한 태스크가 model call → tool 실행 → model call을 N회 반복하면, 사용자 입장의 총 지연은 대략 다음과 같다.

```
T_total ≈ Σ (T_model_i + T_tools_i) + T_overhead
```

이 식에서 플랫폼 엔지니어가 당길 수 있는 레버는 세 종류다. 라운드트립 횟수 N을 줄이는 것([툴 라운드트립 최적화](/02-performance/tool-roundtrips)), 각 항의 크기를 줄이는 것([모델 라우팅](/02-performance/model-routing), 캐싱), 그리고 이 장의 주제인 두 가지 — **같은 총 시간을 더 빠르게 느끼게 만드는 것(스트리밍)** 과 **`T_tools_i` 항을 병렬화로 압축하는 것(병렬 툴 호출)** 이다.

둘은 자주 혼동된다. 스트리밍은 `T_total`을 1ms도 줄이지 않는다. 첫 토큰이 도착하는 시점(TTFT, time to first token) 이후 출력을 점진적으로 노출해 **체감 지연(perceived latency)** 을 개선할 뿐이다. 반면 병렬 툴 호출은 실제 wall-clock을 줄인다 — 단, 툴 호출들이 서로 독립일 때만. 이 구분을 하지 못하면 "스트리밍 붙였는데 대시보드의 p95는 그대로"라는 잘못된 실망과, "병렬화했는데 결과가 가끔 틀린다"는 잘못된 병렬화가 동시에 발생한다.

에이전트에서는 스트리밍의 역할이 챗봇보다 하나 더 있다. 멀티스텝 태스크는 수십 초~수 분이 걸리므로, 중간 진행 상황(어떤 툴을 호출 중인지, 어떤 텍스트를 생성 중인지)을 스트리밍하지 않으면 사용자는 실패와 진행을 구분할 수 없다. 스트리밍은 UX 장식이 아니라 **장기 실행 태스크의 생존 신호**다.

## 핵심 개념

### 체감 지연 vs 실제 지연 — 스트리밍이 바꾸는 것

측정 지표로 구분하면 명확하다.

| 지표 | 정의 | 스트리밍의 효과 |
|---|---|---|
| TTFT (time to first token) | 요청 → 첫 출력 토큰 | 스트리밍으로 **노출 가능**해짐 (비스트리밍에선 관측 불가) |
| ITL (inter-token latency) | 토큰 간 간격 | 변화 없음 — 모델 디코딩 속도의 함수 |
| TTLT (time to last token) | 요청 → 마지막 토큰 = 총 완료 시간 | **변화 없음** |

Anthropic Messages API는 `"stream": true`를 설정하면 server-sent events(SSE)로 응답을 점진 전송한다([Streaming Messages](https://platform.claude.com/docs/en/build-with-claude/streaming)). Bedrock에서는 `Converse` 대신 [`ConverseStream`](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_ConverseStream.html)을 호출한다 — 모델별 지원 여부는 `GetFoundationModel`의 `responseStreamingSupported` 필드로 확인하며, AWS CLI는 Bedrock의 스트리밍 연산을 지원하지 않으므로 SDK를 써야 한다(같은 문서).

지연 구조 자체의 분해(네트워크, 큐잉, prefill, 디코딩)는 [지연의 해부](/02-performance/latency-anatomy)에서 다룬다. 이 장에서 중요한 결론은 하나다: **스트리밍의 개선 목표는 TTFT와 진행 가시성이고, TTLT 개선은 병렬화·라운드트립 감소·모델 선택의 몫이다.**

### SSE 이벤트 구조 — 두 API의 대응 관계

Anthropic Messages API의 스트림은 정해진 흐름을 따른다([공식 문서](https://platform.claude.com/docs/en/build-with-claude/streaming)): `message_start` → 콘텐츠 블록마다 `content_block_start` / 1개 이상의 `content_block_delta` / `content_block_stop` → 1개 이상의 `message_delta` → 최종 `message_stop`. 각 콘텐츠 블록은 최종 Message의 `content` 배열 인덱스에 대응하는 `index`를 가진다. 델타 타입은 세 가지가 핵심이다.

- `text_delta` — 텍스트 조각
- `input_json_delta` — `tool_use` 블록의 `input` 필드 업데이트. **partial JSON 문자열**로 도착한다(아래 참조)
- `thinking_delta` — extended thinking 사용 시의 사고 과정 조각 (블록 종료 직전 `signature_delta`가 무결성 검증용으로 도착)

스트림에는 임의 개수의 `ping` 이벤트가 섞일 수 있고, **에러도 이벤트로 도착한다** — 예컨대 고부하 시 비스트리밍이라면 HTTP 529였을 `overloaded_error`가 `event: error`로 스트림 중간에 나타난다. 문서는 새 이벤트 타입이 추가될 수 있으므로 "unknown event types를 gracefully 처리하라"고 명시한다. 또 하나의 함정: `message_delta`의 `usage` 토큰 카운트는 **누적(cumulative)** 값이다. 델타마다 합산하면 과금 지표가 이중 계산된다.

Bedrock `ConverseStream`은 같은 구조를 다른 이름으로 노출한다([API Reference](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_ConverseStream.html)): `messageStart` → `contentBlockStart` / `contentBlockDelta` / `contentBlockStop` → `messageStop`(stopReason 포함) → `metadata`. `metadata` 이벤트에는 `usage`(input/output/cacheRead/cacheWrite 토큰)와 `metrics.latencyMs`가 담긴다 — 스트리밍 요청의 서버 측 지연을 계측할 수 있는 공식 지점이다. 에러는 `internalServerException`, `modelStreamErrorException`(HTTP 424), `throttlingException`, `validationException` 등이 **스트림 멤버로** 도착한다. 즉 HTTP 200을 받고 스트림을 읽기 시작한 뒤에도 실패할 수 있으며, 상태 코드만 보는 모니터링은 이 실패를 놓친다.

### 툴 호출도 스트리밍된다 — partial JSON 누적 규칙

`tool_use` 블록의 델타는 `input` 필드의 업데이트인데, 최대 granularity를 위해 **partial JSON 문자열**로 전송되고 최종 `tool_use.input`만 객체다. 공식 규칙은: 문자열 델타를 누적하다가 `content_block_stop`을 받은 시점에 파싱하거나, partial JSON 파싱 라이브러리 혹은 SDK 헬퍼를 쓰는 것이다([Streaming Messages — Input JSON delta](https://platform.claude.com/docs/en/build-with-claude/streaming)). 같은 문서는 현재 모델이 `input`의 완성된 key-value 하나씩만 방출하므로 "when using tools, there may be delays between streaming events while the model is working"라고 명시한다 — 툴 인자 생성 구간에서 스트림이 몇 초간 조용해지는 것은 장애가 아니라 정상 동작이다. 스트림 stall 알람의 임계값은 이 구간을 감안해 잡아야 한다.

### AgentCore Runtime의 SSE, 그리고 Harness 스트림

에이전트를 AgentCore Runtime에 올리면 스트리밍은 서비스 계약의 일부가 된다. HTTP 프로토콜 계약에서 `/invocations` 엔드포인트(포트 8080)는 REST JSON과 SSE를 모두 지원한다([AgentCore Runtime service contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-service-contract.html)) — 에이전트 컨테이너가 `text/event-stream`으로 응답하면 Runtime이 이를 클라이언트까지 관통시킨다. 계약의 전체 구조는 [Runtime 심화](/10-agentcore/runtime-deep-dive)와 [Runtime 배포 계약](/10-agentcore/runtime-deploy-contract)에서 다뤘다.

Harness 경로(`InvokeHarness`)의 스트림 이벤트는 `ConverseStream`과 같은 형태를 따른다. 이 책의 실제 데모 코드(`demo/builder-harness/invoke.py`)가 소비하는 이벤트가 그대로 예시다.

```python
response = client.invoke_harness(
    harnessArn=HARNESS_ARN,
    runtimeSessionId=session_id,
    messages=[{"role": "user", "content": [{"text": text}]}],
)
for event in response["stream"]:
    if "contentBlockDelta" in event:
        delta = event["contentBlockDelta"].get("delta", {})
        if "text" in delta:
            print(delta["text"], end="", flush=True)
    elif "messageStop" in event:
        ...  # stopReason 확인
    elif "metadata" in event:
        ...  # usage/metrics
    elif any(k in event for k in ("validationException",
                                  "internalServerException",
                                  "runtimeClientError")):
        ...  # 에러도 스트림 이벤트로 도착한다
```

주목할 점은 마지막 분기다. 에러 이벤트를 처리하지 않는 소비자는 스트림이 조용히 끊긴 것과 서버 에러를 구분하지 못한다.

스트림을 끝까지 관통시키려면 **중간 홉 전체**가 스트리밍 친화적이어야 한다. 클라이언트와 에이전트 사이에 있는 리버스 프록시, 로드밸런서, API 레이어 중 하나라도 응답을 버퍼링하거나 idle timeout이 토큰 간 최대 간격(특히 위의 툴 인자 생성 구간)보다 짧으면, 사용자는 "한참 조용하다가 한 번에 쏟아지는" 가짜 스트리밍이나 중간 절단을 겪는다. 스트리밍 도입 시 경로상 모든 홉의 버퍼링/타임아웃 설정을 점검 목록에 넣어라.

### 병렬 툴 호출 — sum에서 max로

Claude는 기본적으로 한 응답에 여러 개의 `tool_use` 블록을 반환할 수 있다. 공식 문서의 표현을 빌리면: "By default, Claude may call multiple tools in a single response. … The API doesn't prescribe an execution order: you can run the calls concurrently (`Promise.all`, `asyncio.gather`), sequentially in the order they appear, or in any combination that suits your tools." ([Parallel tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use))

지연 산식이 여기서 바뀐다. 한 assistant 턴이 k개의 독립적인 툴을 호출하고 클라이언트가 이를 동시 실행하면, 그 라운드트립의 툴 실행 시간은

```
순차:  T_tools = t_1 + t_2 + … + t_k
병렬:  T_tools = max(t_1, …, t_k)
```

가 된다. 부수 효과로 **모델 라운드트립 수 자체도 줄어든다**: 순차 호출이었다면 k번의 model call이 필요했을 정보 수집이 1번의 model call + 1번의 병렬 실행으로 끝나므로, 각 라운드트립의 prefill 비용(누적 컨텍스트 재처리)도 k−1회분 절약된다.

실행 전략은 클라이언트 책임이다. 공식 가이드: "Independent, read-only operations are usually safe to run in parallel for lower latency. Tools with side effects, shared state, or ordering requirements might be better run sequentially." 그리고 어떤 전략이든 **모든 `tool_result`를 다음 단일 user 메시지에 함께** 담아 반환해야 하며(각 결과는 `tool_use_id`로 매칭, `tool_result` 블록이 텍스트보다 앞), 실행하지 않기로 한 호출에도 `is_error: true`와 간단한 사유를 담은 `tool_result`를 반환해야 한다(같은 문서).

병렬화가 **불가능한 경우**는 정의상 명확하다: 다음 호출의 입력이 이전 호출의 출력에 의존할 때(검색 → 결과 URL fetch → 본문 요약 같은 체인). 이런 의존 체인은 병렬화 대상이 아니라 라운드트립 감소 대상이다 — 여러 스텝을 하나의 복합 툴로 합치는 설계는 [툴 라운드트립 최적화](/02-performance/tool-roundtrips)에서 다룬다. 의존성 있는 호출이 한 배치에 섞여 나오는 문제에 대해 공식 문서는 시스템 프롬프트에 "Only batch tool calls that are independent of each other."를 추가하라고 안내한다.

병렬성을 **극대화하는 프롬프팅**도 공식적으로 문서화되어 있다. Claude 4 이후 모델은 이득이 있을 때 기본적으로 병렬 호출을 하지만, 문서가 제시하는 시스템 프롬프트("For maximum efficiency, whenever you perform multiple independent operations, invoke all relevant tools simultaneously rather than sequentially. …")로 빈도를 높일 수 있다. 반대로 끄려면 `tool_choice` 객체 안에 `disable_parallel_tool_use: true`를 설정한다 — `auto`에서는 응답당 최대 1개 툴, `any`/`tool`에서는 정확히 1개 툴이 된다(톱레벨 파라미터가 아니라는 점에 주의).

효과의 크기는 Anthropic의 멀티 에이전트 리서치 시스템 사례가 정량적으로 보여준다. 리드 에이전트가 서브에이전트 3~5개를 순차가 아닌 병렬로 스핀업하고, 각 서브에이전트가 툴 3개 이상을 병렬로 쓰도록 바꾼 두 가지 병렬화가 "cut research time by up to 90% for complex queries" ([How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)). 같은 글은 비용 측면의 경고도 준다: 에이전트는 챗 대비 약 4배, 멀티 에이전트 시스템은 약 15배의 토큰을 소비한다. 병렬화는 지연을 줄이지 토큰을 줄이지 않는다 — 오히려 더 공격적인 툴 사용을 유도해 토큰과 비용은 늘어나는 방향이다. 지연 SLO와 비용 예산을 함께 봐야 한다.

### 두 층위의 병렬성 — 툴 레벨과 서브에이전트 레벨

위 사례가 보여주듯 병렬성은 두 층위에서 작동한다. (1) 한 에이전트 안에서 한 턴의 여러 `tool_use`를 동시 실행하는 **툴 레벨 병렬성**(이 장), (2) orchestrator가 여러 워커 에이전트를 동시에 돌리는 **서브에이전트 레벨 병렬성**([오케스트레이션 패턴](/01-agent-design/orchestration-patterns)의 parallelization / orchestrator-workers). 그 장이 "어떤 구조가 정확한가"의 패턴 관점이라면, 이 장의 관점은 지연이다: 어느 층위든 병렬 구간의 wall-clock은 `max()`로 떨어지지만, 서브에이전트 병렬성은 워커당 전체 컨텍스트·시스템 프롬프트가 복제되므로 토큰 비용 배수가 훨씬 크고(위의 약 15배), 합성(synthesis) 단계라는 새 직렬 구간이 추가된다. 짧은 독립 조회 여러 개면 툴 레벨 병렬성으로 충분하고, 각 갈래가 자체적으로 멀티스텝 추론을 요구할 때만 서브에이전트 층위로 올라가는 것이 지연·비용 모두에서 유리하다.

### 스트리밍과 가드레일의 딜레마 — 청크 검증 vs 버퍼링

스트리밍의 구조적 약점: 출력 **전체**에 대한 검증(PII 마스킹, 유해성, 근거성)을 하려면 전체가 나올 때까지 기다려야 하는데, 그러면 스트리밍이 아니다. Bedrock Guardrails는 이 트레이드오프를 `streamProcessingMode`로 공식화했다([Configure streaming response behavior](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-streaming.html)).

- **Synchronous(기본)**: 응답 청크를 버퍼링해 정책 적용 후 전송. 청크에 지연이 더해지지만 "every response chunk is scanned by guardrails before being sent to the user."
- **Asynchronous**: 청크를 즉시 전송하고 백그라운드에서 정책을 적용. 지연 영향은 없지만 "response chunks may contain inappropriate content until guardrails scan completes" — 부적절 콘텐츠가 식별되면 이후 청크부터 차단된다. 그리고 **async 모드에서는 sensitive information masking이 지원되지 않는다**(같은 문서의 Warning).

즉 async는 "이미 화면에 나간 유해/민감 청크"를 회수하지 못한다. 규제 도메인(금융·의료·개인정보)이라면 sync 모드의 추가 지연을 받아들이거나, 스트리밍을 UI 내부 버퍼와 결합해 문장/단락 단위로 검증-후-표출하는 절충을 설계해야 한다. 청크 단위 검증은 청크 경계에 걸친 패턴(두 청크에 나뉘어 도착하는 주민등록번호)을 놓칠 수 있으므로, 검증 단위는 토큰이 아니라 의미 단위 + 슬라이딩 윈도우여야 한다. 국내 규제 맥락의 구체적 정책 설계는 [가드레일과 PII](/12-security-korea/guardrails-pii)에서 다룬다.

::: warning 미정착 영역
스트리밍 출력에 대한 검증 아키텍처(청크 경계 처리, 표출 후 회수 UX, 검증 실패 시 스트림 중단 프로토콜)는 업계 표준이 없다. Bedrock의 sync/async 모드는 위와 같이 문서화된 공식 옵션이지만, 자체 후처리 파이프라인(커스텀 PII 필터, LLM-as-judge)을 스트림에 끼워 넣는 패턴은 각사 구현이 갈린다. 규제 요건이 있다면 "전량 버퍼링 후 검증"을 기본값으로 두고 스트리밍을 점진 도입하는 쪽이 안전하다.
:::

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 사용자 대면 챗/에이전트 UI | 스트리밍 (SSE) | TTFT 이후 점진 표시로 체감 지연 개선 + 장기 태스크 진행 가시성 | 클라이언트/중간 홉의 SSE 처리 복잡도, 스트림 중 에러 처리 필요 |
| 백엔드 배치 처리, 결과 전체가 있어야 다음 단계 진행 | 비스트리밍 (`Converse`/일반 Messages 호출) | 스트리밍 이득이 없고 파싱·에러 처리가 단순 | 장시간 요청의 진행 관측 불가 |
| 응답 전체에 대한 마스킹/검증이 규제 요건 | 비스트리밍 또는 Guardrails sync 모드 | async 모드는 마스킹 미지원, 표출 후 회수 불가 ([공식 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-streaming.html)) | 청크당 버퍼링 지연 추가 |
| 한 턴에 독립적인 read-only 조회 여러 건 | 병렬 실행 (`asyncio.gather` 등) | 툴 시간이 `sum` → `max` ([Parallel tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)) | 실패 격리·부분 실패 시 `is_error` 반환 로직 필요 |
| 툴에 부수 효과·공유 상태·순서 의존 | 순차 실행, 필요 시 `disable_parallel_tool_use: true` | 공식 가이드가 순차를 권고, 잘못된 병렬화는 정합성 훼손 | 라운드트립당 지연 증가 |
| 갈래마다 자체 멀티스텝 추론 필요 | 서브에이전트 병렬 ([오케스트레이션 패턴](/01-agent-design/orchestration-patterns)) | 갈래별 독립 컨텍스트·독립 툴 루프 | 토큰 약 15배 규모의 비용, 합성 단계 직렬 구간 ([출처](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| 짧은 독립 조회 여러 건인데 서브에이전트 고려 중 | 툴 레벨 병렬로 충분 | 같은 `max()` 효과를 컨텍스트 복제 없이 획득 | — |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 스트리밍인데 한참 조용하다 한 번에 쏟아짐 | 중간 홉(프록시/LB/게이트웨이)의 응답 버퍼링 | `curl -N`으로 원 서버 직접 호출 시 정상인지 비교 | 경로상 모든 홉의 버퍼링 비활성화, SSE 관통 확인 |
| 스트림이 툴 호출 구간에서 수 초간 멈춤 → stall 알람 오탐 | 모델이 `input` key-value를 완성 단위로 방출하는 정상 동작 ([공식 문서](https://platform.claude.com/docs/en/build-with-claude/streaming)) | 멈춤 직전 이벤트가 `tool_use`의 `content_block_start`/`input_json_delta`인지 확인 | stall 임계값을 툴 인자 생성 구간 감안해 상향, `ping` 이벤트 기준으로 연결 생존 판정 |
| 툴 인자 JSON 파싱 에러가 간헐 발생 | `input_json_delta`를 누적 완료 전에 파싱 (델타는 partial JSON 문자열) | 파싱 실패 시점의 누적 버퍼 로깅 — 잘린 JSON인지 확인 | `content_block_stop` 수신 후 파싱, 또는 partial JSON 파서/SDK 헬퍼 사용 |
| HTTP 200 이후 응답이 미완성으로 끝남 | 에러가 스트림 이벤트로 도착 (`modelStreamErrorException`, `overloaded_error` 등) | 스트림 마지막 이벤트가 `message_stop`/`messageStop`인지 검사 | 에러 이벤트 분기 구현 + 종료 이벤트 미수신을 실패로 집계, 재시도 |
| 병렬 툴 호출이 일어나지 않음 (턴당 툴 1개) | `tool_result`를 user 메시지 하나가 아니라 **개별 user 메시지로 분리** 반환 — 히스토리가 모델에게 순차를 "학습"시킴 ([Troubleshooting](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)) | assistant 메시지당 평균 `tool_use` 블록 수 계측 (>1.0이어야 함) | 모든 결과를 단일 user 메시지에 `tool_use_id` 매칭으로 반환, 필요 시 병렬화 시스템 프롬프트 추가 |
| 병렬 실행 후 400 에러 (`tool_use ids were found without tool_result…`) | 일부 호출의 `tool_result` 누락, 또는 텍스트 블록을 결과보다 앞에 배치 | 에러 메시지의 미매칭 `tool_use_id` 확인 | 미실행 호출 포함 **전부**에 결과 반환(미실행은 `is_error: true`), `tool_result`를 콘텐츠 배열 최앞단에 |
| 병렬 실행 결과가 간헐적으로 틀림 | 의존성 있는 호출이 한 배치에 섞여 stale 입력으로 실행 | 배치 내 호출 간 입출력 의존성 검사 로깅 | 의존 호출은 순차 전환, 시스템 프롬프트에 "Only batch tool calls that are independent of each other." 추가 |
| 스트리밍 응답의 토큰 사용량이 실제의 수 배로 집계 | `message_delta`의 `usage`는 누적값인데 델타마다 합산 | 동일 요청의 비스트리밍 usage와 대조 | 마지막 `message_delta`(또는 `ConverseStream`의 `metadata`) 값만 기록 |
| 스트리밍 중 유해/민감 콘텐츠가 화면에 노출됨 | Guardrails async 모드 — 스캔 완료 전 청크 선전송, 마스킹 미지원 ([공식 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-streaming.html)) | `streamProcessingMode` 설정 확인 | sync 모드 전환 또는 의미 단위 버퍼링-검증-표출 파이프라인 |

## 안티패턴

- ❌ **스트리밍을 총 지연 최적화로 보고함** → ✅ 스트리밍은 TTFT 노출과 체감 개선. TTLT(p95)는 병렬화·라운드트립 감소·모델 선택으로 줄인다. 대시보드에 TTFT와 TTLT를 별도 지표로 둔다.
- ❌ **`tool_use` 블록별로 실행하고 그때마다 user 메시지를 하나씩 추가** → ✅ 한 턴의 모든 `tool_result`를 단일 user 메시지에 반환. 분리 반환은 즉시 에러가 아니라 **이후 턴의 병렬성 저하**라는 지연 회귀로 나타나므로 더 위험하다.
- ❌ **부수 효과 있는 툴(쓰기, 결제, 상태 변경)을 무조건 `gather`로 병렬 실행** → ✅ read-only 독립 호출만 병렬. 쓰기 계열은 순차 + 실패 시 후속 호출에 `is_error: true` 반환. 필요하면 `disable_parallel_tool_use`로 모델 측에서 차단.
- ❌ **스트림 소비 루프에서 텍스트 델타만 처리하고 에러/종료 이벤트 무시** → ✅ `message_stop`/`messageStop` 미수신을 실패로 집계하고 에러 이벤트를 분기 처리. `demo/builder-harness/invoke.py`의 마지막 분기가 최소 구현이다.
- ❌ **지연을 줄이려고 Guardrails를 async로 바꾸고 끝** → ✅ async는 마스킹 미지원 + 노출 후 차단 모델임을 인지하고, 규제 요건과 대조해 결정. 민감 도메인은 sync 또는 버퍼링 절충안.
- ❌ **짧은 독립 조회 몇 개를 위해 서브에이전트 팬아웃 도입** → ✅ 같은 `max()` 효과는 툴 레벨 병렬로 얻는다. 서브에이전트는 토큰 비용이 자릿수로 커진다(챗 대비 약 15배, [출처](https://www.anthropic.com/engineering/multi-agent-research-system)).

## 계측 (SLI)

| SLI | 정의/수집 지점 | 무엇을 말해주는가 |
|---|---|---|
| TTFT p50/p95 | 요청 → 첫 `content_block_delta`(또는 `contentBlockDelta`) 수신 | 스트리밍이 실제로 체감을 개선하는가. prefill·큐잉 회귀 감지 |
| TTLT p50/p95 | 요청 → `message_stop`/`messageStop` | 실제 총 지연. 병렬화·라운드트립 최적화의 효과가 나타나는 지표 |
| 스트림 완결률 | 종료 이벤트 정상 수신 / 전체 스트림 시작 수 | 스트림 중간 에러·절단(200 이후 실패 포함)의 발생률 |
| 최대 inter-event gap | 이벤트 간 최대 간격 (툴 인자 생성 구간 라벨링) | 중간 홉 버퍼링·stall 감지. 임계값은 `input_json_delta` 구간을 감안 |
| assistant 메시지당 평균 `tool_use` 블록 수 | `tool_use` 포함 메시지 기준 평균 — 공식 문서가 제시하는 검증법으로, "Should be > 1.0 if parallel calls are working" ([Troubleshooting](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)) | 병렬성이 유지되는가. 히스토리 포맷 회귀의 조기 신호 |
| 병렬 구간 압축비 | 배치의 `Σt_i / max(t_i)` (실측) | 병렬화가 실제로 얼마나 벌어주는가. 1.0에 가까우면 병렬화 무의미(한 툴이 지배적) |
| 배치 부분 실패율 | 병렬 배치 중 `is_error: true` 반환 비율 | 의존성 오염·툴 신뢰성 문제의 신호 |
| 서버 측 모델 지연 | `ConverseStream` `metadata.metrics.latencyMs` ([API Reference](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_ConverseStream.html)) | 클라이언트 측 TTLT와의 차이 = 네트워크/중간 홉 오버헤드 |
| 토큰 사용량 | 마지막 `message_delta.usage`(누적) 또는 `metadata.usage`만 기록 | 병렬화·팬아웃의 비용 측면. 이중 합산 금지 |

## 체크리스트

- [ ] 사용자 대면 경로는 스트리밍으로, TTFT와 TTLT를 분리 계측하고 있다
- [ ] 클라이언트~에이전트 사이 모든 홉(프록시, LB, 게이트웨이)의 버퍼링·idle timeout이 SSE를 관통시킴을 확인했다
- [ ] 스트림 소비자가 에러 이벤트와 종료 이벤트 미수신을 모두 실패로 처리한다 (HTTP 200 ≠ 성공)
- [ ] `input_json_delta`는 `content_block_stop` 이후 파싱하거나 partial JSON 파서를 쓴다
- [ ] usage는 누적 규칙에 맞게 마지막 값만 기록한다
- [ ] 한 턴의 모든 `tool_result`를 단일 user 메시지로, `tool_result`가 텍스트보다 앞에 오도록 반환한다
- [ ] 미실행 호출에도 `is_error: true` 결과를 반환한다
- [ ] read-only 독립 호출만 병렬 실행하고, 쓰기 계열은 순차(필요 시 `disable_parallel_tool_use`)로 강제한다
- [ ] assistant 메시지당 평균 `tool_use` 수(>1.0)와 병렬 구간 압축비를 대시보드에 올렸다
- [ ] 스트리밍 + 가드레일 조합에서 sync/async 모드를 규제 요건과 대조해 명시적으로 선택했다 (async = 마스킹 미지원)
- [ ] 서브에이전트 팬아웃 도입 전, 툴 레벨 병렬로 충분한지와 토큰 비용 배수를 검토했다

## 참고

- [Streaming Messages — Anthropic 공식 문서](https://platform.claude.com/docs/en/build-with-claude/streaming) — SSE 이벤트 흐름, `text_delta`/`input_json_delta`/`thinking_delta`, 에러 이벤트, 누적 usage
- [Parallel tool use — Anthropic 공식 문서](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use) — 실행 시맨틱, 단일 user 메시지 규칙, `disable_parallel_tool_use`, 병렬화 프롬프팅과 트러블슈팅
- [Handle tool calls — Anthropic 공식 문서](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls) — `tool_result` 포맷 규칙, `is_error` 처리
- [ConverseStream — Amazon Bedrock API Reference](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_ConverseStream.html) — 스트림 이벤트 구조, `metadata.metrics.latencyMs`, 스트림 내 예외
- [Configure streaming response behavior to filter content — Amazon Bedrock User Guide](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-streaming.html) — Guardrails sync/async 모드
- [AgentCore Runtime service contract — AWS 공식 문서](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-service-contract.html) — `/invocations` REST JSON/SSE, 프로토콜 비교
- [How we built our multi-agent research system — Anthropic Engineering](https://www.anthropic.com/engineering/multi-agent-research-system) — 병렬화로 리서치 시간 최대 90% 단축, 토큰 비용 약 4배/15배
- 관련 장: [지연의 해부](/02-performance/latency-anatomy) · [툴 라운드트립 최적화](/02-performance/tool-roundtrips) · [오케스트레이션 패턴](/01-agent-design/orchestration-patterns) · [Runtime 심화](/10-agentcore/runtime-deep-dive) · [가드레일과 PII](/12-security-korea/guardrails-pii)
