---
title: AgentCore 심화 개관
description: AWS Bedrock AgentCore 각 구성요소(Runtime/Gateway/Identity/Tools/Observability/쿼터)를 다루는 Part 10의 개관.
outline: [2, 3]
---

# AgentCore 심화 개관

::: tip 이 파트가 다루는 것
6대 통증점 자체를 전담하지는 않지만, 여러 통증점(1번 지연, 5번 스케일링·비용, 6번 권한)의 해법이 실제로 어느 AgentCore 구성요소에 어떻게 배선되는지를 다룬다.
:::

Part 9가 "무엇을 통제해야 하는가"를 다뤘다면, 이 파트는 "AgentCore의 어느 컴포넌트가 그것을 실제로 집행하는가"를 다룬다. Runtime(배포 계약), Gateway(툴 변환·semantic search), Identity(인증 지형), Tools(Code Interpreter/Browser), Observability(계측), 쿼터·가격까지 — 이 책의 빌더 에이전트 데모(`demo/builder-harness/`)도 이 구성요소들 위에서 동작한다.

## 챕터 요약

| 챕터 | 한 줄 요약 |
|---|---|
| [Runtime 심화](/10-agentcore/runtime-deep-dive) | microVM 격리, Runtime vs Harness, 프레임워크 어댑터 작성법 |
| [Runtime 배포 계약](/10-agentcore/runtime-deploy-contract) | 배포 7단계, 보안 설정, 업데이트·롤백 전략 |
| [Gateway 심화](/10-agentcore/gateway-deep-dive) | OpenAPI/Lambda/MCP 서버를 툴로 변환, semantic tool search |
| [Identity 심화](/10-agentcore/identity-deep-dive) | 인바운드(SigV4/JWT)·아웃바운드(2LO/3LO/OBO/passthrough) 인증 지형 |
| [Tools 심화](/10-agentcore/tools-deep-dive) | Code Interpreter·Browser의 샌드박스 격리 모델 |
| [Observability 심화](/10-agentcore/observability-deep-dive) | ADOT `gen_ai.*` 스팬, CloudWatch Transaction Search 함정 |
| [쿼터와 가격](/10-agentcore/quotas-pricing) | vCPU-second/GB-second 과금, I/O 대기 시 과금 중단, 서비스별 쿼터 |

## 결정 요약

- Runtime(코드 작성)과 Harness(config 기반) 중 어느 것으로 시작할 것인가
- 인바운드 인증을 SigV4로 할지 JWT로 할지 — 한 런타임에 하나만 선택 가능하다는 제약을 반영했는가
- 툴이 50개를 넘기기 전에 Gateway semantic tool search를 도입할 계획이 있는가
- 관측(ADOT + Transaction Search)을 day 1에 켰는가

## 관련 다른 파트

- [Part 9 세밀 권한 제어](/09-authorization/) — Cedar·OBO·엔타이틀먼트가 Gateway/Identity 위에서 어떻게 집행되는지
- [Part 8 스케일링과 비용](/08-scaling-cost/) — 콜드스타트·세션 유휴 비용의 일반 논의
- `demo/builder-harness/README.md`(리포지토리 루트) — 이 책이 Part 11 빌더 에이전트 개념을 실제 AgentCore Harness로 배포한 데모
