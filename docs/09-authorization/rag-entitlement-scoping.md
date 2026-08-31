---
title: RAG 엔타이틀먼트 스코핑
description: Bedrock Knowledge Bases의 메타데이터 필터링과 ACL-aware retrieval로 RAG 파이프라인에 데이터 레벨 엔타이틀먼트를 적용하는 방법을 다룬다.
outline: [2, 3]
---

# RAG 엔타이틀먼트 스코핑

::: tip 이 장에서 얻는 것
- Bedrock Knowledge Bases 메타데이터 필터링의 정확한 스펙(크기 한도, 타입, 연산자, 예약 키)과 managed KB에서 달라지는 제약
- ACL-aware retrieval이 실제로 무엇을 보장하고 무엇을 보장하지 않는지 — "ACL awareness is not authorization"의 의미
- Web Crawler 데이터소스처럼 ACL을 아예 적용할 수 없는 커넥터를 식별하고 우회하는 방법
- Cedar(툴 레벨) + OBO(신원 전파) + KB 메타데이터 필터(데이터 레벨)로 구성되는 3중 방어에서 이 장이 차지하는 위치
:::

## 왜 문제가 되는가

에이전트에 RAG를 붙이는 순간, 권한 문제의 성격이 바뀐다. 툴 호출 레벨의 권한 제어(Cedar 정책, OBO 토큰 교환)는 "이 에이전트가 이 API를 호출할 수 있는가"를 판정하지만, RAG는 그 API 호출 *안에서* 임의의 문서 조각을 골라 LLM 컨텍스트에 밀어넣는다. 벡터 검색은 텍스트 유사도로 문서를 고르기 때문에, 애초에 검색 대상 코퍼스에 사용자가 접근 권한이 없는 문서가 섞여 있다면 유사도가 충분히 높으면 그 문서가 그대로 검색 결과에 포함되고, LLM은 그 내용을 요약해서 사용자에게 되돌려준다. 이것은 툴 호출 인가와 전혀 다른 실패 표면이다 — Cedar 정책이 완벽하게 맞아도, OBO로 신원이 정확히 전파되어도, 벡터 인덱스 자체가 테넌트/부서/문서등급을 구분하지 않으면 막을 수 없다.

더 나쁜 것은 이 유출이 "정상 동작"처럼 보인다는 점이다. 접근 거부 에러가 나지 않는다. 사용자는 그냥 답을 받는다. 감사 로그에도 "검색 성공"으로만 남는다. RAG 엔타이틀먼트 스코핑은 이 표면을 데이터 레벨에서 닫는 작업이다 — 검색이 실행되기 *전에* 후보 문서 집합을 사용자의 엔타이틀먼트로 좁히고, 그 위에 쿼리 시점 재확인을 얹는 것.

## 핵심 개념

### 메타데이터 필터링: 사전 필터(pre-filter)로서의 엔타이틀먼트

Amazon Bedrock Knowledge Bases는 문서마다 메타데이터 속성을 붙이고, 검색(Retrieve/RetrieveAndGenerate/AgenticRetrieveStream) 요청에 `RetrievalFilter`를 실어 이 속성으로 후보 문서를 좁힐 수 있다. 핵심은 이 필터링이 벡터 유사도 계산 *이전* 단계에서 적용되는 사전 필터라는 점이다 — 즉 필터에 걸려 제외된 문서는 유사도가 얼마나 높든 애초에 top-k 후보에 들어오지 않는다.

self-managed(custom) 데이터소스의 경우, S3 커넥터는 문서마다 `<filename>.<ext>.metadata.json` 사이드카 파일을 요구하며, **이 메타데이터 파일은 10 KB를 넘을 수 없다.**

> 출처: [Amazon S3 데이터소스 커넥터 — Document metadata files](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-ds-s3.html)

메타데이터 속성의 타입은 API 스펙상 `STRING | NUMBER | BOOLEAN | STRING_LIST` 네 가지다("double"과 "integer"는 별도 타입이 아니라 `NUMBER` 하나로 통합되어 있다).

