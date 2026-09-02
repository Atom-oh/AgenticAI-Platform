# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 저장소 구성 — 한 플랫폼, 네 산출물

| 경로 | 내용 | 정본 문서 |
|---|---|---|
| `docs/` | VitePress 가이드북 "Agentic AI 플랫폼 엔지니어링" (GitHub Pages 자동 배포) | `docs/.vitepress/config.ts` |
| `platform/` | 아톰은행 데모 — SPEC.md 구현 (단일 SPA + 2 CDK 스택) | 루트 `SPEC.md`(요구사항 정본), `platform/README.md`(배포 상태 — 코드로 확인된 것만 "완료"로 적는다) |
| `demo/builder-harness/` | 자매 데모: 에이전트 컨트롤룸 (한울증권 도메인, AgentCore Harness) | `demo/SECURITY-GOVERNANCE.md` |
| `demo/uiux-studio/` | 자매 데모: 디자이너용 UI/UX 스튜디오 (자체 CDK 스택) | `demo/uiux-studio/README.md` |

SPEC.md는 사용자가 준 정본이며 §17에 결정 변경 이력이 쌓인다(예: A1 — PII는 VPC 안에 두되
Bedrock 등 모델 호출 페이로드에는 익명화 필수). §12 안티 요구사항(LLM이 금액·금리 생성 금지,
Guardrails 목 금지, Vector RAG 비교군 약화 금지, 브라우저 스토리지에 개인데이터 금지 등)을 위반하는
구현은 하지 않는다.

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
python3 cli.py admin health             # 브리지→온프렘/Neptune 상태
python3 cli.py admin reset_demo         # 시연 리셋
bash teardown.sh                        # 플레인 스택 삭제 (Neptune·RDS·ECS 상시 과금)
```

CDK 함정: 배포 전 `platform/infra/cdk.out`이 오래됐으면 지워라 — 캐시된 구버전 템플릿으로
배포돼 이미 고친 오류가 재발한 전례가 있다. synth 후 템플릿을 grep으로 확인하고 배포하는 것이 안전.

## 은행 데모 아키텍처 (platform/)

두 CDK 스택, NAT 없음 — 상세 다이어그램은 `platform/README.md`:

- **`BankPlatform`(클라우드 플레인)**: CloudFront+S3(유일한 퍼블릭 진입점) / WebSocket API
  (`$connect`에서 Cognito access token 검증 — 무토큰 연결 거부) / `WsFn`(VPC 밖) / Guardrails·
  Registry는 IaC. `ReaderFn`은 F7 시연용으로 내부 도구 invoke 권한이 없다(IAM AccessDenied가 의도).
- **`BankPlatformPlane`(격리 VPC)**: `onprem-isolated` 서브넷(인터넷 경로 0)의 ECS 온프렘 서비스
  (정확 조회·계산엔진·마스킹·감사원문·벡터 인덱스) + RDS. Neptune·`BridgeFn`·`WriterFn`은
  cloud-isolated 서브넷. **브리지 Lambda(IAM invoke 전용)가 플레인의 유일한 입구**이고, 관리 작업
  (Neptune 적재·시드·리셋)은 `AdminFn`(IAM 전용) — 사용자 WebSocket 경로에 관리 액션을 노출하지 않는다.
- 데이터 원칙: 프롬프트 원문·재식별 매핑은 플레인 안(RDS `audit_log`)에만. 클라우드 트레이스에는
  해시·길이·메트릭만(§12.3). PII 반출 카운터는 정규식 실측값이며 하드코딩 금지(§3.1).
- 그래프: `graph/store.py`의 `GraphStore` 인터페이스 — `LocalGraphStore`(개발)와
  `NeptuneGraphStore`(시연, 브리지 경유). `GRAPH_BACKEND` env로 전환하고 UI가 현재 백엔드를 항상 표시한다.
  로컬로 시연하며 Neptune이라 말하는 것은 금지(§11).

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
