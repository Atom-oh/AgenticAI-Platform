# 라이브 데모 — 아톰은행 Agentic AI Platform

이 책의 개념들이 실제로 동작하는 **단일 페이지 플랫폼**이다. 은행(가상 "아톰은행") 도메인에서
GraphRAG, 마이데이터 상담 파이프라인, Registry 거버넌스, 경계 계측, Guardrails를 한 앱에서 시연한다.

## 접속 정보

| 항목 | 값 |
|---|---|
| **데모 URL** | <https://agent.atomai.click/> |
| **접속 계정** | ID `demo@atomai.click` / 비밀번호 `!234Qwer` — 데모 전용 계정(합성데이터만 접근). 원본은 Secrets Manager `bank-platform/demo-user` |
| 가입 | 불가 — 계정은 관리자 초대(admin-create)로만 발급 |

> 전부 **합성데이터**다. 실제 고객 데이터·실제 상품명·실제 내규 조항을 사용하지 않는다.

## 15분 시연 순서

| 순서 | 화면 (좌측 레일) | 보여주는 것 | 시간 |
|---|---|---|---|
| S1 | 규정 영향 분석 | 동일 질문을 Vector RAG / GraphRAG에 동시에 — 벡터는 청크만, 그래프는 영향 상품 12·화면 39·부서 7·문서 7건을 노드 ID·순회 경로와 함께 | 4분 |
| S2 | 마이데이터 상담 | 숫자는 LLM이 만들지 않는다 — 정확 조회 → 결정론적 계산엔진(수식) → 마스킹 → 설명 생성 → 수치 검증 | 4분 |
| S3 | Agent Registry | 전 서피스 자산의 승인 수명주기 (AgentCore Agent Registry 실물, CloudTrail 감사) | 3분 |
| S4 | Single Boundary 뷰 | 요청별 실측 — VPC 잔류 항목·경계 통과 토큰·사용 모델 ID·차단 여부 (§8-3) | 2분 |
| S5 | Guardrails 로그 | "어떤 상품이 제일 돈 많이 벌어요?" → 실물 Bedrock Guardrails가 차단 | 2분 |

시연 프리셋 버튼이 각 화면에 있다 — 질문을 타이핑할 필요가 없다.

## 함께 통합된 서피스

- **디자인 스튜디오** (레일 메뉴): UI/UX 스튜디오를 iframe이 아니라 **소스 기반 네이티브 통합** —
  갤러리 시안 승인/반려(AgentCore Memory few-shot 학습), 디자인 자산 등록·버전 이력, 생성 잡.
  로그인 한 번으로 두 Cognito 풀에 인증되어 사용자 신원이 스튜디오 감사에도 그대로 남는다.
- **에이전트** (레일 메뉴): 컨트롤룸의 카탈로그·채팅을 사용자 JWT 프록시로 —
  예산 서킷브레이커·감사·RBAC는 컨트롤룸 백엔드가 그대로 시행한다.
- 자매 데모: [에이전트 컨트롤룸](https://d1twhttjtzqewp.cloudfront.net/) (한울증권 도메인 — 팀 셀프서비스·개인 HR 스코핑),
  [UI/UX 스튜디오 원본](https://d4zwmnh2s47e9.cloudfront.net/). 같은 계정으로 로그인.

## 설계 문서

- [아키텍처와 설계 결정](./architecture) — 구성도, SPEC 매핑, 정직한 미완 사항
- [플랫폼 아키텍처 인터랙티브 다이어그램](https://www.atomai.click/AgenticAI-Platform/platform-architecture.html) · [S2 워크플로우](https://www.atomai.click/AgenticAI-Platform/s2-workflow.html)
- 요구사항 명세 원문: 저장소 루트 `SPEC.md`
- 보안·거버넌스: 저장소 `demo/SECURITY-GOVERNANCE.md`
