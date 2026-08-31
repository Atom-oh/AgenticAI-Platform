---
title: Runtime 배포 계약
description: AgentCore Runtime을 실제로 배포하는 7단계 워크플로우와 IAM/네트워크/시크릿/로그 암호화 보안 계약, 업데이트·롤백 전략을 다룬다.
outline: [2, 3]
---

# Runtime 배포 계약

::: tip 이 장에서 얻는 것
- AgentCore Runtime 배포의 실제 7단계 워크플로우(프로토콜 선택 → 컨테이너 빌드 → ECR 푸시 → `create-agent-runtime` → `create-agent-runtime-endpoint` → `READY` 대기 → 헬스체크 확인)와 각 단계의 AWS CLI 명령
- `--role-arn` 실행 역할을 어떻게 최소 권한으로 스코프하고, confused deputy 방지 조건(`aws:SourceArn`/`aws:SourceAccount`)을 신뢰 정책에 어떻게 넣는지
- `--network-configuration`의 `PUBLIC` vs `VPC` 모드가 배포 커맨드 수준에서 어떻게 달라지는지, 그리고 왜 프로덕션은 기본값을 그대로 쓰면 안 되는지
- 시크릿을 `--environment-variables`에 넣지 말아야 하는 이유와 Secrets Manager로 대체하는 패턴
- CloudWatch 로그 그룹 KMS 암호화 설정
- rolling update와 alias 기반 blue/green 전환, 롤백 절차
- per-request(stateless) vs per-session(stateful) lifecycle 모델이 Memory 서비스 의존성에 미치는 영향
:::

이 장은 **배포 실행과 계약의 스펙**에 집중한다. AgentCore Runtime의 아키텍처 개념(microVM 격리, 세션 모델, 프레임워크 어댑터)은 [Runtime 심화](/10-agentcore/runtime-deep-dive)에서 다룬다. 여기서는 "무엇을 어떤 순서로, 어떤 플래그로 실행해야 하는가"만 다룬다.

## 왜 문제가 되는가

AgentCore Runtime은 컨테이너를 넘기면 알아서 다 해주는 PaaS가 아니다. 컨테이너 계약(포트, 헬스체크 경로, ARM64 아키텍처)을 지키지 못하면 런타임이 시작조차 되지 않고, `create-agent-runtime`과 `create-agent-runtime-endpoint`를 순서를 지키지 않고 호출하면 런타임이 만들어져도 트래픽을 받을 수 없다. 여기에 더해 배포 커맨드 자체가 보안 계약의 상당 부분을 짊어진다 — `--role-arn`이 곧 IAM 최소 권한 경계이고, `--network-configuration`이 곧 네트워크 노출 범위이고, `--environment-variables`에 무엇을 넣는지가 곧 시크릿 유출 여부를 가른다. 즉 "배포 명령을 정확히 아는 것"이 이 표면에서는 곧 "보안 설정을 정확히 하는 것"이다. 플랫폼 엔지니어가 이 계약을 CLI 플래그 단위로 정확히 알아야 하는 이유가 여기에 있다.

## 핵심 개념

### 컨테이너 계약은 배포 이전 전제조건이다

프로토콜별 포트·헬스체크 경로·페이로드 형식(HTTP/MCP/A2A/AG-UI)은 아키텍처 결정이므로 [Runtime 심화](/10-agentcore/runtime-deep-dive)에서 다룬다. 이 장에서 고정해 둘 것은 하나뿐이다 — **어떤 프로토콜을 고르든 컨테이너는 ARM64(Graviton)여야 하고, x86 이미지는 시작되지 않는다.** ECR에 푸시하기 전에 반드시 `--platform linux/arm64`로 빌드했는지 확인해야 한다.

### 배포 계약은 4개의 리소스 생성으로 완성된다

AgentCore Runtime을 실제로 트래픽을 받을 수 있는 상태로 만들려면 최소 두 개의 컨트롤 플레인 리소스가 필요하다 — Runtime(컨테이너 이미지와 역할, 프로토콜, 네트워크/인가 설정을 담는 리소스)과 Runtime Endpoint(실제로 호출 가능한 진입점, 버전 또는 alias를 가리킴). Endpoint 없이 Runtime만 만들면 컨테이너는 존재하지만 어떤 요청도 받을 수 없다.

### 실행 역할은 배포 명령의 일부이자 보안 경계다

