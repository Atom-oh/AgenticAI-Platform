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
