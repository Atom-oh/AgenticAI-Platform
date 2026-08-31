---
title: 프롬프트 캐싱과 KV 캐시 개관
description: 캐시 히트가 안 되는 문제(6대 통증점 3번)를 다루는 Part 4의 개관과 챕터 요약.
outline: [2, 3]
---

# 프롬프트 캐싱과 KV 캐시 개관

::: tip 이 파트가 해결하는 통증점
[6대 통증점](/00-intro/six-pain-points) 중 **3번: 캐시 히트가 안 됨(비용 폭증)**을 전담한다.
:::

에이전트 루프는 매 턴마다 이전 대화 전체를 다시 모델에 보낸다. 캐싱이 제대로 동작하지 않으면 이 재전송이 매번 full price로 청구되고, 토큰 누적이 사실상 quadratic하게 늘어난다. 이 파트는 API 레벨 프롬프트 캐싱부터 셀프호스팅 서빙 인프라의 KV 캐시까지, 계층별로 캐시가 왜 깨지고 어떻게 지키는지를 다룬다.

## 챕터 요약

| 챕터 | 한 줄 요약 |
|---|---|
| [프롬프트 캐싱 기초](/04-caching/prompt-caching-basics) | 캐시 키 파생 순서(tools→system→messages), 가격 배수, TTL, 모델별 최소 토큰 — 이 파트의 정본 수치 |
| [캐시 미스 근본 원인](/04-caching/cache-miss-root-causes) | 동적 프롬프트·타임스탬프·JSON 키 순서 등 히트를 깨는 6가지 원인과 코드 대조 예시 |
| [캐시 지표와 경제성](/04-caching/cache-metrics-economics) | quadratic 토큰 누적을 캐싱이 어떻게 flatten하는지, CloudWatch 지표로 비용을 읽는 법 |
| [vLLM KV 캐시](/04-caching/vllm-kv-cache) | 셀프호스팅 서빙 레이어의 PagedAttention·Automatic Prefix Caching·RadixAttention |
| [게이트웨이 세션 어피니티](/04-caching/gateway-session-affinity) | 다중 인스턴스 간 라우팅으로 KV 재사용률을 올리는 법과 그 한계 |
| [시맨틱 캐싱 vs 정확 캐싱](/04-caching/semantic-vs-exact-caching) | 히트율과 오답 재사용 위험의 트레이드오프, 규제 산업의 승인 게이트 |

## 결정 요약

이 파트를 다 읽고 나면 아래를 결정할 수 있어야 한다:

- 프롬프트를 static → dynamic 순서로 재구성하고 캐시 브레이크포인트를 정적 프리픽스 끝에 둘 것인가
- 세션 길이(5분 TTL vs 1시간 TTL)에 맞춰 어떤 TTL 전략을 쓸 것인가
- 캐시 히트율 70% 미만일 때 무엇을 먼저 점검할 것인가
- 셀프호스팅 모델을 쓴다면 vLLM/SGLang 중 어떤 KV 캐시 전략을 쓸 것인가
- 시맨틱 캐싱을 도입할지, 도입한다면 어떤 승인 절차를 거칠지

## 관련 다른 파트

- [Part 5 컨텍스트 엔지니어링](/05-context/) — 캐시와 컨텍스트 관리는 서로 다른 문제이지만 자주 혼동된다
- [Part 8 스케일링과 비용](/08-scaling-cost/) — KV 캐시 활용률을 오토스케일 신호로 쓰는 법
- [Part 12 보안·안전과 한국 금융 규제](/12-security-korea/) — 시맨틱 캐싱의 규제 산업 승인 게이트
