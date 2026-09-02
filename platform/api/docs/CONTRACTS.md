# Platform 내부 계약 (병렬 구현용) — 2026-09-02

이 문서는 `platform/` 안에서 동시에 작업하는 모듈들이 지켜야 하는 계약이다. 위반하면 통합이 깨진다.

## 0. 디렉토리 소유권 (다른 소유자의 파일은 편집하지 않는다 — 통합 스니펫으로 전달)

| 경로 | 소유 | 내용 |
|---|---|---|
| `api/ws_handler.py`, `api/common/*`, `api/handlers/__init__.py`, `api/handlers/core.py`, `api/handlers/s1.py`, `api/handlers/s2.py`, `api/admin_handler.py` | 통합자(호스트) | 진입점·공통 |
| `registry/`, `api/handlers/registry.py`, `web/src/views/RegistryView.tsx`, `tests/test_registry*.py` | Registry 모듈 | F4 |
| `screengen/`, `gates/`, `skills/`, `api/handlers/screengen.py`, `web/src/views/ScreenGen.tsx`, `tests/test_screengen*.py` | 화면생성 모듈 | F5 |
| `report/`, `api/handlers/report.py`, `web/src/views/Report.tsx`, `web/public/samples/*`, `tests/test_report*.py` | 보고서 모듈 | F7 |
| `onprem/`, `bridge/`, `graph/store.py`(Neptune 부분), `tests/test_onprem*.py` | 플레인 모듈 | §3 온프렘·Neptune |
| `infra/lib/*.ts`, `deploy.sh`, `web/src/App.tsx`, `web/src/lib.ts`, `web/src/Views.tsx` | 통합자 | CDK·셸 |

## 1. 핸들러 계약 (`api/handlers/<module>.py`)

```python
from common.ctx import Ctx
def handle_x(ctx: Ctx, body: dict) -> None: ...
ROUTES = {"x": handle_x, "x_list": ...}   # 액션 이름은 모듈 접두어로 시작: registry_*, screengen*, report*
```
- `ctx.post({...})` 이벤트 1건. `reqId`/`traceId` 자동 부여. 응답 이벤트의 `type`은 액션명과 같게 한다
  (요청/응답형). 스트리밍형은 `<kind>.stage` / `<kind>.token` / `<kind>.done` 3종만 쓴다
  (프론트 `sock.run`이 `.done` 1건에서 종료, `.stage`/`.token`은 구독으로 전달).
- `ctx.email`은 Cognito로 검증된 사용자. 감사 이벤트의 actor로 쓴다.
- 예외는 던져도 된다 — 진입점이 `{"type":"error"}`로 변환한다. 단, 사용자에게 보여야 하는 실패는 직접 `ctx.done(kind, error=...)`.
- Bedrock 생성은 `engine.bedrock.Stream(system, user, max_tokens)` 사용 → 반복 후 `.usage` 에 실측 토큰.
  단발은 `engine.bedrock.generate(system, user)` → `(text, usage)`.
- 계측: `from common import tracing; tracing.record_trace({...})` — 키: `traceId, scenario, email, query, blocked, piiOutbound, maskedFields, tokensIn, tokensOut, cached, plane, elapsedMs` + 자유 필드. **프롬프트 원문·응답 원문·개인데이터 값은 넣지 않는다** (email/query는 자동 해시).
- 로그: `from common.log import log_event; log_event("module.event", ctx.trace_id, key=value)`. 금지 키는 자동 해시.
- 온프렘 플레인 호출: `from common import plane; plane.call("/path", body)` (bridge/direct). `plane.mode()` in {bridge,direct,local,none}.
- 환경변수는 `os.environ.get("X", default)`; 새 env가 필요하면 통합 스니펫에 명시.
- Lambda 런타임: Python 3.12, **외부 pip 패키지 없음**(boto3만). PyYAML도 없다 (semantic은 json 변환본 사용).
- 배포 조립: `deploy.sh`가 `api/*.py`, `api/common`, `api/handlers`, `engine`, `graph`, `onprem`, `semantic`, `seed/out` + **각 모듈 디렉토리**(`registry`, `screengen`, `report`)를 `api-dist/`로 복사한다. 모듈은 `import registry.x` 처럼 최상위 패키지로 import 된다 (`sys.path`에 api-dist 루트).

## 2. 프론트 계약 (`web/src/views/<View>.tsx`)

- React 18 + TS + Tailwind v4, 다크 전용, Pretendard. 공용 클래스: `.panel`, `.chip`, `.md`, `.blink`.
  색 규칙 필수: 온프렘 = `var(--onprem)`(앰버), 클라우드 = `var(--cloud)`(시안), 정상 = `var(--ok)`; 위험은 `text-rose-400`.
- 소켓: `import { sock, auth } from '../lib'`.
  - 요청/응답: `const e = await sock.request('registry_list', { type: 'COMPONENT' })` → 응답 이벤트 1건.
  - 스트리밍: `await sock.run('screengen', { prompt }, (e) => { ... })` — `.stage/.token/.done` 이벤트 수신, `.done`에서 종료.
  - 캐시 재생: 이벤트에 `cached: true`가 있으면 "캐시 응답" 배지를 보여야 한다. `type: 'cache.replay'` 수신 시 상태 초기화.
- 뷰는 `export default function XView()`; 라우팅/내비 등록은 통합자가 `App.tsx`에서 한다.
- `localStorage`/`sessionStorage`에 개인데이터·토큰 저장 금지 (§12.10).
- 프리셋 버튼(시연 원클릭)과 "진행 중" 상태 표시를 반드시 둔다. 미구현 기능은 UI에 "미구현"이라고 명시한다 — 흉내 금지.

