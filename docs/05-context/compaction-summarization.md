---
title: Compaction과 요약
description: 컨텍스트 윈도우 한계에 다가갈 때 오래된 턴을 요약으로 압축하는 compaction 패턴과 그 실패 모드를 다룬다.
outline: [2, 3]
---

# Compaction과 요약

::: tip 이 장에서 얻는 것
- Compaction의 정의와, 이것이 왜 컨텍스트 엔지니어링에서 "첫 번째로 시도할 레버"인지에 대한 근거
- 요약에 무엇을 남기고 무엇을 버릴지 판단하는 원칙 (되돌릴 수 없는 사실 vs 중간 추론 과정)
- 토큰 임계치 기반 트리거와 턴 수 기반 트리거의 차이, 그리고 각각의 실패 양상
- Claude Code의 auto-compact, LangChain/LangGraph의 요약 노드가 이 원칙을 구현하는 방식
- Compaction과 [컨텍스트 격리·오프로딩](./context-isolation-offloading.md)의 역할 분담
:::

## 왜 문제가 되는가

에이전트 세션이 길어지면 대화 히스토리는 선형으로 누적된다. 컨텍스트 윈도우는 유한하고, 윈도우가 채워질수록 비용과 지연이 늘어나며 [context rot](./context-rot.md)으로 품질도 떨어진다. 이 문제에 대응하는 레버는 크게 두 갈래로 나뉜다.

- **Compaction (이 챕터)**: 이미 컨텍스트에 들어간 내용을 손실 압축한다. 오래된 턴을 요약으로 대체하고, 최근 턴은 원문 그대로 유지한다. 정보를 완전히 없애지는 않지만 세부를 뭉개서 자리를 확보한다.
- **격리(isolation)와 오프로딩(offloading)** ([별도 챕터](./context-isolation-offloading.md)): 애초에 메인 컨텍스트에 넣지 않는다. 서브에이전트에게 세부 작업을 위임해 그 결과 요약만 돌려받거나, 파일 시스템·외부 스토리지로 대량 데이터를 빼내고 참조만 남긴다.

두 레버는 순서가 있다. Anthropic의 "Effective context engineering for AI agents"는 다음과 같이 명시한다.

