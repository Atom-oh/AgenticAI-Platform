# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 저장소 구성 — 한 플랫폼, 네 산출물

| 경로 | 내용 | 정본 문서 |
|---|---|---|
| `docs/` | VitePress 가이드북 "Agentic AI 플랫폼 엔지니어링" (GitHub Pages 자동 배포) | `docs/.vitepress/config.ts` |
| `platform/` | 아톰은행 데모 — SPEC.md 구현 (단일 SPA + 2 CDK 스택) | 루트 `SPEC.md`(요구사항 정본), `platform/README.md`(배포 상태 — 코드로 확인된 것만 "완료"로 적는다) |
| `demo/builder-harness/` | 자매 데모: 에이전트 컨트롤룸 (한울증권 도메인, AgentCore Harness) | `demo/SECURITY-GOVERNANCE.md` |
| `demo/uiux-studio/` | 자매 데모: 디자이너용 UI/UX 스튜디오 (자체 CDK 스택) | `demo/uiux-studio/README.md` |

SPEC.md는 사용자가 준 정본이다(2026-09-02 전면 개정판). 착수 전 반드시 읽을 절:
§3 **Single Boundary**(모든 데이터 플레인은 한 VPC 프라이빗 서브넷, **익명화 게이트가 Bedrock으로
나가는 유일한 통과 지점** — 우회 경로 금지), §11 **대체 표기 규칙 ★**(데모가 운영 구성을 대체한
곳은 화면 배지로 사실대로 표기 — 숨기면 최악), §12 **안티 요구사항 13개(예외 없음)**,
§16 구현 메모(명세와 실측이 다른 지점의 기록). 주의: §12.13이 "온프렘", "Two-Plane",
"In-Region" 표현 사용을 금지한다 — "VPC 프라이빗 서브넷"으로 표기한다.

## 명령

```bash
# 가이드북
NODE_OPTIONS=--max-old-space-size=8192 npm run docs:build   # 빌드 (dead link가 빌드 실패 사유)
npm run docs:dev

# 은행 데모 (platform/)
cd platform
python3 -m pytest tests/ -q            # 오프라인 테스트 — AWS 호출 없음
python3 -m pytest tests/test_calc_engine.py -q   # 단일 파일
bash deploy.sh                          # 코드 변경 재배포 (api-dist 조립→CDK→프론트 S3+무효화)
bash deploy.sh --plane                  # 플레인 스택 포함 첫 배포 (20~30분)
GRAPH_BACKEND=neptune bash deploy.sh    # 시연용 — Neptune 적재 자동
python3 cli.py admin health             # 브리지→플레인 서비스/Neptune 상태
python3 cli.py admin reset_demo         # 시연 리셋
bash teardown.sh                        # 플레인 스택 삭제 (Neptune·RDS·ECS 상시 과금)
```

CDK 함정: 배포 전 `platform/infra/cdk.out`이 오래됐으면 지워라 — 캐시된 구버전 템플릿으로
배포돼 이미 고친 오류가 재발한 전례가 있다. synth 후 템플릿을 grep으로 확인하고 배포하는 것이 안전.

## 은행 데모 아키텍처 (platform/)

구조 다이어그램·스택 이름·리소스는 **`platform/README.md`가 살아있는 정본**이다(자주 바뀐다 —
여기 복제하지 않는다). 오래 유지되는 원칙만 적는다:

- **Single Boundary(§3)**: PII 원장(RDS)·벡터 인덱스·온톨로지(Neptune)·계산엔진·감사 원문은 전부
  VPC 프라이빗 서브넷(인터넷 게이트웨이 없음) 안. Bedrock으로 나가는 **모든** 호출은 익명화 게이트
  하나를 지나며 여기서 페이로드를 실측 계측한다. 원장은 복제·벡터화·그래프 적재하지 않는다.
- **모델 라우팅(§4)**: `LLMClient` 인터페이스 + `LLM_ROUTE=claude|gemma`.
  Tier 0/1 = Claude **global 프로파일**(`global.anthropic.claude-sonnet-5` 기본, Converse API).
  Tier 2(PII 추론) 데모 대체 = **Gemma 4 31B via `bedrock-mantle`**(us-west-2, OpenAI 호환
  Chat Completions — Converse/InvokeModel 미지원, 병렬 도구 호출 미지원, 인증은 Bearer 토큰(12h,
  IAM으로 발급) 또는 Secrets Manager의 장기 키). 프롬프트가 국외로 이동할 수 있으므로 익명화가 필수라는
  것이 이 설계의 근거다.