`--role-arn`은 단순한 파라미터가 아니라 "이 에이전트가 어떤 AWS 리소스를 만지는가"를 정의하는 경계다. 이 역할의 신뢰 정책 설계(특히 confused deputy 방지 조건)는 [confused deputy 문제](/09-authorization/confused-deputy) 장에서 원리 수준으로 다룬다. 이 장은 그 조건을 실제로 `create-agent-runtime` 호출과 역할 신뢰 정책 JSON에 어떻게 반영하는지에 집중한다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 프로덕션 워크로드, VPC 내부 리소스(RDS, 내부 API) 접근 필요 | `networkMode: VPC` | 아웃바운드를 서브넷/보안그룹으로 제한, 인터넷 노출 없음 | 서브넷·보안그룹·필요 시 VPC 엔드포인트를 미리 설계해야 함 — [Egress 제어](/09-authorization/egress-control) 참조 |
| 격리된 계정에서의 개발/테스트 | `networkMode: PUBLIC` | 네트워크 설정 없이 즉시 기동 | 아웃바운드가 통제되지 않음 — 프로덕션 금지 |
| 여러 에이전트가 서로 다른 다운스트림 접근 범위를 가짐 | 에이전트별 별도 실행 역할 | 한 역할이 침해돼도 피해 범위가 해당 에이전트로 한정 | 역할 수가 늘어나 IAM 관리 부담 증가 |
| 무상태 Q&A, 단발 도구 호출 | Per-request lifecycle | Memory 서비스 불필요, 인스턴스 재사용 없이 단순 | 멀티턴 컨텍스트 유지 불가 |
| 멀티턴 대화, 세션 내 컨텍스트 누적 | Per-session lifecycle | 세션 동안 상태 유지 | Memory 서비스 연동 필수 — [Part 7](/07-memory/) |
| 컨테이너 이미지를 조금씩 바꾸는 일반 배포 | Rolling update(기본) | 별도 조작 없이 기본 동작으로 처리 | 배포 중 신버전/구버전이 혼재하는 짧은 창이 생김 |
| 즉시 전면 롤백이 필요한 배포, 트래픽 절단 전환 필요 | Alias 기반 blue/green | 신버전을 별도로 기동해 검증 후 alias만 전환 — 실패 시 alias를 되돌리면 즉시 원복 | 신버전 인스턴스를 이중으로 띄우는 동안 비용 증가 |

## 배포 워크플로우 (7단계)

아래는 실제 AWS CLI 명령 기준의 절차다.[^workflow] 순서를 바꾸면 실패한다 — 특히 Step 1은 Step 2보다 먼저, Step 5는 Step 4보다 반드시 나중이어야 한다.

**Step 1 — 프로토콜 선택.** HTTP/MCP/A2A/AG-UI 중 하나. 프로토콜별 포트·헬스체크 계약은 [Runtime 심화](/10-agentcore/runtime-deep-dive)에서 다룬다. 확신이 없으면 HTTP로 시작한다.

**Step 2 — ARM64 컨테이너 빌드.**

```bash
docker buildx build --platform linux/arm64 -t my-agent:latest .
```

**Step 3 — ECR 푸시.**

```bash
aws ecr get-login-password --region <region> \
  | docker login --username AWS --password-stdin <account-id>.dkr.ecr.<region>.amazonaws.com

docker tag my-agent:latest <account-id>.dkr.ecr.<region>.amazonaws.com/my-agent:latest
docker push <account-id>.dkr.ecr.<region>.amazonaws.com/my-agent:latest
```

**Step 4 — Runtime 생성.**

```bash
aws bedrock-agentcore-control create-agent-runtime \
  --agent-runtime-name my-agent \
  --agent-runtime-artifact '{
      "containerConfiguration": {
        "containerUri": "<account-id>.dkr.ecr.<region>.amazonaws.com/my-agent:latest"
      }
    }' \
  --role-arn arn:aws:iam::<account-id>:role/my-agent-runtime-role \
  --network-configuration '{
      "networkMode": "VPC",
      "networkModeConfig": {
        "vpcConfig": {
          "subnets": ["subnet-aaa", "subnet-bbb"],
          "securityGroups": ["sg-ccc"]
        }
      }
    }' \
  --authorizer-configuration '{
      "customJWTAuthorizer": {
        "discoveryUrl": "https://<issuer>/.well-known/openid-configuration",
        "allowedAudience": ["<client-id>"]
      }
    }' \
  --protocol-configuration '{"serverProtocol": "HTTP"}'
```