## 3. Registry 모듈이 노출해야 하는 Python API (다른 모듈이 import)

```python
# registry/api.py
def counts() -> dict                      # {"total": int, "approved": int, "byType": {...}}
def list_approved(record_type: str | None = None, subtype: str | None = None) -> list[dict]   # Consumer API — APPROVED만
def get_record(name: str, version: str) -> dict | None
def search(query: str, record_type: str | None = None) -> list[dict]   # 키워드 + 임베딩 하이브리드
# registry/seed.py
def seed(actor: str, reset: bool = False) -> dict          # 기준선 시드 (멱등)
def reset_demo_state(actor: str) -> dict                   # 시연 리셋: Button v2 APPROVED, v3 PENDING_APPROVAL 등 기준선 복원
```
레코드 형태(공통):
```json
{"name": "Button", "recordVersion": "v2", "recordType": "CUSTOM", "subtype": "COMPONENT",
 "status": "APPROVED", "description": "...", "owner": "UI플랫폼팀", "tags": ["form"],
 "payload": {"propsSchema": {...}, "import": "@atom/ui", "supersededBy": "v3"},
 "createdAt": 1690000000000, "updatedAt": 1690000000000, "updatedBy": "demo@atomai.click"}
```
- recordType ∈ {MCP, AGENT, SKILL, CUSTOM}; 컴포넌트는 `CUSTOM` + `subtype: COMPONENT`.
- 상태 전이: DRAFT→PENDING_APPROVAL→APPROVED→DEPRECATED, PENDING_APPROVAL→REJECTED→DRAFT. 그 외 전이는 400 오류.
- `name`+`recordVersion` 유일 (DynamoDB 조건부 put). 모든 전이는 감사 이벤트(actor, from, to, reason, ts) 저장.
- 테이블: `REGISTRY_TABLE` env. 키 설계는 모듈 자유. 이 테이블은 통합자가 CDK에 추가한다 (pk: string `pk`, sk: string `sk`, GSI `byStatus`: pk `status`, sk `updatedAt` 이라고 가정하고 구현; 다른 설계가 필요하면 스니펫에 명시).

## 4. 화면생성 모듈 (F5) 계약

- 액션 `screengen` (스트리밍): body `{prompt}`. 단계 `registry_lookup`(승인 컴포넌트 + propsSchema, 정확 조회), `skills`, `generate`(토큰 스트리밍), `gates`(결과), `regenerate`(1회만, 실패 사유 포함), `.done` `{code, componentsUsed:[{name,version}], gates:{build,types,lint,a11y,visual}, attempts}`.
- 게이트 실행기는 별도 Node 20 Lambda(`gates/`)이며 Python 오케스트레이터가 `GATES_FN` env로 invoke 한다. 입력 `{code, filename, components:[{name, version, propsSchema}]}` → 출력 `{build:{ok,errors[]}, types:{ok,errors[]}, lint:{ok,errors[]}, a11y:{ok,violations[]}, visual:{ok,note,diff?}}`.
- 컴포넌트 import 규약: 생성 코드는 `import { Button } from '@atom/ui'` 형태만 쓴다. 게이트는 승인 propsSchema로 `@atom/ui` 타입 선언을 합성해 타입검사한다 (Deprecated 컴포넌트는 선언에 없으므로 타입 오류).
- 승인 상태가 바뀌면 결과가 바뀌어야 한다: Registry `list_approved(subtype="COMPONENT")`만 프롬프트에 넣는다. 벡터 검색 금지.

## 5. 보고서 모듈 (F7) 계약

- 액션 `report` (스트리밍): body `{url?}` 기본값은 `web/public/samples/vendor-news.html`(CloudFront 경로 `/samples/vendor-news.html`, 인젤션 지시문 포함).
  단계 `reader_fetch`, `reader_summarize`(도구 호출 시도·AccessDenied 로그 포함), `handoff`(구조화 JSON), `writer_search`(내부 문서), `writer_generate`(토큰 스트리밍), `.done`.
- Reader/Writer는 **별도 Lambda + 별도 IAM 역할**. Reader 역할: Bedrock invoke만(내부 조회 Lambda invoke 권한 없음 → 시도 시 AccessDeniedException을 캡처해 보여준다). Writer: 내부 문서(seed/out Document 노드) 읽기 + Bedrock, URL fetch 코드 없음.
- 오케스트레이터(`api/handlers/report.py`)는 `READER_FN`, `WRITER_FN`, `INTERNAL_TOOL_FN` env로 invoke. 둘 사이는 구조화 JSON만 통과.

## 6. 플레인 모듈 (§3) 계약

- `onprem/service.py` 라우트 유지: `/health`, `/s2/prepare`, `/s2/finalize`, `/audit/recent` + 추가 `/vector/search` `{query, queryEmbedding:[float]}` → `{hits:[{chunkId,text,score,stage}], timing}`.
- 감사 원문·재식별 매핑은 `DATA_BACKEND=rds`면 RDS 테이블(`audit_log`)에, 아니면 파일에. `/audit/recent`는 원문을 노출하지 않는다(길이만).
- 브리지: `bridge/handler.py` (이미 있음) — 변경 시 op 이름 유지.

## 7. 테스트

- Python: `cd platform && python3 -m pytest tests/ -q` — AWS 호출 없이 통과해야 한다 (DynamoDB는 인메모리 페이크로 주입, boto3 클라이언트는 지연 생성).
- Node(gates): `cd platform/gates && npm test`.
- 프론트: `cd platform/web && npx tsc --noEmit && npm run build`.
