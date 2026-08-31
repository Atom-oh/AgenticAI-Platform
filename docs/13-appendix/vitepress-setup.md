---
title: VitePress 설정
description: 이 책 자체를 빌드하는 VitePress 설정의 골격과 선택 이유를 설명한다.
outline: [2, 3]
---

# VitePress 설정

::: tip 이 장에서 얻는 것
- 이 책과 같은 대형 기술 문서 사이트를 VitePress로 구성하는 실제 설정 골격
- Mermaid 다이어그램·로컬 검색·사이드바 구조의 선택 이유
:::

## 왜 문제가 되는가

대형 기술 레퍼런스는 챕터 수가 수십 개를 넘고, 다이어그램·검색·교차 링크가 본문만큼 중요하다. 문서 도구를 잘못 고르거나 초기 구조를 잘못 잡으면 나중에 사이드바·링크 체계를 갈아엎는 비용이 커진다.

## 핵심 개념

### 실제 config.ts 골격

이 리포지토리의 `docs/.vitepress/config.ts`가 정본이다. 핵심 골격은 다음과 같다:

```ts
import { defineConfig } from 'vitepress'
import { withMermaid } from 'vitepress-plugin-mermaid'

export default withMermaid(defineConfig({
  lang: 'ko-KR',
  title: 'Agentic AI 플랫폼 엔지니어링',
  description: 'AgentCore 기반 메타플랫폼 설계·운영 레퍼런스',
  themeConfig: {
    nav: [
      { text: '시작', link: '/00-intro/' },
      { text: 'AgentCore', link: '/10-agentcore/runtime-deep-dive' },
      { text: '보안·규제', link: '/12-security-korea/korea-fsc-regulation' },
    ],
    sidebar: [
      // 파트별로 { text, collapsed: true, items: [...] }
    ],
    search: { provider: 'local' },   // minisearch
    outline: { level: [2, 3] },
    docFooter: { prev: '이전', next: '다음' },
  },
  markdown: { lineNumbers: true },
  mermaid: {},
}))
```

### 선택 이유

| 선택 | 이유 |
|---|---|
| `vitepress-plugin-mermaid` + `mermaid` | 아키텍처·시퀀스·정책 흐름도를 코드 블록으로 버전 관리. 무거운 도해는 `docs/public/`의 SVG로 분리 |
| `search: { provider: 'local' }` | 기본 로컬 minisearch — 별도 인프라 없이 동작. **콘텐츠가 수십만 단어를 넘으면 Algolia DocSearch로 승격**을 검토한다(로컬 인덱스가 커지면 초기 로드가 무거워짐) |
| 파트별 `collapsed: true` | 13개 파트 × 5~9챕터 구조에서 사이드바 전체 펼침은 탐색성을 해침 |
| `outline: { level: [2, 3] }` | 챕터 골격(## 섹션 + ### 하위)과 일치하는 우측 목차 |
| `markdown: { lineNumbers: true }` | 코드 스니펫 인용 시 라인 참조 가능 |

### 디렉터리·빌드

```bash
npm install
npm run docs:dev       # 로컬 개발 서버
npm run docs:build     # 정적 빌드 (docs/.vitepress/dist)
npm run docs:preview   # 빌드 결과 프리뷰
```

- 파일명·디렉터리는 **ASCII만** 사용한다. 한글 파일명은 다운로드·빌드 실패의 원인이 된다.
- 각 파트 폴더에 `index.md`(파트 개관)를 두고, 사이드바의 파트 링크가 이를 가리킨다.

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 빌드가 YAML 파싱 오류로 실패 | frontmatter의 `description`에 따옴표 등 YAML 특수문자가 이스케이프 없이 들어감 | 에러 메시지의 파일 경로·라인 확인 | 특수문자 제거 또는 전체를 따옴표로 감싸고 내부 이스케이프 |
| 사이드바 링크가 404 | config.ts의 링크와 실제 파일 경로 불일치 | `docs:build`가 dead link를 기본으로 오류 처리 | 스텁 파일을 먼저 전부 만들고 본문을 채우는 순서로 작업 |
| Mermaid 다이어그램이 렌더링 안 됨 | 플러그인 미적용 또는 문법 오류 | 개발 서버에서 해당 페이지 콘솔 확인 | `withMermaid` 래핑 확인, mermaid live editor로 문법 검증 |
| 특정 코드 언어 하이라이팅 경고(예: cedar) | Shiki에 해당 언어 문법 미탑재 | 빌드 로그의 "language not loaded" 경고 | 무해(txt로 폴백) — 필요 시 커스텀 문법 등록 |

## 안티패턴

- ❌ 본문을 먼저 쓰고 사이드바를 나중에 구성 → ✅ 목차 확정 후 전체 스텁 생성 → 빌드 통과 확인 → 본문 채움(링크 깨짐 방지).
- ❌ 다이어그램을 이미지 파일로만 관리 → ✅ Mermaid 코드 블록으로 버전 관리, 무거운 것만 SVG.

## 체크리스트

- [ ] 파일명·디렉터리가 전부 ASCII다
- [ ] 모든 사이드바 링크가 실제 파일과 일치한다(빌드 통과로 검증)
- [ ] frontmatter description에 YAML 특수문자가 없다
- [ ] 검색 승격 조건(수십만 단어)을 모니터링하고 있다

## 참고

- [VitePress 공식 문서](https://vitepress.dev/) — 공식
- [vitepress-plugin-mermaid](https://emersonbottero.github.io/vitepress-plugin-mermaid/) — 비공식(커뮤니티 플러그인)
- [VitePress 컨벤션](/13-appendix/vitepress-conventions) — 이 책의 집필 규칙
