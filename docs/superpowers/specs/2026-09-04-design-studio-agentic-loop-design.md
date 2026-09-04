# Design Studio — 상품 명세 기반 에이전틱 루프 (설계)

- 날짜: 2026-09-04
- 대상: `platform/` (아톰은행 데모) 디자인 스튜디오
- 근거: ttobak 미팅 "9월 3일 하나은행 UX AI Platform" (cebb65cb-b712-48f9-8140-0da1949ff642), 원본 스튜디오 `demo/uiux-studio/`, 현 포팅판 `platform/web/src/Studio.tsx`
- 상태: 사용자 승인 (2026-09-04) — 다음 단계는 구현 계획

## 0. 배경과 문제

원본 스튜디오(`demo/uiux-studio/gallery/index.html`)는 갤러리·플레이그라운드·디자인 자산·생성 4탭에
라이브 캔버스, 요소 클릭 선택→refine, 출력 유형(design/mockup/wireframe/ux-flow), 에이전트 프리셋,
잡 이력, 자산 타입 필터·버전 타임라인을 갖고 있었다. 플랫폼 포팅판(`platform/web/src/Studio.tsx`,
`api/handlers/core.py`의 `studio_*` 프록시)은 원본 API를 그대로 프록시하는 시각적 리스킨이라 위 기능
대부분이 빠졌고, 출력 유형은 `design`으로 고정돼 있다.

생성 방식도 두 곳 모두 1회성이다. 하네스(`demo/uiux-studio/harness/app.py`)는 HTML 3안을 한 번 내고
끝나며, 품질 루프는 사람 승인/반려 + 승인본 few-shot 주입만 있다. 상품 명세·체크리스트·UX 흐름 검수
단계는 어디에도 없다.

미팅에서 확인된 요구:

- UX 부서장: "AI가 그럴듯해 보여도 검증하면 다 삐뚤어져 있다… 만들어진 걸 다시 고쳐야 하면 성과와 정반대"
  → 검증 자동화가 성과 조건. "상품 설명서만으로도 정합성 높은 화면"이 목표.
- 축구 [34:27]: "적금 금리를 축구클럽 가입자에게 더 주는 상품이면 프로세스에 축구클럽 입력 페이지가 하나 더
  생겨야 한다" → 우대/자격 조건이 있으면 플로우에 조건 입력·검증 스텝이 추가돼야 하고, 없으면 추가되지 않아야 한다.
- AWS SA: "검증 루프가 없다 — 리뷰 에이전트·테스트 에이전트가 따로 돌고 마스터에 회신".
- UX 실무: SM 모델(몰큘=인텐트, 오가니즘=구성·룰·관계, 템플릿=구조·위치) 메타데이터가 있어야 생성이 된다.
- "하나만 얹혀줘 했는데 위치가 다 흔들린다" → 수정 시 범위 밖 변동은 실패로 봐야 한다.

## 1. 범위

플랫폼 네이티브 디자인 스튜디오로 재구현한다. 원본 스튜디오의 기능을 전부 담고, 상품 명세 기반
에이전틱 루프를 추가한다. 모든 생성은 **DesignSpec 작성 → HTML 시안 생성 → 체크리스트 검수 → 수정
재생성**을 점수 기준으로 반복한다. 모델 호출은 전부 익명화 게이트(`engine.bedrock`)를 지난다(SPEC §3).

사용자 결정 사항:

| 결정 | 선택 |
|---|---|
| 명세 출처 | 온톨로지 Product 노드 확장 (문서 업로드 없음) |
| 루프 정지 | 점수 기반 자동 반복. 라운드 상한 기본 3, 1~20 조정 가능 |
| 실행 위치 | 플랫폼 워커 Lambda (AgentCore Runtime·브라우저 주도 아님) |
| 생성물 | HTML 시안 유지 + 구조·문구 검수. React 변환은 기존 `screengen`으로 연결 |
| 저장 | 시안·잡·라운드·승인 = 플랫폼 소유(DynamoDB + 웹 버킷). 자산·모델 목록·자산 등록 = uiux-studio 프록시 유지 |

범위 밖: React 코드 생성, 픽셀 비교, GitLab 푸시, 문서 업로드 기반 명세, 3안 병렬 생성(축은 1개 선택).

## 2. DesignSpec — 온톨로지에서 체크리스트 만들기

모듈 `platform/studio/spec.py`. 입력: `productCode`, `outputType`, `brief`. `graph.store.GraphStore`만 사용
(Local/Neptune 공통). 출력:

