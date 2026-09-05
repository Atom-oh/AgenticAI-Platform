# 디자인 스튜디오 — 상품명세서 기반 프로세스 생성 + 검수 루프 설계

작성 2026-09-04 · 근거: 2026-09-03 하나은행 UX AI Platform 미팅(ttobak `cebb65cb…`) · 사용자 확정 접근안 A+B(공유 엔진 + 두 UI)

## 1. 목표

1회성 화면 생성을 **상품명세서 → PRD → 프로세스(다단계 플로우) 생성 → 리뷰·테스트 → 리포트**의 유계 루프로 바꾼다.
검수 기준은 "디자인이 상품명세서를 제대로 반영했는가"이며, 상품 특성(예: 축구클럽 우대)에 따라
체크리스트가 자동으로 늘어난다. 같은 루프 엔진을 플랫폼(agent.atomai.click)과 독립 스튜디오
(d4zwmnh2s47e9)가 공유하고, UI는 각각 붙인다.

미팅에서 확인된 요구(요약):
- 병목은 "앞단" 자산화·메타데이터. 아톰은 있으나 몰큘·오가니즘·템플릿이 없어 생성이 깨졌고, **UX SM 모델**
  (몰큘=인텐트 / 오가니즘=구성·룰·관계 / 템플릿=구조·위치)을 붙이자 기존 화면 수준까지 재현됨.
- 입력 체계: 약관·상품설명서 → PRD → 디자인 MD → 컴포넌트 → 화면. 수신·여신부터, 정형 → 반정형 → 비정형.
- 반정형 예: 축구클럽 가입자 우대 적금이면 가입 프로세스에 **축구클럽 입력 스텝**이 하나 더 붙고 정합성 항목도 늘어남.
- 검증 루프 부재가 핵심 갭: 리뷰 에이전트·테스트 에이전트가 PRD 부합을 확인해 마스터에 회신, 승인 시 GitLab 푸시.

제약: SPEC §12.11(재생성 최대 1회), §11(대체 표기 배지), §12.8(수치는 실측), §12.13(금지어).

## 2. 입력 자산 모델 (Registry `CUSTOM`/`SKILL` 레코드, 승인 수명주기·버전 적용)

| 자산 | recordType | 핵심 필드 | 비고 |
|---|---|---|---|
| ProductSpec 상품명세서 | CUSTOM(`kind: product-spec`) | `productName, productType(수신/여신), category(적금/통장/…), eligibility, term, baseRate, preferentialConditions[], mandatoryNotices[], terminationRules, partners[]` | `preferentialConditions[i] = {condition, evidence(입력·증빙 방식), rate}` — 반정형 스텝의 근거 |
| SmModel UX SM 모델 | CUSTOM(`kind: sm-model`) | `molecules[] {id, intent}`, `organisms[] {id, composition[], rules[], relations[]}`, `templates[] {id, structure, slots[]}` | 수신 가입 프로세스용 1세트를 합성으로 제공 |
| Checklist 체크리스트 | SKILL(`kind: checklist`) | `items[] {id, text, method: rule|llm, target: flow|screen, severity}` + `appliesTo {productType?, category?, partnerType?}` | 기본 세트(수신 공통) + 특성별 추가 세트(스포츠 제휴 등) |
| PRD | CUSTOM(`kind: prd`) | `steps[] {id, title, required[], organisms[], branch?: {when, insertStep}}`, `transitions[]`, `derivedFrom {productSpecId, smModelId}` | 에이전트가 도출 → 사람이 수정·승인 → 승인 버전이 생성 입력 |

규칙:
- 체크리스트는 두 원천을 합친다. **기본 세트 = 자산**, **상품 특성 항목 = 상품명세서에서 파생**
  (`preferentialConditions`·`partners`·`mandatoryNotices`에서 결정론적으로 생성). 파생 항목은 리포트에
  `source: derived` 로 표기한다.
