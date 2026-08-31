---
title: 에이전트 스킬
description: Agent Skills(SKILL.md)의 progressive disclosure 구조를 이해하고, 플랫폼의 재사용 가능 capability 패키징 표준으로 채택·운영하는 방법을 다룬다.
outline: [2, 3]
---

# 에이전트 스킬

::: tip 이 장에서 얻는 것
- Agent Skills(SKILL.md) 포맷과 progressive disclosure 3단계 로딩 모델의 정확한 동작
- "LLM 추론으로 할 일"과 "결정적 코드로 할 일"을 분리하는 기준 — 스킬을 플랫폼 capability 패키징 표준으로 채택하는 논거
- 스킬 vs 툴 vs MCP 서버 결정 표: 언제 무엇으로 패키징하는가
- AgentCore Harness의 4가지 스킬 소스와 invoke-time override가 만드는 신뢰 경계
- 스킬 검증 파이프라인(코드 리뷰·공급망)과 계측해야 할 SLI
:::

## 왜 문제가 되는가

Builder Agent가 조직의 도메인 지식을 갖춘 에이전트를 찍어내려면, 그 지식을 담는 **패키징 단위**가 먼저 표준화되어야 한다. 표준이 없으면 팀마다 system prompt에 절차를 하드코딩하고, 같은 "월간 리포트 생성 절차"가 에이전트 수만큼 복제·변형된다. 이 방식의 비용은 두 가지다.

첫째, **컨텍스트 비용**. system prompt에 모든 절차를 상주시키면 실제로 쓰이지 않는 지시가 매 턴 토큰을 소모하고, 지시가 많아질수록 모델의 지시 준수율(instruction following)이 저하된다. 필요할 때만 필요한 지식을 로드하는 구조가 없으면 에이전트에 실을 수 있는 도메인 지식의 총량이 context window에 의해 상한이 걸린다.

둘째, **결정성(determinism) 비용**. PDF 폼 필드 추출, 스프레드시트 수식 검증 같은 작업을 LLM 추론으로 수행하면 매 실행마다 결과가 흔들리고 토큰을 낭비한다. Anthropic의 Agent Skills 엔지니어링 문서는 이 지점을 명시적으로 다룬다 — 스킬은 지시문뿐 아니라 **실행 가능한 코드**를 번들할 수 있고, 예시로 든 PDF 폼 필드 추출 Python 스크립트는 코드 내용을 컨텍스트에 올리지 않은 채 실행된다([Anthropic Engineering — Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)).

