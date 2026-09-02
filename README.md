# Agentic AI Platform

하나의 종합 Agentic AI 플랫폼 — 역할별 서피스가 공통 거버넌스(AgentCore Agent Registry ·
Cognito 초대 전용 · CloudFront 단일 진입) 위에서 동작한다. 기술 레퍼런스 북(가이드북)이
모든 설계 결정의 근거를 제공한다.

## 서피스

| 서피스 | 대상 | 라이브 | 소스 |
|---|---|---|---|
| **은행 Agentic AI 플랫폼 (S1~S5 · F7 · Portal · 에이전트 빌더)** | 전 직군 | https://agent.atomai.click/ (구버전 예비: d15n7n9ypt87h8) | `platform/` |
| **UI/UX 스튜디오** | 디자이너 | https://d4zwmnh2s47e9.cloudfront.net/ | `demo/uiux-studio/` |
| **에이전트 컨트롤룸** | 플랫폼 엔지니어·현업 | https://d1twhttjtzqewp.cloudfront.net/ | `demo/builder-harness/` |
| **가이드북** (93챕터) | 전 직군 | https://www.atomai.click/AgenticAI-Platform/ | `docs/` |

계정: 공유 데모 계정은 `demo/SECURITY-GOVERNANCE.md` 참고. 전 서피스 가입 버튼 없음 —
admin-create 초대 전용.

## 통합 구조 (2026-09-02 재편)

- **거버넌스 정본 단일화**: 전 서피스가 AgentCore Agent Registry(us-east-1)에
  `surface_*` 레코드로 등록·승인되어 있고, 상태 변경이 CloudTrail에 감사 기록된다.
- **상호 내비게이션**: 허브 ↔ 스튜디오 ↔ 컨트롤룸 ↔ 가이드북이 서로 링크된다.
- **온톨로지 편입**: 컨트롤룸 커버리지 그래프에 서피스 간 관계가 존재한다
  (위키 `platform-reorg` 문서 참조).

## 은행 데모 (SPEC.md)

`SPEC.md`가 정본 요구사항. **실제 배포 상태·운영 절차·구현 상태 표는 `platform/README.md`** — 코드와 배포로
확인된 것만 "완료"로 적는다.

- 구조: 두 CDK 스택 — `BankPlatform`(클라우드 플레인: CloudFront·WebSocket API·엔진·Registry·Guardrails IaC)
  + `BankPlatformPlane`(격리 VPC: ECS 온프렘 서비스·RDS·Neptune·브리지·Writer, NAT 없음)
- 시나리오 S1~S5 + F7(Reader/Writer)이 한 SPA(레일 내비)에 있고, 관리 작업(Neptune 적재·시드·리셋)은 IAM 전용 AdminFn
- 데모 계정 비밀번호는 Secrets Manager `bank-platform/demo-user` — 문서·코드에 기록하지 않는다 (SPEC §9)

## 가이드북 개발

```bash
npm install
npm run docs:dev       # 로컬 개발 서버
npm run docs:build     # 정적 빌드
```

- `docs/00-intro/` ~ `docs/13-appendix/` — Part별 챕터
- 집필 규칙과 목차는 `PROMPT.md` 참고. 모든 챕터는 "이 장에서 얻는 것 → 왜 문제가 되는가
  → 핵심 개념 → 결정 표 → 실패 모드 → 안티패턴 → 계측(SLI) → 체크리스트 → 참고" 골격.
