# Platform v4 — 상용화 수준 빌드 플랜 (진행 상태 파일)

목표 10축과 구현 매핑. 각 단계 완료 시 [x] 갱신. 세션 복원 시 이 파일이 앵커.

## 아키텍처 (v4)

```
브라우저 (Cognito JWT, PKCE)
  → CloudFront E3ETCXKSTRXQAT (유일한 퍼블릭 진입점, x-origin-verify 주입)
    → API GW 00l4tzkyqi
        GET /            : 정적 SPA (Lambda가 서빙, 무인증)
        /api/*           : JWT authorizer (Cognito) → Lambda
  → Lambda agentic-book-demo-site (principal = JWT claims)
    → DynamoDB agentic-book-demo-registry (tenant/team-scoped items)
    → AgentCore Harness (실행) / Agent Registry us-east-1 (거버넌스 정본)
    → AgentCore Gateway + Identity (중앙 MCP)
    → S3 (Agent Skills 소스)
```

## 10축 체크리스트

- [x] A1. Cognito User Pool (AdminCreateUserOnly, self sign-up 금지) + 그룹(platform-admins, team-alpha, team-beta) + 데모 계정
- [x] A2. API GW JWT authorizer (/api/*), Lambda principal 추출·소유권·admin RBAC·감사에 principal 기록
- [x] B1. (축2) AgentCore Gateway + Identity — 플랫폼 툴(레지스트리 검색/위키 조회/온톨로지 질의)을 중앙 MCP로 노출, Cognito JWT 인바운드
- [x] B2. (축3) Agent Registry — 이미 통합됨(v3), 팀 소유권 메타 추가
- [x] B3. (축4) Agent Skills — SKILL.md CRUD + S3 소스 → Harness per-invocation skills 연결 + Registry AGENT_SKILLS 등록
- [x] C1. (축5) AI-Ready Data(ontology) — 엔티티 타입/엔티티/관계 모델 + 에디터 + 에이전트 컨텍스트 주입
- [x] C2. (축6) AI Wiki — 마크다운 페이지 CRUD(버전 이력) + 에이전트 조회 툴
- [x] D1. (축7) 커버리지 그래프 뷰 — 팀·에이전트·스킬·데이터소스·툴 관계 인터랙티브 그래프
- [x] D2. (축8) GUI 워크플로우 — Agent Graph/Workflow/Loop DSL + 비주얼 빌더 + 서버 실행 엔진 + 런 이력
- [x] E1. (축9) 디자인 — frontend-design 기준의 SPA 전면 재설계 (정적 자산 Lambda 서빙, /static/* 캐시)
- [x] E2. (축1) 팀 셀프서비스 — 팀(그룹) 소유 에이전트, 팀별 예산/카탈로그 스코프
- [x] F1. (축10) 보안·거버넌스 전략 문서 + 시행 매핑(Cedar 방향 포함) — wiki 시드 + repo 문서
- [x] G1. e2e 검증 스위트(12/12 통과) / [x] G3. README/커밋 (G2 다이어그램 갱신은 후속)

## 배포 절차

lambda: demo/builder-harness/site/ → zip(app + static/) → update-function-code
frontend 정적: static/ 디렉터리를 zip에 포함, /와 /static/* 서빙
검증: demo/builder-harness/site/e2e.sh

## 리소스 ID

- CloudFront: E3ETCXKSTRXQAT (d1twhttjtzqewp.cloudfront.net)
- API GW: 00l4tzkyqi (ap-northeast-2)
- Lambda: agentic-book-demo-site / Role: AgenticBookDemoSiteLambdaRole
- DDB: agentic-book-demo-registry
- Harness: AgenticBookBuilderDemo-6R0pXEwrY1 / Agent Registry: b2hOSZL4eOhDXAyk (us-east-1)
- Cognito: pool ap-northeast-2_h2rhe1TKo / client 3o8u65rhccnr1ug1f94tctmb0b / domain agentic-platform-2rhe1tko / JWT authorizer 9dzkc3
- Gateway: nexus-platform-tools-dgt6g0wppb (MCP, Cognito JWT) / target ET0X4S47UR / tools Lambda nexus-gateway-tools / exec role NexusGatewayExecRole
- Skills S3: agentic-nexus-skills-180294183052 (per-invocation s3 skill source는 SDK 미지원 → 프롬프트 주입, S3+Registry는 게시/카탈로그 계층)

## v5 확장 (금융 도메인 + 크롤러)

- 도메인: 가상 증권사 "한울증권" 직원용 — 증권 용어/시장 제도/HR(휴가·급여·온보딩) 문서, 온톨로지(팀·시스템·규정·시장), 에이전트 6종, 아침 브리핑 워크플로우.
- 뉴스 크롤러: Lambda `agentic-news-crawler` + EventBridge 6h + 운영 탭 "지금 수집". Google News RSS 헤드라인 메타데이터만 수집 → 고정 DS(feed0a01) upsert → 감사 로그.
- 마크다운 렌더러: 표/정렬 목록/코드블록/h4/인용 지원.
- 한글 카피 해요체로 정비.
- 에이전트 생성을 완전 비동기화(EVALUATING → 백그라운드 평가·승인·Registry) — API GW 30초 타임아웃 회피.
- 재현: `python3 demo/builder-harness/seed.py <admin-token> --wipe`
- 크롤러 리소스: Lambda agentic-news-crawler / role AgenticNewsCrawlerRole / rule agentic-news-crawler-6h