`--protocol-configuration`의 값은 Step 1에서 고른 프로토콜과 정확히 일치해야 한다 — HTTP/MCP/A2A는 그대로, AG-UI는 API 값 `AGUI`로 매핑된다.[^workflow] `--network-configuration`과 `--authorizer-configuration`의 상세 설계는 아래 [보안 설정](#보안-설정) 절에서 다룬다.

**Step 5 — Runtime Endpoint 생성.** Runtime이 만들어졌다고 트래픽을 받을 수 있는 것이 아니다 — Endpoint가 있어야 호출 가능하다.

```bash
aws bedrock-agentcore-control create-agent-runtime-endpoint \
  --agent-runtime-id <id-from-step-4> \
  --name production
```

**Step 6 — `READY` 상태 대기.** Endpoint는 생성 직후 `CREATING` 상태이며, `READY`로 전환되기 전에는 호출할 수 없다.

```bash
aws bedrock-agentcore-control get-agent-runtime-endpoint \
  --agent-runtime-id <id> \
  --endpoint-id <endpoint-id> \
  --query 'status'
```

**Step 7 — 헬스체크 확인.** `READY` 전환 후에도 반드시 헬스체크가 실제로 통과하는지 별도로 확인한다.

```bash
aws bedrock-agentcore-control get-agent-runtime-endpoint \
  --agent-runtime-id <id> \
  --endpoint-id <endpoint-id>
```

응답에서 상태가 `READY`이고 헬스체크가 정상인지 확인한다. `/ping`이 `{"status":"Healthy"}` 또는 `{"status":"HealthyBusy"}`를 반환하지 않으면(MCP 프로토콜은 예외) 트래픽이 라우팅되지 않는다.[^workflow]

## 보안 설정

### IAM 실행 역할과 confused deputy 방지

`--role-arn`으로 전달하는 역할은 **최소 권한**으로 스코프해야 하며, 여러 에이전트가 서로 다른 접근 범위를 갖는다면 역할을 공유하지 않고 에이전트별로 분리한다.[^workflow]

신뢰 정책에는 `aws:SourceArn`과 `aws:SourceAccount` 조건을 반드시 넣는다. 이 조건이 왜 confused deputy를 막는지, 그리고 이 패턴이 IAM 조건 키 기반 방어의 표준형인 이유는 [confused deputy 문제](/09-authorization/confused-deputy) 장에서 원리부터 다룬다. 여기서는 실제 정책 형태만 확인한다.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "bedrock-agentcore.amazonaws.com" },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": {
          "aws:SourceAccount": "<account-id>"
        },
        "ArnLike": {
          "aws:SourceArn": "arn:aws:bedrock-agentcore:<region>:<account-id>:runtime/*"
        }
      }
    }
  ]
}
```

이 조건이 없으면 다른 계정의 AgentCore Runtime이나 무관한 리소스가 이 역할을 가로채 assume할 수 있는 여지가 생긴다 — confused deputy 공격의 정확한 진입점이다.

### VPC 모드 vs PUBLIC 모드

Step 4의 `--network-configuration`에서 `networkMode`를 `PUBLIC` 또는 `VPC`로 지정한다. PUBLIC은 네트워크 설정 없이 즉시 기동되지만 아웃바운드가 통제되지 않으며 개발/테스트 용도로만 써야 한다.[^workflow] 프로덕션은 VPC 모드로 서브넷·보안그룹을 지정해야 하고, VPC 모드에서의 아웃바운드 설계(Network Firewall 도메인 allowlist, VPC endpoint policy, 2026년 S3 게이트웨이 엔드포인트 자동 프로비저닝 변경, ECR Public NAT 게이트웨이 함정)는 이미 [Egress 제어](/09-authorization/egress-control) 장에서 상세히 다룬다 — 이 장에서는 배포 커맨드에 반영하는 지점(Step 4의 `vpcConfig`)만 확인하고 그 장으로 넘긴다.

### 인가(authorizer) 설정

프로덕션 런타임은 `--authorizer-configuration` 없이 배포해서는 안 된다 — 인증되지 않은 엔드포인트는 그 자체로 보안 위험이다.[^workflow] Step 4 예시의 `customJWTAuthorizer`처럼 OIDC discovery URL과 허용 audience를 지정해 인바운드 요청을 검증한다.

### 시크릿 관리

`--environment-variables`는 `get-agent-runtime` 호출로 그대로 조회 가능한 평문이다. API 키, DB 자격증명, 서명 키 등 시크릿을 여기 넣으면 그 값을 읽을 수 있는 모든 principal에게 노출된다.[^workflow] 시크릿은 AWS Secrets Manager에 저장하고, 에이전트 코드가 실행 시점에 실행 역할의 권한으로 조회하도록 구성한다. `--environment-variables`는 feature flag, 리전 오버라이드, 로그 레벨 같은 비민감 설정에만 사용한다.

```bash
aws bedrock-agentcore-control create-agent-runtime \
  --agent-runtime-name my-agent \
  --agent-runtime-artifact '{...}' \
  --role-arn arn:aws:iam::<account-id>:role/my-agent-runtime-role \
  --environment-variables '{"LOG_LEVEL": "INFO", "FEATURE_FLAG_X": "true"}'
