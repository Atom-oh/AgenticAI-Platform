---
title: 용어집
description: 이 책 전체에서 사용하는 핵심 용어를 알파벳·가나다 순으로 정리하고 각 용어의 정본 챕터로 연결한다.
outline: [2, 3]
---

# 용어집

이 책에서 반복적으로 쓰이는 용어를 모았다. 각 항목은 짧은 정의와 함께 해당 개념을 가장 깊게 다루는 **정본 챕터** 링크를 제공한다. 영문 용어는 알파벳순, 한국어 용어는 마지막에 가나다순으로 정렬했다.

## A

**actorId 네임스페이스** — AgentCore Memory에서 메모리를 `/strategy/{memoryStrategyId}/actor/{actorId}/...` 형태의 계층 경로로 격리하는 스코핑 메커니즘. `actor_id: "tenant-a:user-123"` 같은 합성 키와 IAM condition key를 결합하면 테넌트+사용자 이중 격리를 강제할 수 있다. → [메모리 검색과 스코핑](/07-memory/memory-retrieval-scoping)

## C

**CAG (Cache-Augmented Generation)** — 코퍼스가 컨텍스트 윈도우에 들어갈 만큼 작으면(약 200K 토큰 미만) 실시간 검색 대신 문서 전체를 미리 컨텍스트에 적재하고 프롬프트 캐싱으로 재사용하는 패턴. 검색 지연과 검색 오류를 동시에 제거하지만, 코퍼스가 자주 갱신되거나 윈도우 한계에 다가가면 경제성과 정확도가 무너진다. → [GraphRAG·agentic RAG, 그리고 RAG를 쓰지 말아야 할 때](/06-vector-search/graphrag-agentic-when-not)

**Cedar** — AWS가 만든 오픈소스 인가 정책 언어. AgentCore Policy는 Gateway 툴 호출 경계에서 Cedar 정책을 집행하고, Amazon Verified Permissions는 같은 언어를 범용 애플리케이션 인가에 쓴다. → [Cedar와 Verified Permissions](/09-authorization/cedar-verified-permissions)

**compaction** — 컨텍스트 윈도우 한계에 다가갈 때 오래된 대화 턴을 요약으로 압축해 컨텍스트를 다이어트하는 패턴. 되돌릴 수 없는 사실은 남기고 중간 추론 과정은 버리는 것이 요약 설계의 핵심이며, 컨텍스트 엔지니어링에서 첫 번째로 시도할 레버다. → [Compaction과 요약](/05-context/compaction-summarization)

**confused deputy** — 권한을 가진 대리인(deputy)이 "누구의 권한으로 이 요청을 수행하는가"라는 두 권한 소스를 구분하지 못해, 낮은 권한의 요청자가 대리인의 높은 권한을 악용하게 되는 고전적 보안 문제(Norm Hardy, 1988). 에이전트가 여러 사용자를 하나의 서비스 신원으로 대신하는 구조에서는 구조적으로 재발한다. → [Confused deputy 문제](/09-authorization/confused-deputy)

**context rot** — 컨텍스트 길이가 늘어날수록 LLM 성능이 비선형적으로 저하되는 현상. 공식 스펙상 윈도우(advertised)와 실제 신뢰 가능한 길이(effective)의 간극은 태스크·모델 쌍마다 다르므로, 남의 수치를 설계 근거로 가져다 쓰지 말고 자체 측정해야 한다. → [Context rot](/05-context/context-rot)

**CRIS (Cross-Region Inference)** — Bedrock 추론 요청을 여러 리전의 용량 풀로 분산 라우팅하는 메커니즘. 모델 ID 대신 inference profile ID를 지정하면 활성화되고 라우팅 자체에 추가 과금은 없지만, Global 프로파일은 처리 리전을 지정할 수 없어 데이터 레지던시 요건과 충돌할 수 있다. → [Bedrock 추론 티어](/08-scaling-cost/bedrock-inference-tiers)