```json
{"productCode": "PRD-DEP-001", "productName": "아톰 축구사랑 적금", "category": "수신",
 "hasPreferential": true,
 "steps": [{"screenId": "SCR-DEP-001", "name": "상품안내", "entryCondition": "로그인 완료"}, ...],
 "terms": ["적금 우대금리", ...], "palette": {...},
 "items": [{"id": "COND-001", "category": "명세반영", "text": "우대금리 +1.0% (축구클럽 회원 인증) 조건이 화면에 표기된다",
            "required": true, "weight": 3, "check": "llm", "source": {"nodeId": "CND-...", "label": "Condition"}},
           {"id": "FLOW-COND", "category": "UX흐름", "text": "우대 조건 입력·검증 스텝이 플로우에 존재한다",
            "required": true, "weight": 3, "check": "dom", "expect": {"stepContains": "축구클럽"}, "source": {...}},
           ...]}
```

순회 규칙:

| 출처 | 항목 |
|---|---|
| `Product -HAS_CONDITION-> Condition` | 조건마다 "화면에 반영" 항목. type이 우대/자격이면 **FLOW-COND: 조건 입력 스텝 존재**(필수). 우대/자격 조건이 없으면 **FLOW-NOCOND: 불필요한 조건 스텝이 없다** |
| `Product -SOLD_VIA-> Screen <-INCLUDES- Procedure` | 단계 순서. ux-flow면 프레임 순서 일치(dom). 단일 화면이면 ScreenMeta prev/next와 내비 일치(llm) |
| `PolicyRule -CONSTRAINS-> Screen` (ACTIVE) | ruleType별: 고지의무→고지 문구, 동의→동의 컨트롤, 입력검증→입력 형식 힌트, 표기→명칭 통일, 접근성→터치 타깃, 보안→세션 안내. severity HIGH=필수·가중 3, MEDIUM 2, LOW 1 |
| `UXTerm -USED_IN-> Screen` | 용어 사용 항목(text, 결정적) |
| `Screen -FOLLOWS-> Pattern` (APPROVED) | 패턴 구조 존재(llm) |
| 카테고리 템플릿 `platform/studio/checklists/{common,deposit,loan,card,fx,conditional}.json` | 공통 + 카테고리 + 우대 조건 존재 시 `conditional` 추가(조건 입력 화면, 자격 검증 결과 표시, 우대 전/후 금리 표기, 미충족 시 기본금리 안내) |

디자이너는 실행 전 `studio_spec`으로 체크리스트를 미리 본다.

### 2-1. 시드 추가 (`platform/seed/generate.py`)

기존 노드·엣지는 바이트 단위로 유지한다(임베딩 캐시). 파일 끝에 별도 난수열(`SEED + 2`) 블록을 추가:

- **PRD-DEP-001 아톰 축구사랑 적금** (수신): Condition — 기본금리 3.0%, 우대금리 +1.0% "축구클럽 회원 인증"(type 우대),
  자동이체 우대 +0.2%(type 우대), 가입기간 6/12개월, 월 납입 1만~50만.
  Procedure **PRC-030 축구사랑 적금 가입 절차**: 상품안내 → 기간선택 → 금액입력 → **축구클럽 인증(우대조건)** → 납입방식 → 약관동의 → 완료.
  화면 7건(`SCR-DEP-0xx`) + ScreenMeta 7건(prev/next 사슬) + PolicyRule(REG-CS-003 DERIVED_FROM: 우대 조건·미충족 불이익 설명 확인, 고지·이해 확인) + 적금 UXTerm.
- **PRD-DEP-002 아톰 기본 적금** (수신, 대조군): 같은 플로우에서 조건 스텝만 없음. 우대 Condition 없음.

기존 §5-4 커버리지 테스트는 영향 없음(신규 노드는 기존 Regulation/Component 영향 집계에 들어가지 않도록 REG-CS-003 외 연결 금지 — 들어가더라도 하한 제약만 있으므로 안전).

## 3. 루프 워커

### 3-1. 실행 흐름

```
브라우저 sock.run('studio_run', job)
  → WsFn handlers/studio.py: 검증 · 잡 저장 · StudioLoopFn 비동기 invoke · {"type":"studio_run","jobId"} 회신
  → StudioLoopFn (platform/studio/worker_handler.py → studio/loop.py)
       Ctx(apigw, connId, email, reqId, traceId) 로 같은 커넥션에 이벤트 push
       spec_build → assets → [generate → review → (regenerate)]×N → publish → studio.done
```

이벤트(CONTRACTS §1 3종만): `studio.stage`(step ∈ spec_build, assets, generate, review, regenerate, publish),
`studio.token`, `studio.done`. `studio_run` 회신은 `.done`이 아니므로 `sock.run`이 종료로 보지 않는다.

