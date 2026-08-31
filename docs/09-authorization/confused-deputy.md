---
title: Confused deputy 문제
description: 1988년 Norm Hardy가 정의한 confused deputy 문제의 원형과, MCP/에이전트 환경에서 이것이 왜 더 심각해지는지, 그리고 IAM 조건 키·OAuth aud 검증·Cedar 정책이라는 3중 방어를 다룬다.
outline: [2, 3]
---

# Confused deputy 문제

::: tip 이 장에서 얻는 것
- confused deputy 문제의 원형(Norm Hardy, 1988) — deputy가 "두 개의 권한 소스"를 구분하지 못할 때 왜 항상 이 문제가 재발하는지
- 에이전트가 여러 사용자를 대신해 **하나의 서비스 신원**으로 다운스트림 툴을 호출할 때 confused deputy가 왜 구조적으로 발생하는지
- AWS IAM의 표준 방어 패턴(`aws:SourceArn`/`aws:SourceAccount`/`sts:ExternalId`)의 정확한 동작 방식과 적용 범위
- OAuth `aud`(audience) 클레임 검증이 MCP 환경에서 이 문제를 막는 핵심 메커니즘인 이유, 그리고 "token passthrough"가 왜 금지되는지
- Cedar 정책이 이 문제에 추가하는 방어 층 — principal(위조 불가능한 신원)과 context(에이전트가 만든 인자)의 분리
- 이 장이 Part 9의 다른 장(OAuth 토큰 교환, 툴별 OBO, Cedar)과 어떻게 맞물리는지
:::

## 왜 문제가 되는가

에이전트 시스템에서 가장 흔한 인가 실패 패턴 하나는 이렇게 요약된다. 에이전트가 다운스트림 툴이나 API를 호출할 때, 그 호출은 에이전트(또는 에이전트를 운영하는 서비스)의 신원으로 나간다. 그런데 그 호출을 유발한 실제 요청자는 여러 명의 서로 다른 최종 사용자다. 다운스트림 서비스는 "누가 이 요청을 실제로 유발했는가"를 구분할 방법이 없고, 오직 "에이전트 서비스가 요청했다"는 사실만 본다. 이 틈을 이용하면 사용자 A가 사용자 B의 데이터에 접근하거나, 원래는 거부됐어야 할 작업을 에이전트라는 "권한이 더 큰 대리인(deputy)"을 통해 실행시킬 수 있다.

