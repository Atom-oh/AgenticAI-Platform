---
title: 캐시 미스 근본 원인
description: 프롬프트 캐시 히트가 깨지는 6가지 근본 원인과 static→dynamic 정렬 원칙을 다룬다.
outline: [2, 3]
---

# 캐시 미스 근본 원인

::: tip 이 장에서 얻는 것
- 캐시 히트가 안 나는 6가지 근본 원인을 증상 → 원인 → 확인 방법 → 해결로 진단하는 표
- "static → dynamic" 프롬프트 정렬 원칙과 캐시 브레이크포인트 배치 규칙
- 나쁜 프롬프트 조립 코드와 좋은 프롬프트 조립 코드의 실제 차이(캐시 키 파생 관점)
- 백그라운드 프로세스로 캐시를 warm 상태로 유지하는 패턴
:::

## 왜 문제가 되는가

프롬프트 캐싱의 가격·TTL·최소 토큰 같은 기초 개념은 [프롬프트 캐싱 기초](/04-caching/prompt-caching-basics)에서 다뤘다. 문제는 그 구조를 이해했다고 해서 실제로 캐시가 맞는 게 아니라는 점이다. Anthropic 문서는 캐시 히트 조건을 명확히 규정한다 — "Cache hits require 100% identical prompt segments, including all text and images up to and including the block marked with cache control"([Anthropic, Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). 즉 브레이크포인트까지의 프리픽스가 **바이트 단위로 완전히 동일**해야 하며, 토큰 하나만 달라도 그 지점부터 뒤의 모든 캐시가 무효화된다.

실무에서 캐시 히트율이 낮다고 보고되는 사례의 대부분은 "캐싱을 안 켜서"가 아니라 "켰는데 프롬프트 조립 코드가 매 요청마다 프리픽스를 미세하게 바꿔서"다. 타임스탬프 하나, 딕셔너리 순서 하나, MCP 서버 재시작 한 번이 캐시 적중률을 70%에서 0%로 떨어뜨릴 수 있다. 이 장은 그 미세한 원인들을 체계적으로 나열하고, 각각을 어떻게 탐지하고 고치는지 다룬다.

## 핵심 개념

### 캐시 키는 프리픽스 해시다

Anthropic의 캐시는 "프롬프트 → 캐시 키"의 단순 해시 매핑이 아니라, **정확 프리픽스 매칭(exact prefix matching)** 기반이다. 캐시 프리픽스는 다음 순서로 계층을 이룬다([Anthropic, Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)):

```
tools → system → messages
```

상위 계층(`tools`)이 바뀌면 그 아래 모든 계층(`system`, `messages`)의 캐시도 함께 무효화된다. 반대로 `messages`만 바뀌면 `tools`와 `system` 캐시는 그대로 재사용된다. 이 계층 구조 때문에 **가장 자주 바뀌는 내용을 가장 뒤에, 가장 안 바뀌는 내용을 가장 앞에** 두는 것이 원칙이다 — 문서 표현으로는 "Place static content (tool definitions, system instructions, context, examples) at the beginning of your prompt"([Anthropic, Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).

캐시 조회는 브레이크포인트에서 뒤로 최대 20개 블록까지만 확인한다("The system checks at most 20 positions per breakpoint, counting the breakpoint itself as the first", [Anthropic, Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). 즉 브레이크포인트 앞에 있는 내용이 자동으로 캐시되는 게 아니라, **이전 요청이 실제로 써놓은 캐시 항목**을 찾는 것뿐이다 — 프리픽스가 안정적이지 않으면 아무리 브레이크포인트를 잘 둬도 히트할 대상 자체가 없다.

가격·TTL·최소 토큰 임계치 등 캐싱의 경제성 자체는 이 장의 범위가 아니다. [프롬프트 캐싱 기초](/04-caching/prompt-caching-basics)와 [캐시 지표와 경제성](/04-caching/cache-metrics-economics)을 참고한다.

### static → dynamic 정렬 원칙

프롬프트를 조립할 때 다음 순서를 지킨다.

1. **tools** — 세션 내내(이상적으로는 배포 내내) 바뀌지 않는 툴 스키마
2. **system 프롬프트의 정적 부분** — 역할, 정책, few-shot 예시
3. **캐시 브레이크포인트** — 정적 프리픽스의 끝
4. **system 프롬프트의 동적 부분** (있다면 최소화) 또는 **messages의 앞부분**
5. **매 요청 달라지는 내용** — 타임스탬프, per-user 컨텍스트, 최신 사용자 turn

브레이크포인트는 반드시 3번 위치, 즉 "정적 프리픽스의 끝"에 둔다. 브레이크포인트보다 앞에 동적 내용이 있으면 그 브레이크포인트 자체가 무의미해진다 — 캐시가 매 요청 다른 내용을 프리픽스로 갖게 되기 때문이다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 시스템 프롬프트에 사용자 이름·권한처럼 요청마다 바뀌는 데이터가 필요 | 정적 정책/역할 설명 뒤, messages 앞부분(또는 system 끝)에 별도 블록으로 삽입 | tools+정적 system 캐시는 유지, 동적 블록만 새로 계산 | 동적 블록 이후 내용은 캐시 재사용 불가 — 동적 블록을 가능한 뒤쪽·짧게 유지해야 손실 최소화 |
| 프롬프트에 "현재 시각" 정보가 필요 | 분/시 단위로 버킷화(예: 5분 단위 truncate)하거나 system 맨 끝(가장 마지막 동적 블록)에만 배치 | 초 단위 타임스탬프는 5분 TTL 내에도 거의 항상 다른 값이라 캐시 무효화가 구조적으로 발생 | 시각 정밀도를 낮추는 대신 캐시 히트율을 얻는 명시적 트레이드오프 |
| 툴을 상황에 따라 켜고 끄고 싶음 | 툴 정의 자체는 항상 전체 세트를 유지하고, 호출 가능 여부는 `tool_choice`나 애플리케이션 레벨 라우팅으로 제어 | 툴 정의 변경은 전체 캐시를 무효화하지만 `tool_choice` 변경은 messages 캐시에 영향 없음([Anthropic, Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)) | 사용하지 않는 툴의 스키마도 매 요청 토큰으로 계산됨(캐시 히트 시엔 0.1배 가격) |
| MCP 서버가 재시작마다 스키마 설명 문구를 약간 다르게 생성 | MCP 서버 측에서 스키마를 정적 상수로 고정하거나, 클라이언트에서 스키마를 정규화(canonicalize)한 뒤 해시 비교로 변경 여부 판단 | 스키마 텍스트가 1바이트만 달라도 tools 캐시부터 전체가 무효화 | 서버 코드 수정 권한이 없으면 클라이언트 측 정규화 레이어가 필요해 복잡도 증가 |
| 딕셔너리/맵 구조를 JSON으로 직렬화해 프롬프트에 넣음 | 직렬화 직전 키를 항상 정렬(`sort_keys=True` 등)하고 float 포맷을 고정 | 언어·런타임에 따라 dict/set 순회 순서가 요청마다 달라질 수 있음 | 정렬로 인해 사람이 읽기엔 약간 부자연스러운 순서가 될 수 있으나 캐시 안정성이 우선 |
| 캐시 TTL(5분) 사이에 요청 간격이 벌어짐 | 백그라운드 프로세스로 주기적인 no-op/ping 요청을 보내 캐시를 warm 상태로 유지 | TTL 만료 전에 재요청해 캐시 항목을 갱신(refresh)시킴 | ping 요청 자체도 캐시 write/read 토큰이 과금되므로 트래픽 패턴에 따라 비용 분석 필요 — [캐시 지표와 경제성](/04-caching/cache-metrics-economics) 참고 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 캐시 히트율이 세션 내내 0%에 가까움, `cache_creation_input_tokens`만 매번 발생 | 동적 시스템 프롬프트 — 요청마다 system 문자열 전체를 새로 조립(예: 현재 대화 요약, 최근 조회 결과를 system에 직접 삽입) | 두 연속 요청의 system 필드를 diff — 완전히 동일해야 할 부분에서 문자 단위 차이가 나는지 확인 | system을 "정적 정책/역할"과 "동적 컨텍스트"로 분리하고, 동적 컨텍스트는 messages의 별도 블록(브레이크포인트 뒤)으로 이동 |
| 응답 시간이 짧은 대화에서도 캐시가 안 잡히고, 로그에 초 단위로 다른 문자열이 보임 | 타임스탬프가 프롬프트 앞부분(시스템 프롬프트 초반)에 초 단위 정밀도로 삽입됨 | system 프롬프트에서 `datetime.now()` / `Date.now()` 계열 호출을 grep, 그 결과가 브레이크포인트 이전에 있는지 확인 | 타임스탬프를 분 단위 이상으로 버킷화하거나, 반드시 필요하면 프롬프트 맨 끝(가장 동적인 블록)으로 이동 |
| 특정 사용자만 캐시 히트가 안 되고 나머지는 정상 | per-user 데이터(사용자 ID, 권한, 최근 활동)가 캐시 브레이크포인트보다 **앞**에 주입됨 — 사용자마다 프리픽스가 달라짐 | 동일 사용자의 연속 요청은 히트하는지, 다른 사용자 간에는 히트하는지 비교 | per-user 데이터를 브레이크포인트 뒤(messages 쪽)로 옮기고, 공유 가능한 시스템 정책만 브레이크포인트 앞에 유지 — 필요하면 사용자군별로 별도 브레이크포인트(최대 4개) 운용 |
| 배포 후 또는 오토스케일링으로 새 인스턴스가 뜬 직후부터 캐시 히트율이 급락 | 툴 목록이 코드에서 `dict`/`set` 순회나 플러그인 로딩 순서에 의존해 재정렬됨 — 같은 툴이지만 순서가 매 프로세스마다 다름 | 두 프로세스(또는 재시작 전후)에서 `tools` 배열을 JSON으로 dump해 순서까지 완전히 diff | 툴 목록을 이름 등 고정 키로 명시적으로 정렬(sort)한 뒤 API에 전달, 정렬 로직 자체를 배포 산출물에 고정 |
| 같은 애플리케이션 코드인데 인스턴스마다 캐시 히트율이 다름 | 비결정적 JSON 직렬화 — 딕셔너리 키 순서가 언어/런타임/프로세스마다 달라 동일 데이터가 다른 바이트열로 직렬화됨(Swift·Go 등 일부 런타임은 키 순서를 무작위화) | 동일 입력에 대해 직렬화 결과를 여러 번 생성해 바이트 단위로 비교 | 직렬화 시 `sort_keys=True`(Python `json.dumps`) 등 키를 항상 정렬하는 옵션을 강제, float/숫자 포맷도 고정 |
| MCP 서버를 재시작하거나 재배포한 뒤부터 tools 캐시가 통째로 깨짐 | MCP 툴 스키마 churn — MCP 서버가 시작할 때마다 description 문구, 예시, 버전 문자열 등을 약간씩 다르게 생성(비결정적 템플릿, 타임스탬프 포함 등) | 재시작 전후의 `tools/list` 응답을 그대로 저장해 diff | MCP 서버 쪽에서 스키마 텍스트를 정적 상수로 고정하거나, 게이트웨이/클라이언트 레벨에서 스키마를 정규화한 뒤 캐노니컬 해시로 비교해 실제 의미 변경이 없으면 이전 스키마 문자열을 재사용 |

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요
> 위 표의 "비결정적 JSON 직렬화"와 "MCP 툴 스키마 churn" 행은 Anthropic 공식 문서가 아니라 커뮤니티 사례([DevelopersIO, Understanding Prompt Caching's "Prefix Principle"](https://dev.classmethod.jp/en/articles/prompt-caching-prefix-breakpoint-tool-schema-optimization/), [GitHub Issue #530, esengine/DeepSeek-Reasonix](https://github.com/esengine/DeepSeek-Reasonix/issues/530))에 기반한다. 캐시 계층 구조(tools→system→messages)와 정확 매칭 요구사항 자체는 Anthropic 공식 문서로 확인됐지만, 특정 언어(Go/Swift)의 JSON 키 순서 무작위화 여부는 언어 버전마다 다를 수 있으므로 자신의 런타임에서 직접 재현 테스트할 것.

## 안티패턴

- ❌ `system` 프롬프트를 매 요청마다 f-string으로 완전히 새로 조립(정적 부분과 동적 부분을 문자열 하나로 뭉침) → ✅ 정적 부분은 상수로 고정하고, 동적 부분만 별도 블록으로 분리해 브레이크포인트 뒤에 붙인다.
- ❌ 툴 목록을 `dict.values()`나 플러그인 자동 탐색 순서 그대로 API에 전달 → ✅ 툴 이름 등 고정 키로 명시적으로 `sort()`한 뒤 전달한다.
- ❌ 사용자 프로필·최근 활동 로그를 시스템 프롬프트 맨 앞에 삽입("당신은 {user_name}님을 돕는 어시스턴트입니다") → ✅ 역할/정책 설명을 앞에 두고, 사용자별 데이터는 브레이크포인트 뒤 별도 블록에 둔다.
- ❌ 요청 바디를 만들 때 언어 기본 직렬화기를 그대로 사용(키 순서 미보장) → ✅ 직렬화 전 키 정렬을 강제하는 유틸리티 함수를 하나로 통일해 모든 프롬프트 조립 경로에서 재사용한다.
- ❌ MCP 서버 description을 코드에서 매 시작마다 동적으로 생성(버전 문자열, 빌드 타임스탬프 포함) → ✅ description을 빌드 시점에 고정된 정적 문자열로 컴파일해 넣는다.

## 코드 예시

아래는 동일한 기능(툴 정의 + 시스템 프롬프트 + 사용자 메시지로 채팅 요청 구성)을 하되, 캐시 히트 여부만 다른 두 조립 방식이다. 캐시 키 파생 순서(`tools` → `system` → `messages`, 브레이크포인트까지 정확 매칭)를 그대로 반영했다.

### ❌ 나쁜 예 — 매 요청마다 프리픽스가 바뀜

```python
import json
import time
from datetime import datetime

def build_request(user_id: str, user_profile: dict, tool_registry: dict, user_message: str):
    # 문제 1: dict.values() 순회 순서는 삽입 순서에 의존하지만,
    #         tool_registry가 플러그인 자동 탐색으로 채워지면 프로세스마다 순서가 달라진다.
    tools = list(tool_registry.values())

    # 문제 2: 초 단위 타임스탬프 + per-user 데이터가 system 앞부분에 통째로 섞여 들어간다.
    system_prompt = (
        f"현재 시각: {datetime.now().isoformat()}\n"
        f"사용자 {user_id} ({user_profile.get('tier')}) 님을 돕는 어시스턴트입니다.\n"
        "당신은 정책을 준수하며 툴을 신중하게 사용합니다.\n"
        "... (수백~수천 토큰의 정적 정책/가이드라인) ..."
    )

    # 문제 3: 비결정적 직렬화 — 키 순서 미보장, float 포맷도 런타임에 따라 달라질 수 있음
    context_blob = json.dumps(user_profile)  # sort_keys 미지정

    return {
        "model": "claude-sonnet-5",
        "tools": tools,  # 브레이크포인트 없음 — tools 자체가 매번 다른 순서
        "system": system_prompt + "\n\n" + context_blob,
        "messages": [{"role": "user", "content": user_message}],
    }
```

이 조립 방식은 tools 배열 순서, 초 단위 타임스탬프, 정렬되지 않은 JSON이 모두 브레이크포인트보다(사실은 브레이크포인트 자체가 없다) 앞에 있어 매 요청 완전히 새로운 프리픽스를 만든다. "Cache hits require 100% identical prompt segments"([Anthropic, Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)) 조건을 구조적으로 만족할 수 없다.

### ✅ 좋은 예 — static → dynamic 순서 + 명시적 브레이크포인트

```python
import json
from datetime import datetime, timedelta

# 정적 정책은 배포 시점에 고정된 모듈 레벨 상수 — 런타임에 재조립하지 않는다.
STATIC_POLICY_PROMPT = (
    "당신은 정책을 준수하며 툴을 신중하게 사용하는 어시스턴트입니다.\n"
    "... (수백~수천 토큰의 정적 정책/가이드라인, 배포 후 변경되지 않음) ..."
)

def sorted_tools(tool_registry: dict) -> list:
    # 툴 이름으로 고정 정렬 — 등록 순서/플러그인 탐색 순서와 무관하게 항상 동일한 배열
    return [tool_registry[name] for name in sorted(tool_registry.keys())]

def bucketed_timestamp(now: datetime, minutes: int = 5) -> str:
    # 초 단위 타임스탬프 대신 5분 단위로 버킷화 — TTL 창 안에서는 대부분 동일한 값
    floored = now - timedelta(
        minutes=now.minute % minutes, seconds=now.second, microseconds=now.microsecond
    )
    return floored.isoformat()

def build_request(user_id: str, user_profile: dict, tool_registry: dict, user_message: str):
    tools = sorted_tools(tool_registry)

    # 정적 프리픽스 끝에 캐시 브레이크포인트를 명시적으로 표시
    system_blocks = [
        {
            "type": "text",
            "text": STATIC_POLICY_PROMPT,
            "cache_control": {"type": "ephemeral"},
        },
        {
            # 동적이지만 저빈도로 바뀌는 내용(버킷화된 타임스탬프)은 브레이크포인트 뒤
            "type": "text",
            "text": f"현재 시각(5분 버킷): {bucketed_timestamp(datetime.now())}",
        },
    ]

    # per-user 데이터는 system이 아니라 messages 쪽, 브레이크포인트보다 뒤에 위치
    user_context = json.dumps(user_profile, sort_keys=True)  # 키 정렬로 직렬화 결정성 확보
    messages = [
        {
            "role": "user",
            "content": f"[사용자 컨텍스트: {user_context}]\n\n{user_message}",
        }
    ]

    return {
        "model": "claude-sonnet-5",
        "tools": tools,       # 정렬된 배열 — 항상 동일한 프리픽스
        "system": system_blocks,
        "messages": messages,
    }
```

두 버전은 기능적으로 동일한 요청을 만든다. 차이는 (1) `tools`가 항상 동일한 순서로 정렬되고, (2) 정적 정책과 동적 내용이 분리돼 브레이크포인트가 실제로 의미를 갖고, (3) per-user 데이터가 브레이크포인트 뒤로 밀려 tools+정적 system 캐시를 사용자 전체가 공유할 수 있고, (4) JSON 직렬화가 `sort_keys=True`로 결정적이라는 점이다.

### 백그라운드 warm-keeper 패턴

TTL(기본 5분)이 지나면 캐시 항목이 사라진다. 트래픽이 뜸한 시간대(예: 야간 배치, 저빈도 챗봇)에서는 다음 요청이 왔을 때 이미 캐시가 만료돼 있을 수 있다. 이를 막기 위해 별도 백그라운드 프로세스가 동일한 정적 프리픽스로 최소한의 no-op 요청을 주기적으로 보내 캐시를 갱신(refresh)한다.

```python
import time
import threading

def warm_cache_loop(client, static_request_builder, interval_seconds: int = 240):
    """TTL(5분)보다 짧은 주기로 정적 프리픽스와 동일한 요청을 보내 캐시를 refresh한다."""
    while True:
        request = static_request_builder()  # tools + system(정적 부분)까지만 동일하게 구성
        request["messages"] = [{"role": "user", "content": "ping"}]
        request["max_tokens"] = 1
        client.messages.create(**request)
        time.sleep(interval_seconds)

# 애플리케이션 시작 시 데몬 스레드로 실행
threading.Thread(target=warm_cache_loop, args=(client, build_static_prefix), daemon=True).start()
```

이 패턴은 캐시 write/read 토큰 자체가 과금되므로("Cache read tokens" 등 가격 구조는 [프롬프트 캐싱 기초](/04-caching/prompt-caching-basics) 참고), 유휴 시간이 길고 재시작 지연에 민감한 워크로드에서만 사용한다. 트래픽이 이미 TTL보다 촘촘하면 불필요한 비용이다.

## 계측 (SLI)

캐시 미스 근본 원인을 잡는 계측은 원인별로 다른 신호를 봐야 한다.

- **프리픽스 diff**: 연속된 두 요청의 `tools`/`system` 필드를 바이트 단위로 저장해 diff — 어떤 필드가, 몇 번째 문자부터 달라지는지가 근본 원인을 바로 알려준다.
- **`cache_creation_input_tokens` vs `cache_read_input_tokens` 비율**: 응답의 `usage` 필드에서 이 비율을 시계열로 추적. write만 계속 발생하고 read가 없으면 프리픽스가 매번 새로 만들어지고 있다는 신호다.
- **사용자군별 히트율 분리**: 전체 평균 히트율은 문제를 가릴 수 있다. per-user 데이터 위치 문제는 사용자별로 쪼개서 봐야 드러난다.
- **배포/재시작 이벤트와 히트율 상관**: 배포 직후 히트율이 급락했다가 서서히 회복되면 툴 순서·MCP 스키마 churn을 의심한다.

세부 지표 정의와 대시보드 설계는 [캐시 지표와 경제성](/04-caching/cache-metrics-economics)에서 다룬다.

## 체크리스트

- [ ] 시스템 프롬프트가 정적 부분과 동적 부분으로 명확히 분리돼 있고, 캐시 브레이크포인트가 정적 프리픽스의 끝에 있다
- [ ] 프롬프트에 타임스탬프가 있다면 초 단위가 아니라 분 단위 이상으로 버킷화돼 있다
- [ ] per-user 데이터(사용자 ID, 권한, 프로필)가 캐시 브레이크포인트보다 뒤(messages 쪽)에 위치한다
- [ ] 툴 목록이 고정된 키(이름 등)로 명시적으로 정렬돼 API에 전달된다
- [ ] JSON 직렬화 시 키 정렬(`sort_keys=True` 등)을 강제하는 공용 유틸리티를 모든 프롬프트 조립 경로가 사용한다
- [ ] MCP 서버 재시작 전후로 `tools/list` 응답을 diff해 스키마 churn이 없는지 확인했다
- [ ] 연속 요청의 `tools`/`system` 프리픽스를 diff하는 계측(또는 최소한 수동 점검 스크립트)이 있다
- [ ] 트래픽이 뜸한 시간대가 있다면 warm-keeper 백그라운드 프로세스 도입 여부를 비용 대비 검토했다

## 참고

- [Anthropic, Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) — 캐시 프리픽스 순서(tools→system→messages), 정확 매칭 요구사항, 20블록 lookback window, 브레이크포인트 규칙의 공식 출처
- [Manus, Context Engineering for AI Agents: Lessons from Building Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus) — 타임스탬프를 시스템 프롬프트 앞부분에 넣지 말라는 실무 권고, append-only 컨텍스트, 툴 masking 패턴

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요
> - [DevelopersIO, Understanding Prompt Caching's "Prefix Principle"](https://dev.classmethod.jp/en/articles/prompt-caching-prefix-breakpoint-tool-schema-optimization/) — 툴 순서·JSON 키 순서 비결정성이 캐시를 깨는 사례
> - [GitHub Issue #530, esengine/DeepSeek-Reasonix — Stabilize MCP tool schema serialization for prefix cache reuse](https://github.com/esengine/DeepSeek-Reasonix/issues/530) — MCP 툴 스키마 직렬화 비결정성 사례
