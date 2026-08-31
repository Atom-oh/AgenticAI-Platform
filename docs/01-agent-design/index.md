---
title: 에이전트 설계 기초 개관
description: 에이전트 설계의 근본 결정들 — 워크플로우냐 에이전트냐, 단일이냐 멀티냐, 툴과 MCP 서버를 어떻게 설계하느냐.
outline: [2, 3]
---

# 에이전트 설계 기초 개관

::: tip 이 파트가 다루는 것
6대 통증점의 **원인이 되는 설계 결정**을 다룬다. 여기서 내리는 결정(에이전트 vs 워크플로우, 단일 vs 멀티, 툴 개수와 응답 크기)이 이후 지연(Part 2)·정확도(Part 3)·비용(Part 4, 8)의 상한을 정한다.
:::

에이전트 설계에서 가장 비싼 실수는 코드가 아니라 아키텍처 선택에서 나온다. 필요 없는 곳에 에이전트를 쓰고, 필요 없는 곳에 멀티에이전트를 쓰고, 툴을 무한정 늘리는 것 — 이 파트는 그 결정들을 내리는 기준을 제공한다.

## 챕터 요약

| 챕터 | 한 줄 요약 |
|---|---|
| [에이전트 vs 워크플로우](/01-agent-design/agent-vs-workflow) | 가장 단순한 해법부터 — 언제 고정 워크플로우로 충분한가 |
| [단일 vs 멀티 에이전트](/01-agent-design/single-vs-multi-agent) | 90.2% 우위 vs 동일 토큰 예산에서는 단일 우세 — 미정착 논쟁의 양면 |
| [오케스트레이션 패턴](/01-agent-design/orchestration-patterns) | prompt chaining부터 evaluator-optimizer까지 5개 패턴 카탈로그 |
| [툴 설계](/01-agent-design/tool-design) | pagination·filtering·truncation, 응답 상한 정책, 에러 메시지 설계 |
| [MCP 서버 설계](/01-agent-design/mcp-server-design) | 스키마 안정성, code execution 패턴으로 토큰·PII 우회 |
| [신뢰성과 durable execution](/01-agent-design/reliability-durable-execution) | 멱등성, 체크포인팅, Temporal/Step Functions/Lambda durable functions |
| [프레임워크 지형](/01-agent-design/framework-landscape) | LangGraph·CrewAI·Strands·OpenAI Agents SDK 비교, 프레임워크 중립 하니스 전략 |

## 결정 요약

- 이 유스케이스는 에이전트가 필요한가, 고정 워크플로우로 충분한가
- 멀티에이전트를 도입한다면 비용 서킷 브레이커와 per-run 예산 캡을 함께 설계했는가
- 툴 응답 상한을 플랫폼 정책으로 강제할 것인가
- MCP 툴 정의의 스키마 안정성을 보장할 프로세스가 있는가
- 10단계 이상 파이프라인에 durable execution을 도입할 것인가
- 특정 프레임워크에 락인되지 않는 어댑터 전략을 취할 것인가

## 관련 다른 파트

- [Part 3 정확도와 평가](/03-accuracy-eval/) — 에러 복합(p^n)과 툴 과부하의 정량 분석
- [Part 5 컨텍스트 엔지니어링](/05-context/) — 서브에이전트 격리의 컨텍스트 관리 관점
- [Part 10 AgentCore 심화](/10-agentcore/) — 프레임워크 중립 배포 계약의 실체