2025년 Anthropic이 공개한 Agent Skills 포맷은 이후 [agentskills.io](https://agentskills.io/)의 **오픈 스탠다드**로 발전했다. 사이트가 밝히는 대로 "originally developed by Anthropic, released as an open standard"이며, Claude Code·Claude뿐 아니라 GitHub Copilot, VS Code, Cursor, Gemini CLI, OpenAI Codex, Goose, Kiro 등 다수의 에이전트 클라이언트가 채택했다([Client Showcase](https://agentskills.io/clients)). AWS도 Bedrock AgentCore Harness에서 같은 포맷을 지원한다([AgentCore Harness — Skills](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-skills.html)). 즉 스킬은 특정 벤더 기능이 아니라, 우리 플랫폼이 "재사용 가능 capability"를 정의하는 **이식 가능한 패키징 표준**의 후보다. 이 장은 그 채택 논거와 운영 설계를 다룬다.

## 핵심 개념

### SKILL.md와 디렉터리 구조

스킬의 최소 단위는 `SKILL.md` 하나를 담은 폴더다. YAML frontmatter에 `name`과 `description`(최소 필수)을 선언하고, 본문에 절차 지시를 쓴다. 스크립트·참조 문서·템플릿을 함께 번들할 수 있다([agentskills.io Specification](https://agentskills.io/specification)).

```
monthly-report/
├── SKILL.md          # 필수: metadata + 지시문
├── scripts/          # 선택: 실행 가능한 코드 (결정적 로직)
├── references/       # 선택: 상세 참조 문서 (필요 시에만 로드)
└── assets/           # 선택: 템플릿, 리소스
```

### Progressive disclosure — 3단계 로딩

스킬의 핵심 설계는 progressive disclosure다. 세 단계로 로드된다([agentskills.io](https://agentskills.io/), [Anthropic Engineering](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)).

1. **Discovery(시작 시)** — 에이전트는 설치된 모든 스킬의 **metadata(name/description)만** system prompt에 로드한다. 스킬이 언제 관련 있는지 판단할 최소 정보다.
2. **Activation(트리거 시)** — 작업이 description과 매칭되면 그때 `SKILL.md` 전체 지시문을 컨텍스트에 읽어 들인다.
3. **Execution(실행 시)** — 지시를 따르며 필요한 경우에만 번들 스크립트를 실행하거나 참조 파일을 추가 로드한다.

이 구조 덕분에 수십~수백 개 스킬을 상시 장착해도 컨텍스트 상주 비용은 metadata 목록 수준에 머문다. 플랫폼 관점에서 이것은 "capability 카탈로그를 크게 유지해도 per-turn 토큰 비용이 선형으로 늘지 않는다"는 뜻이며, 카탈로그 셀프서비스([/11-builder-agent/catalog-registry](/11-builder-agent/catalog-registry))의 확장성 전제가 된다.

여기서 나오는 실전 작성 규칙 하나: **상호배타적(mutually exclusive)인 컨텍스트는 별도 참조 파일로 분리하라**. 예컨대 "국내 정산"과 "해외 정산" 절차가 한 작업에서 동시에 쓰일 일이 없다면, 둘 다 `SKILL.md` 본문에 넣지 말고 `references/domestic.md`, `references/overseas.md`로 쪼갠다. Activation 시점에 둘 중 하나만 로드되므로 토큰이 절약되고 무관한 지시로 인한 오염도 줄어든다([Anthropic Engineering](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)).

### 지식 + 결정적 코드: 채택 논거의 핵심

스킬은 "지시문 문서"가 아니라 **지식과 코드의 하이브리드 패키지**다. Anthropic 엔지니어링 문서의 PDF 예시가 보여주는 분리 기준은 이렇다.

- **LLM 추론으로 할 일**: 사용자의 모호한 요청 해석, 어떤 폼 필드에 어떤 값을 넣을지 판단, 예외 상황 대응.
- **결정적 코드로 할 일**: PDF에서 폼 필드 목록을 추출하는 파싱 — 스크립트를 실행하고 결과만 컨텍스트로 받는다. 코드 자체는 컨텍스트에 올라가지 않는다.

이 분리가 우리 플랫폼이 스킬을 **재사용 가능 capability의 패키징 표준으로 채택하는 논거**다. 대안들과 비교하면:

- system prompt 하드코딩은 지식은 담지만 코드를 담지 못하고, 상시 컨텍스트 비용을 낸다.
- 순수 툴/API는 코드는 담지만 "언제, 어떤 순서로, 어떤 예외 처리와 함께 쓰는가"라는 절차 지식을 담지 못한다.
- 스킬은 둘을 하나의 버전 관리 가능한 폴더에 담고, progressive disclosure로 비용을 통제하며, 오픈 스탠다드라 Claude Code·AgentCore Harness·기타 클라이언트 간에 **같은 아티팩트를 재사용**할 수 있다.

Builder Agent가 생성하는 에이전트에 조직 표준 절차를 주입할 때, 스킬은 "생성물마다 프롬프트를 복제"하는 대신 "검증된 스킬을 참조로 부착"하는 모델을 가능하게 한다. 스킬 저장소가 single source of truth가 되고, 스킬 버전 업데이트가 모든 에이전트에 일괄 반영된다.

### 스킬 vs 툴 vs MCP 서버

세 개념은 대체재가 아니라 서로 다른 층이다.

- **툴(tool)**: 에이전트가 "할 수 있는 것" — 실행 가능한 동작의 선언(스키마 + 구현). 예: `query_database`, `send_email`.
- **스킬(skill)**: "하는 방법"의 지식 + 보조 스크립트 — 툴들을 어떤 절차·규칙·순서로 조합하는지, 그리고 결정적 처리를 맡을 코드. 예: "우리 조직의 월간 정산 리포트 작성 절차".
- **MCP(Model Context Protocol)**: 툴·리소스를 에이전트에 **연결하는 프로토콜/전송 계층**. 무엇을 할 수 있는지의 원격 공급 표준이지, 절차 지식의 포맷이 아니다([MCP specification](https://modelcontextprotocol.io/specification/latest)).

한 문장으로: MCP 서버가 손(할 수 있는 것)을 공급하고, 스킬이 손을 쓰는 법(방법 지식)을 공급하며, 둘 다 아닌 판단은 모델 추론에 남긴다.

### AgentCore Harness의 스킬 지원

AWS Bedrock AgentCore Harness(관리형 에이전트 루프)는 AgentSkills.io 표준 스킬을 **4가지 소스**에서 부착할 수 있다: 사전 구축된 **AWS Skills**, 임의의 **Git 리포지터리**(예: Anthropic skills repo), **Amazon S3**, 세션 **filesystem** 경로. harness 생성 시 기본값으로 선언하거나, **per-invocation override**로 호출 시점에 교체할 수 있다([AgentCore Harness — Skills](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-skills.html), [InvokeHarness API](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_InvokeHarness.html)).

```jsonc
// CreateHarness의 skills 필드 — HarnessSkill union (path | s3 | git | awsSkills)
"skills": [
  { "path": "./skills/my-local-skill" },
  { "s3": { "uri": "s3://my-bucket/skills/my-skill/" } },
  { "git": { "url": "https://github.com/example/skills-repo", "path": "subdir/my-skill" } },
  { "awsSkills": {} }
]
```

> ⚠️ 비공식 출처 기반 — 위 필드 형태와 아래 override 동작은 로컬 플러그인 참조 문서(`amazon-bedrock` skill의 agentcore-harness 레퍼런스)에서 API 모델 기준으로 검증된 내용이다. 배포 전 [CreateHarness API reference](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateHarness.html)에서 최신 스키마를 교차확인하라.

여기서 보안상 결정적인 사실: **invoke-time skill은 같은 이름의 harness 기본 스킬을 override한다.** 그리고 invocation의 `skills` 필드를 invocation 단위로 제한하는 IAM condition key는 없다. 따라서 caller-supplied `skills` 필드는 **신뢰 경계(trust boundary)**다 — 호출자 입력을 `InvokeHarness`로 그대로 전달하는 애플리케이션은, 플랫폼이 검증한 기본 스킬을 공격자가 임의 Git repo의 악성 스킬(스크립트 포함)로 바꿔치기하는 경로를 열어 준다. 스킬은 세션마다 소스에서 fetch되어 **trusted input으로 에이전트 컨텍스트에 주입**되므로, 애플리케이션 계층에서 caller-supplied `skills` 필드를 strip하거나 allowlist해야 한다([AgentCore Harness security](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-security.html)). 생성형 에이전트 전반의 가드레일 설계는 [/11-builder-agent/generated-agent-guardrails](/11-builder-agent/generated-agent-guardrails)에서 이어서 다룬다.

### 비개발자 저작 경로와 검증

스킬 저작의 진입 장벽은 낮다 — `SKILL.md`는 마크다운이고, 도메인 전문가(정산 담당자, 법무 검토자)가 자신의 절차를 직접 기술할 수 있다. 플랫폼은 이 경로를 카탈로그 셀프서비스([/11-builder-agent/catalog-registry](/11-builder-agent/catalog-registry))로 열어 주되, **스킬이 실행 가능한 스크립트를 포함할 수 있다는 사실** 때문에 게시 전 검증을 코드 리뷰 수준으로 취급해야 한다. Anthropic 공식 가이드도 "install skills only from trusted sources"를 명시하고, 신뢰도가 낮은 스킬은 코드 의존성과 외부 네트워크 연결을 감사하라고 권고한다([Anthropic Engineering](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)). 스킬 공급망은 MCP 서버 공급망과 같은 위협 모델을 공유한다 — [/12-security-korea/mcp-supply-chain](/12-security-korea/mcp-supply-chain) 참조.

## 결정 표

**무엇으로 패키징할 것인가:**

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 외부 시스템에 대한 새로운 동작(조회/변경)이 필요 | **툴** (직접 정의 또는 MCP) | 실행 가능한 동작은 스키마 있는 툴이 정확·감사 가능 | 절차 지식은 담지 못함 — 스킬로 보완 |
| 여러 팀이 쓰는 원격 시스템 연동을 표준화 | **MCP 서버** | 프로토콜 레벨 재사용, 중앙 배포·인증 | 서버 운영 부담, 공급망 검증 필요([mcp-supply-chain](/12-security-korea/mcp-supply-chain)) |
| 툴 조합 절차·조직 규칙·도메인 워크플로 | **스킬** | 지식+스크립트를 버전 관리 폴더로, progressive disclosure로 토큰 통제 | 트리거는 description 매칭에 의존 — 발동 정확도 관리 필요 |
| 결정적·반복적 데이터 처리(파싱, 변환, 검증) | **스킬 내 스크립트** | LLM 추론보다 정확·저렴·재현 가능 | 스크립트는 코드 리뷰 대상이 됨 |
| 모든 턴에 항상 적용될 짧은 원칙(톤, 금지사항) | **system prompt** | 항상 필요하면 progressive disclosure 이득이 없음 | 길어지면 준수율 저하 — 절차는 스킬로 이관 |
| 한 작업에서 동시에 쓰이지 않는 변형 절차들 | **스킬 + 분리된 references/** | 상호배타 컨텍스트는 필요한 쪽만 로드 | 파일 수 증가, 참조 링크 관리 |
| 스킬 배포 소스 (AgentCore Harness) | Git(리뷰 게이트 통과본 태그) 또는 S3(불변 버전 경로) | 소스 자체가 배포 파이프라인의 신뢰 앵커 | filesystem 소스는 세션 내 임시 실험용으로 한정 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 스킬이 있는데 발동하지 않음 | `description`이 사용자 요청 어휘와 매칭 안 됨 (Discovery 단계에서 name/description만 보임) | 트리거 시나리오 셋으로 발동률 측정; 세션 로그에서 스킬 로드 이벤트 확인 | description에 트리거 조건·동의어를 명시적으로 기술, 발동 평가를 CI에 포함 |
| 무관한 작업에 스킬이 과발동 | description이 과도하게 광범위 | 발동 로그를 작업 유형별로 교차 분석 | description 범위 축소, "~에는 사용하지 않음" 부정 조건 추가 |
| 컨텍스트 토큰이 스킬 수에 비례해 증가 | progressive disclosure를 깨고 전체 본문을 상주 로드하는 자체 구현 | 시작 직후 프롬프트 토큰 수를 스킬 개수별로 회귀 | 표준 3단계 로딩(metadata → SKILL.md → references) 준수 |
| 같은 스킬인데 실행마다 결과가 다름 | 결정적 처리(파싱·계산)를 지시문으로만 기술해 LLM이 추론으로 수행 | 동일 입력 반복 실행의 출력 diff | 해당 단계를 `scripts/`의 실행 코드로 이관 |
| 스킬 활성화 후 오답·혼선 증가 | 상호배타 변형 절차가 SKILL.md 본문에 병렬 기술되어 함께 로드됨 | 활성화 시 로드되는 토큰량·본문 구조 검토 | 변형별 `references/` 파일로 분리, 본문에는 분기 기준만 |
| Harness 에이전트가 검증 안 된 스킬을 실행 | caller-supplied `skills` 필드가 그대로 `InvokeHarness`에 전달되어 기본 스킬을 override | 애플리케이션 계층 코드에서 skills 필드 처리 경로 감사; CloudTrail로 invocation 파라미터 확인 | 앱 계층에서 `skills` strip/allowlist ([harness security](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-security.html)) |
| 배포된 스킬의 내용이 소리 없이 바뀜 | Git branch(HEAD)나 mutable S3 경로를 소스로 지정 | 스킬 fetch 소스의 참조 방식 점검 | 태그/커밋 고정, S3는 버전 경로 + 버킷 버저닝, 해시 검증 |
| 스킬 스크립트가 외부로 데이터 유출 | 게시 전 코드 리뷰 없이 카탈로그 등록 (스크립트 = 임의 코드) | 스크립트의 네트워크 호출·의존성 정적 분석 | 게시 게이트에 코드 리뷰·의존성 감사 의무화 ([mcp-supply-chain](/12-security-korea/mcp-supply-chain)) |

## 안티패턴

- ❌ 조직 절차를 에이전트마다 system prompt에 복제 → ✅ 절차를 스킬로 1회 패키징하고 에이전트들이 참조·부착 — 저장소가 single source of truth
- ❌ `SKILL.md` 하나에 모든 변형 절차·상세 레퍼런스를 몰아넣기 → ✅ 본문은 분기 기준과 공통 절차만, 상호배타적·상세 내용은 `references/`로 분리해 필요 시 로드
- ❌ "PDF에서 필드를 찾아 읽어라"를 지시문으로 기술 → ✅ 추출은 `scripts/`의 결정적 코드로, LLM에는 판단(어떤 값을 채울지)만 남김
- ❌ description을 한 줄 요약("리포트 도우미")으로 작성 → ✅ 발동 조건·대상 작업·비대상 작업을 담은 트리거 계약으로 작성하고 발동률을 평가로 검증
- ❌ 호출자 입력의 `skills` 필드를 `InvokeHarness`에 pass-through → ✅ 앱 계층에서 strip 또는 allowlist — invoke-time skill이 harness 기본 스킬을 이름으로 override하는 신뢰 경계임을 전제
- ❌ 스킬을 "문서니까" 문서 리뷰 프로세스로만 게시 승인 → ✅ 스크립트 포함 여부와 무관하게 코드 리뷰 + 의존성/네트워크 감사 게이트 적용
- ❌ Git HEAD·mutable S3 prefix를 프로덕션 스킬 소스로 지정 → ✅ 불변 참조(태그/커밋/버전 경로)로 고정하고 변경은 배포 파이프라인으로만
- ❌ 스킬로 툴을 대체하려는 시도(스크립트로 인증·쓰기 작업 수행) → ✅ 권한이 필요한 동작은 툴/Gateway로, 스킬은 방법 지식과 로컬 결정적 처리로 한정

## 계측 (SLI)

스킬을 플랫폼 표준으로 운영한다면 다음을 계측한다. 구체 목표치는 조직 데이터로 설정하라 — 아래는 지표 정의다.

- **발동 정밀도/재현율(activation precision/recall)**: 트리거 시나리오 평가 셋 대비, 발동해야 할 때 발동한 비율과 무관 작업에서 오발동한 비율. 스킬 게시·수정 시 CI에서 회귀 측정.
- **Discovery 오버헤드**: 시작 시 프롬프트에 상주하는 스킬 metadata 토큰 총량과 스킬 개수의 비율. progressive disclosure가 깨지면 이 곡선이 급증한다.
- **Activation 토큰량 분포**: 스킬 활성화 1회당 로드되는 토큰(SKILL.md + references). 상위 outlier는 분리 리팩터링 후보.
- **스크립트 실행 성공률·소요시간**: `scripts/` 실행의 exit code 분포와 latency. 결정적 코드가 흔들리면 스킬 전체 신뢰가 무너진다.
- **스킬 버전 채택 지연(version lag)**: 최신 게시 버전 대비 프로덕션 에이전트들이 참조 중인 버전의 분포 — 보안 패치 전파 속도의 대리 지표.
- **비검증 소스 invocation 비율** (AgentCore Harness): CloudTrail의 `InvokeHarness` 기록에서 caller-supplied `skills` override가 발생한 호출 수. 목표는 0 — 0이 아니면 앱 계층 strip이 뚫린 것이다([harness security](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-security.html)).
- **스킬 기인 실패율**: 에이전트 작업 실패 중 특정 스킬 활성화와 상관된 비율 — 스킬 단위 품질 회귀 탐지.

## 체크리스트

**스킬 저작:**
- [ ] `SKILL.md` frontmatter에 `name`/`description` 선언, description은 발동 조건·비대상까지 담은 트리거 계약으로 작성
- [ ] 상호배타적 컨텍스트를 `references/` 파일로 분리 — 본문에는 분기 기준만
- [ ] 결정적·반복적 처리는 `scripts/`의 실행 코드로 이관, 지시문에는 실행 방법과 결과 해석만
- [ ] 트리거/비트리거 시나리오 평가 셋 작성, 발동 정밀도·재현율 CI 측정

**게시·공급망:**
- [ ] 게시 게이트: 스크립트 코드 리뷰 + 의존성·외부 네트워크 호출 감사 (신뢰 소스 원칙 — [Anthropic 가이드](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills), [/12-security-korea/mcp-supply-chain](/12-security-korea/mcp-supply-chain))
- [ ] 비개발자 저작 경로는 카탈로그 셀프서비스로 열되 같은 게이트를 통과 ([/11-builder-agent/catalog-registry](/11-builder-agent/catalog-registry))
- [ ] 프로덕션 소스는 불변 참조(Git 태그/커밋, S3 버전 경로)로 고정, 스킬 소스는 HTTPS만

**AgentCore Harness 운영 시:**
- [ ] 호출자 입력을 `InvokeHarness`로 전달하는 경로에서 `skills` 필드 strip/allowlist — invoke-time override는 IAM으로 막을 수 없음
- [ ] CloudTrail로 `InvokeHarness` 파라미터 감사, caller-supplied skills override 0건 알람
- [ ] harness 기본 스킬 변경은 버전·엔드포인트 체계로 배포(즉시 롤백 가능), 세부는 [/11-builder-agent/generated-agent-guardrails](/11-builder-agent/generated-agent-guardrails)

**운영:**
- [ ] SLI(발동 정밀도, Discovery 오버헤드, 스크립트 성공률, version lag) 대시보드 구성
- [ ] 스킬 버전 업데이트 시 참조 중인 에이전트 목록에 전파 확인

## 참고

- Anthropic Engineering, *Equipping agents for the real world with Agent Skills* — https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
- Agent Skills 오픈 스탠다드 (개요·스펙·클라이언트 목록) — https://agentskills.io/ , https://agentskills.io/specification
- Anthropic Claude Docs, *Agent Skills overview* — https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- Claude Code, *Skills* — https://code.claude.com/docs/en/skills
- AWS Bedrock AgentCore Harness — 개요 https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness.html · 스킬 https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-skills.html · 보안 https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-security.html
- AgentCore API — CreateHarness https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateHarness.html · InvokeHarness https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_InvokeHarness.html
- Model Context Protocol specification — https://modelcontextprotocol.io/specification/latest
- 관련 장: [/11-builder-agent/catalog-registry](/11-builder-agent/catalog-registry) · [/11-builder-agent/generated-agent-guardrails](/11-builder-agent/generated-agent-guardrails) · [/12-security-korea/mcp-supply-chain](/12-security-korea/mcp-supply-chain)