> 출처: [MetadataAttributeSchema](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_MetadataAttributeSchema.html)

쿼리 시점에 사용하는 `RetrievalFilter`는 union 타입으로, 다음 연산자를 지원한다: `equals`, `notEquals`, `greaterThan`, `greaterThanOrEquals`, `lessThan`, `lessThanOrEquals`, `in`, `notIn`, `startsWith`, `stringContains`, `andAll`, `orAll`.

> 출처: [RetrievalFilter API Reference](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_RetrievalFilter.html)

**단, 이 전체 목록이 managed knowledge base에는 그대로 적용되지 않는다.** managed KB에서는 `startsWith`와 `stringContains`가 지원되지 않으며, 대신 `equals`/`greaterThan`/`lessThan`/`in`/`notIn`을 조합해야 한다. 범위 연산자(`greaterThan` 계열)는 날짜-시간 필터링 시 ISO-8601 offset 형식 문자열만 받는다.

> 출처: [Configure and customize queries for managed knowledge bases — Manual metadata filtering](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-test-config.html)

예약 메타데이터 키의 접두사도 KB 타입에 따라 다르다. self-managed(custom) KB에서는 `x-amz-bedrock` 접두사가 서비스 예약어이고(예: `x-amz-bedrock-kb-source-uri`로 S3 경로를 필터링, `x-amz-bedrock-kb-data-source-id`로 데이터소스를 필터링), managed KB에서는 언더스코어 접두사(`_source_uri`, `_data_source_id`)가 동일한 역할을 한다. 두 타입 모두 예약 필드를 사용자가 덮어쓸 수 없다.

