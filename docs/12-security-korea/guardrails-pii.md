---
title: 가드레일과 PII
description: Amazon Bedrock Guardrails의 필터 지형과 PII 처리 동작을 해부하고, 마스킹이 로그에는 적용되지 않는 컴플라이언스 갭과 한국어 PII의 커버리지 공백을 메우는 방법을 다룬다.
outline: [2, 3]
---

# 가드레일과 PII

::: tip 이 장에서 얻는 것
- Bedrock Guardrails의 정확한 기능 지형 — content filters, denied topics, word filters, sensitive information filters, contextual grounding check, prompt attack 필터가 각각 무엇을 어디서(입력/출력) 막는지
- PII 처리의 세 가지 액션(BLOCK / ANONYMIZE / NONE)과 입력·출력에 서로 다른 액션을 거는 방법
- **가장 위험한 함정**: PII 마스킹은 API 응답에만 적용되고 원본은 CloudWatch Logs에 평문으로 남는다 — 공식 문서로 확인된 사실과 그 대응책
- 스트리밍 sync/async 모드가 PII 마스킹에 미치는 영향, ApplyGuardrail 독립 API의 활용 지점
- 한국어 PII(주민등록번호, 계좌번호, 휴대전화)의 내장 타입 커버리지 공백과 regex 필터 보완 패턴
- 툴 호출 인자·툴 결과가 sensitive information filter의 평가 대상이 아니라는, 에이전틱 워크로드 특유의 사각지대
:::

## 왜 문제가 되는가

에이전트 플랫폼에서 PII는 세 방향으로 흐른다. 사용자가 입력 프롬프트에 담아 보내고, 모델이 응답에 생성해 내보내고, 툴/RAG 파이프라인이 컨텍스트로 끌어들인다. 이 중 어느 한 경로라도 통제 밖에 있으면 개인정보 유출 사고다 — 그리고 국내에서는 개인정보보호법·신용정보법상 유출 통지·과징금으로 직결되는 문제다(규제 프레임 자체는 [국내 금융권 규제 지형](/12-security-korea/korea-fsc-regulation)이 정본이므로 이 장에서는 다루지 않는다).

Amazon Bedrock Guardrails는 이 문제에 대한 AWS의 매니지드 답변이다. 하지만 실무에서 세 가지 오해가 반복된다.

1. **"가드레일을 켰으니 PII는 안전하다"** — 아니다. 마스킹은 API 응답에만 적용되고, model invocation logging을 켜 두었다면 원본(마스킹 전) 콘텐츠가 CloudWatch Logs에 평문으로 남는다. 공식 문서가 명시한다: "the `input` field in Amazon CloudWatch Logs always contains the original, unmodified request regardless of guardrail intervention"([Sensitive information filters](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html)). 가드레일 설정 화면만 보고 컴플라이언스를 선언하면 로그 계층에서 그대로 위반이다.
2. **"내장 PII 타입이 우리 데이터를 커버한다"** — 내장 타입 목록에 한국 특화 타입은 없다. 국가별 타입은 USA/Canada/UK 전용뿐이다([공식 타입 목록](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html)). 주민등록번호·국내 계좌번호는 regex 필터로 직접 정의해야 한다.
3. **"가드레일이 인젝션을 막아준다"** — prompt attack 필터는 방어 레이어 중 하나일 뿐이다. 인젝션 방어의 전체 그림은 [프롬프트 인젝션](/12-security-korea/prompt-injection)에서 다루며, 이 장은 가드레일이 그 그림에서 담당하는 조각만 정확히 규정한다.

이 장의 목표는 "가드레일이 무엇을 하는가"가 아니라 "가드레일이 무엇을 **하지 않는가**"를 경계선까지 정확히 그리는 것이다. 경계선을 모르면, 경계 밖의 유출은 감사에서 처음 발견된다.

## 핵심 개념

### 필터 지형: 여섯 개의 정책, 각기 다른 적용 지점

Guardrails 하나는 여러 정책의 묶음이다. [공식 컴포넌트 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-components.html) 기준으로:

| 정책 | 하는 일 | 적용 지점 | 액션 |
|---|---|---|---|
| **Content filters** | Hate, Insults, Sexual, Violence, Misconduct, Prompt Attack 여섯 카테고리의 유해 텍스트/이미지 탐지. 카테고리별 강도 설정 | 입력 + 출력 | BLOCK |
| **Prompt attacks** | content filter의 한 카테고리. jailbreak, prompt injection, prompt leakage(leakage는 Standard tier 전용) 탐지 | 입력 | BLOCK |
| **Denied topics** | 자연어로 정의한 금지 주제(예: "투자 자문") 차단. Standard tier는 주제 정의 1,000자, Classic은 200자까지 | 입력 + 출력 | BLOCK |
| **Word filters** | 커스텀 단어/구문 exact match + managed profanity 목록 | 입력 + 출력 | BLOCK |
| **Sensitive information filters** | 내장 PII 타입 + custom regex로 민감정보 탐지. ML 기반 문맥 의존 판정 | 입력 + 출력 | BLOCK / ANONYMIZE / NONE |
| **Contextual grounding check** | 응답이 grounding source에 근거하는지(grounding)와 질의에 관련 있는지(relevance)를 점수화해 환각 차단 | 출력 | BLOCK |
| **Automated Reasoning checks** | 자연어로 정의한 논리 규칙에 대한 응답 정합성 검증 | 출력 | 검증/제안 |

플랫폼 관점에서 중요한 구분: **content filters/denied topics/word filters는 "무엇을 말하면 안 되는가"의 문제**이고, **sensitive information filters는 "무엇이 새어 나가면 안 되는가"의 문제**다. 전자는 차단이 답이지만, 후자는 차단과 마스킹 사이의 선택이 서비스 설계 자체를 바꾼다.

### PII 액션: BLOCK vs ANONYMIZE vs NONE

sensitive information filter는 PII 타입별로 세 액션 중 하나를 건다([공식 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html)):

- **BLOCK** — PII가 탐지되면 콘텐츠 전체를 차단하고 설정한 canned message를 반환. 공개 문서 기반 Q&A처럼 PII가 아예 등장할 이유가 없는 워크로드용.
- **ANONYMIZE(Mask)** — PII를 `{NAME}`, `{EMAIL}` 같은 타입 플레이스홀더로 치환하고 나머지 응답은 반환. 상담 대화 요약처럼 PII가 필연적으로 섞이지만 응답 자체는 필요한 워크로드용.
- **NONE** — 아무 조치 없이 탐지 정보만 반환(detect 모드). 정책 도입 전 오탐률 측정, 계측 파이프라인 구축용.

`CreateGuardrail` API는 타입별로 `inputAction`/`outputAction`, `inputEnabled`/`outputEnabled`를 분리 설정할 수 있다 — 즉 "입력의 이메일은 통과시키되 출력의 이메일은 마스킹"처럼 방향별 정책이 가능하다. 신용카드 번호는 BLOCK, 이름은 ANONYMIZE처럼 타입별 혼합도 당연히 가능하다.

주의: 공식 문서가 명시하듯 이 필터는 확률적 ML 기반이며 문맥 의존이다 — "a string of digits might represent an AWS KMS key or a user ID depending on the surrounding information". 짧은 단문에서는 정확도가 떨어지므로, 단어 하나만 던져 검사하는 설계(예: 폼 필드 단위 검사)는 이 필터의 강점을 버리는 것이다.

### 적용 방식 세 가지: guardrailConfig, guardContent, ApplyGuardrail

가드레일을 트래픽에 거는 방법은 세 가지이며, 혼동하면 사각지대가 생긴다([Converse API에서 가드레일 사용](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-use-converse-api.html)).

1. **`guardrailConfig`(전면 적용)** — Converse 호출의 모든 메시지에 적용. 가장 일반적인 모드.
2. **`guardContent` 블록(선택 평가)** — 요청에 `guardContent` 블록이 하나라도 있으면, 대부분의 필터(content filters, denied topics, PII, contextual grounding)는 **블록 안의 콘텐츠만** 평가한다(word filter는 예외적으로 전체를 본다). 시스템 프롬프트는 자체 `guardContent` 블록이 없으면 평가되지 않는다. 에이전틱/RAG 워크로드에서 사용자 입력만 감싸고 툴 결과·검색 청크를 감싸지 않으면, 간접 인젝션 경로가 통째로 미평가 상태가 된다.
3. **`ApplyGuardrail` 독립 API** — 모델 호출 없이 임의 텍스트를 검사한다. `source: INPUT | OUTPUT`과 콘텐츠만 넘기면 `action: GUARDRAIL_INTERVENED | NONE`, 마스킹된 출력, 정책별 assessment를 반환한다([공식 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-use-independent-api.html)). RAG에서 검색 전에 사용자 입력을 선검사하거나, retrieval-only 경로로 반환된 원문 청크를 별도 스캔하는 데 쓴다 — 후자는 [RAG 엔타이틀먼트 스코핑](/09-authorization/rag-entitlement-scoping)에서 제시한 완화책과 같은 패턴이다.

