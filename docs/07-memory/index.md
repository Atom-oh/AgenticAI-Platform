---
title: 메모리 아키텍처 개관
description: 세션을 넘어 지속되는 에이전트 메모리 — 유형, 쓰기 정책, 스코핑, AgentCore Memory와 대안, 보안.
outline: [2, 3]
---

# 메모리 아키텍처 개관

::: tip 이 파트가 다루는 것
6대 통증점 중 **4번(컨텍스트 유지)**의 세션 간 확장이다. Part 5가 세션 내 컨텍스트 관리라면, 이 파트는 세션을 넘어 축적되는 상태를 다룬다.
:::

메모리는 "무엇을 기억할 것인가"(쓰기 정책), "누구의 기억을 누가 볼 수 있는가"(스코핑), "오염된 기억을 어떻게 막을 것인가"(보안)라는 세 가지 정책 문제다. 저장소 선택(AgentCore Memory vs Mem0/Zep/Letta/LangGraph)은 그 다음이다.

## 챕터 요약

| 챕터 | 한 줄 요약 |
|---|---|
| [메모리 유형](/07-memory/memory-types) | 단기/장기, semantic·episodic·procedural 유형학과 RAG와의 경계 |
| [메모리 쓰기 정책](/07-memory/memory-write-policies) | 무엇을 언제 저장할 것인가 — 추출→consolidation 파이프라인 |
| [메모리 검색과 스코핑](/07-memory/memory-retrieval-scoping) | 네임스페이스 설계, 테넌트+사용자 이중 스코핑, IAM 조건 키 |
| [AgentCore Memory와 대안](/07-memory/agentcore-memory-alternatives) | 2계층 구조(이벤트/전략)와 Mem0·Zep·Letta·LangGraph 비교 |
| [메모리 보안과 프라이버시](/07-memory/memory-security-privacy) | 메모리 포이즈닝, PII TTL, 잊힐 권리와 삭제 API |

## 결정 요약

- 어떤 장기 메모리 전략(semantic/summary/userPreference/episodic)을 켤 것인가
- 테넌트 격리를 actorId 합성으로 할 것인가 커스텀 네임스페이스 변수로 할 것인가
- 메모리 승격에 검증 게이트를 둘 것인가(포이즈닝 방어)
- PII 메모리의 TTL과 삭제 절차를 설계했는가

## 관련 다른 파트

- [Part 5 컨텍스트 엔지니어링](/05-context/) — 세션 내 컨텍스트 관리(compaction·오프로딩)와의 역할 분담
- [Part 9 세밀 권한 제어](/09-authorization/) — 메모리 접근의 IAM 조건과 신원 전파
- [Part 12 보안·안전과 한국 금융 규제](/12-security-korea/) — 프롬프트 인젝션과 잊힐 권리의 규제 맥락