- **관리/사용자 경로 분리**: Neptune 적재·시드·리셋 같은 관리 작업은 IAM invoke 전용 Lambda —
  사용자 WebSocket 경로에 관리 액션을 노출하지 않는다(과거 `load_neptune` 노출 결함의 교훈).
- **그래프**: `graph/store.py`의 `GraphStore` — `LocalGraphStore`(개발)/`NeptuneGraphStore`(시연).
  UI가 현재 백엔드를 항상 표시하고, 로컬로 시연하며 Neptune이라 말하지 않는다.
- **온톨로지 2개 도메인(§5)**: 여신(Regulation·Product·Condition…) + UX 자산(Pattern·Procedure·
  PolicyRule·UXTerm·ScreenMeta). 두 도메인의 연결점은 `(PolicyRule)-[:DERIVED_FROM]->(Regulation)`.
  합성데이터는 §5-4 커버리지 제약(Regulation 3건 + Component 3건 영향 기준)을 테스트로 검증한다.
- **§11 배지 의무**: PII 추론 경로(운영 IDC GPU+vLLM / 데모 Gemma 대체), 익명화 게이트 구현 수준,
  AgentCore insights·Evaluations·Policy를 쓰는 화면의 `Tier 0/1 전용` 배지. Related 카운트 등
  수치는 전부 실쿼리 — 하드코딩을 실측처럼 보이게 하지 않는다(§12.8).

### 병렬 작업 계약

`platform/docs/CONTRACTS.md`가 모듈별 디렉토리 소유권과 핸들러 계약의 정본이다. 요점:

- 액션 핸들러는 `api/handlers/<module>.py`에 `ROUTES = {"액션명": handler}` — 액션명은 모듈 접두어
  (`registry_*`, `screengen*`, `report*`). 응답 이벤트 `type`은 액션명과 동일, 스트리밍형은
  `<kind>.stage`/`<kind>.token`/`<kind>.done` 3종만(프론트 `sock.run`이 `.done`에서 종료).
- Bedrock 생성은 `engine.bedrock.Stream`(실측 usage) 또는 `generate`. 계측은
  `common.tracing.record_trace` — 원문·개인데이터 값 금지.
- 다른 소유자의 파일은 편집하지 말고 통합 스니펫으로 전달한다.

### 이 저장소의 멀티 세션 관행

여러 Claude 세션이 동시에 작업하는 저장소다. 작업 시작 전 `git status`로 다른 세션의 미커밋
변경을 확인하고, **`git add -A` 대신 파일을 지정해 커밋**한다(남의 미완성 파일이 섞여 들어간 사고 전례).
CDK 배포는 한 세션이 단독 소유해야 한다 — 동시 배포는 CloudFormation 직렬화로 서로의 롤백을 유발한다.

## 자격증명·보안 (이 저장소 고유)

- 은행 데모 비밀번호는 Secrets Manager `bank-platform/demo-user` — 문서·코드에 적지 않는다(SPEC §9).
  자매 데모(builder-harness·uiux-studio)는 공유 데모 계정 `demo@atomai.click`을 쓴다.
- Cognito는 전부 초대 전용(`AllowAdminCreateUserOnly=true`) — 가입 UI를 만들지 않는다.
- uiux-studio API에 POST할 때는 CloudFront OAC 때문에 `x-amz-content-sha256`(본문 sha256 hex)
  헤더가 필수 — 없으면 403 SignatureDoesNotMatch.
- Bedrock Guardrails에서 한국어 토픽 차단은 STANDARD 티어 + `apac.guardrail.v1:0` 크로스리전
  프로파일이 필요하다(클래식 티어는 한국어 미탐지).

## 가이드북 집필 규칙 (docs/)

- 모든 챕터는 "이 장에서 얻는 것 → 왜 문제가 되는가 → 핵심 개념 → 결정 표 → 실패 모드 →
  안티패턴 → 계측(SLI) → 체크리스트 → 참고" 골격. 상세 컨벤션은 `docs/13-appendix/vitepress-conventions.md`.
- 미정착 주제는 `::: warning 미정착 영역` 블록으로 정직하게 표기한다.
- 내부 링크가 깨지면 빌드가 실패한다. `docs/public/`의 archify HTML(다이어그램)로의 링크는
  절대 URL(`https://www.atomai.click/AgenticAI-Platform/...`)로 적는다 — 루트 상대 링크는 dead link로 잡힌다.
- 다이어그램 소스는 `docs/diagrams/*.json`(archify 스펙) — HTML을 직접 고치지 말고 스펙을 고쳐
  archify로 재딜리버한다.