`studio.done`: `{jobId, draftId, url, score, passed, rounds, maxRounds, stopReason, items, history:[{round, score, failures, url}], usage, model, elapsedMs, bestRound}`.
`stopReason ∈ passed | max_rounds | time_cap | error`.

### 3-2. 라운드

1. **generate** — `engine.bedrock.Stream`. 시스템 프롬프트 = 스튜디오 스킬(`platform/skills/studio-*.md`, 원본 hana-design-system·design-draft-html·a11y-finance를 이식) + 출력 유형 스타일(design/mockup/wireframe/ux-flow, 원본 `OUTPUT_STYLES` 이식) + 축(밀도/강조/흐름) + DesignSpec 요약(단계·조건·용어·정책) + 선택 자산 내용(프록시로 로드) + 승인본 few-shot ≤2 + 에이전트 프리셋(자산 type `agent`). 출력 계약: 자기완결 HTML 1개, 프레임은 `<section data-step="n" data-screen="SCR-…">`, 390px 모바일, 외부 리소스 금지.
2. **review** — 하이브리드.
   - 결정적 검사(`studio/review.py`, 모델 호출 없음): `check: text`(용어·문구 존재, 정규화 후 부분 일치), `check: dom`(`html.parser`로 프레임 수·`data-step` 순서·입력/동의 컨트롤 존재·브랜드 hex 사용·외부 스크립트/fetch 금지). 라운드 2+와 refine 모드에서는 이전 HTML과 DOM 골격(태그 경로 다중집합) diff → 수정 대상 밖 변동은 **경고 항목 `STABLE`(가중 1)**.
   - 리뷰어 모델 호출(`engine.bedrock.generate`, purpose `studio_review`, 별도 "리뷰 에이전트" 역할): `check: llm` 항목 + DOM 다이제스트(프레임별 헤딩·레이블·버튼·입력, ≤6k자) → 엄격 JSON `{items:[{id, verdict: pass|fail, evidence, fix}]}`. 파싱 실패·누락 항목은 **미판정(None)** — pass로 세지 않는다(SPEC §12 흉내 금지).
   - **점수** = Σ가중(pass) / Σ가중 × 100 (정수). **passed** = 필수 항목 전부 pass AND 점수 ≥ passScore(기본 85, 50~100 조정).
3. **regenerate** — 이전 HTML + 실패 항목(fix 포함)을 넣고 "필요한 부분만 수정, 나머지 마크업·텍스트 보존"을 지시. 라운드 상한(1~20, 기본 3) 또는 벽시계 13분 도달 시 중단(`time_cap`).
4. **publish** — 라운드마다 HTML을 S3에 저장하고 라운드 기록을 테이블에 쓴다(소켓이 끊겨도 복구). 최종 시안 = **최고 점수 라운드**(UI에 "최고 점수 라운드 n 선택"으로 표기).

**refine 모드**(요소 클릭 선택 + 지시): 같은 루프에 `baseDraftId, selector, elementHtml, instruction`을 넣고 기본 1라운드. 결과는 `parentId`를 가진 새 시안.

계측: 라운드마다 `common.tracing.record_trace(scenario="studio", ...)` — 프롬프트·HTML 원문 금지, 길이·점수만.

## 4. 저장·인프라

- **DynamoDB `STUDIO_TABLE`** (pk/sk): `job#<id>` (요청·상태·라운드 요약), `round#<jobId>#<n>`, `draft#<id>` (title, axis, productCode, score, status ∈ 검토중|승인됨|반려, comment, parentId, url, createdAt). GSI 없이 pk=`drafts` sk=`<createdAt>#<id>` 목록 항목을 함께 쓴다.
- **시안 HTML**: 기존 `WebBucket` `studio/drafts/<draftId>.html` — 기존 CloudFront로 서빙(`https://<domain>/studio/drafts/...`).
- **StudioLoopFn**: Python 3.12, `apiCode` 동일 번들, 2048MB, timeout 15분. 권한: Bedrock invoke + guardrail apply, `execute-api:ManageConnections`, 테이블 RW, 버킷 `studio/*` Put, 그래프(Neptune/브리지 invoke) — WsFn과 동일 env 부분집합 + `STUDIO_TABLE`, `WEB_BUCKET`, `WS_ENDPOINT`.
- **WsFn**: StudioLoopFn invoke 권한, 테이블 RW.
- 핸들러 `api/handlers/studio.py` ROUTES: `studio_run`, `studio_jobs`(목록/단건+라운드), `studio_drafts`, `studio_feedback`(status+comment), `studio_products`(Product 목록: 이름·카테고리·조건 수·우대 여부), `studio_spec`(체크리스트 미리보기), `studio_asset`·`studio_models`·`studio_register`(프록시 이관). `core.py`의 `studio_*` 프록시 라우트와 `_studio()`는 `api/common/studio_proxy.py`로 옮기고 `hub`/`assets`만 그대로 쓴다.
- `handlers/__init__.py`에 `handlers.studio` 등록. `deploy.sh`가 `studio/` 디렉토리를 `api-dist/`에 복사.

