# 빌더 에이전트 데모 — AgentCore Harness

Part 11(`docs/11-builder-agent/`)에서 설명하는 "요구사항 대화 → 에이전트 생성 계획" 흐름을 보여주는 최소 데모다. 코드 없이 **AgentCore Harness**(config-based managed agent loop, Strands 기반)로 배포했다 — 플랫폼에서 Strands SDK로 직접 오케스트레이션 코드를 작성하지 않는다는 결정을 그대로 반영한다.

## 배포된 리소스 (계정 180294183052, ap-northeast-2)

| 리소스 | ID / ARN |
|---|---|
| Harness | `AgenticBookBuilderDemo-6R0pXEwrY1` (`arn:aws:bedrock-agentcore:ap-northeast-2:180294183052:harness/AgenticBookBuilderDemo-6R0pXEwrY1`) |
| 실행 역할 | `AgenticBookBuilderHarnessRole` |
| 모델 | `apac.anthropic.claude-sonnet-4-20250514-v1:0` (APAC geo 추론 프로파일 — 데이터 레지던시 고정, Part 8 참고) |
| Memory | Harness가 자동 생성한 managed memory (`AgenticBookBuilderDemo-*`) |

이 harness는 **개념 데모**다: 실제로 하위 에이전트를 배포하지는 않고, 요구사항 대화 후 배포 계획(어떤 툴/스킬/AgentCore 구성요소가 필요한지)만 제안하도록 시스템 프롬프트에 명시했다.

## 실행

```bash
python3 -m pip install --user boto3
python3 invoke.py "계약서 검토 에이전트를 만들고 싶어요"
```

같은 `session_id`를 두 번째 인자로 넘기면 대화가 이어진다.

## 배포 재현

```bash
aws iam create-role --role-name AgenticBookBuilderHarnessRole \
  --assume-role-policy-document file://trust-policy.json
aws iam put-role-policy --role-name AgenticBookBuilderHarnessRole \
  --policy-name AgenticBookBuilderHarnessExecPolicy \
  --policy-document file://execution-policy.json
aws bedrock-agentcore-control create-harness --cli-input-json file://create-harness.json --region ap-northeast-2
# get-harness로 status가 READY가 될 때까지 폴링 후 invoke.py 실행
```

### 함정 (직접 겪은 것)

- `create-harness`가 만드는 내부 리소스는 harness ARN이 아니라 **agent runtime ARN**(`runtime/*`)에서 role을 assume한다. 신뢰정책의 `aws:SourceArn` ArnLike 조건에 `harness/*`만 넣으면 `CREATE_FAILED`(Role validation failed)가 난다 — `runtime/*`도 함께 넣어야 한다.
- `delete-harness`는 비동기다. 같은 이름으로 즉시 재생성하면 managed memory가 아직 `DELETING` 상태라 `Memory with name ... already exists` 오류가 난다. 완전히 사라질 때까지 폴링 후 재시도해야 한다.
- 실행 역할에 `bedrock-agentcore:ListEvents`/`CreateEvent` 등 memory 관련 액션이 없으면 `InvokeHarness`가 스트림 중 `runtimeClientError`(AccessDeniedException)로 실패한다. `bedrock:InvokeModel`만으로는 부족하다.

## 정리(teardown)

```bash
aws bedrock-agentcore-control delete-harness \
  --harness-id AgenticBookBuilderDemo-6R0pXEwrY1 --region ap-northeast-2
# memory가 완전히 삭제된 뒤:
aws iam delete-role-policy --role-name AgenticBookBuilderHarnessRole \
  --policy-name AgenticBookBuilderHarnessExecPolicy
aws iam delete-role --role-name AgenticBookBuilderHarnessRole
```

## 라이브 데모 사이트 — 플랫폼 워크스페이스

브라우저에서 바로 쓰는 워크스페이스 UI: **https://d1twhttjtzqewp.cloudfront.net/**

Amazon Quick 스타일의 3개 흐름을 갖춘 미니 플랫폼이다:

| 흐름 | 동작 | 책의 근거 |
|---|---|---|
| **만들기** | 빌더 에이전트와 요구사항 대화 → "스펙으로 변환" → 검토 후 카탈로그 등록 | Part 11 요구사항 대화 → spec → 카탈로그 |
| **사용하기** | 카탈로그에서 에이전트 선택 → 채팅. 에이전트별 세션 유지 | Part 11 카탈로그와 레지스트리 |
| **데이터소스** | 텍스트 코퍼스 등록 → 에이전트에 연결 → 근거 기반 답변 | Part 6 CAG(소규모 코퍼스는 프리로딩) |

아키텍처(퍼블릭 진입점은 CloudFront뿐):

```
브라우저 → CloudFront (E3ETCXKSTRXQAT)
        → API Gateway HTTP API (00l4tzkyqi) ── x-origin-verify 시크릿 헤더 검증
        → Lambda agentic-book-demo-site (예약 동시성 5)
        ├→ DynamoDB agentic-book-demo-registry (에이전트/데이터소스 레지스트리)
        └→ InvokeHarness (AgenticBookBuilderDemo-6R0pXEwrY1)
```

핵심 설계 결정: **"에이전트 생성"이 실제 AgentCore 리소스를 만들지 않는다.** 에이전트는 DynamoDB에 저장된 config(이름·시스템 프롬프트·연결 데이터소스)이고, 실행은 공유 Harness 하나에 `InvokeHarness`의 per-invocation `systemPrompt` override로 이뤄진다 — 생성 즉시 사용 가능하고, 비용·남용 위험이 없으며, 책 Part 11의 "카탈로그 config 기반 골든 패스" 개념을 그대로 보여준다. 데이터소스는 최대 20,000자 텍스트를 시스템 프롬프트에 프리로딩하는 CAG 방식이다(대규모는 Bedrock Knowledge Bases로 확장하는 것이 정석 — Part 6 참고).

### 플랫폼 통제면 (v2 — 비판적 재설계)

첫 버전은 사용자 흐름만 있어 "플랫폼" 데모로는 부족했다. 이 책의 통증점 기준으로 다음 통제를 추가했다:

| 통제 | 구현 | 책의 근거 |
|---|---|---|
| 차등 승인 게이트 | Tier 1(읽기 전용)은 평가 통과 시 자동 승인, Tier 2는 승인 대기 | Part 11 골든 패스 |
| evals-as-gate | 생성 시 스모크 평가 자동 실행, 실패하면 무조건 승인 대기 | Part 3/11 |
| 토큰 계량·차지백 | 에이전트별 호출 수·입출력 토큰·추정 비용 누적(DynamoDB ADD) | Part 8 |
| 예산 서킷 브레이커 | 에이전트별 토큰 예산(기본 50,000) 소진 시 429로 차단, 운영 탭에서 증액 | Part 8 |
| 툴 노출 통제 | 모든 에이전트 호출에 서버가 `allowedTools` 차단 강제(빌트인 shell/file 미노출) | Part 9 |
| 메모리 스코핑 | `actorId`를 에이전트/사용자별 분리 — 실제로 겪은 managed memory 세션 간 누출을 수정 | Part 7 |

"⚙️ 플랫폼 운영" 탭이 플랫폼 엔지니어 뷰다: 승인 대기 검토(스모크 평가 결과 포함), 에이전트별 사용량·비용·예산 바, 예산 증액. 데모라 인증 없이 열려 있다 — 실서비스라면 이 탭이 별도 권한이다.

### AgentCore Agent Registry 통합 (v3)

거버넌스 상태의 정본을 자체 DynamoDB에서 **AgentCore Agent Registry**(Preview, us-east-1 `b2hOSZL4eOhDXAyk`)로 이관했다:

- 에이전트 생성 → `CreateRegistryRecord`(CUSTOM descriptor) → `SubmitRegistryRecordForApproval` → `PENDING_APPROVAL`
- Tier 1 + 평가 통과 → 자동 `APPROVED`(사유 기록) / 운영 탭 승인·거부 → `UpdateRegistryRecordStatus`(사유 필수)
- **모든 승인/거부가 control-plane API라 CloudTrail에 주체·시각·사유가 자동 감사 기록**된다 — Codex 리뷰 gap #4(감사 로그 부재)를 관리형으로 해소
- DynamoDB는 런타임 config(프롬프트·데이터소스·usage·예산)와 상태 캐시로 유지, `registryRecordArn`을 항목에 저장

한계(정직하게): ① Registry는 Preview이며 ap-northeast-2 미지원 → 크로스리전 control plane 사용(메타데이터만 이동) ② 데모에 사용자 인증이 없으므로 CloudTrail 주체가 전부 Lambda 역할 하나로 찍힘 — **사람 단위 감사는 여전히 SSO(P0)가 전제** ③ 자동 동기화가 아니라 앱 코드가 이중 기록(registry 실패 시 로컬 진행 + 로그).

### 추가로 겪은 함정 (v2)

- **managed memory의 actor 미분리 누출**: 모든 InvokeHarness가 기본 actor를 공유하면 장기 기억이 에이전트 간에 넘어온다(신규 에이전트 평가 응답에 이전 빌더 대화의 온보딩 문맥이 섞여 나옴). `actorId` override로 격리 — Part 7 memory-retrieval-scoping이 경고하는 그대로.
- **API Gateway HTTP API 30초 하드 타임아웃**: Harness 턴이 30초를 넘으면 클라이언트에 503. 대부분의 턴은 3~20초라 드물지만, 장문 생성 시 발생할 수 있다(재시도 안내). Function URL이 조직 정책으로 막힌 환경에서의 트레이드오프.
- **DynamoDB UpdateItem 권한 누락**: 계량 도입 시 IAM에 `dynamodb:UpdateItem` 추가 필요 — 최소권한 정책은 기능 추가 때마다 함께 진화해야 한다.

데모 한도: 에이전트 20개, 데이터소스 10개(각 20,000자), 메시지 2,000자, Lambda 예약 동시성 5.

- 소스: `site/lambda_function.py` (SPA + API 단일 파일)
- API: `GET/POST /api/agents`, `DELETE /api/agents/{id}`, `GET/POST /api/datasources`, `DELETE /api/datasources/{id}`, `POST /api/chat`, `POST /api/builder`, `POST /api/spec`

### 겪은 함정

- **Lambda Function URL + CloudFront OAC 패턴이 이 계정에서 403**: 조직 가드레일(SCP/RCP)이 Function URL 호출(익명·CloudFront 서비스 주체 모두)을 차단했다. 리소스 정책·OAC가 교과서적으로 맞아도 조직 정책이 우선한다. API Gateway 경유(`lambda:InvokeFunction`)로 전환해 해결.
- **퍼블릭 Function URL 잔존은 Security Hub Lambda.1을 트리거**: 전환 후 남아 있던 AuthType NONE URL이 "Lambda function policies should prohibit public access" finding을 발생시켰다. URL을 삭제하면 PASSED로 전환된다.
- **Bedrock 스로틀**: 짧은 간격의 연속 호출에서 `ServiceUnavailableException: Too many connections`가 간헐 발생 — 데모 UI는 단건 요청이라 영향이 적지만, 부하 시 재시도/백오프가 필요하다(가이드북 Part 8 참고).

### 사이트 정리(teardown)

```bash
aws cloudfront get-distribution-config --id E3ETCXKSTRXQAT   # disable 후 삭제
aws apigatewayv2 delete-api --api-id 00l4tzkyqi --region ap-northeast-2
aws lambda delete-function --function-name agentic-book-demo-site --region ap-northeast-2
aws dynamodb delete-table --table-name agentic-book-demo-registry --region ap-northeast-2
aws iam delete-role-policy --role-name AgenticBookDemoSiteLambdaRole --policy-name AgenticBookDemoSitePolicy
aws iam delete-role --role-name AgenticBookDemoSiteLambdaRole
```