> "Compaction typically serves as the first lever in context engineering to drive better long-term coherence."
> — [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

즉 컨텍스트가 커지기 시작할 때 먼저 시도할 것은 compaction이다. compaction으로도 부족하거나, 애초에 대량의 중간 산물(파일 전체 내용, 로그, 검색 결과)이 메인 루프에 들어오는 구조라면 격리·오프로딩으로 아키텍처를 바꿔야 한다. 이 장은 전자를, [컨텍스트 격리와 오프로딩](./context-isolation-offloading.md)은 후자를 다룬다. 두 레버는 배타적이지 않다 — 실무에서는 서브에이전트로 격리한 위에 각 서브에이전트 세션 안에서도 compaction이 걸린다.

컨텍스트 엔지니어링 원칙 전반(고신호 토큰 집합을 유지한다는 관점)은 [컨텍스트 엔지니어링 원칙](./context-engineering-discipline.md) 챕터에서 다룬다. compaction은 그 원칙을 "시간이 지나 낡은 토큰"에 적용한 구체적 기법이다.

## 핵심 개념

### Compaction의 정의

같은 문서는 compaction을 다음처럼 정의한다.

> "Compaction is the practice of taking a conversation nearing the context window limit, summarizing its contents, and reinitiating a new context window with the summary."
> — [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

정리하면 세 단계다.

1. 컨텍스트 윈도우가 한계에 근접했음을 감지한다(트리거).
2. 대화 내용을 요약한다.
3. 요약 + 최근 원문 턴으로 새 컨텍스트 윈도우를 다시 시작한다.

"요약으로 대체"라는 표현이 핵심이다. compaction은 정보를 버리는 게 아니라 **정밀도(precision)를 낮추는** 연산이다. 오래된 턴의 구체적 문구, 도구 호출의 원문 출력, 탐색 과정의 디테일은 사라지지만 "무엇이 결정되었는가", "무엇이 아직 미해결인가" 같은 상위 사실은 요약에 남는다.

### 무엇을 남기고 무엇을 버리는가

같은 문서는 요약 프롬프트가 보존해야 할 것을 다음처럼 제시한다.

> "The model preserves architectural decisions, unresolved bugs, and implementation details while discarding redundant tool outputs or messages."
> — [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

그리고 가장 가볍고 안전한 compaction 형태로 도구 결과 삭제를 든다.

> "One of the safest lightest touch forms of compaction is tool result clearing"
> — [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

이걸 실무 원칙으로 일반화하면, 보존/폐기의 기준은 "**되돌릴 수 없는가**"이다.

- **되돌릴 수 없는 사실**: 사용자가 준 제약(예산, 마감, 금지 사항), 파일 경로·ID·자격 정보 참조, 이미 내려진 아키텍처 결정, 아직 닫히지 않은 버그·이슈 상태. 이것들은 다시 물어보거나 다시 조회할 방법이 없거나 비용이 크다. 요약에서도 원문 그대로, 혹은 손실 없이 재진술되어야 한다.
- **압축 가능한 중간 산물**: 파일을 읽어본 원문, grep/도구 호출의 raw 출력, 탐색 과정에서 시도했다가 버린 경로, 이미 적용되어 코드에 반영된 diff의 원문. 이것들은 결론(어떤 파일을 어떻게 바꿨는가)만 남기면 원문은 다시 필요할 때 파일 시스템에서 재조회 가능하다.

이 구분을 놓치면 요약이 "그럴듯하게 짧지만 핵심 제약이 빠진" 텍스트가 되고, 에이전트는 몇 턴 전에 사용자와 합의한 제약을 잊고 위반한다. 아래 결정 표와 실패 모드 표에서 이 문제를 더 다룬다.

### 트리거: 토큰 임계치 vs 턴 수

compaction을 언제 발동할지는 크게 두 방식으로 나뉜다.

**토큰 임계치 기반**은 컨텍스트 윈도우 사용량이 특정 값(절대 토큰 수 또는 전체 대비 비율)에 도달하면 발동한다. Claude Code의 auto-compact가 이 방식이다.

> "Sessions auto-compact before the window fills, at about 967K tokens by default; you can set `CLAUDE_CODE_AUTO_COMPACT_WINDOW` to choose a different threshold... The auto-compact window setting accepts a number of tokens from 100000 to 1000000, and Claude Code caps the value at your model's context window."
> — [Manage costs effectively — Claude Code Docs](https://code.claude.com/docs/en/costs)

또한 공식 문서는 auto-compact 경고를 사용량 한도가 아니라 컨텍스트 관리 신호로 명시한다.

> "A context or auto-compact warning: not a usage limit. The conversation has grown close to the session's auto-compact window, the threshold where Claude Code summarizes older history to free space."
> — [Manage costs effectively — Claude Code Docs](https://code.claude.com/docs/en/costs)

LangChain의 단기 메모리(short-term memory) 가이드도 토큰 기반 트리거를 기본으로 제시한다. `SummarizationNode` 설정에서 `trigger=("tokens", 4000)`처럼 누적 토큰 수가 특정 값을 넘을 때 요약을 실행하고, `keep=("messages", 20)`처럼 최근 N개 메시지는 원문으로 유지하는 방식이다([Short-term memory — LangChain docs](https://docs.langchain.com/oss/python/langchain/short-term-memory)).

**턴 수 기반**은 대화 턴(또는 도구 호출) 개수가 임계치를 넘으면 발동한다. 구현이 단순하지만 턴마다 토큰 크기가 크게 다른 에이전트 워크플로(예: 파일 전체를 읽어오는 턴과 한 줄 응답 턴이 섞인 경우)에서는 실제 컨텍스트 사용량과 상관이 약해진다. 이 때문에 실무에서는 토큰 임계치 기반이 더 신뢰할 수 있는 신호로 선호되는 경향이 있다.

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요
> auto-compact가 "컨텍스트 윈도우의 약 98%에서 발동한다"는 구체적 비율은 서드파티 블로그(예: CometAPI, howaiworks.ai)에서 언급되지만 Claude Code 공식 문서에서는 절대 토큰 수(967K 기본값, `/autocompact` 명령으로 조정)로만 설명되며 퍼센트 수치는 확인되지 않았다. 버전마다 바뀔 수 있으므로 정확한 임계값은 `/autocompact` 명령이나 최신 [Model configuration 문서](https://code.claude.com/docs/en/model-config)로 직접 확인하라.

### 실패 시 나타나는 패턴

compaction이 잘못되면 전형적으로 다음 순서로 문제가 드러난다.

1. 요약 프롬프트가 "최근성"과 "빈도"를 신호로 써서 중요도를 판단한다 — 하지만 사용자가 세션 초반에 한 번만 말한 제약(예: "이 API는 절대 호출하지 말 것")은 최근성도 빈도도 낮다.
2. 압축 후 새 컨텍스트 윈도우에는 그 제약이 요약문에서 빠지거나 흐려진 형태로만 남는다.
3. 이후 턴에서 에이전트는 이미 합의된 제약을 위반한 행동을 다시 시도한다. 사용자 입장에서는 "몇 턴 전에 말했잖아"라는 반응이 나온다.

Anthropic 문서는 이 위험을 다음처럼 요약한다.

> "Overly aggressive compaction can result in the loss of subtle but critical context whose importance only becomes apparent later."
> — [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

이에 대한 대응 전략으로 같은 문서는 요약 프롬프트를 만드는 순서를 제시한다.

> "Start by maximizing recall to ensure your compaction prompt captures every relevant piece of information from the trace, then iterate to improve precision by eliminating superfluous content."
> — [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

즉 처음부터 "짧고 정확한" 요약을 목표로 하지 말고, 먼저 "누락 없이 다 담는" 요약을 만든 뒤 반복적으로 다듬어 불필요한 것을 줄이는 순서로 접근하라는 것이다. recall을 먼저 최대화하고 precision을 나중에 올리는 순서이며, 반대로 하면(처음부터 짧게 쓰려고 하면) 핵심 제약이 누락될 위험이 커진다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 세션이 길어지지만 턴 크기가 균일하지 않음(파일 읽기 vs 짧은 응답 혼재) | 토큰 임계치 기반 트리거 | 실제 컨텍스트 사용량과 직결된 신호 | 임계치 산정에 모델별 컨텍스트 윈도우 크기를 알아야 함 |
| 턴 크기가 대체로 균일하고 구현 단순성이 더 중요 | 턴 수 기반 트리거 | 구현·디버깅이 쉬움 | 실제 토큰 사용량과 상관이 약해 너무 늦거나 너무 이르게 발동할 수 있음 |
| 사용자 제약·ID·경로처럼 되돌릴 수 없는 사실 | 요약에서도 원문 그대로 보존 | 재조회 불가능하거나 비용이 큼 | 요약 길이가 늘어남 |
| 이미 적용되어 코드/상태에 반영된 도구 호출 원문 출력 | 결론만 남기고 원문 폐기 (tool result clearing) | 원문은 필요 시 파일 시스템에서 재조회 가능 | 재조회 시 지연 발생 |
| 요약 프롬프트를 처음 설계하는 단계 | recall 우선 최적화 후 precision 반복 개선 | 처음부터 짧게 쓰면 핵심 제약 누락 위험 | 초기 요약이 장황해질 수 있음 |
| 파일 전체 내용, 대량 로그, 검색 결과처럼 애초에 크기가 큰 산물 | compaction 대신 격리·오프로딩 검토 | 압축이 아니라 애초에 메인 컨텍스트에 안 넣는 게 근본 해법 | 아키텍처 변경 비용, 서브에이전트 오케스트레이션 복잡도 |
| 특정 작업(버그 수정 등)에 집중해 compaction 결과를 통제하고 싶을 때 | 포커스 지시를 포함한 명시적 compaction (`/compact focus on ...`) | 자동 요약이 무엇을 중요하다고 추측하는 것보다 신뢰 가능 | 사용자가 매번 지시해야 함 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 에이전트가 몇 턴 전에 합의한 제약(금지 API, 예산 한도 등)을 다시 위반 | 요약 프롬프트가 최근성·빈도로만 중요도를 판단해 1회성 제약을 누락 | compaction 전후 요약문에 해당 제약 문구가 남아 있는지 직접 대조 | 요약 프롬프트에 "사용자가 명시한 제약·금지 사항은 무조건 보존"을 별도 규칙으로 추가, recall 우선 설계 |
| compaction 직후 에이전트가 파일 경로나 ID를 잘못 참조 | 요약이 구체적 식별자를 의역해 뭉갬(예: 정확한 경로 대신 "해당 파일") | 압축 전/후 컨텍스트에서 경로·ID 리터럴이 그대로 남아있는지 diff | 식별자류는 요약 대상에서 제외하고 원문 그대로 인용하도록 지시 |
| 토큰 사용량은 줄었는데 응답 품질이 오히려 나빠짐 | 지나치게 공격적인(precision 과다) compaction으로 subtle하지만 중요한 컨텍스트 손실 | 압축 전후로 같은 태스크에 대한 응답을 비교(A/B) | 압축 강도를 낮추고 recall 우선 원칙으로 되돌림 |
| 턴 수 기반 트리거인데 특정 세션만 계속 컨텍스트 초과 오류 발생 | 그 세션이 유난히 큰 도구 출력(대용량 로그, 파일 전체)을 자주 포함해 턴당 토큰이 편차가 큼 | 세션별 턴당 토큰 분포 확인 | 토큰 임계치 기반 트리거로 전환하거나, 대용량 산물은 격리·오프로딩으로 먼저 빼냄 |
| compaction이 반복적으로 발동해 비용이 급증 | 대화가 계속 유지되며 매번 전체 히스토리를 다시 읽어 요약(요약 자체가 큰 요청) | `/usage` 또는 OTel로 compaction 호출 빈도·비용 추적 | 관련 없는 작업 전환 시 요약 대신 완전히 새 세션(`/clear`)으로 시작 |
| 요약 후 스킬/도구 목록 관련 동작이 사라짐 | 일부 구현에서는 시작 시 로드된 특정 목록(예: 스킬 설명)이 compaction 후 재주입되지 않고, 실제 호출된 것만 보존됨 | compaction 전후 사용 가능한 스킬·도구 목록 비교 | 압축 후에도 필요한 스킬은 명시적으로 다시 언급하거나 재호출 |

## 안티패턴

- ❌ 요약 프롬프트를 "가능한 짧게"로 처음부터 설계 → ✅ 먼저 recall을 최대화해 모든 관련 정보를 담고, 이후 반복적으로 precision을 높여 불필요한 내용을 제거
- ❌ 사용자 제약과 중간 추론 과정을 동일한 강도로 압축 → ✅ 되돌릴 수 없는 사실(제약, ID, 경로, 미해결 이슈)은 원문 보존, 중간 산물(도구 raw 출력, 버려진 탐색 경로)만 압축
- ❌ 턴 수만 보고 compaction 시점을 정함 → ✅ 실제 토큰 사용량(또는 컨텍스트 윈도우 대비 비율)을 트리거 신호로 사용
- ❌ 파일 전체 내용이나 대량 로그를 매 턴 컨텍스트에 누적시키고 나서 압축으로 해결하려 함 → ✅ 애초에 서브에이전트 격리나 파일 시스템 오프로딩으로 메인 컨텍스트에 넣지 않음 ([컨텍스트 격리와 오프로딩](./context-isolation-offloading.md) 참고)
- ❌ compaction을 "블랙박스"로 두고 결과를 검증하지 않음 → ✅ 압축 전후 응답 품질을 비교하고, 중요한 작업 전에는 명시적 포커스 지시(`/compact focus on ...`)로 통제

## 계측 (SLI)

compaction의 건전성을 판단하려면 최소한 다음을 계측한다.

- **Compaction 발동 빈도/세션**: 세션당 몇 번 compaction이 발동하는지. 지나치게 자주 발동하면 세션을 분리(`/clear`)하는 게 더 저렴할 수 있다 — compaction 자체가 전체 히스토리를 다시 읽는 큰 요청이기 때문이다([Manage costs effectively](https://code.claude.com/docs/en/costs)).
- **압축 전/후 토큰 수와 절감률**: 압축으로 얼마나 자리를 확보했는지. Claude Code의 context-window 시뮬레이션 문서는 압축 전후 토큰 수 차이를 직접 노출한다([Explore the context window](https://code.claude.com/docs/en/context-window)).
- **제약 보존율**: 압축 전 컨텍스트에 있던 사용자 제약·식별자가 압축 후에도 검증 가능한 형태로 남아 있는지. 자동 검증이 어렵다면 회귀 테스트 세트(제약이 담긴 대표 세션)로 주기적으로 샘플링 확인.
- **compaction 이후 태스크 실패율**: compaction이 발생한 직후 턴에서의 실패(제약 위반, 잘못된 경로 참조 등) 비율을 compaction이 없었던 턴과 비교.
- **비용 기여도**: `/usage`의 behavior flag(전체 사용량의 10% 이상을 차지하는 행동) 또는 OpenTelemetry export로 long-context/compaction이 전체 비용에서 차지하는 비율을 추적([Manage costs effectively](https://code.claude.com/docs/en/costs)).

## 체크리스트

- [ ] compaction 트리거 방식(토큰 임계치 vs 턴 수)을 명시적으로 정하고, 워크로드의 턴당 토큰 편차를 고려해 선택했는가
- [ ] 요약 프롬프트에 "사용자가 명시한 제약·ID·경로·미해결 이슈는 무조건 보존"이라는 규칙이 별도로 명시되어 있는가
- [ ] 요약 프롬프트를 recall 우선으로 초안을 만들고, 이후 precision 개선을 반복했는가 (처음부터 짧게 쓰지 않았는가)
- [ ] 이미 적용된 도구 호출의 raw 출력처럼 재조회 가능한 중간 산물은 tool result clearing으로 가볍게 압축하고 있는가
- [ ] 압축 전후 응답 품질을 비교하는 회귀 테스트(A/B) 또는 대표 세션 샘플링이 있는가
- [ ] 파일 전체 내용·대량 로그·검색 결과처럼 애초에 큰 산물은 compaction이 아니라 격리·오프로딩으로 먼저 처리하는가
- [ ] compaction 발동 빈도, 토큰 절감률, compaction 이후 실패율을 계측하고 있는가
- [ ] 관련 없는 작업으로 전환할 때 compaction 대신 새 세션 시작(`/clear`)이 더 저렴한지 검토했는가

## 참고

- [Effective context engineering for AI agents — Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) — compaction의 정의, 첫 번째 레버라는 위치, 보존/폐기 기준, recall-then-precision 요약 설계 원칙의 출처. 컨텍스트 엔지니어링 전반의 원칙은 [컨텍스트 엔지니어링 원칙](./context-engineering-discipline.md) 챕터 참고.
- [Manage costs effectively — Claude Code Docs](https://code.claude.com/docs/en/costs) — auto-compact 기본 임계값(967K 토큰), `CLAUDE_CODE_AUTO_COMPACT_WINDOW`/`/autocompact` 설정, `/compact` 포커스 지시, compaction 자체의 비용 특성.
- [Explore the context window — Claude Code Docs](https://code.claude.com/docs/en/context-window) — `/compact` 실행 시 무엇이 재주입되고(시스템 프롬프트, CLAUDE.md, 메모리, MCP 도구) 무엇이 재주입되지 않는지(호출되지 않은 스킬 목록)에 대한 인터랙티브 설명.
- [Short-term memory — LangChain docs](https://docs.langchain.com/oss/python/langchain/short-term-memory) — `SummarizationNode`의 토큰 기반 트리거(`trigger=("tokens", N)`)와 최근 메시지 보존(`keep=("messages", N)`) 패턴, `RemoveMessage`를 통한 메시지 삭제와 요약의 차이.
- [컨텍스트 격리와 오프로딩](./context-isolation-offloading.md) — compaction으로 해결되지 않는, 애초에 메인 컨텍스트에 넣지 않는 전략.
- [Context rot](./context-rot.md) — compaction이 방어하고자 하는 근본 현상(입력 길이 증가에 따른 성능 저하).

::: warning 미정착 영역
"어느 정도의 압축 강도가 적정한가"(recall과 precision의 균형점)는 태스크·도메인에 따라 달라지며, 업계 전반에 합의된 정량적 기준은 아직 없다. 현재로서는 위 계측 항목(제약 보존율, compaction 이후 실패율)을 자체 워크로드에서 측정해 반복적으로 조정하는 것이 유일한 실무적 접근이다.
:::
