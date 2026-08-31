---
title: 스케일링과 비용 개관
description: 스케일링·비용 통제 불가 문제(6대 통증점 5번)를 다루는 Part 8의 개관과 챕터 요약.
outline: [2, 3]
---

# 스케일링과 비용 개관

::: tip 이 파트가 해결하는 통증점
[6대 통증점](/00-intro/six-pain-points) 중 **5번: 스케일링·비용 통제 불가**를 전담한다.
:::

LLM 워크로드의 스케일링은 전통적 웹 서비스와 다르다. 쿼터는 요청 수가 아니라 토큰(TPM)에 걸리고, 오토스케일 신호는 CPU/GPU%가 아니라 큐 깊이와 KV 캐시 활용률이며, 비용 폭주는 청구서가 아니라 실시간 서킷 브레이커로 막아야 한다. 이 파트는 쿼터·티어·차지백·오토스케일·콜드스타트의 다섯 축을 다룬다.

## 챕터 요약

| 챕터 | 한 줄 요약 |
|---|---|
| [동시성 쿼터와 스로틀링](/08-scaling-cost/concurrency-quotas-throttling) | TPM burndown 메커니즘, maxTokens 미설정의 함정, 멀티에이전트의 쿼터 증폭 |
| [Bedrock 추론 티어](/08-scaling-cost/bedrock-inference-tiers) | CRIS(지리 vs 글로벌), Provisioned Throughput, Reserved/Priority/Flex 티어 |
| [토큰 어카운팅과 차지백](/08-scaling-cost/token-accounting-chargeback) | 트레이스 기반 run별 합산, application inference profile, per-run 예산 캡 |
| [오토스케일 신호](/08-scaling-cost/autoscaling-signals) | CPU/GPU%가 아니라 큐 깊이 + KV 캐시 활용률 |
| [콜드스타트와 세션 유휴](/08-scaling-cost/coldstart-session-idle) | microVM 과금 구조(I/O 대기 시 CPU 무과금), 세션 재사용 설계 |

## 결정 요약

- 모든 호출에 maxTokens를 명시적으로 설정했는가(미설정 = 쿼터 과다 예약)
- TPM 스로틀이 빈발하면 CRIS → Provisioned/Reserved 순으로 검토했는가 — 글로벌 프로파일의 데이터 레지던시 트레이드오프를 확인했는가
- 테넌트별 비용 귀속 수단(application inference profile, 태그)을 갖췄는가
- per-run 예산 캡과 비용 서킷 브레이커를 하니스/게이트웨이에 넣었는가
- 세션 재사용을 설계했는가 — idle 타임아웃과 유휴 비용의 트레이드오프를 계산했는가

## 관련 다른 파트

- [Part 4 프롬프트 캐싱](/04-caching/) — 캐시 읽기는 TPM burndown에서 제외된다
- [Part 10 AgentCore 심화](/10-agentcore/quotas-pricing) — AgentCore 자체의 가격 단위와 쿼터
- [Part 12 보안·안전과 한국 금융 규제](/12-security-korea/hybrid-architecture) — CRIS 지리 프로파일 고정의 규제 맥락