```

시크릿 값 자체를 코드에서 직접 fetch해 LLM 컨텍스트나 로그에 노출시키지 않는 런타임 참조 패턴은 `aws-secrets-manager` 스킬의 dynamic reference 가이드를 따른다.

### CloudWatch 로그 암호화

AgentCore Runtime은 요청/응답 페이로드를 CloudWatch로 자동 전송하며, 여기에는 PII가 포함될 수 있다.[^workflow] 로그 그룹 `/aws/bedrock-agentcore/runtimes/<agent-id>`는 반드시 KMS 키로 암호화한다.

```bash
aws logs associate-kms-key \
  --log-group-name /aws/bedrock-agentcore/runtimes/<agent-id> \
  --kms-key-id arn:aws:kms:<region>:<account-id>:key/<key-id>
```

보존 기한도 함께 설정한다 — 무한 보존은 규정 준수 관점에서도, 침해 시 노출 범위 관점에서도 불리하다.

```bash
aws logs put-retention-policy \
  --log-group-name /aws/bedrock-agentcore/runtimes/<agent-id> \
  --retention-in-days 90
```

### 감사와 모니터링

`bedrock-agentcore-control` API 호출(Runtime 생성/수정/삭제) 전체를 CloudTrail로 감사한다.[^workflow] CloudWatch 지표 네임스페이스는 대소문자를 구분하므로 먼저 실제 값을 확인해야 한다.

```bash
aws cloudwatch list-metrics --namespace "Bedrock-AgentCore"
# 결과가 없으면
aws cloudwatch list-metrics --namespace "Bedrock-Agentcore"
```

확인된 네임스페이스로 오류율·지연 알람을 구성한다.

## 업데이트 및 롤백 전략

**Rolling update(기본).** 새 컨테이너 이미지로 `update-agent-runtime`을 호출하면 기본 동작은 rolling update다 — 별도 조작 없이 신버전으로 점진 전환된다.[^workflow] 대부분의 일반 배포에 적합하지만, 배포 중 신/구 버전이 짧게 혼재하는 창을 감수해야 한다.

**Blue/green(alias 전환).** 즉시 전면 전환이 필요하거나 신버전을 실제 트래픽 없이 먼저 검증하고 싶다면, 신버전을 별도 Runtime 버전으로 기동한 뒤 Endpoint가 가리키는 대상(alias)만 전환한다. 문제가 발생하면 alias를 이전 버전으로 되돌리는 것만으로 즉시 원복된다 — 컨테이너를 다시 빌드/푸시할 필요가 없다는 점이 rolling update 대비 롤백 속도의 핵심 차이다.

**롤백.** 배포된 버전에 문제가 있으면 이전에 검증된 컨테이너 이미지 URI로 `update-agent-runtime`을 다시 호출해 재배포한다.[^workflow] 즉 "롤백"은 새로운 리소스 타입이 아니라 "이전 이미지로의 재배포"다 — 따라서 이전 이미지 태그를 ECR에서 삭제하지 않고 보존하는 운영 규칙이 전제된다.

## Agent Lifecycle Models

| 모델 | 상태 | Memory 서비스 | 적합한 경우 |
|---|---|---|---|
| Per-request | Stateless — 요청마다 새 인스턴스 | 불필요 | 단순 Q&A, 상태 없는 도구 호출 |
| Per-session | Stateful — 세션 내 요청 간 상태 유지 | 필수 | 멀티턴 대화, 컨텍스트 누적 |

Per-session 에이전트는 세션 상태 영속화를 위해 Memory 서비스가 필요하다.[^workflow] 어떤 메모리 타입을 쓸지, 쓰기 정책과 검색 스코핑을 어떻게 설계할지는 [Part 7 — 메모리](/07-memory/)에서 다룬다. 이 장에서 고정해 둘 것은 하나다 — lifecycle 모델 선택이 곧 Memory 서비스 통합 여부를 결정하는 배포 전 결정이라는 점이다. Stateless로 설계했다가 나중에 멀티턴을 추가하려면 배포 계약 자체(lifecycle 모델, Memory 연동 코드)를 다시 설계해야 한다.

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| Runtime 생성은 성공했지만 호출 시 오류 | Endpoint를 생성하지 않음(Step 5 누락) | `list-agent-runtime-endpoints`로 대상 Runtime의 Endpoint 존재 여부 확인 | `create-agent-runtime-endpoint` 실행 후 `READY` 대기 |
| 컨테이너가 시작되지 않음 | x86 이미지를 ARM64 대상 런타임에 푸시 | ECR 이미지 매니페스트의 아키텍처 확인 | `docker buildx build --platform linux/arm64`로 재빌드 |
| Endpoint가 `READY`인데 요청이 실패 | `/ping` 헬스체크가 기대 응답을 반환하지 않음 | Step 7의 `get-agent-runtime-endpoint` 응답에서 헬스체크 상태 확인, 컨테이너 로그 확인 | 컨테이너의 `/ping` 구현이 `{"status":"Healthy"}`를 반환하도록 수정 |
| `AssumeRole` 실패 또는 다른 리소스가 역할을 탈취할 여지 | 신뢰 정책에 `aws:SourceArn`/`aws:SourceAccount` 조건 누락 | 역할 신뢰 정책 JSON 검토 | 조건 추가, [confused deputy 문제](/09-authorization/confused-deputy) 참조 |
| 시크릿이 `get-agent-runtime` 응답에 평문으로 노출 | 시크릿을 `--environment-variables`에 저장 | `get-agent-runtime` 호출 결과에 시크릿 값이 보이는지 확인 | Secrets Manager로 이전, 런타임에서 동적 조회 |
| VPC 모드 런타임이 기동 직후 컨테이너 이미지를 못 받아옴 | VPC에 필요한 S3/ECR 엔드포인트가 없음(2026년 5월 이후 신규 VPC 런타임은 S3 게이트웨이 엔드포인트 자동 생성 안 됨) | 서브넷 라우트 테이블에 필요한 VPC 엔드포인트가 있는지 확인 | [Egress 제어](/09-authorization/egress-control) 장의 VPC 엔드포인트 설계 참조 |
| CloudWatch 로그에 PII가 평문으로 누적 | 로그 그룹에 KMS 암호화 미설정 | `describe-log-groups`로 `kmsKeyId` 필드 확인 | `associate-kms-key`로 즉시 적용, 신규 요청부터 암호화 적용됨(과거 로그는 재암호화 불가) |
| Blue/green 전환 후 이전 트래픽 패턴이 사라지지 않음 | alias 전환 시 이전 버전 인스턴스를 곧바로 종료 | 전환 직후 CloudWatch 지표에서 신/구 버전별 요청 분포 확인 | 신버전 검증 기간 동안 구버전 인스턴스를 유지한 뒤 종료 |
| Per-session 에이전트가 두 번째 요청에서 컨텍스트를 잃음 | Memory 서비스 연동 누락(per-request로 잘못 설계) | 세션 ID별로 상태가 실제로 영속화되는지 확인 | lifecycle 모델을 per-session으로 재설계, Memory 서비스 연동 — [Part 7](/07-memory/) |

## 안티패턴

- ❌ Runtime을 만들고 곧바로 트래픽을 보낸다 → ✅ Endpoint를 생성하고 `READY` 상태와 헬스체크 통과를 확인한 뒤에 트래픽을 라우팅한다.
- ❌ 모든 에이전트에 하나의 공용 실행 역할을 재사용한다 → ✅ 에이전트별로 최소 권한 역할을 분리해, 한 역할의 침해가 다른 에이전트로 전파되지 않게 한다.
- ❌ API 키를 `--environment-variables`에 넣고 코드에서 `os.environ`으로 읽는다 → ✅ Secrets Manager에 저장하고 실행 시점에 동적으로 조회한다.
- ❌ 개발 환경에서 편하다고 PUBLIC 모드를 프로덕션까지 그대로 가져간다 → ✅ 프로덕션은 VPC 모드로 전환하고 서브넷/보안그룹/필요 엔드포인트를 명시적으로 설계한다.
- ❌ 신뢰 정책 없이(또는 조건 없이) `sts:AssumeRole`을 허용한다 → ✅ `aws:SourceArn`/`aws:SourceAccount` 조건을 넣어 confused deputy 진입로를 차단한다.
- ❌ 롤백을 "새 배포 프로세스"로 다시 설계한다 → ✅ 이전에 검증된 이미지 URI로 동일한 `update-agent-runtime` 절차를 재실행한다.
- ❌ 로그 그룹 암호화를 배포 후 "언젠가" 처리할 항목으로 미룬다 → ✅ Runtime 생성과 같은 변경 세트에서 KMS 연동을 함께 적용한다.

## 계측 (SLI)

- **Endpoint 생성→`READY` 전환 소요 시간**: 배포 파이프라인의 대기 단계 길이를 결정한다. 비정상적으로 길어지면 컨테이너 시작 실패나 헬스체크 미응답을 의심한다.
- **헬스체크 성공률(`/ping` 응답 코드 분포)**: `Bedrock-AgentCore`(또는 `Bedrock-Agentcore`) 네임스페이스에서 확인. 실패율 상승은 배포 회귀의 선행 지표다.
- **`bedrock-agentcore-control` API 호출의 CloudTrail 이벤트 수/실패율**: 승인되지 않은 Runtime 변경 시도를 탐지한다.
- **롤아웃 중 신/구 버전별 요청 분포**: rolling update나 alias 전환 동안 트래픽이 의도한 비율로 이동하는지 확인한다.
- **KMS로 암호화되지 않은 AgentCore 로그 그룹 수**: 0이어야 하는 컴플라이언스 지표로 별도 추적한다.

## 체크리스트

- [ ] 컨테이너가 ARM64로 빌드되었고 ECR에 푸시됐다.
- [ ] `--role-arn`에 지정한 실행 역할이 이 에이전트 전용이며 최소 권한으로 스코프됐다.
- [ ] 역할 신뢰 정책에 `aws:SourceArn`/`aws:SourceAccount` 조건이 들어 있다.
- [ ] 프로덕션 환경이라면 `--network-configuration`이 `VPC` 모드이고 서브넷/보안그룹이 지정됐다.
- [ ] VPC 모드라면 필요한 VPC 엔드포인트(S3, ECR 등)가 준비돼 있다 — 2026년 5월 이후 신규 런타임은 자동 생성되지 않는다.
- [ ] `--authorizer-configuration`이 설정돼 있고, 인증되지 않은 요청이 거부된다.
- [ ] 시크릿이 `--environment-variables`가 아니라 Secrets Manager에 있다.
- [ ] CloudWatch 로그 그룹에 KMS 키가 연동돼 있고 보존 기한이 설정돼 있다.
- [ ] `create-agent-runtime-endpoint` 실행 후 `READY` 상태와 헬스체크 통과를 확인했다.
- [ ] lifecycle 모델(per-request/per-session)이 결정됐고, per-session이라면 Memory 서비스 연동이 준비됐다.
- [ ] 롤백에 쓸 이전 컨테이너 이미지 태그가 ECR에 보존돼 있다.
- [ ] `bedrock-agentcore-control` API 호출에 대한 CloudTrail 감사와 오류율/지연 알람이 구성돼 있다.

## 참고

- [amazon-bedrock skill — AgentCore Runtime 배포 워크플로우 및 보안 고려사항](file:///home/atomoh/.claude/skills/amazon-bedrock/references/agentcore-runtime.md)[^workflow]
- [Runtime 심화](/10-agentcore/runtime-deep-dive) — 프로토콜별 컨테이너 계약, microVM 격리, 프레임워크 어댑터
- [confused deputy 문제](/09-authorization/confused-deputy) — `aws:SourceArn`/`aws:SourceAccount` 조건의 원리
- [Egress 제어](/09-authorization/egress-control) — VPC 모드 아웃바운드 설계, S3 엔드포인트 변경점, ECR Public NAT 함정
- [Part 7 — 메모리](/07-memory/) — per-session lifecycle의 Memory 서비스 연동

[^workflow]: 배포 7단계, IAM/네트워크/시크릿/로그 암호화 보안 고려사항, lifecycle 모델, rolling/blue-green 업데이트 전략은 로컬 skill 참조 문서(amazon-bedrock skill, `agentcore-runtime.md`)에 정리된 AWS CLI 워크플로우를 따른다. 공식 AWS 문서의 최신 API 스펙(파라미터명, 필수/선택 여부)은 배포 전 `aws bedrock-agentcore-control create-agent-runtime help`로 교차확인할 것을 권장한다.