## D

**DiskANN** — 벡터 인덱스를 SSD에 두고 메모리에는 압축 표현만 유지하는 디스크 기반 ANN 인덱스. 메모리 예산 대비 코퍼스가 매우 클 때 HNSW의 대안이 된다. → [ANN 인덱스와 양자화](/06-vector-search/ann-indexes-quantization)

## E

**error compounding (p^n)** — 단계당 성공률이 p인 n단계 파이프라인의 완주 확률이 p^n으로 기하급수적으로 떨어지는 현상. 에이전트 파이프라인이 길어질수록 체크포인트·durable execution이 필요해지는 수치적 근거다. → [평가 하니스](/03-accuracy-eval/eval-harness)

**evals-as-gate** — 평가(evals)를 사람 리뷰의 보조가 아니라 CI/CD 파이프라인의 **프로모션 게이트**로 편입하는 패턴. 요구사항의 성공 기준을 assertions/expectedTrajectory로 변환해 배치 평가를 통과해야만 다음 스테이지로 승격시킨다. → [에이전트 CI/CD](/11-builder-agent/agent-cicd)

## H

**HITL (human-in-the-loop)** — 되돌리기 어렵거나 폭발 반경이 큰 에이전트 액션에 사람의 승인을 강제하는 패턴. 액션의 reversibility·blast radius·금액 임계치로 위험 등급을 나누고 등급별 승인 메커니즘을 매핑한다. → [HITL과 감사](/09-authorization/hitl-audit)

**HNSW (Hierarchical Navigable Small World)** — 계층적 그래프를 탐색해 근사 최근접 이웃을 찾는 메모리 상주형 ANN 인덱스. 대부분의 벡터 스토어 기본값이며, 튜닝은 `ef_search`부터 잡는 것이 원칙이다. → [ANN 인덱스와 양자화](/06-vector-search/ann-indexes-quantization)

## K

**KV cache** — LLM 서빙에서 이미 처리한 토큰의 key/value 텐서를 GPU 메모리에 저장해 재계산을 피하는 캐시. 이 캐시의 메모리 관리 효율이 자체 호스팅 서빙의 처리량 상한을 좌우한다. → [vLLM KV 캐시](/04-caching/vllm-kv-cache)

## L

**LLM-as-a-judge** — 사람 대신 LLM으로 에이전트 출력·궤적을 채점하는 평가 방식. 규모 확장이 가능하지만 판정자 자체의 편향·비일관성을 캘리브레이션해야 신뢰할 수 있다. → [LLM 판정자와 trajectory 평가](/03-accuracy-eval/llm-judge-trajectory)

## M

**MCP (Model Context Protocol)** — 에이전트와 툴·데이터 소스를 연결하는 개방형 프로토콜. 이 책은 MCP 서버를 "툴 모음"이 아니라 스키마 안정성·툴 개수 절제·응답 상한·컨텍스트 통과 경로 통제를 갖춘 플랫폼 컴포넌트로 설계할 것을 요구한다. → [MCP 서버 설계](/01-agent-design/mcp-server-design)

**microVM** — AgentCore Runtime이 세션마다 새로 띄우는 하드웨어 수준 격리 단위. 세션 간 메모리·파일시스템이 공유되지 않는 강한 격리를 제공하며, `maxLifetime` 8시간 한계가 있다. → [Runtime 심화](/10-agentcore/runtime-deep-dive)

**MRL (Matryoshka Representation Learning)** — 임베딩 벡터의 앞쪽 차원에 정보를 집중시키도록 학습해, 하나의 모델에서 벡터를 잘라 저차원으로 써도 성능 저하를 최소화하는 기법. 저장 비용과 recall의 트레이드오프를 차원 수로 조절할 수 있게 한다. → [임베딩 모델 선택](/06-vector-search/embedding-model-choice)

## O

