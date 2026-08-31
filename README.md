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

## 샘플 데모

`demo/builder-harness/` — Part 11(빌더 에이전트)을 실제 AgentCore Harness로 배포한 데모. 코드 없이 config만으로 배포해 Strands SDK 직접 사용을 피한다. 자세한 내용은 해당 디렉터리의 README 참고.

## 집필 규칙

챕터 작성 규칙과 목차는 `PROMPT.md`(집필 지시 원문)를 참고한다. 모든 챕터는 "이 장에서 얻는 것 → 왜 문제가 되는가 → 핵심 개념 → 결정 표 → 실패 모드 → 안티패턴 → 계측(SLI) → 체크리스트 → 참고" 골격을 따른다.