### 가장 위험한 함정: 마스킹은 로그에 적용되지 않는다

::: danger PII 마스킹 ≠ PII가 어디에도 안 남음
Guardrails의 PII 마스킹은 **모델에 보내는 입력과 모델이 반환하는 응답에만** 적용된다. 공식 문서가 예외를 명시한다([Sensitive information filters — Note](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html)):

1. **Model invocation logs** — invocation logging을 켰다면 CloudWatch Logs의 `input` 필드에는 가드레일 개입 여부와 무관하게 **원본 그대로의 요청이 항상** 기록된다. 컴포넌트 문서도 별도로 경고한다: "All blocked content from the above policies will appear as plain text in Amazon Bedrock Model Invocation Logs"([Create your guardrail](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-components.html)).
2. **Trace 출력** — trace를 켜면 API 응답의 `GuardrailPiiEntityFilter.match` 필드에 **마스킹 전 원본 PII 값**이 담긴다. 탐지 결과를 애플리케이션 로직에 쓰라고 의도된 동작이다.

즉 "화면에는 `{PHONE}`이 보이는데 로그에는 010-XXXX-XXXX가 평문으로 있는" 상태가 기본값이다. 프로덕션 컴플라이언스 대응:

- CloudWatch Logs 로그 그룹을 customer-managed KMS 키로 암호화 — [Encrypt log data in CloudWatch Logs using AWS KMS](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/encrypt-log-data-kms.html)
- 로그 그룹 접근을 최소권한 IAM으로 제한하고, 보존 기간(retention)을 규제 요건에 맞게 명시적으로 설정 — 무기한 보존이 기본값이며, 개인정보 최소보유 원칙과 충돌한다
- CloudWatch Logs **data protection 정책**으로 로그 계층에서 별도 마스킹 — 공식 문서가 이 상황의 대응책으로 직접 지목하는 기능이다([Help protect sensitive log data with masking](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/mask-sensitive-log-data.html))
- S3로 로그를 내보낸다면 SSE-KMS + 퍼블릭 액세스 차단 + [Amazon Macie](https://docs.aws.amazon.com/macie/latest/user/what-is-macie.html)로 PII 잔존 탐지
- 민감 워크로드는 invocation logging 자체를 끄는 선택지도 검토 — 관측성과 컴플라이언스의 트레이드오프를 명시적으로 결정하라

trace는 프로덕션에서 `disabled`가 원칙이다. 디버깅으로 켰다면 그 API 응답 전체를 민감 데이터로 취급해야 한다([Test your guardrail](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-test.html)).
:::

### 에이전틱 사각지대: 툴 호출은 평가되지 않는다

에이전트 플랫폼에서 특히 치명적인 공식 명시 제약이 있다. sensitive information filter는 텍스트 콘텐츠만 평가하며, tool use 워크로드에서 다음은 **평가 대상이 아니다**([공식 문서 Note](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html)):

- 모델이 **툴 호출 인자에 생성한 PII**(`toolUse.input`) — 예: 모델이 고객 이메일을 파일 쓰기 툴의 인자로 넘기면 마스킹되지 않는다
- 애플리케이션이 모델에 돌려주는 **툴 결과 안의 PII**(`toolResult`)
- **툴 정의 자체**(`toolSpec.description`, `inputSchema`)

즉 "가드레일을 걸었으니 에이전트가 PII를 외부 툴로 내보내는 것도 막힌다"는 가정은 틀렸다. PII가 툴 경계를 넘는 것을 막으려면 툴 실행 게이트웨이에서 `ApplyGuardrail`로 인자를 별도 검사하거나 자체 필터를 둬야 한다. 이 경로는 [이그레스 컨트롤](/09-authorization/egress-control)의 문제의식과 정확히 겹친다.

### 스트리밍: sync vs async, 그리고 마스킹

`ConverseStream`에서 가드레일은 `streamProcessingMode`로 두 모드를 제공한다([Configure streaming response behavior](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-streaming.html)):

- **Synchronous(기본)** — 응답 청크를 버퍼링해 정책 적용 후 전송. 지연이 늘지만 정책 위반 콘텐츠가 화면에 나가지 않는다.
- **Asynchronous** — 청크를 즉시 전송하고 백그라운드에서 평가. 지연 영향은 없지만 스캔 완료 전까지 부적절 콘텐츠가 사용자에게 도달할 수 있고, **async 모드에서는 sensitive information masking이 지원되지 않는다**(같은 문서의 Warning). 이미 표출된 청크는 회수 불가.

이 트레이드오프의 지연 관점 분석과 청크 경계 문제(두 청크에 나뉘어 도착하는 주민등록번호)는 [스트리밍과 병렬 툴 호출](/02-performance/streaming-parallel-tools)에서 이미 다뤘다. 이 장의 결론만 반복하면: **PII 마스킹이 규제 요건인 워크로드에서 async 모드는 선택지가 아니다.** sync의 추가 지연을 받아들이거나, 의미 단위 버퍼링-검증-표출 절충을 직접 설계해야 한다.

### 한국어 지원과 tier: 정책마다 다르다

Guardrails는 Standard/Classic 두 tier를 제공하며, **언어 지원이 정책별로 갈린다**([Safeguard tiers](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-tiers.html), [Supported languages](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-supported-languages.html)):

| 정책 | 한국어 지원 |
|---|---|
| Content filters / prompt attacks | **Standard tier에서 Korean "Optimized and supported"**. Classic은 영/불/서만 |
| Denied topics | **Standard tier에서 Korean "Optimized and supported"**. Classic은 영/불/서만 |
| Sensitive information filters | **Korean "Optimized and supported"** (17개 언어 목록에 포함) |
| Word filters | **영어/프랑스어/스페인어만** — 한국어 미지원 |
| Contextual grounding checks | **영어/프랑스어/스페인어만** — 한국어 미지원 |

세 가지 실무 결론이 나온다. 첫째, **한국어 트래픽에는 Standard tier가 사실상 필수**다 — Classic tier의 content filter/denied topics는 한국어를 지원하지 않으므로, Classic으로 한국어 프롬프트를 흘리면 필터가 "켜져 있지만 작동하지 않는" 상태가 된다. 공식 문서 스스로 경고한다: "Guardrails are ineffective with languages that aren't supported." 둘째, Standard tier는 [cross-Region inference](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-cross-region.html)를 사용한다 — 가드레일 평가 요청이 리전 경계를 넘을 수 있다는 뜻이므로, 국내 데이터 레지던시 요건이 있다면 guardrail profile의 대상 리전 집합을 검토해야 한다(레지던시 논의는 [하이브리드 아키텍처](/12-security-korea/hybrid-architecture) 참고). Seoul 리전은 safeguard tier 지원 리전 목록에 포함되어 있다. 셋째, **한국어 응답에는 contextual grounding check와 word filter를 신뢰할 수 없다** — 환각 억제가 필요하면 영어 내부 표현을 거치거나 별도 검증 레이어를 설계해야 한다.

### 한국어 PII: 내장 타입의 공백과 regex 보완

내장 PII 타입 목록을 보면 General(NAME, EMAIL, PHONE, ADDRESS, AGE, USERNAME, PASSWORD, DRIVER_ID, LICENSE_PLATE, VIN), Finance(CREDIT_DEBIT_CARD_*, PIN, IBAN, SWIFT), IT(IP, MAC, URL, AWS_ACCESS_KEY, AWS_SECRET_KEY), 그리고 국가 특화는 **USA·Canada·UK 세 나라뿐**이다([전체 목록](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html)). 한국 특화 타입 — 주민등록번호, 외국인등록번호, 국내 계좌번호, 사업자등록번호 — 은 존재하지 않는다.

따라서 한국 서비스의 PII 정책은 두 층으로 구성해야 한다:

1. **내장 타입으로 커버되는 것** — NAME, EMAIL, PHONE, ADDRESS 등 일반 타입. sensitive information filter가 한국어 "Optimized and supported"이므로 한국어 문맥의 이름·전화번호 탐지는 ML 필터에 맡길 수 있다. 단, PHONE 타입이 국내 모든 표기 변형(010-1234-5678, 01012345678, +82-10-…)을 잡는지는 자체 테스트 코퍼스로 실측해야 한다 — AWS가 형식별 재현율을 공표하지 않는다.
2. **regex 필터로 직접 정의하는 것** — 주민등록번호 등 한국 고유 식별자. regex 필터는 name 1~100자, pattern 1~500자, 타입별 BLOCK/ANONYMIZE/NONE 및 입력/출력 분리 설정을 지원하고, **lookaround(lookahead/lookbehind)는 지원하지 않는다**([공식 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html)).

```json
{
  "sensitiveInformationPolicyConfig": {
    "piiEntitiesConfig": [
      { "type": "NAME",  "action": "ANONYMIZE" },
      { "type": "EMAIL", "action": "ANONYMIZE" },
      { "type": "PHONE", "action": "ANONYMIZE" },
      { "type": "CREDIT_DEBIT_CARD_NUMBER", "action": "BLOCK" }
    ],
    "regexesConfig": [
      {
        "name": "KR-RRN",
        "description": "주민등록번호(생년월일 6 + 성별코드 1 + 6)",
        "pattern": "\\d{2}[01]\\d[0-3]\\d[- ]?[1-8]\\d{6}",
        "action": "BLOCK"
      },
      {
        "name": "KR-MOBILE",
        "description": "국내 휴대전화(내장 PHONE의 보완)",
        "pattern": "01[016789][- ]?\\d{3,4}[- ]?\\d{4}",
        "action": "ANONYMIZE"
      }
    ]
  }
}
```

::: warning 미정착 영역
한국어 PII regex의 "정답 패턴"은 업계 표준이 없다. 위 패턴은 예시이며 그대로 프로덕션에 넣을 물건이 아니다. 주민등록번호는 마지막 자리가 검증번호(check digit)지만 regex로는 체크섬을 계산할 수 없으므로 형식 매칭만 가능하고, lookaround 미지원 제약 때문에 전후 문맥 조건(예: "숫자 13자리이되 더 긴 숫자열의 일부가 아닐 것")도 걸 수 없다 — 오탐(운송장 번호, 시리얼)과 미탐(공백·개행이 끼어든 변형) 양쪽이 구조적으로 발생한다. 계좌번호는 은행마다 자릿수 체계가 달라 단일 regex로 정밀 매칭이 불가능하다. 결론: regex 필터는 1차 그물이고, 오탐/미탐률을 자체 테스트 코퍼스로 측정한 뒤 임계 워크로드에는 후단 검증(체크섬 검증 로직, 전용 PII 탐지 서비스)을 겹쳐라. 어느 조합이 표준인지는 아직 정착되지 않았다.
:::

### 우회 방지: 가드레일은 강제되어야 정책이다

개발자가 API 호출에서 `guardrailConfig`를 빼먹으면 가드레일은 그냥 적용되지 않는다. 이를 정책으로 승격하려면 IAM condition key `bedrock:GuardrailIdentifier`로 "지정 가드레일 없는 InvokeModel/Converse를 Deny"하는 정책을 걸거나([Use condition keys to enforce guardrails](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-permissions-id.html)), 계정/조직 레벨 강제 구성을 쓴다([Guardrail enforcements](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-enforcements.html)). 버전 관리도 강제의 일부다: `DRAFT`는 가변이므로 프로덕션은 반드시 numbered version을 고정하라 — DRAFT를 참조하면 누군가의 콘솔 수정이 곧바로 프로덕션 동작 변경이 된다.

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요
> 로컬 운영 노하우(사내 Bedrock skill 문서) 기준으로, contextual grounding check의 grounding/relevance threshold는 0.7에서 시작해 "정상 응답이 차단되면 낮추고, 환각이 통과하면 올리는" 방식의 평가 기반 튜닝이 권장된다. 시작값 0.7 자체는 공식 권고 수치로 확인하지 못했다 — threshold 의미와 설정 방법은 [공식 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-contextual-grounding-check.html)를 기준으로 삼고, 시작값은 자체 평가셋으로 결정하라. 단, 앞서 확인했듯 contextual grounding은 한국어 미지원이므로 한국어 응답에는 이 튜닝 자체가 성립하지 않는다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| PII가 등장할 이유가 없는 공개 지식 Q&A | PII 액션 BLOCK | 유출 허용치 0, 마스킹된 응답도 불필요 | 오탐 시 정상 응답까지 차단 — canned message UX 필요 |
| 상담 요약 등 PII가 필연적으로 섞이는 워크로드 | PII 액션 ANONYMIZE | 응답은 필요하되 식별자만 제거 | 로그에는 원본이 남는다 — 로그 계층 대응 필수 |
| 정책 도입 전 오탐률 측정 | 액션 NONE(detect 모드) + assessment 수집 | 트래픽 영향 없이 탐지 분포 확보 | 보호 효과는 0 — 측정 기간을 명시적으로 한정 |
| 한국어 트래픽 + content filter/denied topics | Standard tier | Classic은 한국어 미지원 — 켜도 무효 ([공식](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-supported-languages.html)) | cross-Region inference 사용 — 레지던시 검토 필요 |
| 주민등록번호·국내 계좌번호 | custom regex 필터 (+ 후단 검증) | 내장 타입에 한국 특화 타입 부재 ([공식 목록](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html)) | lookaround 미지원, 체크섬 검증 불가 — 오탐/미탐 실측 필요 |
| 스트리밍 + PII 마스킹 요건 | sync 모드 (또는 비스트리밍) | async는 마스킹 미지원 + 노출 후 차단 ([공식](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-streaming.html)) | 청크 버퍼링 지연 |
| RAG 검색 청크·툴 인자 검사 | ApplyGuardrail 독립 API | 모델 호출 없이 임의 텍스트 검사 가능 ([공식](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-use-independent-api.html)) | 호출 추가 = 지연·비용 추가, 파이프라인 통합 코드 필요 |
| 시스템 프롬프트는 제외하고 비신뢰 콘텐츠만 평가 | guardContent 블록 | 필터 대상을 명시적으로 지정 | **모든** 비신뢰 콘텐츠(툴 결과 포함)를 감싸야 함 — 누락 = 사각지대 |
| 개발자의 가드레일 누락 방지 | `bedrock:GuardrailIdentifier` condition key / 계정·조직 강제 | 코드 리뷰가 아니라 IAM이 강제 ([공식](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-permissions-id.html)) | 가드레일 변경 시 IAM 정책 동기화 운영 부담 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 응답은 마스킹되는데 감사에서 로그의 평문 PII 발견 | PII 마스킹은 API 입출력에만 적용 — invocation log의 `input`은 항상 원본 ([공식](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html)) | 해당 로그 그룹에서 마스킹된 요청의 원본 조회 | KMS 암호화 + IAM 제한 + retention 설정 + CloudWatch data protection 마스킹, 필요시 logging 비활성 |
| 한국어 유해 프롬프트가 content filter를 그대로 통과 | Classic tier — 한국어 미지원 언어에서 가드레일 무효 | 가드레일의 tier 설정 확인, 한국어 red-team 프롬프트로 실측 | Standard tier 전환 + cross-Region profile 검토 |
| 주민등록번호가 마스킹되지 않고 응답에 노출 | 내장 타입에 KR 타입 없음 / regex 미정의 또는 표기 변형 미탐 | ApplyGuardrail로 대표 변형(하이픈·공백·개행) 일괄 검사 | regex 필터 추가·보강, 변형 코퍼스 기반 회귀 테스트 |
| 에이전트가 고객 PII를 외부 API 툴 인자로 전송 | sensitive information filter는 `toolUse.input`/`toolResult`를 평가하지 않음 ([공식](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html)) | 툴 게이트웨이 로그에서 인자 원문 확인 | 툴 실행 전 ApplyGuardrail/자체 필터로 인자 검사 — [이그레스 컨트롤](/09-authorization/egress-control) |
| 스트리밍 화면에 PII가 잠깐 표출된 후 차단 | async 모드 — 스캔 전 청크 선전송, 마스킹 미지원 | `streamProcessingMode` 설정 확인 | sync 전환 또는 의미 단위 버퍼링 — [스트리밍 장](/02-performance/streaming-parallel-tools) |
| 툴 결과에 실린 인젝션이 가드레일 미평가로 통과 | guardContent 블록을 사용자 입력에만 적용 — 블록 밖 콘텐츠는 대부분의 필터가 건너뜀 | 요청 페이로드에서 guardContent 래핑 범위 확인 | 모든 비신뢰 콘텐츠(툴 결과, 검색 청크)를 guardContent로 래핑 |
| 디버깅 후 응답 로그에 원본 PII 잔존 | trace enabled — `match` 필드에 마스킹 전 원본 포함 | 응답 저장 경로에서 trace 객체 존재 확인 | 프로덕션 trace disabled, 디버깅 응답은 민감 데이터로 취급 |
| 어느 날 갑자기 차단/통과 기준이 바뀜 | 프로덕션이 DRAFT 버전 참조 — 콘솔 수정이 즉시 반영 | guardrailVersion 값 확인 | numbered version 고정, 변경은 새 버전 발행으로만 |
| 가드레일이 아예 적용되지 않은 트래픽 발견 | 호출 코드에서 guardrailConfig 누락 — 기본은 미적용 | CloudTrail/게이트웨이 로그에서 guardrail 파라미터 유무 집계 | `bedrock:GuardrailIdentifier` condition key 또는 계정/조직 강제 |

## 안티패턴

- ❌ **가드레일 설정 완료 = PII 컴플라이언스 완료로 보고** → ✅ 로그 계층(invocation logs, trace, S3 export)까지 포함한 데이터 흐름도를 그리고, 각 저장 지점의 암호화·접근제어·보존기간을 개별 확인.
- ❌ **Classic tier 가드레일에 한국어 트래픽을 흘리면서 "필터가 켜져 있다"고 안심** → ✅ 지원 언어 표를 정책별로 대조하고, 한국어 red-team 코퍼스로 실측. 미지원 언어에서 가드레일은 무효라는 공식 경고를 전제하라.
- ❌ **주민등록번호를 내장 PHONE/PIN 타입이 잡아주길 기대** → ✅ 한국 특화 타입은 없다. regex 필터를 명시 정의하고 표기 변형 코퍼스로 회귀 테스트.
- ❌ **guardContent로 사용자 입력만 감싸고 툴 결과·RAG 청크는 방치** → ✅ 비신뢰 콘텐츠 전부를 래핑하거나, 전면 적용(guardrailConfig) + 필요 지점 ApplyGuardrail 이중화.
- ❌ **지연 줄이려고 async 스트리밍으로 전환하고 PII 정책은 그대로 유지** → ✅ async는 마스킹 미지원이다. 전환 결정에 규제 요건 대조를 포함하라.
- ❌ **가드레일을 인젝션 방어의 전부로 취급** → ✅ prompt attack 필터는 여러 레이어 중 하나다. 시스템 프롬프트 설계, 권한 최소화, 이그레스 통제와 함께 봐야 한다 — [프롬프트 인젝션](/12-security-korea/prompt-injection).
- ❌ **DRAFT 버전을 프로덕션에서 참조** → ✅ numbered version 고정. 변경은 새 버전 발행 + 단계적 롤아웃.
- ❌ **가드레일 적용을 개발자 관례에 맡김** → ✅ IAM condition key 또는 계정/조직 레벨 강제로 "누락하면 호출 자체가 거부"되게 만든다.

## 계측 (SLI)

가드레일은 정책이자 트래픽 경로 위의 컴포넌트다. 두 관점 모두 계측한다.

- **개입률(intervention rate)**: `action: GUARDRAIL_INTERVENED` 비율을 정책별(assessment의 topicPolicy/contentPolicy/sensitiveInformationPolicy/…)로 분해 추적. 급등은 공격 또는 오탐 폭주, 0 고착은 미적용(누락) 신호.
- **PII 탐지 분포**: sensitive information assessment의 타입별 탐지 건수(내장 타입 vs custom regex 분리). regex 탐지가 0이면 패턴이 죽었거나 트래픽 성격이 바뀐 것 — 둘 다 조사 대상.
- **오탐률/미탐률**: 골든 코퍼스(정상 문장 + PII 변형 문장)를 주기적으로 `ApplyGuardrail`에 흘려 회귀 측정. NONE(detect) 모드를 섀도 가드레일로 병행 운영하면 프로덕션 영향 없이 신규 정책 후보를 평가할 수 있다.
- **가드레일 지연**: assessment의 `invocationMetrics.guardrailProcessingLatency`와 `guardrailCoverage`(검사된 문자 수/전체)를 수집([ApplyGuardrail 응답 스키마](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-use-independent-api.html)). sync 스트리밍에서는 이 값이 TTFT 예산에 직접 얹힌다 — [지연의 해부](/02-performance/latency-anatomy)의 예산 프레임에 편입하라.
- **적용 커버리지**: 전체 모델 호출 대비 guardrail 파라미터가 실린 호출 비율(게이트웨이/CloudTrail 기준). 100% 미만이면 강제 메커니즘이 뚫린 것.
- **로그 계층 잔존 PII**: S3 export에 대한 Macie 스캔 결과 또는 CloudWatch data protection 탐지 카운트를 별도 SLI로 — "응답 마스킹률"과 "저장 데이터 잔존률"은 다른 지표다.
- **구성 변경 감사**: CloudTrail의 CreateGuardrail/UpdateGuardrail/CreateGuardrailVersion 이벤트에 알람 — 보호 약화가 조용히 일어나지 않게 ([Bedrock CloudTrail logging](https://docs.aws.amazon.com/bedrock/latest/userguide/logging-using-cloudtrail.html)).

## 체크리스트

- [ ] 정책별 적용 지점(입력/출력)과 액션(BLOCK/ANONYMIZE/NONE)을 표로 문서화하고 워크로드별로 결정했다
- [ ] **invocation logging이 켜진 로그 그룹의 원본 PII 잔존을 인지하고 대응했다** — KMS 암호화, IAM 최소권한, retention, CloudWatch data protection(또는 logging 비활성)
- [ ] 프로덕션 가드레일의 trace가 disabled다 (켜야 한다면 응답 저장 경로를 민감 데이터로 취급)
- [ ] 한국어 트래픽에 Standard tier를 사용하며, cross-Region inference의 대상 리전을 레지던시 요건과 대조했다
- [ ] word filter·contextual grounding이 한국어 미지원임을 인지하고 해당 기능에 의존하는 설계가 없다
- [ ] 주민등록번호 등 한국 고유 PII에 대한 custom regex를 정의하고, 표기 변형 코퍼스로 오탐/미탐을 실측했다
- [ ] 툴 호출 인자(`toolUse.input`)와 툴 결과가 가드레일 미평가 대상임을 인지하고 툴 게이트웨이에 별도 검사를 두었다
- [ ] guardContent를 쓴다면 사용자 입력뿐 아니라 툴 결과·RAG 청크 등 모든 비신뢰 콘텐츠를 래핑했다
- [ ] 스트리밍 경로의 `streamProcessingMode`를 규제 요건과 대조해 명시적으로 선택했다 (PII 마스킹 요건 = sync)
- [ ] retrieval-only RAG 경로의 원문 청크에 ApplyGuardrail 방어가 있다 ([RAG 엔타이틀먼트 스코핑](/09-authorization/rag-entitlement-scoping) 참조)
- [ ] 프로덕션이 DRAFT가 아닌 numbered version을 참조하며, 변경은 새 버전 발행으로만 이뤄진다
- [ ] `bedrock:GuardrailIdentifier` condition key 또는 계정/조직 레벨 강제로 가드레일 누락 호출이 거부된다
- [ ] 개입률·PII 탐지 분포·가드레일 지연·적용 커버리지·로그 잔존 PII를 SLI로 수집하고 있다
- [ ] 가드레일 구성 변경(CloudTrail 이벤트)에 알람이 걸려 있다

## 참고

- [Create your guardrail (컴포넌트 개요) — Amazon Bedrock User Guide](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-components.html) — 정책 목록, "blocked content는 invocation log에 평문" 경고
- [Remove PII from conversations by using sensitive information filters](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html) — 내장 PII 타입 전체 목록, BLOCK/ANONYMIZE/NONE, regex 제약(1~500자·lookaround 미지원), 로그·trace·tool use 예외
- [Languages supported by Amazon Bedrock Guardrails](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-supported-languages.html) — 정책별 한국어 지원 여부
- [Safeguard tiers for guardrails policies](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-tiers.html) — Standard vs Classic, cross-Region inference, 지원 리전
- [Configure streaming response behavior to filter content](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-streaming.html) — sync/async 모드, async의 마스킹 미지원
- [Use the ApplyGuardrail API in your application](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-use-independent-api.html) — 독립 검사 API, 응답 스키마, invocationMetrics
- [Use a guardrail with the Converse API](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-use-converse-api.html) — guardrailConfig, guardContent 동작
- [Use contextual grounding check to filter hallucinations](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-contextual-grounding-check.html)
- [Use condition keys to enforce guardrails](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-permissions-id.html) · [Guardrail enforcements](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-enforcements.html)
- [Encrypt log data in CloudWatch Logs using AWS KMS](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/encrypt-log-data-kms.html) · [Help protect sensitive log data with masking](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/mask-sensitive-log-data.html)
- 관련 장: [스트리밍과 병렬 툴 호출](/02-performance/streaming-parallel-tools) · [RAG 엔타이틀먼트 스코핑](/09-authorization/rag-entitlement-scoping) · [이그레스 컨트롤](/09-authorization/egress-control) · [프롬프트 인젝션](/12-security-korea/prompt-injection) · [국내 금융권 규제 지형](/12-security-korea/korea-fsc-regulation) · [하이브리드 아키텍처](/12-security-korea/hybrid-architecture)