> 출처: [kb-managed-test-config.html](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-test-config.html), [Amazon Connect — Knowledge base retrieval configuration](https://docs.aws.amazon.com/connect/latest/adminguide/multiple-knowledge-base-setup-and-content-segmentation.html)

실무적으로는 이 사전 필터를 엔타이틀먼트 표현 수단으로 쓴다 — `tenant_id`, `department`, `doc_classification` 같은 커스텀 메타데이터를 문서마다 부여하고, 매 검색 요청마다 호출 주체의 엔타이틀먼트(세션 컨텍스트에서 파생)를 `andAll`/`orAll`로 조합해 강제한다.

```json
{
  "retrievalConfiguration": {
    "vectorSearchConfiguration": {
      "numberOfResults": 8,
      "filter": {
        "andAll": [
          {
            "equals": {
              "key": "tenant_id",
              "value": "acct-7f21a9"
            }
          },
          {
            "in": {
              "key": "department",
              "value": ["finance", "legal"]
            }
          },
          {
            "orAll": [
              {
                "equals": {
                  "key": "doc_classification",
                  "value": "internal"
                }
              },
              {
                "andAll": [
                  {
                    "equals": {
                      "key": "doc_classification",
                      "value": "confidential"
                    }
                  },
                  {
                    "in": {
                      "key": "clearance_level",
                      "value": ["L3", "L4"]
                    }
                  }
                ]
              }
            ]
          }
        ]
      }
    }
  }
}
```

이 예시는 세 가지를 동시에 강제한다: (1) `tenant_id` equals로 멀티테넌시 격리, (2) `department` in으로 부서 경계, (3) `doc_classification`과 `clearance_level`의 조합으로 문서등급별 접근 — `confidential` 문서는 `clearance_level`이 L3 이상인 요청자에게만 노출된다. 이 필터 객체는 요청마다 애플리케이션 서버가 세션의 검증된 신원 컨텍스트로부터 동적으로 생성해야 한다. 필터를 클라이언트(에이전트 프롬프트)가 스스로 구성하게 두면 곧바로 confused deputy 패턴이 된다 — 이 챕터의 안티패턴 절에서 다룬다.

### ACL-aware retrieval: pre-filter + real-time verification

메타데이터 필터가 애플리케이션이 직접 설계하는 엔타이틀먼트 축이라면, managed KB는 SharePoint·OneDrive·Google Drive·Confluence 같은 커넥터에서 크롤링한 네이티브 ACL을 그대로 인식하는 기능을 별도로 제공한다. 공식 문서는 이 동작을 두 단계로 명시한다.

> **How ACL-aware retrieval works**
> - **Pre-retrieval filtering** — 수집(ingestion) 시점에 크롤링해 둔 allow/deny 리스트로, 쿼리 시점에 제공된 사용자 컨텍스트와 대조해 후보를 좁힌다.
> - **Real-time ACL verification** — 이를 지원하는 커넥터(SharePoint, OneDrive, Google Drive, Confluence)에 한해, 반환하려는 각 문서에 대해 원본 데이터소스에 실시간으로 재확인 호출을 보내 동기화 사이 발생한 권한 변경을 잡아낸다.

> 출처: [Access Control Lists awareness enablement](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-acl.html)

같은 문서의 커넥터 지원 매트릭스가 사용자 브리핑보다 더 정확한 그림을 준다 — "managed KB는 항상 실시간 재확인을 한다"는 명제는 부정확하다. 실제로는:

| 커넥터 | Pre-retrieval filter | Real-time ACL | 비고 |
|---|---|---|---|
| SharePoint | 지원 | 지원 | ENTRA_ID_APP_ONLY 인증 필요 |
| OneDrive | 지원 | 지원 | ENTRA_APP_ID 인증 필요 |
| Google Drive | 지원 | 지원 | 도메인 위임(SERVICE_ACCOUNT) 필요 |
| Confluence | 지원 | 지원 | 관리자 API 토큰(BASIC) 필요 |
| Amazon S3 | 지원 | **미지원** | 고객 제공 ACL 파일이 source of truth이므로 실시간 재확인 대상 없음 |
| Custom | 지원 | **미지원** | 고객 제공 메타데이터가 source of truth |
| Web Crawler | **미지원** | N/A | 웹 콘텐츠에는 권한 모델 자체가 없음 |

> 출처: [Access Control Lists awareness enablement — Connector support matrix](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-acl.html)

pre-retrieval filtering 단계에서 걸러진 문서는 애초에 검색 결과 집합에 들어오지 않는다 — 즉 LLM 프롬프트에도, 최종 사용자 응답에도 노출되지 않는 **transient**한 존재다. 이 시점에서 걸러지지 않고 반환된 문서만 real-time 단계로 넘어가 재검증되며, 두 단계 모두 "allow에 있고 deny에 없어야 함"이라는 동일한 평가 로직을 쓴다. deny는 allow를 항상 override한다.

실패 처리(failure behavior)도 fail-closed로 명시되어 있다 — group 해석 실패, 실시간 검증 타임아웃, 내부 오류가 발생하면 해당 문서는 반환되지 않는다. 즉 일시적 장애가 미인가 사용자에게 문서를 노출시키는 방향으로는 절대 작동하지 않는다.

> 출처: [Access Control Lists awareness enablement — Failure behavior](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-acl.html)

### "ACL awareness is not authorization" — 가장 중요한 한 줄

이 장에서 인용할 가장 중요한 문장은 기능 설명이 아니라 경고문이다. 공식 문서는 다음을 명시적으로 못박는다.

> **ACL awareness is not authorization:** Bedrock Managed Knowledge Base provides ACL-aware filtering, not a security boundary. Bedrock Managed Knowledge Base does not authenticate end users — your application is responsible for authenticating users and passing verified identity context. Because Bedrock Managed Knowledge Base cannot verify the authenticity of the user context you provide, this feature filters results based on the identity you supply but does not constitute true authorization.

> 출처: [Access Control Lists awareness enablement — Your responsibilities](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-acl.html)

풀어 말하면: KB에 넘기는 `userContext.userId`나 이메일이 진짜 그 사용자의 것인지 KB는 검증하지 않는다. 애플리케이션이 이미 인증을 마치고 신뢰할 수 있는 신원을 넘겨준다는 전제 위에서만 이 필터링이 의미를 갖는다. 위조된(또는 상위 계층에서 이미 confused deputy에 당한) 신원을 그대로 전달하면, KB의 ACL-aware retrieval은 그 위조된 신원 기준으로 "정확하게" 필터링해줄 뿐이다 — 보안 경계가 아니라 신뢰된 입력에 대한 필터라는 점을 정확히 이해해야 한다.

### 데이터소스 자체가 ACL을 모르는 경우: Web Crawler

Web Crawler 데이터소스의 공식 문서는 이 제약을 직접적으로 서술한다.

> **Note:** The Web Crawler does not support document-level access control (ACLs). All indexed content is accessible to any user who has access to the knowledge base. If you need ACL filtering, use a connector that supports it (for example, Amazon S3, SharePoint, or OneDrive).

> 출처: [Web Crawler data source connector](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-ds-webcrawler.html)

이유는 단순하다 — 웹 콘텐츠에는 크롤링할 수 있는 네이티브 권한 모델 자체가 없다. 내부망 위키를 Web Crawler로 색인하면서 "이 페이지는 인사팀만 봐야 한다"는 규칙을 ACL 크롤링으로 구현할 방법이 없다는 뜻이다. 이런 코퍼스는 반드시 커스텀 메타데이터 필터(department 등)로 스코핑하거나, ACL을 크롤링할 수 있는 커넥터(S3, SharePoint 등)로 우회해야 한다.

### 스트리밍 검색과 가드레일의 상호작용

managed KB에서 스트리밍 응답이 필요하면 `RetrieveAndGenerateStream`이 아니라 `AgenticRetrieveStream`(또는 `Retrieve`)을 사용해야 한다 — API 문서가 명시적으로 "`RetrieveAndGenerateStream`은 managed knowledge base에 사용할 수 없다"고 적어 두었다.

> 출처: [RetrieveAndGenerateStream](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_RetrieveAndGenerateStream.html)

`AgenticRetrieveStream`은 요청에 `policyConfiguration.bedrockGuardrailConfiguration`(`guardrailId`/`guardrailVersion`만 받음)과 `userContext.userId`(ACL 필터링용)를 함께 실을 수 있다.

> 출처: [AgenticRetrieveStream API Reference](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_AgenticRetrieveStream.html)

::: warning 미정착 영역
사용자 브리핑에서 언급된 "AgenticRetrieveStream 가드레일은 BLOCK만 가능하고 MASK는 불가"라는 제약을, 위 API 문서에서 문자 그대로 확인할 수는 없었다. `AgenticRetrieveBedrockGuardrailConfiguration`은 `guardrailId`/`guardrailVersion`만 받고 액션(BLOCK/ANONYMIZE)에 대한 호출 단위 오버라이드 필드가 없다 — 즉 액션 자체는 가드레일 리소스 정의에 귀속된다. 한편 Bedrock Guardrails 문서는 `ANONYMIZE`(MASK) 액션을 "if sensitive information is detected in the **model response**"로 한정해 정의한다([Guardrails harmful content handling options](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-harmful-content-handling-options.html)). `AgenticRetrieveStream`은 `generateResponse: false`로 호출하면 모델이 생성한 응답 없이 원문 청크(`result.results[].content.text`)만 반환한다. 이 경우 마스킹할 "model response"가 존재하지 않으므로, 순수 retrieval-only 경로에서는 ANONYMIZE 액션이 원문 청크에 적용될 근거 자체가 없다는 것이 합리적 추론이지만, 이는 필자의 해석이며 AWS가 이를 "MASK 미지원"이라고 명문화한 문서는 확인하지 못했다. 실무에서는 이 가정에 의존하지 말고, retrieval-only 경로로 반환되는 원문 청크에 대해 별도로 `ApplyGuardrail` API를 호출해 민감정보를 명시적으로 처리하는 것을 권장한다.
:::

스트리밍과 가드레일을 함께 쓸 때는 지연시간 트레이드오프도 있다. 가드레일은 스트리밍 출력을 청크 단위로 버퍼링해서 평가하므로, 특히 time-to-first-token(TTFT)에 추가 지연이 발생한다.

> 출처: [Create AI guardrails for AI agents — Important things to know](https://docs.aws.amazon.com/connect/latest/adminguide/create-ai-guardrails.html)

### 3중 방어에서 이 장의 위치

이 파트에서 다루는 세 가지 통제는 서로 다른 레이어에서 서로 다른 질문에 답한다.

- **Cedar/Verified Permissions**([/09-authorization/cedar-verified-permissions](./cedar-verified-permissions)) — "이 에이전트/툴이 이 액션을 수행할 권한이 있는가?"를 툴 호출 레벨에서 판정한다. 정책은 액션·리소스 단위이며, 문서 하나하나를 알지 못한다.
- **Per-tool OBO**([/09-authorization/per-tool-obo](./per-tool-obo)) — "이 호출이 실제로 누구를 대신해 이루어지는가?"를 신원 전파 레벨에서 보장한다. Cedar 정책이 평가할 신원 컨텍스트 자체가 위조되지 않았음을 담보하는 것이 이 장의 역할이다.
- **RAG 엔타이틀먼트 스코핑(이 장)** — "검색이 실제로 어떤 문서 집합을 대상으로 실행되는가?"를 데이터 레벨에서 좁힌다. Cedar가 "이 사용자는 KB를 쿼리할 수 있다"고 허용해도, 그 KB 안의 개별 문서 단위 접근은 Cedar의 관할이 아니다 — 벡터 인덱스는 Cedar 정책을 모른다.

세 통제는 서로를 대체하지 않는다. Cedar가 툴 경계를 지키고, OBO가 그 경계를 넘는 신원이 진짜임을 보증하고, 메타데이터 필터/ACL-aware retrieval이 그 신원으로 어떤 문서까지 볼 수 있는지를 좁힌다. 어느 한 층이라도 "다른 층이 이미 처리했을 것"이라고 가정하고 생략하면, 그 층에서 정확히 그 유형의 유출이 발생한다 — 특히 RAG는 유출이 조용히 "정상 응답"으로 위장되기 때문에 가장 늦게 발견된다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 멀티테넌트 SaaS의 자체 문서 코퍼스(S3 등 self-managed) | 커스텀 메타데이터 필터(`tenant_id` 등)를 매 요청마다 서버가 동적으로 주입 | 사전 필터라 유사도 계산 전에 후보를 좁혀 비용·유출 리스크를 동시에 줄임 | 메타데이터 설계·유지 비용, 10 KB 한도 내에서 속성 수 제한 |
| SharePoint/OneDrive/Google Drive/Confluence의 기존 문서 | ACL-aware retrieval 활성화 + 애플리케이션이 검증한 identity context 전달 | 네이티브 권한 변경이 실시간 재확인으로 반영됨 | 크롤링 주기 사이의 그룹 멤버십은 최신이 아닐 수 있음, 인증 방식(2LO 등) 요건 충족 필요 |
| S3/Custom 데이터소스에 ACL이 필요한 경우 | 커스텀 ACL 메타데이터를 `IngestKnowledgeBaseDocuments`로 직접 부여 | 유일한 방법 — S3/Custom은 real-time verification을 지원하지 않으므로 pre-retrieval filter가 유일한 방어선 | 권한 변경 시 재수집(ingestion) 필요, 지연된 반영 |
| 공개 웹페이지를 포함해야 하는데 내부 민감 페이지도 있는 코퍼스 | Web Crawler로는 민감 페이지를 색인하지 말고, 별도 커넥터(S3 등)로 분리 + 메타데이터로 스코핑 | Web Crawler는 ACL 크롤링 자체를 지원하지 않음 | 파이프라인 이원화, 운영 복잡도 증가 |
| 스트리밍 응답이 필요한 managed KB | `AgenticRetrieveStream` 사용(`RetrieveAndGenerateStream`은 managed KB 미지원) | API가 명시적으로 강제 | retrieval-only 경로에서 가드레일 MASK 동작이 불확실하므로 별도 `ApplyGuardrail` 방어 필요 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| managed KB에서 `startsWith` 필터가 조용히 무시되거나 검증 에러 | managed KB는 `startsWith`/`stringContains`를 지원하지 않음 | 필터 응답/에러 코드 확인, KB가 managed/custom 중 어느 타입인지 재확인 | `equals`/`in`/범위 연산자로 재작성 |
| 다른 부서 문서가 응답에 등장 | 필터 구성을 클라이언트(에이전트 프롬프트)가 스스로 생성하게 방치 → confused deputy | 요청 로그에서 필터 객체가 어디서 생성됐는지 추적 | 필터는 서버 측에서 세션의 검증된 신원 컨텍스트로만 생성 |
| SharePoint 권한을 방금 회수했는데 여전히 검색됨 | 재수집(ingestion) 주기 사이 — real-time verification이 실패했거나 타임아웃 | ACL 실패 시 fail-closed로 0건 반환되는지, 아니면 오류로 인해 재시도 로직이 stale 캐시를 쓰는지 확인 | 재확인 타임아웃/재시도 정책 점검, 긴급 회수는 강제 재수집으로 보완 |
| 내부 위키(Web Crawler로 색인)의 민감 페이지가 전 직원에게 노출 | Web Crawler는 ACL을 아예 크롤링하지 못함 | 데이터소스 타입이 Web Crawler인지 확인 | 민감 페이지는 S3/SharePoint 등 ACL 지원 커넥터로 재색인, 또는 메타데이터로 별도 격리 |
| 커스텀 메타데이터 필터를 걸었는데도 10 KB 초과로 인제스트 실패 | 메타데이터 파일이 10 KB 한도 초과 | 인제스트 실패 로그, 문서별 metadata.json 크기 | 속성 수 축소, 긴 문자열 속성을 별도 조회 테이블로 이전하고 KB에는 키만 저장 |
| `AgenticRetrieveStream` retrieval-only 응답에 PII가 마스킹되지 않고 그대로 노출 | ANONYMIZE 액션은 "model response"에 한정되고, retrieval-only에는 model response가 없음(미정착 영역 참고) | `generateResponse` 값 확인, 반환된 `result.results[].content.text`에 원문 그대로인지 확인 | 원문 청크에 별도 `ApplyGuardrail` 호출을 추가하거나 항상 `generateResponse: true`로 생성 경로를 통과시킴 |

## 안티패턴

- ❌ 에이전트 프롬프트/LLM 출력이 `RetrievalFilter` JSON 자체를 구성하게 둔다 → ✅ 필터는 서버가 인증된 세션 컨텍스트에서만 생성하고, 에이전트에는 필터 값을 바꿀 권한을 주지 않는다.
- ❌ "ACL-aware retrieval을 켰으니 KB가 인가를 대신 해준다"고 가정한다 → ✅ KB는 넘겨받은 신원을 검증하지 않는다(ACL awareness is not authorization). 상위 계층의 인증/OBO가 여전히 필수다.
- ❌ Web Crawler로 사내 위키 전체를 색인하고 "필요하면 나중에 ACL을 켠다"고 미룬다 → ✅ Web Crawler는 ACL 크롤링을 지원하지 않는다는 사실을 색인 전에 확인하고, 민감 콘텐츠는 처음부터 다른 커넥터로 분리한다.
- ❌ 메타데이터 필터와 ACL-aware retrieval 중 하나만 쓰면 충분하다고 판단한다 → ✅ 둘은 서로 다른 축(테넌트/부서 vs. 네이티브 문서 권한)이라 대부분 함께 필요하다.
- ❌ managed KB의 필터 문법을 self-managed KB 문서에서 그대로 복붙한다(`startsWith` 등) → ✅ KB 타입별 지원 연산자 목록을 먼저 확인한다.

## 계측 (SLI)

RAG 엔타이틀먼트 스코핑은 "필터가 걸려 있다"는 사실만으로는 검증되지 않는다 — 다음을 계측해야 실제로 스코핑이 작동하는지 확인할 수 있다.

- **필터 적용률**: 전체 Retrieve/RetrieveAndGenerate/AgenticRetrieveStream 호출 중 엔타이틀먼트 필터(`andAll`에 tenant/department 등 필수 키가 포함됨) 없이 실행된 비율. 0%가 목표.
- **ACL 실패 시 반환 건수**: real-time ACL verification이 실패(타임아웃/에러)했을 때 반환된 문서 수 — fail-closed 동작이 맞다면 항상 0이어야 한다. 0이 아니면 즉시 조사 대상.
- **거부 문서 누출 카나리아**: 의도적으로 특정 테넌트/부서에만 속한 카나리아 문서를 주입하고, 다른 테넌트 신원으로 정기적으로 쿼리해 카나리아가 검색되는지 감시.
- **retrieval-only 경로의 PII 노출률**: `generateResponse: false`로 반환된 원문 청크에 대해 별도 `ApplyGuardrail` 스캔을 돌려 민감정보 탐지율을 별도로 추적(가드레일이 생성 경로에서만 작동한다는 미정착 영역을 실측으로 보완).
- **메타데이터 크기 근접 경고**: 10 KB 한도의 80% 이상을 사용하는 문서 비율 — 인제스트 실패로 이어지기 전 조기 경보.

## 체크리스트

- [ ] 모든 RAG 검색 요청에 서버 측에서 생성한 엔타이틀먼트 필터(`andAll`/`orAll`)가 강제되는가, 클라이언트가 필터를 조작할 수 없는가
- [ ] KB 타입(managed/self-managed)별로 지원되는 필터 연산자를 확인했는가(`startsWith`/`stringContains` 사용 금지 여부 포함)
- [ ] 예약 메타데이터 키 접두사(managed: `_source_uri` 등, self-managed: `x-amz-bedrock-kb-source-uri` 등)를 커스텀 속성명과 충돌시키지 않았는가
- [ ] 각 데이터소스 커넥터의 ACL 지원 여부(pre-retrieval / real-time)를 커넥터별로 확인했는가, Web Crawler에 민감 콘텐츠가 섞여 있지 않은가
- [ ] ACL 검증 실패 시 fail-closed로 동작하는지(0건 반환) 실제로 테스트했는가
- [ ] 스트리밍 검색에 `RetrieveAndGenerateStream`이 아닌 `AgenticRetrieveStream`/`Retrieve`를 사용하는가(managed KB인 경우)
- [ ] retrieval-only(`generateResponse: false`) 경로에 대해 별도 `ApplyGuardrail` 방어가 있는가
- [ ] 메타데이터 파일이 10 KB 한도 내에 있는가, 대용량 속성을 KB 밖 조회 테이블로 분리했는가
- [ ] Cedar 정책(툴 레벨)과 OBO(신원 전파)가 이 장의 데이터 레벨 스코핑과 계층적으로 연결되어 있는가 — 어느 한 층도 다른 층이 대신 처리한다고 가정하지 않는가
- [ ] 카나리아 문서 기반 누출 탐지를 정기적으로 실행하는가

## 참고

- [Access Control Lists awareness enablement](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-acl.html)
- [Configure and customize queries for managed knowledge bases](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-test-config.html)
- [Amazon S3 data source connector — Document metadata files](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-ds-s3.html)
- [Web Crawler data source connector](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-ds-webcrawler.html)
- [RetrievalFilter API Reference](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_RetrievalFilter.html)
- [MetadataAttributeSchema API Reference](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_MetadataAttributeSchema.html)
- [AgenticRetrieveStream API Reference](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_AgenticRetrieveStream.html)
- [RetrieveAndGenerateStream API Reference](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_RetrieveAndGenerateStream.html)
- [Options for handling harmful content detected by Amazon Bedrock Guardrails](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-harmful-content-handling-options.html)
- 관련 챕터: [Cedar와 Verified Permissions](./cedar-verified-permissions), [툴별 On-Behalf-Of](./per-tool-obo)