이것은 새로운 문제가 아니다. 1988년 Norm Hardy가 *Operating Systems Review* 22권 4호에 기고한 "The Confused Deputy (or why capabilities might have been invented)"에서 이미 정확히 같은 구조의 문제를 기술했다.[[원문]](https://cap-lore.com/CapTheory/ConfusedDeputy.html) 다만 에이전트/MCP 환경에서는 이 문제가 훨씬 넓은 표면에서, 훨씬 자주 재발한다 — 왜냐하면 "하나의 서비스 신원이 여러 사용자를 대신해 다수의 다운스트림 툴을 호출한다"는 구조 자체가 MCP Gateway·에이전트 런타임의 기본 배치 형태이기 때문이다. 사용자별 인가 경계를 명시적으로 설계하지 않으면, 에이전트는 설계상 confused deputy가 된다.

## 핵심 개념

### 원형: Norm Hardy의 컴파일러와 SYSX.BILL

Hardy가 기술한 사건은 Tymshare의 타임셰어링 시스템에서 있었다.[[원문]](https://cap-lore.com/CapTheory/ConfusedDeputy.html) 구조는 다음과 같다.

- 컴파일러 `(SYSX)FORT`는 자신의 홈 디렉터리 `SYSX`에 파일을 쓸 수 있는 **home files license**를 갖고 있었다. 이 라이선스가 필요했던 이유는 컴파일러가 언어 기능 사용 통계를 `(SYSX)STAT`에 기록해야 했기 때문이다.
- 동시에 컴파일러는 사용자가 지정한 임의의 파일명으로 디버그 출력을 쓰는 기능도 제공했다 — `RUN (SYSX)FORT` 호출 시 사용자가 출력 파일명을 넘길 수 있었다.
- 과금 정보 파일 `(SYSX)BILL`도 같은 디렉터리 `SYSX`에 있었다. 한 사용자가 이 파일명을 알아내 디버그 출력 대상으로 지정했고, 컴파일러는 자신의 home files license로 `(SYSX)BILL`을 열어 덮어썼다. 과금 정보가 소실됐다.

Hardy의 진단은 정확히 이 지점을 짚는다: "컴파일러는 두 개의 권한 소스에서 authority를 받아 실행된다. (그래서 컴파일러는 confused deputy다.)"[[원문]](https://cap-lore.com/CapTheory/ConfusedDeputy.html) 컴파일러는 통계를 쓸 때는 자신의 home files license를 쓰려는 의도였고, 디버그 출력을 쓸 때는 호출자의 권한을 쓰려는 의도였지만, **이 두 의도를 구분해서 표현할 방법이 시스템에 없었다.** 파일을 열 때 시스템은 "컴파일러가 home files license를 갖고 있는가"만 확인했고, "이 특정 쓰기가 어느 권한에 근거한 것인지"는 확인하지 않았다.

이 원형에서 뽑아낼 일반 법칙은 세 가지다.

1. Deputy(대리인)가 **자기 권한**과 **위임받은 권한**을 뒤섞어 하나의 실행 경로에서 쓰면, 호출자는 deputy의 더 큰 권한을 빌려 쓸 수 있다.
2. 이름(파일명, 리소스 식별자, ARN 문자열)만으로 권한을 판단하는 시스템은 근본적으로 취약하다 — 이름은 호출자가 자유롭게 지정할 수 있기 때문이다. Hardy는 이 문제를 해결하려면 capability(신원과 권한이 분리 불가능하게 결합된 토큰) 방식이 필요하다고 결론짓는다.
3. deputy가 "누구를 대신해서" 행동하는지 시스템 차원에서 구분·강제할 수 없다면, 임시방편(파일명 필터링, 디렉터리 이름 검사 등)은 새 예외가 추가될 때마다 뚫린다. Hardy는 실제로 "파일을 열기 위한 규칙에 14개의 boolean 연산자가 필요해졌다"고 기록한다.[[원문]](https://cap-lore.com/CapTheory/ConfusedDeputy.html)

### MCP/에이전트 환경에서의 재현

에이전트 런타임을 Hardy의 컴파일러에 대응시켜 보면 구조가 그대로 겹친다.

- **컴파일러의 home files license** ↔ 에이전트(또는 MCP Gateway)가 다운스트림 도구를 호출하는 데 쓰는 서비스 신원(IAM 역할, 서비스 계정, 고정된 client credential).
- **사용자가 지정한 디버그 출력 파일명** ↔ 사용자 프롬프트에서 LLM이 만들어내는 툴 인자(대상 리소스 ID, 계정 번호, 파일 경로 등).
- **`(SYSX)BILL`을 덮어쓴 사고** ↔ 사용자 A의 요청으로 시작된 에이전트 실행이, 에이전트의 공유 서비스 신원을 통해 사용자 B의 데이터나 권한 범위에 접근하는 사고.

MCP 표준화 이전에는 이 문제가 "OAuth 프록시" 구현에서 구체적인 공격 형태로 이미 문서화됐다. MCP 공식 보안 모범 사례 문서는 MCP 프록시 서버가 제3자 인가 서버에 **고정된(static) client ID**를 쓰면서, MCP 클라이언트에는 **동적 클라이언트 등록(dynamic client registration)**을 허용하고, 제3자 인가 서버가 **동의(consent) 쿠키**를 세팅하는 조건이 모두 겹칠 때 발생하는 공격을 "confused deputy" 공격으로 명시적으로 이름 붙여 다룬다.[[MCP Security Best Practices]](https://modelcontextprotocol.io/specification/2025-06-18/basic/security_best_practices) 공격 흐름은 다음과 같다.

1. 정상 사용자가 MCP 프록시를 통해 제3자 API에 접근하며 동의를 완료하면, 제3자 인가 서버는 "고정 client ID에 대한 동의" 쿠키를 사용자 브라우저에 남긴다.
2. 공격자는 악성 `redirect_uri`로 새로 동적 등록한 client를 만들고, 피해자에게 조작된 인가 요청 링크를 보낸다.
3. 피해자가 링크를 클릭하면 브라우저에는 여전히 (같은 고정 client ID에 대한) 동의 쿠키가 남아 있으므로, 제3자 인가 서버는 동의 화면을 건너뛴다.
4. MCP 인가 코드가 공격자의 `redirect_uri`로 전달되고, 공격자는 이를 토큰으로 교환해 피해자로 위장한다.

MCP 프록시 서버는 여기서 정확히 Hardy의 컴파일러 역할을 한다 — 프록시는 "고정 client ID"(자신의 권한)와 "이 요청이 실제로 어느 최종 사용자·어느 동적 등록 client를 대신하는지"(위임받은 맥락)를 구분하지 못한 채 하나의 인가 흐름을 처리했다. 공식 문서가 제시하는 완화책은 **MCP 서버 자신이 소유한 per-client 동의 저장소**를 두고, 제3자 인가 흐름으로 넘어가기 **전에** client별 동의를 검사하도록 강제하는 것이다.[[MCP Security Best Practices]](https://modelcontextprotocol.io/specification/2025-06-18/basic/security_best_practices)

::: warning 미정착 영역
공개적으로 이름이 붙고 CVE 등으로 정형화된 "MCP confused deputy" 실사고 사례는 이 리서치에서 1차 출처로 확인하지 못했다. 위 공격 흐름은 MCP 공식 스펙 문서가 일반화된 공격 패턴으로 기술한 것이며, 특정 제품·특정 인시던트를 지칭하지 않는다. 실제 프로덕션 사고 사례가 필요하다면 벤더별 보안 공지(GitHub Security Advisories, 각 MCP 서버 구현체의 CHANGELOG)를 직접 확인할 것.
:::

더 넓게 보면, 에이전트가 여러 사용자를 대신해 하나의 서비스 신원으로 다운스트림 MCP 툴을 호출하는 배치 형태 자체가 이 문제의 일반적 토양이다. 다운스트림 툴 서버는 "이 호출이 에이전트 서비스로부터 왔다"는 것은 알지만, "이 서비스가 지금 어느 최종 사용자를 대신해서 요청하는지"를 구분할 신호가 없으면, 사용자 스코프를 넘어서는 요청도 서비스 신원의 권한 범위 안에서는 전부 통과한다. 이는 Part 0 [6대 통증점](/00-intro/six-pain-points)에서 "서비스 신원으로 실행(감사 추적 붕괴)"으로 이름 붙인 통증점과 동일한 근본 원인이다.

### 방어 축 1 — AWS IAM: `aws:SourceArn`/`aws:SourceAccount`와 `sts:ExternalId`

AWS IAM 공식 문서는 confused deputy 문제를 **cross-account**(제3자에게 역할을 위임하는 경우)와 **cross-service**(AWS 서비스 주체가 다른 서비스의 리소스에 접근하는 경우) 두 시나리오로 나눠 정의한다.[[AWS IAM 공식 문서]](https://docs.aws.amazon.com/IAM/latest/UserGuide/confused-deputy.html)

**Cross-account 시나리오**: 계정 소유자가 제3자(Example Corp)에게 역할 ARN을 주고 계정 접근을 위임했는데, 다른 고객이 같은 역할 ARN을 알아내(또는 추측해) Example Corp에게 "내 요청도 이 역할로 처리해줘"라고 시키면, Example Corp가 confused deputy가 되어 원래 계정의 리소스에 의도치 않게 접근하게 된다. 방어책은 역할의 신뢰 정책(trust policy)에 `sts:ExternalId` 조건을 거는 것이다 — Example Corp가 고객마다 고유한 `ExternalId`를 발급·관리하고, `AssumeRole` 호출 시 그 값을 반드시 포함하도록 강제한다. 다른 고객이 역할 ARN을 알아내도 그 고객이 속한 `ExternalId`를 조작할 수는 없으므로, 조건이 일치하지 않는 `AssumeRole`은 거부된다.[[AWS IAM 공식 문서]](https://docs.aws.amazon.com/IAM/latest/UserGuide/confused-deputy.html)

**Cross-service 시나리오**: 예시로 AWS IAM 문서가 드는 것은 CloudTrail이 다른 계정의 S3 버킷에 로그를 쓰는 경우다. S3 버킷 정책이 `cloudtrail.amazonaws.com` 서비스 주체를 조건 없이 신뢰하면, 그 버킷 이름을 알아낸 임의의 계정이 자신의 CloudTrail을 그 버킷으로 설정해 로그를 흘려보낼 수 있다. 방어책은 `aws:SourceArn`, `aws:SourceAccount`, `aws:SourceOrgID`, `aws:SourceOrgPaths` 글로벌 조건 키다 — 이 키들은 "이 서비스 주체가 정확히 어떤 리소스/계정/조직을 대신해서 행동하는지"를 리소스 정책 조건으로 검증하게 해준다.[[AWS IAM 공식 문서]](https://docs.aws.amazon.com/IAM/latest/UserGuide/confused-deputy.html)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AWSCloudTrailWrite",
      "Effect": "Allow",
      "Principal": { "Service": "cloudtrail.amazonaws.com" },
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::amzn-s3-demo-bucket1/[optionalPrefix]/Logs/myAccountID/*",
      "Condition": {
        "StringEquals": { "aws:SourceAccount": "111122223333" }
      }
    }
  ]
}
```

이 정책 예시는 AWS IAM 공식 문서에서 그대로 인용한 것이다.[[AWS IAM 공식 문서]](https://docs.aws.amazon.com/IAM/latest/UserGuide/confused-deputy.html) `aws:SourceArn`은 특정 리소스(예: 특정 CloudTrail trail, 특정 AppStream fleet)를 대신할 때만 허용하도록 더 좁게 스코프할 수 있고, `aws:SourceOrgID`/`aws:SourceOrgPaths`는 조직 단위로 스코프한다. 최근에는 **리소스 제어 정책(Resource Control Policy, RCP)**으로 이 조건을 계정·OU·조직 단위에 중앙에서 강제할 수도 있다 — 개별 리소스 정책마다 조건을 반복해서 넣을 필요 없이, `aws:PrincipalIsAWSService`와 `aws:SourceAccount`/`aws:SourceOrgID`를 조합한 RCP로 전사적으로 방어선을 세운다.[[AWS IAM 공식 문서]](https://docs.aws.amazon.com/IAM/latest/UserGuide/confused-deputy.html)

이 패턴은 이 책의 여러 장에서 반복해서 등장한다 — Part 1의 MCP 서버 설계, Part 10의 런타임 딥다이브에서도 "서비스 간 호출에는 source 조건을 걸어라"는 형태로 다시 나온다. 이 장이 그 정본(canonical reference)이다: **서비스 주체를 신뢰하는 모든 리소스 정책에는 `aws:SourceArn` 또는 `aws:SourceAccount`(또는 조직 단위라면 `aws:SourceOrgID`) 조건이 있어야 한다.** 조건 없이 서비스 주체만 허용하는 정책은 그 서비스를 통해 접근 가능한 모든 계정으로부터의 요청을 구분 없이 받아들인다는 뜻이다.

### 방어 축 2 — OAuth `aud`(audience) 검증과 token passthrough 금지

AWS IAM의 `aws:SourceArn`/`aws:SourceAccount`가 "이 서비스 주체가 어느 리소스/계정을 대신하는가"를 검증하는 것과 대응하는 개념이, MCP/OAuth 환경에서는 **토큰의 `aud`(audience) 클레임 검증**이다. 토큰이 "누구를 위해 발급됐는가"를 명시하고, 수신 측(다운스트림 리소스 서버)이 자신이 그 audience인지 반드시 확인해야 confused deputy가 막힌다.

MCP 공식 스펙은 이를 **token passthrough**라는 이름의 안티패턴으로 정의하고 명시적으로 금지한다: "MCP 서버가 MCP 클라이언트로부터 받은 토큰이 실제로 그 MCP 서버 앞으로 발급된 것인지 검증하지 않고 다운스트림 API로 그대로 전달하는 것"이 token passthrough다.[[MCP Security Best Practices]](https://modelcontextprotocol.io/specification/2025-06-18/basic/security_best_practices) 이것이 위험한 이유는 다음과 같이 정리된다.

- **보안 제어 우회**: rate limiting, 요청 검증, 트래픽 모니터링 등 audience나 credential 제약에 의존하는 다운스트림 보안 제어가 통째로 우회된다.
- **책임 추적성 붕괴**: MCP 서버가 여러 클라이언트를 구분하지 못하고, 다운스트림 리소스 서버의 로그에는 MCP 서버 자신이 아닌 "다른 신원"이 요청한 것처럼 남는다 — 감사·사고 조사가 불가능해진다.
- **신뢰 경계 붕괴**: 하나의 서비스가 침해되면 검증 없이 토큰을 받아들이는 다른 서비스로도 그 토큰이 그대로 먹힌다.

공식 문서의 결론은 단정적이다: "MCP 서버는 자신을 향해 명시적으로 발급되지 않은 토큰을 절대 받아들여서는 안 된다(MUST NOT)."[[MCP Security Best Practices]](https://modelcontextprotocol.io/specification/2025-06-18/basic/security_best_practices) 이것이 곧 `aud` 검증이다 — 토큰을 받는 서버는 자신이 그 토큰의 audience 목록에 포함돼 있는지 확인해야 하고, 포함돼 있지 않다면 그 토큰이 아무리 유효한 서명을 갖고 있어도 거부해야 한다.

이 메커니즘의 구체적인 구현(토큰 교환, 스코프 다운, per-tool 위임 흐름)은 이 책의 다른 두 장에서 다룬다.

- [OAuth 토큰 교환과 MCP](./oauth-token-exchange-mcp) — 에이전트가 사용자 대신 다운스트림 토큰을 얻는 흐름과 `aud` 클레임이 실제로 어디서 발급·검증되는지.
- [툴별 On-Behalf-Of](./per-tool-obo) — 툴마다 사용자 신원을 보존한 스코프 제한 토큰으로 호출해야 하는 이유와 패턴.

### 방어 축 3 — Cedar 정책: principal과 context의 분리

`aud` 검증과 IAM의 source 조건은 "이 호출이 누구를 대신하는가"를 확인하는 계층이다. 그런데 그 위에 "그 사람을 대신한다고 확인됐다 해도, 이 특정 요청(이 인자, 이 금액, 이 리소스)을 허용해도 되는가"라는 별도의 질문이 남는다. 이 질문에 답하는 것이 정책 엔진 층이다.

Part 9의 [Cedar와 Verified Permissions](./cedar-verified-permissions)에서 다루는 AgentCore Policy는 이 지점에서 confused deputy 방어에 정확히 기여한다. Cedar 정책은 principal 조건에 **위조 불가능한 신원 신호**(OAuth JWT 클레임에서 나온 태그)를 쓰고, context 조건에 **에이전트/LLM이 만들어낸 가변 입력**(툴 인자)을 쓴다. 이 둘을 정책 안에서 명확히 분리해 교차 검증하면, `aud` 검증으로 "이 요청이 사용자 X를 대신한다"는 사실을 확인한 이후에도, "사용자 X가 실제로 이 금액/이 리소스에 접근할 권한이 있는가"까지 결정론적으로 제약할 수 있다. 즉 confused deputy 방어는 한 층으로 끝나지 않는다 — 신원 확인(누구를 대신하는가)과 인가 결정(그 사람이 이 구체적 요청을 할 권한이 있는가)은 서로 다른 실패 모드를 막는 별개의 층이며, 둘 다 있어야 한다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| AWS 서비스 주체(예: CloudTrail, AppStream)가 자기 계정의 리소스에 접근 | 리소스 정책에 `aws:SourceArn`/`aws:SourceAccount` 조건 | 서비스가 어느 리소스/계정을 대신하는지 리소스 정책 레벨에서 검증 | 서비스별로 지원 조건 키가 다름(문서 확인 필요), 기존 조건 없는 정책에 추가 시 회귀 테스트 필요 |
| 제3자 벤더에게 IAM 역할을 위임(cross-account) | 역할 신뢰 정책에 `sts:ExternalId` | 벤더가 다른 고객을 대신해 내 역할을 잘못 assume하는 것을 방지 | ExternalId는 벤더가 발급·관리해야 함(고객이 임의로 정하면 무의미) |
| 조직 전체에 cross-service confused deputy 방어를 일관 적용 | 리소스 제어 정책(RCP)으로 `aws:SourceOrgID` 강제 | 개별 리소스 정책마다 조건을 반복하지 않고 중앙 집중 관리 | RCP를 지원하는 서비스 범위 내에서만 유효, 조직 구조 변경 시 재검토 필요 |
| MCP 서버가 다운스트림 API로 토큰을 전달해야 함 | 다운스트림에 새 토큰을 발급/교환(토큰 교환), 수신 받은 토큰을 그대로 전달(passthrough) 금지 | audience 분리로 신뢰 경계·책임 추적성 유지 | 교환 흐름 추가로 지연 증가, 토큰 교환 인프라 구축 필요 |
| 여러 사용자를 대신하는 에이전트가 다운스트림 MCP 툴 호출 | per-tool OBO(사용자 신원 보존 스코프 토큰) | 서비스 신원 하나로 뭉치지 않고 사용자별 감사 추적·권한 경계 유지 | 툴마다 토큰 매핑·스코프 설계 필요, 구현 복잡도 증가 |
| 신원은 확인됐지만 요청 인자(금액·리소스 ID) 자체를 제약해야 함 | Cedar 정책의 `context.input.*` 조건 | LLM이 만든 인자를 정책으로 결정론적 거부 가능 | 툴 스키마 변경 시 정책 재검증 필요 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 다른 AWS 계정이 내 S3 버킷/리소스에 예상치 못하게 쓰기 성공 | 서비스 주체를 신뢰하는 리소스 정책에 `aws:SourceArn`/`aws:SourceAccount` 조건이 없음 | 리소스 정책에서 서비스 principal(`Service: "xxx.amazonaws.com"`) 항목을 찾아 Condition 블록 존재 여부 확인 | 해당 서비스가 지원하는 source 조건 키를 추가하고 최소 스코프로 제한 |
| 벤더가 다른 고객 계정을 대신해 내 역할을 assume한 로그가 CloudTrail에 남음 | 역할 신뢰 정책에 `sts:ExternalId` 조건이 없거나, ExternalId를 고객이 스스로 정함(벤더가 강제하지 않음) | CloudTrail의 `AssumeRole` 이벤트에서 `externalId` 필드 존재·발급 주체 확인 | 벤더가 고객마다 고유하게 발급하는 ExternalId를 트러스트 정책 조건으로 강제 |
| MCP 서버 로그에는 모든 다운스트림 요청이 같은 신원으로 남아 사용자별 추적 불가 | 클라이언트가 전달한 토큰을 검증 없이 다운스트림에 그대로 전달(token passthrough) | 다운스트림 리소스 서버 로그의 caller identity가 MCP 서버 자신인지, 아니면 매번 다른 최종 사용자인지 대조 | 토큰을 그대로 넘기지 않고, MCP 서버가 자신을 audience로 하는 토큰만 받고 다운스트림에는 별도 교환된 토큰을 발급 |
| 사용자 A의 요청이 사용자 B의 데이터에 접근하는 사고 발생 | 에이전트가 여러 사용자를 대신해 단일 서비스 신원으로 다운스트림 호출 — 다운스트림이 "누구를 대신하는지" 구분할 신호가 없음 | 사고 재현 시 다운스트림 호출의 인증 컨텍스트에 사용자별 클레임이 있는지 확인 | per-tool OBO로 전환해 사용자 신원을 스코프 토큰에 보존 |
| MCP 프록시에서 공격자가 사용자 동의 없이 인가 코드를 탈취 | 고정 client ID + 동적 클라이언트 등록 + 동의 쿠키 조합에서 per-client consent 검사가 없음 | 인가 흐름에서 동의 화면이 client_id별로 매번 뜨는지, 아니면 쿠키로 스킵되는지 확인 | MCP 서버 자체의 per-client 동의 저장소를 두고 제3자 인가 흐름 진입 전에 검사 |
| 신원 확인은 정상인데도 환각된 인자(과도한 금액, 잘못된 리소스 ID)로 작업이 실행됨 | `aud`/OBO로 "누구를 대신하는가"만 확인하고, "이 구체적 요청이 허용되는가"를 확인하는 정책 층이 없음 | 실행된 툴 호출의 인자 값이 정책상 허용 범위였는지 역산 | Cedar 등 정책 엔진에서 `context.input.*` 조건으로 인자 자체를 제약 |

## 안티패턴

- ❌ 서비스 주체(Service principal)를 리소스 정책에서 조건 없이 신뢰 → ✅ `aws:SourceArn`/`aws:SourceAccount`/`aws:SourceOrgID` 중 해당 서비스가 지원하는 조건을 반드시 추가
- ❌ 제3자에게 위임한 역할의 신뢰 정책에 `ExternalId` 없이 역할 ARN만으로 신뢰 → ✅ 벤더가 발급·관리하는 고유 `sts:ExternalId`를 조건으로 강제
- ❌ MCP 서버가 클라이언트로부터 받은 토큰의 audience를 확인하지 않고 다운스트림에 그대로 전달(token passthrough) → ✅ 자신을 audience로 하는 토큰만 받고, 다운스트림 호출에는 별도로 교환·발급된 토큰 사용
- ❌ 여러 사용자를 대신하는 에이전트가 다운스트림 호출을 항상 하나의 서비스 신원으로만 수행 → ✅ per-tool OBO로 사용자 신원을 보존한 스코프 토큰을 유지
- ❌ "누구를 대신하는가"만 확인하고 "이 요청이 허용 범위 안인가"는 확인하지 않음 → ✅ 신원 검증(aud, source 조건) 위에 정책 엔진(Cedar 등)으로 인자·금액·리소스 범위까지 결정론적으로 제약
- ❌ MCP 프록시가 고정 client ID + 동의 쿠키 조합을 쓰면서 client별 동의 여부를 구분하지 않음 → ✅ per-client 동의 저장소를 두고 제3자 인가 흐름 전에 검사

## 계측 (SLI)

confused deputy 방어가 실제로 동작하는지 관측하려면 최소한 다음을 추적한다.

- **source 조건 커버리지**: 서비스 주체를 신뢰하는 리소스 정책 중 `aws:SourceArn`/`aws:SourceAccount`/`aws:SourceOrgID` 조건이 있는 비율. IaC 정적 분석(cfn-lint, 커스텀 정책 스캐너)으로 배포 전에 측정 가능.
- **token passthrough 탐지**: 다운스트림 호출에 사용된 토큰의 `aud` 클레임이 실제 호출 대상과 일치하는지 검사하는 비율, 그리고 불일치(또는 검증 자체를 생략한) 요청의 카운트.
- **사용자-신원 매핑률**: 다운스트림 호출 로그에서 "실제 최종 사용자 식별자까지 추적 가능한 호출" 대 "서비스 신원으로만 남은 호출"의 비율. 후자가 높을수록 confused deputy 위험도가 높다.
- **`ExternalId`/`SourceAccount` 불일치로 인한 명시적 거부(Deny) 건수**: 방어가 실제로 트래픽에서 발동하고 있는지, 그리고 예상치 못한 대량 거부가 발생하면 정책 오설정 신호일 수 있다.
- **Cedar(또는 동급 정책 엔진)의 context 조건 매칭 거부율**: 신원은 통과했지만 인자 조건에서 거부된 요청 — "인가된 사용자의 환각/과도한 요청"이 실제로 얼마나 발생하는지 보여준다.

## 체크리스트

- [ ] AWS 서비스 주체를 신뢰하는 모든 리소스 정책에 `aws:SourceArn` 또는 `aws:SourceAccount`(조직 단위라면 `aws:SourceOrgID`/`aws:SourceOrgPaths`) 조건이 있는가
- [ ] 제3자에게 위임한 IAM 역할의 신뢰 정책에 벤더가 발급한 고유 `sts:ExternalId` 조건이 있는가
- [ ] 조직 전체에 걸친 방어가 필요한 경우 리소스 제어 정책(RCP)으로 중앙 집중 강제를 검토했는가
- [ ] MCP 서버(또는 게이트웨이)가 클라이언트로부터 받은 토큰의 `aud` 클레임이 자신인지 검증하는가, 그리고 검증 없이 다운스트림에 그대로 전달(token passthrough)하는 경로가 없는가
- [ ] 여러 사용자를 대신하는 에이전트의 다운스트림 호출이 사용자별로 신원을 보존하는가(per-tool OBO), 아니면 단일 서비스 신원으로 뭉쳐 있는가
- [ ] MCP 프록시가 있다면 client별 동의(consent)를 제3자 인가 흐름 진입 전에 검사하는가
- [ ] 신원 검증(누구를 대신하는가) 위에 정책 엔진(Cedar 등)으로 요청 인자·금액·리소스 범위까지 결정론적으로 제약하는 층이 있는가
- [ ] 위 항목들에 대한 계측(SLI)이 실제 운영 트래픽에서 수집되고 있는가

## 참고

- [Norm Hardy, "The Confused Deputy (or why capabilities might have been invented)", Operating Systems Review Vol. 22 No. 4, 1988 (원문)](https://cap-lore.com/CapTheory/ConfusedDeputy.html)
- [The confused deputy problem - AWS IAM 공식 문서](https://docs.aws.amazon.com/IAM/latest/UserGuide/confused-deputy.html)
- [MCP Security Best Practices - Confused Deputy Problem & Token Passthrough](https://modelcontextprotocol.io/specification/2025-06-18/basic/security_best_practices)
- [OAuth 토큰 교환과 MCP](./oauth-token-exchange-mcp)
- [툴별 On-Behalf-Of](./per-tool-obo)
- [Cedar와 Verified Permissions](./cedar-verified-permissions)
- [6대 통증점](/00-intro/six-pain-points)