**OBO (On-Behalf-Of)** — 에이전트가 다운스트림 툴을 호출할 때 사용자 신원(subject)과 에이전트 신원(actor)을 RFC 8693 토큰교환으로 함께 전파하는 패턴. "서비스 신원으로 실행"과 "사용자 토큰 그대로 전달(패스스루)"이라는 두 실패 축 사이의 정답 위치다. → [툴별 On-Behalf-Of](/09-authorization/per-tool-obo)

## P

**PagedAttention** — KV cache를 OS 가상 메모리의 페이지처럼 고정 크기 블록으로 나눠 관리해 메모리 프래그멘테이션을 해결하는 vLLM의 핵심 메커니즘. 같은 GPU에서 동시에 서빙 가능한 시퀀스 수, 즉 처리량 상한을 끌어올린다. → [vLLM KV 캐시](/04-caching/vllm-kv-cache)

**pass^k** — 같은 과제를 독립적으로 k번 시도했을 때 k번 **모두** 성공할 확률의 과제 평균(tau-bench). "한 번이라도 성공"을 재는 pass@k와 방향이 정반대로, 에이전트의 일관성·신뢰성을 포착한다. → [평가 하니스](/03-accuracy-eval/eval-harness)

**PDP (Policy Decision Point)** — 인가 결정을 애플리케이션 코드 밖으로 외부화해 정책 엔진이 허용/거부를 판정하게 하는 구성 요소. Amazon Verified Permissions가 Cedar 기반의 범용 외부화 PDP다. → [Cedar와 Verified Permissions](/09-authorization/cedar-verified-permissions)

**prefix caching (prompt caching)** — 프롬프트의 앞부분(prefix)이 정확히 일치할 때 해당 구간의 연산을 재사용하는 캐싱. 캐시 키는 `tools` → `system` → `messages` 순서의 exact prefix match로 파생되며, cache read는 0.1x 과금으로 지연과 비용을 동시에 줄인다. → [프롬프트 캐싱 기초](/04-caching/prompt-caching-basics)

**progressive disclosure** — Agent Skills의 3단계 로딩 모델: 시작 시에는 metadata만 상주시키고, 트리거 시 SKILL.md 본문을, 필요할 때만 참조 파일을 로드해 컨텍스트 토큰을 통제하는 설계 원칙. → [에이전트 스킬](/11-builder-agent/agent-skills)

## R

**RRF (Reciprocal Rank Fusion)** — dense와 sparse 검색의 결과를 점수가 아니라 **순위**만으로 결합하는 하이브리드 검색 융합 기법. 점수 스케일 정규화가 필요 없어 가중 선형 결합보다 튜닝 부담이 적다. → [하이브리드 검색과 리랭킹](/06-vector-search/hybrid-search-rerank)

**rug-pull** — 온보딩 심사 시점에는 정상이던 MCP 툴 정의를 서버 운영자가 사후에 악성으로 바꿔치기하는 공급망 공격. 툴 정의 핀닝과 변경 감지가 직접적 방어다. → [MCP 공급망 공격](/12-security-korea/mcp-supply-chain)

## S

**semantic tool search** — 전체 툴 목록을 프롬프트에 선적재하는 대신, 쿼리 시점에 의미 검색으로 관련 툴만 골라 노출하는 기법. AgentCore Gateway는 `x_amz_bedrock_agentcore_search` 툴로 이를 내장 제공하며, tool overload로 인한 선택 정확도 저하를 구조적으로 완화한다. → [Gateway 심화](/10-agentcore/gateway-deep-dive)

## T

**tool overload** — LLM에 노출되는 툴 개수가 늘어날수록 올바른 툴을 고를 확률이 떨어지는 구조적 문제. 컨텍스트 점유(prompt bloat)와 결정 복잡도라는 두 메커니즘이 원인이며, 이 책은 툴 50개 전에 대응하는 것을 운영 임계치로 삼는다. → [툴 과부하](/03-accuracy-eval/tool-overload)

