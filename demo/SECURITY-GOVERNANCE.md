# Agentic AI Platform 보안·거버넌스 전략 (v4)

플랫폼에 **시행 중인** 통제와 **residual gap**을 구분해 기록한다. 데모라도 시행되지 않은 통제를 시행 중이라고 쓰지 않는다.

## 1. 신뢰 경계와 신원

| 통제 | 시행 방식 | 검증 |
|---|---|---|
| 퍼블릭 진입점 단일화 | CloudFront만 공개. API GW·Lambda는 `x-origin-verify` 시크릿 헤더 없으면 403 | 직접 호출 403 확인 |
| 사용자 인증 | Cognito User Pool `ap-northeast-2_h2rhe1TKo` — **self sign-up 차단, admin-create only** (CLAUDE.md 정책) | 무토큰 `/api/*` 401 |
| API 인가 | API GW **JWT authorizer**(Cognito issuer/audience) — 앱 코드 진입 전에 차단 | 만료/위조 토큰 401 |
| RBAC | `cognito:groups` → `platform-admins`(운영 탭·승인·예산), `team-*`(팀 소유권) | 팀원 admin API 403 |
| 리소스 소유권 | 에이전트/데이터소스/스킬/워크플로우에 `ownerEmail`+`team` — 소유자·같은 팀·관리자만 변경/삭제 | 타팀 삭제 403 |
| 중앙 MCP 인바운드 | AgentCore Gateway `CUSTOM_JWT`(같은 Cognito) — access token 필요 | 무토큰 MCP 401 |

## 2. 에이전트 거버넌스 (골든 패스)

```
생성 요청 → 스모크 평가(evals-as-gate) → Tier 판정
  Tier 1 + 평가 통과 → 자동 승인          (셀프서비스 골든 패스)
  Tier 2 또는 평가 실패 → PENDING         (플랫폼 엔지니어 검토)
승인/거부 = Agent Registry 상태 변경(사유 필수) → CloudTrail 감사
```

- 거버넌스 **정본은 AgentCore Agent Registry**(us-east-1 `b2hOSZL4eOhDXAyk`, 수동 승인 모드). DynamoDB는 런타임 캐시.
- 감사 이중화: ① Registry 상태 변경 = CloudTrail(주체·시각·사유) ② 플랫폼 감사 테이블(모든 mutating API, principal 포함) — 운영 탭에서 열람.

## 3. 실행 통제

| 통제 | 시행 방식 |
|---|---|
| 툴 노출 차단 | 모든 생성-에이전트 호출에 서버가 `allowedTools` 차단 강제 — 클라이언트 우회 불가 |
| 메모리 격리 | `actorId = agent-{id}-{user}` — 에이전트 간·사용자 간 장기 기억 격리 (Part 7) |
| 예산 서킷 브레이커 | 에이전트별 토큰 예산(기본 50k), 소진 시 429. 증액은 관리자 전용 + 감사 |
| 남용 상한 | Lambda 예약 동시성 5, 에이전트 30/스킬 15/데이터소스 15, 메시지 2,000자, 워크플로우 4단계·4루프 |
| 최소권한 IAM | 역할 4개 분리(사이트 Lambda / Gateway 툴 Lambda(DDB read-only) / Gateway 실행 / Harness 실행) — 전부 리소스 스코프, confused-deputy 조건 포함 |
| 데이터 레지던시 | Harness 모델 = apac geo CRIS 프로파일 고정 (Part 8/12) |

## 4. Residual gaps (정직한 기록)

1. **승인자 권한의 IAM 분리 미완** — 승인은 admin 그룹 JWT로 게이트되지만, Registry API 호출 자체는 사이트 Lambda 단일 역할. 다음 단계: 승인 전용 역할 분리(AssumeRole) 또는 사용자별 자격 위임(OBO).
2. **위험 등급 자가 신고** — Tier를 생성자가 선택. 다음 단계: 프롬프트/툴 요구 기반 자동 분류 + Cedar 정책.
3. **예산 검사 비원자성** — 동시 요청 시 소폭 초과 가능. 다음 단계: DDB 조건부 업데이트 예약.
4. **평가 게이트의 깊이** — 스모크 1문항. 다음 단계: AgentCore Evaluations(trajectory/goal)를 승인 게이트로.
5. **Registry Preview 마이그레이션** — `bedrock-agentcore`→`agent-registry` 네임스페이스 변경 예고. IAM·SDK 추적 필요.
6. **토큰 수명** — SPA가 refresh token 미사용(1시간 후 재로그인). 데모 트레이드오프.

## 5. 운영 계정 (데모)

| 계정 | 그룹 | 용도 |
|---|---|---|
| demo@atomai.click | platform-admins | 공유 데모 계정 (!234Qwer — uiux-studio 포함 전 데모 공통) |
| admin@demo.nexus | platform-admins | 플랫폼 운영(승인·예산·감사) |
| alpha@demo.nexus | team-alpha | 서비스팀 셀프서비스 |
| beta@demo.nexus | team-beta | 서비스팀 셀프서비스 |

가입 버튼은 존재하지 않는다 — 계정은 `admin-create-user`로만 발급한다. (데모 편의로 비밀번호 최소 길이를 8자로 완화 — 프로덕션은 12자+ 권장)

> 참고: 일부 AWS 리소스 ID(`nexus-gateway-tools`, `NexusGateway*` 역할, `agentic-nexus-skills-*` 버킷)는 초기 명명이 남아 있다 — 기능과 무관하며 재생성 비용 때문에 유지.


## 개인 인사정보 스코핑 (v6)

에이전트가 "내 잔여 연차" 같은 개인 질문에 답하되 타인 정보는 차단한다.
- 신원은 **서버가 검증한 JWT의 email 클레임**으로만 판단(클라이언트 입력 불신).
- `usePersonalHr` 플래그 에이전트에 한해, 그 이메일의 `EMP` 레코드 1건만 시스템 프롬프트에 주입.
- `EMP`는 공개 쓰기 API가 없다 — HR 마스터에서 동기화(데모는 `demo/builder-harness/seed_employees.py`).
- 검증: 김데모→본인 5.5일, 이알파→본인 12일, 타인 조회는 거절. 가이드북 Part 9 엔타이틀먼트 스코핑의 시연.