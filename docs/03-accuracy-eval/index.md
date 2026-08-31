---
title: 정확도와 평가 개관
description: 정확도가 떨어지는 문제(6대 통증점 2번)를 다루는 Part 3의 개관과 챕터 요약.
outline: [2, 3]
---

# 정확도와 평가 개관

::: tip 이 파트가 해결하는 통증점
[6대 통증점](/00-intro/six-pain-points) 중 **2번: 정확도 떨어짐**을 전담한다.
:::

에이전트 정확도는 세 곳에서 무너진다: 툴이 너무 많아 잘못 고르고(툴 과부하), 검색이 나쁜 문서를 가져오거나 인자를 지어내고(검색·환각), 단계가 많아 오류가 복합된다(p^n). 이 파트는 세 원인을 각각 진단하고, 재발을 막는 평가 하니스와 AWS의 평가 서비스를 다룬다.

## 챕터 요약

| 챕터 | 한 줄 요약 |
|---|---|
| [툴 과부하](/03-accuracy-eval/tool-overload) | 툴 수 증가에 따른 선택 정확도 저하 — 50개 전에 semantic search |
| [검색과 환각된 인자](/03-accuracy-eval/retrieval-and-hallucinated-args) | 검색 실패의 전이와 환각 인자의 4계층 방어 |
| [평가 하니스](/03-accuracy-eval/eval-harness) | p^n 에러 복합 표(정본), pass^k, 평가 도구 지형 |
| [LLM 판정과 trajectory 평가](/03-accuracy-eval/llm-judge-trajectory) | judge 편향 3종과 방지법, trajectory 매칭 방법론 |
| [AWS 평가 서비스](/03-accuracy-eval/aws-evaluations) | AgentCore Evaluations(OTel 스팬 평가)와 Bedrock RAG evaluation |

## 결정 요약

- 턴당 노출되는 툴 수를 몇 개로 제한할 것인가 — 50개 전에 semantic search를 도입할 것인가
- 환각된 인자를 스키마·검증·Cedar·에러 피드백 중 어느 계층에서 막을 것인가
- 파이프라인 단계 수가 10을 넘으면 축소·체크포인트를 검토했는가
- judge 모델을 대상 모델과 다른 계열로 선정했는가
- 어떤 평가를 CI 배포 게이트로 만들 것인가

## 관련 다른 파트

- [Part 1 에이전트 설계 기초](/01-agent-design/) — 툴 설계·통합이 정확도의 상류
- [Part 6 임베딩과 벡터 검색](/06-vector-search/) — 검색 품질 지표와 개선 레버의 정본
- [Part 11 에이전트를 만드는 에이전트](/11-builder-agent/agent-cicd) — evals-as-gate 배포 파이프라인
