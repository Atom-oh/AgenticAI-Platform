---
title: 에이전트를 만드는 에이전트 개관
description: 빌더 에이전트의 요구사항 대화부터 가드레일, 카탈로그, evals-as-gate CI/CD, 스킬 패키징까지.
outline: [2, 3]
---

# 에이전트를 만드는 에이전트 개관

::: tip 이 파트가 다루는 것
이 책의 핵심 컨셉인 **메타플랫폼의 1번 시나리오** — 빌더 에이전트가 사용자와 대화하며 또 다른 에이전트를 생성·조립·배포하는 전체 파이프라인이다.
:::

빌더 에이전트의 산출물은 곧바로 배포되는 에이전트가 아니라 **구조화된 요구사항 파일**이고, 그 파일이 가드레일 검증 → 카탈로그 재사용 검토 → 샌드박스 시운전 → evals-as-gate 프로모션을 통과해야 프로덕션에 도달한다. 이 책의 `demo/builder-harness/`가 이 파트의 1단계(요구사항 대화)를 AgentCore Harness로 실제 배포한 실례다.

## 챕터 요약

| 챕터 | 한 줄 요약 |
|---|---|
| [요구사항 대화](/11-builder-agent/requirements-dialogue) | 명확화 질문의 설계와 요구사항 파일(spec) 작성 패턴 |
| [생성된 에이전트 가드레일](/11-builder-agent/generated-agent-guardrails) | 플랫폼이 생성물에 강제 주입하는 불변 가드레일 4계층 |
| [카탈로그와 레지스트리](/11-builder-agent/catalog-registry) | Skill·MCP 레지스트리 단일 진실원, 골든 패스 거버넌스 |
| [에이전트 CI/CD](/11-builder-agent/agent-cicd) | spec-driven 생성 → 샌드박스 검증 → dev→staging→prod evals-as-gate |
| [에이전트 스킬](/11-builder-agent/agent-skills) | SKILL.md progressive disclosure, 재사용 capability 패키징 표준 |

## 결정 요약

- 요구사항 파일에 성공 기준(→ 평가 assertions)·권한·예산·위험 등급을 포함시켰는가
- 생성된 에이전트의 위험 등급별 차등 게이트(자동 승인 vs 사람 리뷰)를 정의했는가
- 새로 만들기 전에 카탈로그를 검색하는 재사용 우선 원칙을 빌더에 넣었는가
- trajectory·goal 평가를 프로모션 게이트로 강제했는가
- 재사용 capability를 스킬/툴/MCP 중 무엇으로 패키징할지 기준이 있는가

## 관련 다른 파트

- [Part 0 메타플랫폼 전체 그림](/00-intro/meta-platform-overview) — 이 파이프라인의 전체 지도
- [Part 3 정확도와 평가](/03-accuracy-eval/aws-evaluations) — evals-as-gate에 쓰는 평가 서비스
- [Part 9 세밀 권한 제어](/09-authorization/) — 생성물의 권한을 최소로 묶는 3중 방어