- PRD 도출 규칙(결정론 부분): 우대조건마다 `evidence`가 "입력"이면 `증빙 입력` 스텝을 분기로 추가한다.
  LLM은 스텝 제목·필수 요소 문구를 다듬을 뿐, 스텝 존재 여부는 규칙이 결정한다(검수 기준이 흔들리지 않게).

## 3. 루프 엔진 — `platform/design_loop/` (공유 파이썬 모듈)

두 런타임(플랫폼 `agents/`, 구 스튜디오 `harness/`)이 빌드 시 이 디렉토리를 복사해 import 한다.
외부 의존은 표준 라이브러리 + 호출자가 주입하는 `generate(system, user) -> str` 콜백만. Bedrock 클라이언트는
호출자가 준다(플랫폼: 게이트 경유 `engine.llm`, 구 스튜디오: 자체 BedrockModel).

```
design_loop/
  models.py      ProductSpec / SmModel / Checklist / Prd / FlowDraft / ReviewReport dataclass + from_dict/validate
  prd.py         derive_prd(spec, sm) -> Prd            (결정론 골격 + LLM 문구 다듬기 옵션)
  checklist.py   build_checklist(spec, base_sets) -> list[Item]   (기본 + 파생)
  generate.py    build_prompts(prd, sm, assets, failures=None) / parse_flow(text) -> FlowDraft
  rules.py       rule 판정기: step_exists, transition_matches_button, required_text_present, notice_present, a11y_basics
  review.py      review(flow, items, llm_judge) -> ReviewReport   (rule → 파이썬, llm → 콜백)
  loop.py        run(spec, sm, assets, deps, emit) : prd → checklist → generate → review → [regenerate ×1] → report
```

이벤트(계약 3종): `design.stage {step: prd|checklist|generate|review|test|regenerate|report, ...}`,
`design.token {text}`(생성 스트림), `design.done {flow, report, attempts, regenerated}`.

FlowDraft = `{steps[] {id, title, html, organisms[]}, transitions[] {from, to, trigger, when?}, flowMap}`.
ReviewReport = `{items[] {id, text, source: base|derived, method, verdict: pass|fail|incomplete, evidence, attempt}, score {pass, fail, incomplete}, attempts, regenerated, openItems[]}`.

재생성: 실패 항목을 `failures`로 프롬프트에 넣어 **최대 1회**. 그 후 남은 항목은 `openItems`로 리포트에 남긴다.
최종 승인은 사람이 갤러리에서 한다. "마스터 승인 → 운영 GitLab 푸시"는 §11 배지("데모: Registry 버전 / 운영: GitLab")로 표기한다.

## 4. 런타임 통합

- 플랫폼: `agentcore/agent_specs.py`에 `design_flow_agent` 추가. `agents/app.py`가 `agent == design_flow_agent`이면
  Strands 대화 대신 `design_loop.run`을 실행하고 이벤트를 SSE로 흘린다. 디자인 자산(팔레트·토큰)은 Gateway 도구
  `lookup_design_asset`(신규, PlatformToolsFn)로 조회. `prepare_context.sh`가 `../design_loop`를 `_ctx/`에 복사.
- 구 스튜디오: `harness/app.py`에 `mode == "flow"` 분기 → `design_loop.run`. Dockerfile이 `design_loop/`를 복사
  (빌드 전 `scripts/deploy_runtime.py`가 `platform/design_loop` → `harness/design_loop` 동기화). 자산 정본은 플랫폼
  Registry이며 구 스튜디오는 `GET /api/registry/mirror`(읽기)로 받는다.
- 플랫폼 API: `api/handlers/studio.py` 신설(`studio_*` 액션 이관 + `design_flow`, `design_specs`, `design_prd_preview`).
  `design_flow`는 런타임 스트림을 `design.stage/token/done`으로 중계한다.

## 5. UI

플랫폼 `web/src/Studio.tsx` → `web/src/studio/` 디렉토리로 분리:
- 탭 4개 복원: 갤러리 · 플레이그라운드 · 자산 · 생성. 출력유형(디자인/목업/와이어프레임/UX플로우), 에이전트 프리셋,
  잡 목록·토큰 사용량·모델 표시 복원.
