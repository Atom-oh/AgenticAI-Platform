# Agentic AI Platform

하나의 종합 Agentic AI 플랫폼 — 역할별 서피스가 공통 거버넌스(AgentCore Agent Registry ·
Cognito 초대 전용 · CloudFront 단일 진입) 위에서 동작한다. 기술 레퍼런스 북(가이드북)이
모든 설계 결정의 근거를 제공한다.

## 서피스

| 서피스 | 대상 | 라이브 | 소스 |
|---|---|---|---|
| **플랫폼 허브 · 규정 영향 분석(S1)** | 심사·기획 | https://d15n7n9ypt87h8.cloudfront.net/ | `platform/` |
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

## 은행 데모 (SPEC.md — 진행 중)

`SPEC.md`가 정본 요구사항. 진행 상황은 `platform/README.md`.

- Phase 1 완료 — 온톨로지(3,317노드/8,520엣지) · GraphStore · Semantic Layer · 커버리지 테스트
- Phase 2 완료 — S1 규정 영향 분석 라이브 (GraphRAG vs Vector RAG, 스트리밍, 근거 검증)
- Phase 3~5 — 온프렘 플레인 분리(ECS·RDS·Guardrails), Registry S3 반전 시연, 보고서·마무리

## 가이드북 개발

```bash
npm install
npm run docs:dev       # 로컬 개발 서버
npm run docs:build     # 정적 빌드
```

- `docs/00-intro/` ~ `docs/13-appendix/` — Part별 챕터
- 집필 규칙과 목차는 `PROMPT.md` 참고. 모든 챕터는 "이 장에서 얻는 것 → 왜 문제가 되는가
  → 핵심 개념 → 결정 표 → 실패 모드 → 안티패턴 → 계측(SLI) → 체크리스트 → 참고" 골격.
