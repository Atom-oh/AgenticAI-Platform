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

## 라이브 데모 사이트

브라우저에서 바로 대화할 수 있는 채팅 UI: **https://d1twhttjtzqewp.cloudfront.net/**

아키텍처(퍼블릭 진입점은 CloudFront뿐):

```
브라우저 → CloudFront (E3ETCXKSTRXQAT)
        → API Gateway HTTP API (00l4tzkyqi) ── x-origin-verify 시크릿 헤더 검증
        → Lambda agentic-book-demo-site (예약 동시성 5)
        → InvokeHarness (AgenticBookBuilderDemo-6R0pXEwrY1)
```

- CloudFront가 오리진 요청에 `x-origin-verify` 시크릿 커스텀 헤더를 주입하고, Lambda가 이를 검증한다. API Gateway 엔드포인트를 직접 호출하면 403이다.
- `sessionId`는 브라우저 localStorage에 저장되어 같은 브라우저에서 대화가 이어진다(InvokeHarness `runtimeSessionId` 재사용).
- 소스: `site/lambda_function.py` (HTML UI + `/chat` 프록시 단일 파일).

### 이 경로를 선택한 이유 (겪은 함정)

처음에는 Lambda Function URL(AWS_IAM) + CloudFront OAC 패턴으로 구성했으나, 이 계정의 조직 가드레일이 Function URL 호출(익명·CloudFront 서비스 주체 모두)을 차단해 403 `AccessDeniedException`이 발생했다 — 리소스 정책·OAC 설정이 교과서적으로 맞아도 조직 SCP/RCP가 우선한다. API Gateway 경유(`lambda:InvokeFunction`)로 전환해 해결했다.

### 사이트 정리(teardown)

```bash
aws cloudfront get-distribution-config --id E3ETCXKSTRXQAT   # disable 후 삭제
aws apigatewayv2 delete-api --api-id 00l4tzkyqi --region ap-northeast-2
aws lambda delete-function --function-name agentic-book-demo-site --region ap-northeast-2
aws iam delete-role-policy --role-name AgenticBookDemoSiteLambdaRole --policy-name AgenticBookDemoSitePolicy
aws iam delete-role --role-name AgenticBookDemoSiteLambdaRole
```