**tool poisoning** — MCP 툴의 description 등 메타데이터에 악성 지시를 심어, 코드가 아니라 **에이전트의 컨텍스트**를 공격하는 공급망 공격. 정적 스캔·온보딩 파이프라인·게이트웨이 격리로 계층 방어한다. → [MCP 공급망 공격](/12-security-korea/mcp-supply-chain)

**TPM burndown** — Bedrock 온디맨드 쿼터에서 요청 시작 시 `총 입력 토큰 + max_tokens`가 TPM 쿼터에서 선차감되는 예약 모델. max_tokens를 넉넉히 잡는 습관이 "요청이 많지 않은데 ThrottlingException"의 가장 흔한 원인이다. → [동시성 쿼터와 스로틀링](/08-scaling-cost/concurrency-quotas-throttling)

**trajectory 평가** — 최종 답만이 아니라 에이전트가 답에 도달한 툴 호출 경로(궤적) 전체를 채점하는 평가. 우연히 맞은 실행과 경로는 옳았지만 외부 요인으로 실패한 실행을 구분해야 개선 루프가 돈다. → [LLM 판정자와 trajectory 평가](/03-accuracy-eval/llm-judge-trajectory)

**TTFT (Time To First Token)** — 요청 후 첫 토큰이 나오기까지의 지연. 입력 길이에 비례하는 prefill 단계가 지배하므로, 컨텍스트 다이어트와 프롬프트 캐싱이 곧 TTFT 최적화 수단이 된다. → [지연의 해부](/02-performance/latency-anatomy)

## 한국어 용어

**골든 패스 (golden path)** — 플랫폼이 미리 닦아둔 "지원되는 안전한 기본 경로". 전통 IDP에서는 배포 파이프라인이지만, Agentic AI 플랫폼에서는 승인된 블루프린트·카탈로그를 통한 에이전트 셀프서비스 생성이 골든 패스다. → [AI를 위한 플랫폼 엔지니어링](/00-intro/platform-engineering-for-ai)

**망분리** — 내부 업무망과 인터넷 등 외부망의 분리·차단을 요구해 온 한국 금융권 규제(전자금융감독규정 제15조). 2024-08-13 「금융분야 망분리 개선 로드맵」 이후 생성형 AI 활용을 허용하는 방향으로 단계적으로 완화되고 있다. → [한국 금융권 규제](/12-security-korea/korea-fsc-regulation)

**메모리 포이즈닝** — 공격자가 조작한 내용을 에이전트의 장기 메모리에 심어, 1회성 프롬프트 인젝션을 세션을 넘어 지속되는 **지속성 공격**으로 승격시키는 위협. 메모리 승격 게이트, 출처 추적, 신뢰 등급별 네임스페이스 격리로 방어한다. → [메모리 보안과 프라이버시](/07-memory/memory-security-privacy)

**빌더 에이전트** — 사용자와 요구사항을 대화로 구체화한 뒤 또 다른 에이전트를 생성·조립·배포하는 에이전트. 이 책이 다루는 메타플랫폼의 1번 시나리오다. → [에이전트를 만드는 에이전트 개관](/11-builder-agent/)

**시맨틱 캐싱** — 프리픽스 정확 일치 대신 임베딩 유사도로 "비슷한 질문"의 캐시된 답을 재사용하는 캐싱. 히트율은 높아지지만 오답 재사용(wrong-answer reuse) 위험이 새로 생기므로 검증 레이어가 필수다. → [시맨틱 캐싱 vs 정확 캐싱](/04-caching/semantic-vs-exact-caching)

**에이전트 스킬 (Agent Skills)** — 절차 지식과 스크립트를 SKILL.md 중심의 버전 관리 가능한 폴더로 패키징하는 오픈 스탠다드. progressive disclosure로 토큰 비용을 통제하며, 클라이언트 간에 같은 아티팩트를 재사용할 수 있다. → [에이전트 스킬](/11-builder-agent/agent-skills)