## 5. 프론트엔드 (`platform/web/src/studio/`)

`Studio.tsx`를 셸 + `Gallery.tsx`, `Playground.tsx`, `Assets.tsx`, `SpecPanel.tsx`, `RoundTimeline.tsx`로 분리. 현재의 밝은 Hana 팔레트(#008485 계열) 유지.

복원(원본 스튜디오 기능):
- 플레이그라운드: 라이브 캔버스 iframe, 모바일/전체 폭 토글, 요소 선택 모드(`cssPath`) → refine 입력, 턴 히스토리, 변형 칩, 새 탭 열기, 경과 시간.
- 출력 유형 select(design/mockup/wireframe/ux-flow), 축 select, 에이전트 프리셋(자산 type `agent`), 모델 select.
- 잡 이력(모델·토큰·라운드·점수), 자산 타입 필터 칩, mine/shared 스코프 스위치, 버전 타임라인, 반려 코멘트, 갤러리 [편집]→플레이그라운드.

추가(루프):
- 상품 선택 드롭다운(조건 수·"우대 조건" 배지) + SpecPanel(실행 전 체크리스트 미리보기, 항목별 출처 노드 칩 → `explore`).
- 라운드 상한 슬라이더(1~20, 기본 3), 통과 점수(기본 85).
- 진행 타임라인(stage별 경과), 라운드별 점수 배지, 체크리스트 결과(✓/✗/— + evidence + fix), 최종 "최고 점수 라운드 n" 표기.
- 프리셋: "축구사랑 적금 가입 플로우(ux-flow)", "기본 적금 가입 플로우(ux-flow)", "전세대출 심사 결과(design)".
- 갤러리 카드: 점수·상태·축·라운드 수·상품명, 승인/반려(+코멘트), 검수 리포트 보기.

§11 배지(사실대로): "모델 호출: 익명화 게이트 경유", "그래프: Local / Neptune"(실값), "자산 원본: uiux-studio 레지스트리(프록시)", "검수: 구조·문구·흐름 — 픽셀 비교 미구현", `time_cap` 발생 시 "13분 상한으로 중단".

## 6. 오류 처리

- 그래프에 Product 없음 → `studio.done(error=...)`, 루프 미실행.
- 워커 invoke 실패 → WsFn이 `error` 이벤트, 잡 status `failed`.
- 리뷰어 JSON 파싱 실패 → 해당 항목 미판정, 점수에 pass로 반영하지 않음, `review` stage에 `reviewerError` 표기.
- 게이트 거부(`GateRefused`) → 라운드 중단, `stopReason=error`, 사유 표기.
- 소켓 단절 → 워커는 `GoneException`을 무시하고 계속 기록; 프론트는 재접속 후 `studio_jobs`로 라운드 복구.
- 라운드 상한은 서버에서 1~20으로 클램프, passScore 50~100.

## 7. 테스트 (오프라인, `platform/tests/test_studio*.py`)

- `spec.py`: LocalGraphStore 시드에서 PRD-DEP-001 → `FLOW-COND` 필수 항목 생성, PRD-DEP-002 → `FLOW-NOCOND`; PolicyRule severity→가중치; 용어 항목 수 > 0.
- `review.py`: 픽스처 HTML로 text/dom 검사 pass/fail, 프레임 순서, 외부 스크립트 탐지, DOM 골격 diff `STABLE` 경고, 점수 산식, 미판정은 pass 아님.
- `loop.py`: 주입 생성기·리뷰어로 (a) 1라운드 통과 시 즉시 종료, (b) 상한 도달 `max_rounds`, (c) 최고 점수 라운드 선택, (d) 시간 상한 `time_cap`, (e) 리뷰어 JSON 오류 → 미판정, (f) refine 모드 1라운드.
- 핸들러 스모크: `studio_run` 검증·클램프, `studio_spec`, `studio_products`.
- 기존 `test_ontology_v2`·`test_coverage` 통과 유지. 웹 `tsc --noEmit` + `vite build`.

## 8. 문서 반영

- `SPEC.md` §16 구현 메모에 스튜디오 루프 결정(명세 출처·라운드 상한·워커 위치·HTML 유지) 기록.
- `platform/README.md` 배포 상태에 StudioLoopFn·STUDIO_TABLE 추가(배포 확인 후에만 "완료").
- `platform/docs/CONTRACTS.md` 소유권 표에 `studio/`, `api/handlers/studio.py`, `web/src/studio/` 행 추가.