- 생성 탭 **프로세스 모드**: 상품명세서 선택 → PRD 미리보기(스텝·분기) → 체크리스트(기본/파생 구분) → 실행 →
  루프 타임라인(단계별 채움).
- 결과 **플로우 뷰**: 상단 스텝·분기 맵, 중단 스텝별 화면 프리뷰, 우측 검수 리포트(✓/✗/미판정 + 근거 + 재생성 이력).
  갤러리 카드에 검수 점수 배지. refine(클릭 수정)은 "다음 단계" 배지로만 표기.

독립 스튜디오 `gallery/index.html`: 생성 탭에 프로세스 모드(상품명세서 선택), 갤러리 카드에 플로우 썸네일 + 리포트 패널.

## 6. 데이터·배포

- 합성 시드(`platform/seed/design/`): ProductSpec 3종 — 정형 "하나 목돈 적금", 반정형 "축구클럽 우대 적금"
  (우대조건: 축구클럽 회원, 증빙 = 회원번호 입력; 제휴: 스포츠), 반정형 "급여이체 우대 통장"; SmModel 1세트;
  Checklist 기본 세트(수신 공통 12항목) + 스포츠 제휴 추가 세트(6항목: 제휴사 로고·상표 규정, 증빙 입력 스텝 존재,
  이벤트 기간 표기, 광고 심의 문구, 우대금리 조건 고지, 제휴 종료 시 처리 안내).
- `admin seed_registry`가 시드를 Registry에 등록(APPROVED 기준선). `reset_demo`가 기준선을 복원.
- 두 런타임 재빌드: 플랫폼 `deploy.sh`, 구 스튜디오 `scripts/deploy_runtime.py`.
- 비용: 실행당 생성 1~2회 + llm 판정 항목 수. 기존 코스트 가드·캐시 리플레이 적용.

## 7. 오류 처리

- PRD 도출 실패(명세서 필드 부족) → `design.done {error, code: 'spec-incomplete', missing[]}`; UI가 부족 필드를 표시.
- 생성 파싱 실패(플로우 JSON 불량) → 1회 재생성 카운트에 포함하지 않고 같은 시도 내 재파싱 요청 1회, 실패 시 오류 종료.
- 게이트 러너 불가(Lambda 미배포) → 접근성 항목 `incomplete` + 러너 배지, 루프는 계속.
- 모델 오류 → 코스트 가드 폴백(캐시 응답 배지) 또는 오류 종료. 무한 재시도 없음.

## 8. 테스트

오프라인(pytest, AWS 호출 없음):
- `derive_prd`: 우대조건 `evidence=입력`이면 증빙 스텝이 분기로 추가된다; 없으면 추가되지 않는다.
- `build_checklist`: 스포츠 제휴 상품이면 추가 6항목이 `source: derived/base`로 붙고, 일반 상품은 기본 세트만.
- `rules`: 스텝 누락·전이-버튼 불일치·필수 문구 누락을 검출한다.
- `loop.run`: 첫 리뷰 실패 → 재생성 1회 → 두 번째 실패에도 세 번째 생성이 없다; 이벤트가 3종 계약만 쓴다.
- 핸들러 스모크: `design_flow`가 stage/token/done 순서로 방출.

e2e(배포 후): 축구클럽 우대 적금 실행 → 플로우에 회원번호 입력 스텝 존재 → 리포트에 파생 항목 판정 존재.
실패 시연: SmModel에서 증빙 입력 오가니즘을 제거한 버전으로 실행 → 리뷰 실패 → 재생성 → 이력이 리포트에 남는다.
브라우저 확인은 두 UI 모두.

## 9. 범위 밖 (이번 주)

refine(클릭 수정), Figma 동기화, 실제 리액트 컴포넌트 매핑(내부망 트랙), GitLab 실제 푸시.
