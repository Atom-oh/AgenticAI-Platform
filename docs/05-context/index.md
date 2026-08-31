---
title: 컨텍스트 엔지니어링 개관
description: 컨텍스트가 유지 안 되는 문제(6대 통증점 4번)를 다루는 Part 5의 개관과 챕터 요약.
outline: [2, 3]
---

# 컨텍스트 엔지니어링 개관

::: tip 이 파트가 해결하는 통증점
[6대 통증점](/00-intro/six-pain-points) 중 **4번: 컨텍스트가 유지 안 됨**을 전담한다.
:::

컨텍스트 윈도우가 커진다고 문제가 풀리지 않는다. 입력 토큰이 늘어날수록 성능이 저하되는 context rot은 18개 프런티어 모델 전부에서 관측된 현상이다. 이 파트는 "가장 작은 고신호 토큰 집합"을 유지하기 위한 네 가지 레버 — compaction, 격리·오프로딩, JIT 검색 — 를 다룬다.

## 챕터 요약

| 챕터 | 한 줄 요약 |
|---|---|
| [컨텍스트 엔지니어링 원칙](/05-context/context-engineering-discipline) | 프롬프트 엔지니어링의 확장, "가장 작은 고신호 토큰 집합"이라는 핵심 원칙 |
| [Context rot](/05-context/context-rot) | 입력 길이 증가에 따른 성능 저하 — Chroma·Lost in the Middle·Databricks Mosaic 연구 |
| [Compaction과 요약](/05-context/compaction-summarization) | 첫 번째 레버 — 오래된 턴을 요약으로 압축, 되돌릴 수 없는 사실은 보존 |
| [컨텍스트 격리와 오프로딩](/05-context/context-isolation-offloading) | 서브에이전트 격리, 파일 오프로딩, 초기화 에이전트의 요구사항 파일 패턴 |
| [JIT 검색과 토큰 예산](/05-context/jit-retrieval-token-budget) | just-in-time 검색 vs 프리로딩, 토큰 예산 배분 |

## 결정 요약

- 시스템 프롬프트·툴 정의를 "고신호" 기준으로 다이어트할 것인가
- 대화 길이가 늘어날 때 compaction을 어느 임계치에서 트리거할 것인가
- 서브에이전트로 컨텍스트를 격리할지, 파일로 오프로딩할지
- 문서 코퍼스를 JIT 검색할지 프리로딩(+캐싱)할지

## 관련 다른 파트

- [Part 4 프롬프트 캐싱과 KV 캐시](/04-caching/) — 캐시와 컨텍스트 관리는 다른 문제이지만 자주 혼동된다
- [Part 6 임베딩과 벡터 검색](/06-vector-search/graphrag-agentic-when-not) — RAG를 쓰지 말고 CAG로 전체를 캐시하는 대안
- [Part 11 에이전트를 만드는 에이전트](/11-builder-agent/requirements-dialogue) — 초기화 에이전트의 요구사항 파일 패턴
