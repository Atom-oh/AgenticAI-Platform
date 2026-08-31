---
title: 보안·안전과 한국 금융 규제 개관
description: 프롬프트 인젝션·MCP 공급망·가드레일·한국 금융 규제·하이브리드 아키텍처를 다루는 Part 12의 개관.
outline: [2, 3]
---

# 보안·안전과 한국 금융 규제 개관

::: tip 이 파트가 다루는 것
6대 통증점을 가로지르는 **보안·안전 레이어**와, 한국 금융권 특유의 **규제 제약**을 다룬다. 규제 관련 내용은 법률자문이 아니다.
:::

에이전트 보안의 출발점은 "프롬프트 인젝션은 완전히 막을 수 없다"는 정직한 전제다. 따라서 방어는 탐지가 아니라 **피해를 한정하는 아키텍처**(권한 최소화·egress 제한·HITL·메모리 게이트)에 있다. 여기에 MCP 공급망이라는 새로운 공격면과, 한국 금융권의 망분리 규제 완화라는 움직이는 규제 지형이 겹친다.

## 챕터 요약

| 챕터 | 한 줄 요약 |
|---|---|
| [프롬프트 인젝션](/12-security-korea/prompt-injection) | 직접 vs 간접 인젝션, 막을 수 없다는 전제 위의 5레이어 방어 |
| [MCP 공급망 공격](/12-security-korea/mcp-supply-chain) | tool poisoning·rug-pull·CVE 사례와 스캔·게이트웨이 방어 |
| [가드레일과 PII](/12-security-korea/guardrails-pii) | Bedrock Guardrails 기능 지형과 CloudWatch 평문 로그 함정 |
| [한국 금융 규제](/12-security-korea/korea-fsc-regulation) | 망분리 개선 로드맵, 연구개발망 예외, 분기별 규제 추적 |
| [하이브리드 아키텍처](/12-security-korea/hybrid-architecture) | EKS Hybrid Nodes, PrivateLink, PII 토큰화 후 라우팅 |

## 결정 요약

- 인젝션 방어를 탐지 필터에만 의존하지 않고 권한·egress·HITL 레이어로 설계했는가
- 신규 MCP 서버 온보딩에 공급망 스캔 파이프라인이 있는가
- Guardrails PII 마스킹의 로그 평문 잔존을 KMS·IAM·보존정책으로 보완했는가
- 규제 대상이라면 CRIS 지리 프로파일 고정·PII 토큰화·하이브리드 경로를 검토했는가
- 규제 변화를 분기별로 추적하는 운영 항목이 있는가

## 관련 다른 파트

- [Part 9 세밀 권한 제어](/09-authorization/) — 인젝션 피해를 한정하는 권한 레이어의 정본
- [Part 7 메모리 아키텍처](/07-memory/memory-security-privacy) — 메모리 포이즈닝과 잊힐 권리
- [Part 8 스케일링과 비용](/08-scaling-cost/bedrock-inference-tiers) — CRIS 데이터 레지던시
