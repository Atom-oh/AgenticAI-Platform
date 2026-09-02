# Agentic AI 플랫폼 엔지니어링

AWS Bedrock AgentCore 기반 Agentic AI 메타플랫폼을 설계·운영하는 플랫폼 엔지니어를 위한 기술 레퍼런스 북. VitePress로 빌드된다.

## 개발

```bash
npm install
npm run docs:dev       # 로컬 개발 서버
npm run docs:build     # 정적 빌드
npm run docs:preview   # 빌드 결과 프리뷰
```

## 구조

- `docs/00-intro/` ~ `docs/13-appendix/` — Part별 챕터
- `docs/.vitepress/config.ts` — 사이드바·네비게이션·검색 설정

## 샘플 데모 — Agentic AI Platform (v4)

`demo/builder-harness/` — 상용화 수준을 지향한 Agentic AI 플랫폼 데모: https://d1twhttjtzqewp.cloudfront.net/ (Cognito 로그인 필요, 계정은 `demo/SECURITY-GOVERNANCE.md` 참고). 팀 셀프서비스 에이전트, 중앙 MCP(AgentCore Gateway+Identity), Agent Registry 거버넌스, Agent Skills, 온톨로지(AI-Ready Data), AI Wiki, 커버리지 그래프, GUI 워크플로우(Chain/Loop), Cognito RBAC + 감사. 상세는 `demo/PLATFORM-V4-PLAN.md`와 `demo/SECURITY-GOVERNANCE.md`.

## 샘플 데모 — UI/UX Studio (디자이너용)

`demo/uiux-studio/` — 디자이너(비개발자)용 UI/UX Agentic AI 플랫폼: https://d4zwmnh2s47e9.cloudfront.net/ (공유 데모 계정은 `demo/SECURITY-GOVERNANCE.md` 참고). 디자인 자산 8종(토큰·팔레트·아이콘셋·컴포넌트·스타일가이드·스킬·워크플로우·에이전트) 등록/버전 히스토리, AgentCore Gateway 공유 MCP, Bedrock 전 모델 선택, 플레이그라운드(에이전트 기반 생성 + **컴포넌트 클릭 후 프롬프트 수정**), AgentCore Memory 기반 디자이너 취향 학습, 승인 패턴 few-shot 루프. 스펙/플랜은 `demo/uiux-studio/docs/` 참고.

## 이전 데모 문서

`demo/builder-harness/` — Part 11(빌더 에이전트)을 실제 AgentCore Harness로 배포한 데모. 코드 없이 config만으로 배포해 Strands SDK 직접 사용을 피한다. 자세한 내용은 해당 디렉터리의 README 참고.

## 집필 규칙

챕터 작성 규칙과 목차는 `PROMPT.md`(집필 지시 원문)를 참고한다. 모든 챕터는 "이 장에서 얻는 것 → 왜 문제가 되는가 → 핵심 개념 → 결정 표 → 실패 모드 → 안티패턴 → 계측(SLI) → 체크리스트 → 참고" 골격을 따른다.
