---
title: 성능과 지연 개관
description: 느려지는 문제(6대 통증점 1번)를 다루는 Part 2의 개관과 챕터 요약.
outline: [2, 3]
---

# 성능과 지연 개관

::: tip 이 파트가 해결하는 통증점
[6대 통증점](/00-intro/six-pain-points) 중 **1번: 느려짐(지연)**을 전담한다.
:::

에이전트의 지연은 단일 LLM 호출의 지연 × 루프 반복 횟수 + 툴 실행 시간의 합이다. 가장 큰 레버는 모델 속도가 아니라 반복 횟수 축소다. 이 파트는 지연을 구성요소로 분해하고, 라운드트립·스트리밍·모델 라우팅이라는 세 가지 레버를 다룬 뒤, 실전 진단 런북으로 마무리한다.

## 챕터 요약

| 챕터 | 한 줄 요약 |
|---|---|
| [지연의 해부](/02-performance/latency-anatomy) | TTFT·ITL, prefill vs decode, 에이전트 루프의 지연 구조식 |
| [툴 라운드트립](/02-performance/tool-roundtrips) | 라운드트립당 비용 분해와 4개 축소 레버(통합·code execution·병렬·응답 설계) |
| [스트리밍과 병렬 툴 호출](/02-performance/streaming-parallel-tools) | 체감 지연 개선(스트리밍)과 sum→max 병렬화, 스트리밍 가드레일 딜레마 |
| [모델 라우팅](/02-performance/model-routing) | 작업 난이도에 맞는 모델 선택, Bedrock Intelligent Prompt Routing |
| [지연 체크리스트](/02-performance/latency-checklist) | "느리다"는 보고를 받았을 때의 7단계 진단 런북 |

## 결정 요약

- 루프 반복 횟수 상한(maxIterations)을 어디에 둘 것인가
- 어떤 툴들을 통합하거나 code execution으로 묶을 것인가
- 스트리밍을 켤 것인가 — 켠다면 가드레일 검증을 어떻게 처리할 것인가
- 스텝 성격별로 모델을 라우팅할 것인가

## 관련 다른 파트

- [Part 4 프롬프트 캐싱과 KV 캐시](/04-caching/) — 캐시 히트는 prefill을 스킵해 TTFT를 직접 개선한다
- [Part 5 컨텍스트 엔지니어링](/05-context/) — 입력을 줄이면 prefill이 빨라진다
- [Part 8 스케일링과 비용](/08-scaling-cost/) — 콜드스타트와 스로틀링이 만드는 꼬리 지연
